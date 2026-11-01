import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from fintrace import agent, gemini_adapter, harness, tools
from fintrace.banking_db import connect_readonly
from fintrace.synthetic_data import generate_database_with_scenarios


def reply(*calls, text=None):
    parts = [SimpleNamespace(function_call=SimpleNamespace(name=n, args=a), text=None) for n, a in calls]
    if text:
        parts.insert(0, SimpleNamespace(function_call=None, text=text))
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))])


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
    step = agent._to_step(reply(("get_loan", {"loan_id": "LN-1"}), text=None))

    assert step == {"type": "tool_call", "tool": "get_loan", "arguments": {"loan_id": "LN-1"}}


def test_commentary_text_next_to_one_call_is_ignored():
    assert agent._to_step(reply(("get_loan", None), text="Let me look."))["arguments"] == {}


def test_submit_final_answer_becomes_a_final_step_and_cannot_override_the_type():
    answer = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": [], "type": "tool_call"}

    step = agent._to_step(reply(("submit_final_answer", answer)))

    assert step == {**answer, "type": "final"}


@pytest.mark.parametrize(
    "response",
    [
        reply(("get_loan", {}), ("get_transactions", {})),
        reply(("submit_final_answer", {}), ("get_loan", {})),
        reply(text="I think it is a timing issue."),
        reply(),
        SimpleNamespace(candidates=[]),
        SimpleNamespace(candidates=None),
        SimpleNamespace(),
    ],
)
def test_anything_but_exactly_one_function_call_is_an_invalid_step(response):
    step = agent._to_step(response)

    assert step["type"] == "invalid" and step["reason"]


def test_multiple_function_calls_report_their_names_in_the_reason():
    step = agent._to_step(reply(("get_loan", {"loan_id": "LN-1", "secret": "s3cr3t"}), ("get_transactions", {})))

    assert step["type"] == "invalid"
    assert "get_loan" in step["reason"] and "get_transactions" in step["reason"]
    assert "s3cr3t" not in step["reason"]


def test_system_prompt_requires_exactly_one_function_call_per_response():
    assert "exactly one function call" in agent.SYSTEM_PROMPT


# --- history -> messages --------------------------------------------------------------------


def test_history_is_rendered_as_alternating_model_and_user_messages():
    history = [
        {"role": "task", "loan_id": "LN-1", "max_tool_calls": 8},
        {"role": "tool_call", "tool": "get_loan", "arguments": {"loan_id": "LN-1"}},
        {"role": "tool_result", "tool": "get_loan", "outcome": "ok", "content": {"found": True}, "remaining_tool_calls": 7},
        {"role": "tool_call", "tool": "bogus", "arguments": "not-an-object"},
        {"role": "tool_result", "tool": "bogus", "outcome": "rejected", "content": {"error": "tool not allowed: bogus"}, "remaining_tool_calls": 6},
    ]

    contents = agent._contents(history)

    assert [c["role"] for c in contents] == ["user", "model", "user", "model", "user"]
    assert "LN-1" in contents[0]["parts"][0]["text"] and "8" in contents[0]["parts"][0]["text"]
    assert contents[1]["parts"][0]["function_call"] == {"name": "get_loan", "args": {"loan_id": "LN-1"}}
    assert contents[2]["parts"][0]["function_response"] == {
        "name": "get_loan", "response": {"outcome": "ok", "content": {"found": True}, "remaining_tool_calls": 7}}
    assert contents[3]["parts"][0]["function_call"]["args"] == {}
    assert contents[4]["parts"][0]["function_response"]["response"]["outcome"] == "rejected"


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
    for word in ("S1", "S2", "S3", "S4", "PAY-", "LN-5", "partial posting", "reversal", "batch"):
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


def test_a_reply_with_two_function_calls_is_rejected_by_the_harness(db):
    report, _ = run(db, "S1", [reply(("get_loan", {"loan_id": "x"}), ("get_loan", {"loan_id": "y"}))])

    assert report["stop_reason"] == "invalid_model_output" and report["tool_calls"]["agent_requested"] == 0


# --- provider retry ----------------------------------------------------------------------------------


class FakeClient:
    outcomes: list = []
    config = None
    calls = 0

    def __init__(self):
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        FakeClient.calls += 1
        FakeClient.config = config
        outcome = FakeClient.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_gemini(monkeypatch):
    FakeClient.outcomes, FakeClient.calls, FakeClient.config = [], 0, None
    monkeypatch.setattr(gemini_adapter.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_adapter, "RETRY_DELAY_SECONDS", 0)
    return FakeClient


def server_error():
    return errors.ServerError(503, {"error": {"message": "busy", "status": "UNAVAILABLE"}})


def call_adapter():
    return gemini_adapter.generate_with_tools(
        [{"role": "user", "parts": [{"text": "hi"}]}], [dict(tools.contracts()["get_loan"])], "system text")


def rate_limit(delay):
    details = [{"@type": "type.googleapis.com/google.rpc.Help"}]
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return errors.ClientError(429, {"error": {"code": 429, "message": "slow", "status": "RESOURCE_EXHAUSTED", "details": details}})


def test_transient_errors_are_retried_up_to_three_attempts(fake_gemini):
    fake_gemini.outcomes = [server_error(), rate_limit("0s"), "reply"]

    assert call_adapter() == "reply" and fake_gemini.calls == 3


@pytest.mark.parametrize("delay", ["31423s", "61s", None, "soon"])
def test_a_429_without_a_short_requested_delay_is_not_retried(fake_gemini, delay):
    fake_gemini.outcomes = [rate_limit(delay), "never reached"]

    with pytest.raises(errors.ClientError):
        call_adapter()
    assert fake_gemini.calls == 1


def test_a_short_429_waits_at_least_the_requested_delay(fake_gemini, monkeypatch):
    waits = []
    monkeypatch.setattr(gemini_adapter.time, "sleep", waits.append)
    monkeypatch.setattr(gemini_adapter, "RETRY_DELAY_SECONDS", 5)
    fake_gemini.outcomes = [rate_limit("12s"), rate_limit("1s"), server_error(), "x"]

    with pytest.raises(errors.ServerError):
        call_adapter()
    assert waits == [12.0, 5]  # the requested 12 s, then the 5 s floor for a 1 s request


def test_the_third_transient_failure_is_raised(fake_gemini):
    fake_gemini.outcomes = [server_error(), server_error(), server_error()]

    with pytest.raises(errors.ServerError):
        call_adapter()
    assert fake_gemini.calls == 3


@pytest.mark.parametrize("error", [errors.ClientError(400, {"error": {"message": "bad", "status": "INVALID_ARGUMENT"}}), ValueError("no key")])
def test_non_transient_errors_are_not_retried(fake_gemini, error):
    fake_gemini.outcomes = [error, "never reached"]

    with pytest.raises(type(error)):
        call_adapter()
    assert fake_gemini.calls == 1


def test_automatic_function_calling_is_disabled_and_declarations_are_sent(fake_gemini):
    fake_gemini.outcomes = ["reply"]

    call_adapter()

    config = fake_gemini.config
    assert config.automatic_function_calling.disable is True
    assert config.system_instruction == "system text"
    assert [d.name for d in config.tools[0].function_declarations] == ["get_loan"]


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


def test_cli_fails_clearly_without_an_api_key_and_prints_no_secret(db, capsys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    code = agent.main(["LN-1", "--db", str(db["path"])])

    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert "GEMINI_API_KEY is not set" in captured.err
    assert "=" not in captured.err.split("GEMINI_API_KEY")[1]  # names the variable, shows no value
