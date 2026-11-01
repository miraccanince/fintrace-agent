import hashlib
import json
import subprocess
import sys

import pytest

from fintrace import agent, harness, mcp_client, tools
from fintrace.banking_db import connect_readonly
from fintrace.synthetic_data import generate_database_with_scenarios


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mcp")
    db_path, truth_path = directory / "banking.db", directory / "truth.json"
    generate_database_with_scenarios(db_path, truth_path, seed=42)
    truth = {e["scenario_id"]: e for e in json.loads(truth_path.read_text())["scenarios"]}
    connection = connect_readonly(db_path)
    with mcp_client.connect(db_path) as (advertised, mcp_execute):
        yield {
            "path": db_path, "truth": truth, "advertised": advertised,
            "mcp": mcp_execute, "local": tools.make_executor(connection),
        }
    connection.close()


def calls_for(loan_id):
    loan = {"loan_id": loan_id}
    return [
        ("get_loan", loan), ("get_balance_snapshots", loan), ("get_transactions", loan),
        ("compare_latest_snapshots", loan), ("summarize_payment_refs", loan),
        ("get_batch_runs", {"system": "loan_system", "date_from": "2026-10-01", "date_to": "2026-10-10"}),
    ]


def test_discovery_finds_exactly_the_six_tools_with_our_schemas(world):
    advertised = world["advertised"]

    assert set(advertised) == set(tools.TOOLS)
    for name, contract in tools.contracts().items():
        assert advertised[name]["parameters"] == contract["parameters"]


def test_every_tool_gives_the_same_result_over_mcp_as_in_process(world):
    for scenario in world["truth"].values():
        for name, arguments in calls_for(scenario["loan_id"]):
            assert world["mcp"](name, arguments) == world["local"](name, arguments)


@pytest.mark.parametrize("name, arguments", [("get_loan", {}), ("get_loan", {"loan_id": 5}), ("delete_loan", {"loan_id": "LN-1"}), ("get_loan", {"loan_id": "LN-NOPE"})])
def test_bad_calls_and_unknown_loans_give_the_same_structured_result(world, name, arguments):
    assert world["mcp"](name, arguments) == world["local"](name, arguments)


def test_calls_over_mcp_never_change_the_database(world):
    before = hashlib.sha256(world["path"].read_bytes()).hexdigest()

    for scenario in world["truth"].values():
        for name, arguments in calls_for(scenario["loan_id"]):
            world["mcp"](name, arguments)

    assert hashlib.sha256(world["path"].read_bytes()).hexdigest() == before


def scripted(*steps):
    queue = list(steps)
    return lambda history, visible: queue.pop(0)


def test_a_harness_run_over_mcp_gives_the_same_report_as_in_process(world):
    loan_id = world["truth"]["S1"]["loan_id"]
    final = {"type": "final", "finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}
    steps = [{"type": "tool_call", "tool": "summarize_payment_refs", "arguments": {"loan_id": loan_id}},
             {"type": "tool_call", "tool": "bogus", "arguments": {}}, final]

    over_mcp = harness.run_investigation(loan_id, scripted(*steps), world["mcp"], advertised=world["advertised"])
    in_process = harness.run_investigation(loan_id, scripted(*steps), world["local"])

    assert over_mcp == in_process
    assert [t["outcome"] for t in over_mcp["trace"]] == ["ok", "rejected", "ok"]


def test_a_batch_with_a_failing_call_gives_the_same_report_over_mcp_as_in_process(world):
    loan_id = world["truth"]["S1"]["loan_id"]
    batch = {"type": "tool_calls", "calls": [
        {"tool": "get_loan", "arguments": {"loan_id": loan_id}},
        {"tool": "get_transactions", "arguments": {}},
        {"tool": "compare_latest_snapshots", "arguments": {"loan_id": loan_id}},
    ]}
    final = {"type": "final", "finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}

    over_mcp = harness.run_investigation(loan_id, scripted(batch, final), world["mcp"], advertised=world["advertised"])
    in_process = harness.run_investigation(loan_id, scripted(batch, final), world["local"])

    assert over_mcp == in_process
    assert [t["outcome"] for t in over_mcp["trace"][:3]] == ["ok", "rejected", "not_executed"]


def test_cli_mcp_flag_gives_the_same_report_as_the_default(world, capsys):
    loan_id = world["truth"]["S4"]["loan_id"]
    final = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}

    def run(*extra):
        reply = lambda name, args: {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}
        replies = [reply("compare_latest_snapshots", {"loan_id": loan_id}), reply("submit_final_answer", final)]
        assert agent.main([loan_id, "--db", str(world["path"]), *extra], call_model=lambda *a: replies.pop(0)) == 0
        return json.loads(capsys.readouterr().out)

    assert run("--mcp") == run()


def test_server_that_cannot_open_its_database_exits_cleanly_without_protocol_output(tmp_path):
    done = subprocess.run([sys.executable, "-m", "fintrace.mcp_server", "--db", str(tmp_path / "missing.db")], capture_output=True, text=True, timeout=30)

    assert done.returncode == 2 and done.stdout == "" and "cannot open database" in done.stderr
    assert not (tmp_path / "missing.db").exists()


def test_connecting_to_a_server_that_fails_to_start_raises_instead_of_hanging(tmp_path):
    with pytest.raises(Exception):
        with mcp_client.connect(tmp_path / "missing.db"):
            pass
