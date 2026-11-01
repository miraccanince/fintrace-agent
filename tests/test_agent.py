import json
import httpx
import pytest

from fintrace import agent, harness, ollama_adapter, tools
from fintrace.banking_db import connect_readonly
from fintrace.synthetic_data import generate_database_with_scenarios


def reply(*calls, text=None, extra=None):
    message = {"role": "assistant", "content": text or "", **(extra or {})}
    if calls:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return {"message": message}


def scripted_model(*replies):
    queue, seen = list(replies), []

    def call_model(contents, declarations, system_prompt):
        seen.append({"contents": contents, "declarations": declarations, "system": system_prompt})
        return queue.pop(0)

    call_model.seen = seen
    return call_model


@pytest.fixture
def db(tmp_path):
    path, truth_path = tmp_path / "banking.db", tmp_path / "truth.json"
    generate_database_with_scenarios(path, truth_path, seed=42)
    truth = {e["scenario_id"]: e for e in json.loads(truth_path.read_text())["scenarios"]}
    return {"path": path, "truth": truth}


# --- reply -> harness step ------------------------------------------------------------


def test_one_function_call_becomes_a_tool_call_step():
    response = reply(("get_loan", {"loan_id": "LN-1"}))

    step = agent._to_step(response)

    assert step == {
        "type": "tool_call", "tool": "get_loan", "arguments": {"loan_id": "LN-1"},
        "provider_content": response["message"],
    }


def test_commentary_text_next_to_one_call_is_ignored():
    assert agent._to_step(reply(("get_loan", None), text="Let me look."))["arguments"] == {}


def test_submit_final_answer_becomes_a_final_step_and_cannot_override_the_type():
    answer = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": [], "type": "tool_call"}

    step = agent._to_step(reply(("submit_final_answer", answer)))

    assert step == {**answer, "type": "final"}


@pytest.mark.parametrize(
    "response",
    [
        reply(("submit_final_answer", {}), ("get_loan", {})),
        reply(text="I think it is a timing issue."),
        reply(),
        {"message": None},
        {"message": {"tool_calls": "get_loan"}},
        {"message": {"tool_calls": [{"function": {"arguments": {}}}]}},
        {},
        None,
    ],
)
def test_anything_but_exactly_one_function_call_is_an_invalid_step(response):
    step = agent._to_step(response)

    assert step["type"] == "invalid" and step["reason"]


def test_final_answer_with_non_object_arguments_is_an_invalid_step():
    assert agent._to_step(reply(("submit_final_answer", "all done")))["type"] == "invalid"


def test_several_function_calls_become_one_ordered_batch_step():
    response = reply(("get_loan", {"loan_id": "LN-1"}), ("get_transactions", {"loan_id": "LN-1"}))

    assert agent._to_step(response) == {
        "type": "tool_calls",
        "calls": [{"tool": "get_loan", "arguments": {"loan_id": "LN-1"}}, {"tool": "get_transactions", "arguments": {"loan_id": "LN-1"}}],
        "provider_content": response["message"],
    }


def test_a_final_answer_mixed_with_other_calls_is_invalid_and_names_the_calls_without_arguments():
    step = agent._to_step(reply(("get_loan", {"loan_id": "LN-1", "secret": "s3cr3t"}), ("submit_final_answer", {})))

    assert step["type"] == "invalid"
    assert "get_loan" in step["reason"] and "submit_final_answer" in step["reason"]
    assert "s3cr3t" not in step["reason"]


def test_a_batch_with_an_unnamed_call_is_invalid():
    response = {"message": {"tool_calls": [{"function": {"name": "get_loan", "arguments": {}}}, {"function": {"arguments": {}}}]}}

    assert agent._to_step(response)["type"] == "invalid"


def test_system_prompt_asks_for_a_short_plain_explanation_without_record_ids_in_prose():
    prompt = agent.SYSTEM_PROMPT

    assert "about 60 words in at most 3 short sentences" in prompt and "plain English" in prompt
    assert "not in the explanation text" in prompt
    description = agent.FINAL_ANSWER_DECLARATION["parameters"]["properties"]["explanation"]["description"]
    assert "3 short sentences" in description and "no record ids" in description


def test_system_prompt_limits_citations_to_retrieved_records():
    prompt = agent.SYSTEM_PROMPT

    assert "ids of records that a tool returned as records" in prompt
    assert "transaction_id (TX-...)" in prompt and "snapshot_id (SN-...)" in prompt and "run_id (RUN-...)" in prompt
    assert "Do not cite payment references (PAY-...)" in prompt
    assert "batch_run_id, unless you retrieved that record" in prompt


def test_system_prompt_forbids_blaming_a_loan_for_a_batch_run_it_is_not_linked_to():
    prompt = agent.SYSTEM_PROMPT

    assert "covers many loans" in prompt
    assert "unless a retrieved record of this loan references that run" in prompt


def test_system_prompt_asks_to_stop_once_the_evidence_is_enough():
    assert "as soon as the records you retrieved are enough" in agent.SYSTEM_PROMPT


def test_system_prompt_allows_ordered_batches_and_requires_the_final_answer_alone():
    assert "independent lookups together in one response" in agent.SYSTEM_PROMPT
    assert "run in order" in agent.SYSTEM_PROMPT and "submit_final_answer alone" in agent.SYSTEM_PROMPT


# --- history -> messages --------------------------------------------------------------------


def test_history_is_rendered_as_chat_messages_with_tool_results_named_by_tool():
    history = [
        {"role": "task", "loan_id": "LN-1", "max_tool_calls": 8},
        {"role": "tool_call", "tool": "get_loan", "arguments": {"loan_id": "LN-1"}},
        {"role": "tool_result", "tool": "get_loan", "outcome": "ok", "content": {"found": True}, "remaining_tool_calls": 7},
        {"role": "tool_call", "tool": "bogus", "arguments": "not-an-object"},
        {"role": "tool_result", "tool": "bogus", "outcome": "rejected", "content": {"error": "tool not allowed: bogus"}, "remaining_tool_calls": 6},
    ]

    messages = agent._messages(history)

    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant", "tool"]
    assert "LN-1" in messages[0]["content"] and "8" in messages[0]["content"]
    assert messages[1]["tool_calls"] == [{"function": {"name": "get_loan", "arguments": {"loan_id": "LN-1"}}}]
    assert messages[2]["tool_name"] == "get_loan"
    assert json.loads(messages[2]["content"]) == {"outcome": "ok", "content": {"found": True}, "remaining_tool_calls": 7}
    assert messages[3]["tool_calls"][0]["function"]["arguments"] == {}
    assert json.loads(messages[4]["content"])["outcome"] == "rejected"


def test_model_is_offered_the_visible_tools_plus_the_final_answer_function():
    call_model = scripted_model(reply(("submit_final_answer", {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []})))
    visible = tools.contracts()

    step = agent.make_generate_step(call_model)([{"role": "task", "loan_id": "LN-1", "max_tool_calls": 8}], visible)

    sent = call_model.seen[0]
    assert [d["name"] for d in sent["declarations"]] == [*visible, "submit_final_answer"]
    assert sent["system"] == agent.SYSTEM_PROMPT and step["type"] == "final"
    assert agent.FINAL_ANSWER_DECLARATION["parameters"]["properties"]["finding"]["enum"] == list(harness.FINDINGS)
    assert agent.FINAL_ANSWER_TOOL not in tools.TOOLS


def test_system_prompt_is_generic_not_scenario_specific():
    # "batch run" is a generic tool concept (get_batch_runs); "PAY-" appears only to say what must not be cited.
    for word in ("S1", "S2", "S3", "S4", "LN-5", "partial posting", "reversal", "failed batch", "timeout"):
        assert word not in agent.SYSTEM_PROMPT


# --- agent + real harness and tools, scripted model --------------------------------------------


def run(db, scenario, replies, **kwargs):
    connection = connect_readonly(db["path"])
    try:
        loan_id = db["truth"][scenario]["loan_id"]
        model = scripted_model(*replies)
        report = harness.run_investigation(loan_id, agent.make_generate_step(model), tools.make_executor(connection), **kwargs)
        return report, model
    finally:
        connection.close()


def test_scripted_replies_drive_a_complete_investigation(db):
    loan_id = db["truth"]["S1"]["loan_id"]
    answer = {"finding": "possible_explanation", "explanation": "x", "evidence_references": db["truth"]["S1"]["required_evidence"]}

    report, model = run(db, "S1", [
        reply(("summarize_payment_refs", {"loan_id": loan_id})),
        reply(("compare_latest_snapshots", {"loan_id": loan_id})),
        reply(("submit_final_answer", answer)),
    ])

    assert report["stop_reason"] == "final_answer" and report["cited_records"] == answer["evidence_references"]
    assert len(model.seen[2]["contents"]) == 5  # task + two (call, result) pairs


ANSWER = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}


def outcomes(report):
    return [t["outcome"] for t in report["trace"] if t["origin"] == "agent"]


def test_a_reply_mixing_a_final_answer_with_a_call_is_rejected_by_the_harness(db):
    report, _ = run(db, "S1", [reply(("get_loan", {"loan_id": "x"}), ("submit_final_answer", ANSWER))])

    assert report["stop_reason"] == "invalid_model_output" and report["tool_calls"]["agent_requested"] == 0


def test_a_batch_runs_in_order_and_each_result_goes_back_after_the_one_reply(db):
    loan_id = db["truth"]["S1"]["loan_id"]
    batch = reply(("get_loan", {"loan_id": loan_id}), ("compare_latest_snapshots", {"loan_id": loan_id}))

    report, model = run(db, "S1", [batch, reply(("submit_final_answer", ANSWER))])

    assert [t["tool"] for t in report["trace"] if t["origin"] == "agent"] == ["get_loan", "compare_latest_snapshots"]
    assert outcomes(report) == ["ok", "ok"] and report["tool_calls"]["agent_requested"] == 2
    messages = model.seen[1]["contents"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool"]
    assert messages[1] == batch["message"] and len(messages[1]["tool_calls"]) == 2
    assert [m["tool_name"] for m in messages[2:]] == ["get_loan", "compare_latest_snapshots"]
    assert [json.loads(m["content"])["remaining_tool_calls"] for m in messages[2:]] == [7, 6]


@pytest.mark.parametrize("failing", [("bogus_tool", {}), ("get_loan", {})])  # not allowed / invalid arguments
def test_after_a_failed_call_the_rest_of_the_batch_is_not_executed(db, failing):
    loan_id = db["truth"]["S1"]["loan_id"]
    batch = reply(("get_loan", {"loan_id": loan_id}), failing, ("get_transactions", {"loan_id": loan_id}))

    report, model = run(db, "S1", [batch, reply(("submit_final_answer", ANSWER))])

    assert outcomes(report) == ["ok", "rejected", "not_executed"]
    assert report["tool_calls"]["agent_requested"] == 2  # a skipped call does not use budget
    skipped = [t for t in report["trace"] if t["outcome"] == "not_executed"][0]
    assert skipped["reason"] == "an earlier call in the same reply failed"
    results = [json.loads(m["content"]) for m in model.seen[1]["contents"][2:]]
    assert [r["outcome"] for r in results] == ["ok", "rejected", "not_executed"]
    assert report["stop_reason"] == "final_answer"


def test_a_batch_cannot_exceed_the_total_call_budget(db):
    loan_id = db["truth"]["S1"]["loan_id"]
    batch = reply(*[("get_loan", {"loan_id": loan_id})] * 3)

    report, _ = run(db, "S1", [batch], max_tool_calls=2)

    assert outcomes(report) == ["ok", "ok", "not_executed"]
    assert report["stop_reason"] == "tool_call_limit" and report["tool_calls"]["agent_requested"] == 2
    assert [t["reason"] for t in report["trace"] if t["outcome"] == "not_executed"] == ["tool call limit reached"]


# --- Ollama adapter ----------------------------------------------------------------------------------


class FakePost:
    def __init__(self, outcome):
        self.outcome, self.calls, self.url, self.kwargs = outcome, 0, None, None

    def __call__(self, url, **kwargs):
        self.calls, self.url, self.kwargs = self.calls + 1, url, kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeHttpResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://x"), response=httpx.Response(self.status))

    def json(self):
        return self.payload


def call_adapter(monkeypatch, outcome):
    post = FakePost(outcome)
    monkeypatch.setattr(ollama_adapter.httpx, "post", post)
    result = ollama_adapter.generate_with_tools(
        [{"role": "user", "content": "hi"}], [dict(tools.contracts()["get_loan"])], "system text")
    return result, post


def test_request_uses_native_chat_with_context_timeout_system_message_and_tools(monkeypatch):
    result, post = call_adapter(monkeypatch, FakeHttpResponse({"message": {"content": "ok"}}))

    body = post.kwargs["json"]
    assert result == {"message": {"content": "ok"}}
    assert post.url == f"{ollama_adapter.BASE_URL}/api/chat" and post.url.endswith("/api/chat")
    assert post.kwargs["timeout"] == 120
    assert body["options"] == {"num_ctx": 8192} and body["stream"] is False and body["model"] == ollama_adapter.MODEL
    assert body["messages"] == [{"role": "system", "content": "system text"}, {"role": "user", "content": "hi"}]
    assert [t["function"]["name"] for t in body["tools"]] == ["get_loan"]
    assert body["tools"][0]["type"] == "function" and "parameters" in body["tools"][0]["function"]


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timed out"), httpx.ConnectError("server down")])
def test_transport_failures_are_raised_once_not_retried(monkeypatch, failure):
    post = FakePost(failure)
    monkeypatch.setattr(ollama_adapter.httpx, "post", post)

    with pytest.raises(type(failure)):
        ollama_adapter.generate_with_tools([], [], "s")
    assert post.calls == 1


def test_an_http_error_status_is_raised(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        call_adapter(monkeypatch, FakeHttpResponse({}, status=500))


def test_harness_reports_a_timeout_as_a_model_error(db, monkeypatch):
    monkeypatch.setattr(ollama_adapter.httpx, "post", FakePost(httpx.ReadTimeout("timed out")))
    connection = connect_readonly(db["path"])
    try:
        report = harness.run_investigation(
            db["truth"]["S1"]["loan_id"], agent.make_generate_step(ollama_adapter.generate_with_tools), tools.make_executor(connection))
    finally:
        connection.close()

    assert report["stop_reason"] == "model_error" and "ReadTimeout" in report["error"]


# --- the model's own reply is replayed unchanged ---------------------------------------------------


def test_the_models_reply_is_replayed_unchanged_in_the_next_request(db):
    loan_id = db["truth"]["S1"]["loan_id"]
    answer = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}
    first = reply(("get_loan", {"loan_id": loan_id}), extra={"thinking": "look at the loan first"})

    report, model = run(db, "S1", [first, reply(("submit_final_answer", answer))])

    assert report["stop_reason"] == "final_answer"
    replayed = model.seen[1]["contents"][1]
    assert replayed == first["message"] and replayed is not first["message"]  # equal, though the harness deep-copies history
    assert replayed["thinking"] == "look at the loan first"
    result = model.seen[1]["contents"][2]
    assert result["role"] == "tool" and result["tool_name"] == "get_loan"


def test_history_without_provider_content_is_rebuilt_unsigned():
    messages = agent._messages([
        {"role": "task", "loan_id": "LN-1", "max_tool_calls": 8},
        {"role": "tool_call", "tool": "get_loan", "arguments": {}},
    ])

    assert messages[1] == {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_loan", "arguments": {}}}]}


# --- CLI --------------------------------------------------------------------------------------------------


def test_cli_prints_the_report_and_exits_zero(db, capsys):
    loan_id = db["truth"]["S4"]["loan_id"]
    final = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}
    model = scripted_model(reply(("compare_latest_snapshots", {"loan_id": loan_id})), reply(("submit_final_answer", final)))

    code = agent.main([loan_id, "--db", str(db["path"])], call_model=model)

    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["stop_reason"] == "final_answer" and report["authoritative"]["difference"] == "-4500.00"


def test_cli_passes_the_call_limit_to_the_harness(db, capsys):
    loan_id = db["truth"]["S4"]["loan_id"]
    model = scripted_model(*[reply(("get_loan", {"loan_id": loan_id}))] * 3)

    code = agent.main([loan_id, "--db", str(db["path"]), "--max-tool-calls", "2"], call_model=model)

    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["stop_reason"] == "tool_call_limit" and report["tool_calls"]["agent_budget"] == 2


def test_cli_reports_a_missing_database(tmp_path, capsys):
    code = agent.main(["LN-1", "--db", str(tmp_path / "missing.db")], call_model=scripted_model())

    captured = capsys.readouterr()
    assert code == 2 and "cannot open database" in captured.err and captured.out == ""
    assert not (tmp_path / "missing.db").exists()


def test_cli_uses_the_ollama_adapter_by_default(db, capsys, monkeypatch):
    loan_id = db["truth"]["S4"]["loan_id"]
    final = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}
    model = scripted_model(reply(("submit_final_answer", final)))
    monkeypatch.setattr(ollama_adapter, "generate_with_tools", model)

    code = agent.main([loan_id, "--db", str(db["path"])])

    assert code == 0 and json.loads(capsys.readouterr().out)["stop_reason"] == "final_answer" and len(model.seen) == 1
