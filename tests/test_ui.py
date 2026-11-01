import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from fintrace import api
from fintrace.synthetic_data import generate_database_with_scenarios


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "banking.db"
    generate_database_with_scenarios(db_path, tmp_path / "ground_truth.json", seed=42)
    monkeypatch.setattr(api, "_DB_PATH", str(db_path))
    return TestClient(api.app)


def test_known_discrepancy_loan_returns_the_comparison(client):
    response = client.get("/api/loans/LN-5008/comparison")

    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True
    assert data["status"] == "discrepancy"
    assert data["difference"] == "7000.00"
    assert data["loan_system"]["amount"] and data["risk_system"]["amount"]


def test_unknown_loan_is_a_200_not_found_result(client):
    response = client.get("/api/loans/LN-NOPE/comparison")

    assert response.status_code == 200
    assert response.json() == {"loan_id": "LN-NOPE", "found": False}


def test_missing_database_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", str(tmp_path / "missing.db"))
    client = TestClient(api.app)

    response = client.get("/api/loans/LN-5008/comparison")

    assert response.status_code == 503


def test_ui_serves_the_html_page(client):
    response = client.get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Compare Balances" in response.text and 'id="loan-id"' in response.text


def test_ui_route_duplicates_no_business_calculation():
    import inspect

    source = inspect.getsource(api.loan_comparison)
    assert "Decimal(" not in source and "difference" not in source


# --- AI investigation ---------------------------------------------------------------------------------


def agent_reply(name, arguments):
    return {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


@pytest.fixture
def fake_model():
    """Install a scripted model for the endpoint; returns the list of requests it served."""
    served = []

    def install(*replies):
        queue = list(replies)

        def call_model(messages, declarations, system_prompt):
            served.append(messages)
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        api.app.dependency_overrides[api.get_agent_call_model] = lambda: call_model
        return served

    yield install
    api.app.dependency_overrides.clear()


def test_ai_investigation_returns_the_full_report_over_mcp(client, fake_model):
    answer = {"finding": "possible_explanation", "explanation": "may be partial posting", "evidence_references": ["TX-00087"]}
    served = fake_model(
        agent_reply("summarize_payment_refs", {"loan_id": "LN-5008"}), agent_reply("get_transactions", {"loan_id": "LN-5008"}),
        agent_reply("submit_final_answer", answer))

    response = client.post("/api/loans/LN-5008/investigation")

    report = response.json()
    assert response.status_code == 200 and len(served) == 3
    assert report["stop_reason"] == "final_answer" and report["finding"] == "possible_explanation"
    assert report["explanation"] == "may be partial posting" and report["cited_records"] == ["TX-00087"]
    assert report["requires_human_review"] is True and report["authoritative"]["difference"] == "7000.00"
    assert [t["tool"] for t in report["trace"]] == ["summarize_payment_refs", "get_transactions", "compare_latest_snapshots"]
    assert [t["origin"] for t in report["trace"]] == ["agent", "agent", "harness"]


@pytest.mark.parametrize("failure", [RuntimeError("ollama is down"), {"message": {"content": "no tool call"}}])
def test_an_incomplete_investigation_is_a_normal_result_with_no_finding(client, fake_model, failure):
    fake_model(failure)

    response = client.post("/api/loans/LN-5008/investigation")

    report = response.json()
    assert response.status_code == 200 and report["stop_reason"] != "final_answer"
    assert report["finding"] is None and report["explanation"] is None and report["cited_records"] == []
    assert report["requires_human_review"] is True and report["authoritative"]["difference"] == "7000.00"


def test_an_unknown_loan_does_not_start_the_model(client, fake_model):
    served = fake_model()

    response = client.post("/api/loans/LN-NOPE/investigation")

    assert response.status_code == 200 and response.json() == {"loan_id": "LN-NOPE", "found": False} and served == []


def test_a_missing_database_returns_503_for_the_investigation(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", str(tmp_path / "missing.db"))

    assert TestClient(api.app).post("/api/loans/LN-5008/investigation").status_code == 503


def test_only_one_investigation_runs_at_a_time_and_the_lock_is_always_released(client, fake_model):
    served = fake_model(RuntimeError("boom"), agent_reply("submit_final_answer", {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}))

    assert api._investigation_lock.acquire(blocking=False)
    try:
        busy = client.post("/api/loans/LN-5008/investigation")
    finally:
        api._investigation_lock.release()
    first = client.post("/api/loans/LN-5008/investigation")  # fails inside the model
    second = client.post("/api/loans/LN-5008/investigation")  # lock was released after the failure

    assert busy.status_code == 409 and len(served) == 2  # the busy request never reached the model
    assert first.json()["stop_reason"] == "model_error" and second.json()["stop_reason"] == "final_answer"


def test_compare_balances_is_still_the_same_separate_deterministic_call(client):
    page = client.get("/ui").text

    assert 'id="investigate">Compare Balances</button>' in page
    assert 'id="ai-investigate"' in page and "Investigate with AI" in page
    assert "/api/loans/${encodeURIComponent(loanId)}/comparison" in page  # unchanged compare request


def test_model_generated_content_is_rendered_with_textcontent_only():
    page = (api.Path(api.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    ai_code = page[page.index("// --- AI investigation"):page.index('button.addEventListener("click", investigate);')]

    assert ".textContent" in ai_code and "innerHTML" not in ai_code
    assert "insertAdjacentHTML" not in ai_code and "outerHTML" not in ai_code


def test_a_slow_investigation_keeps_the_connection_alive_with_leading_whitespace(client, fake_model, monkeypatch):
    import time

    monkeypatch.setattr(api, "_KEEPALIVE_SECONDS", 0.05)
    answer = {"finding": "insufficient_evidence", "explanation": "x", "evidence_references": []}
    fake_model(agent_reply("submit_final_answer", answer))
    original = api.agent.investigate
    monkeypatch.setattr(api.agent, "investigate", lambda *a, **k: (time.sleep(0.3), original(*a, **k))[1])

    response = client.post("/api/loans/LN-5008/investigation")

    assert response.text.startswith("\n") and len(response.text) - len(response.text.lstrip()) >= 2  # several keepalives
    assert response.json()["stop_reason"] == "final_answer"  # leading newlines are valid JSON whitespace
    assert api._investigation_lock.acquire(blocking=False)
    api._investigation_lock.release()


def test_an_unexpected_failure_is_reported_in_the_body_and_releases_the_lock(client, fake_model, monkeypatch):
    fake_model()
    monkeypatch.setattr(api.agent, "investigate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cannot start tool server")))

    response = client.post("/api/loans/LN-5008/investigation")

    assert response.status_code == 200 and response.json() == {"server_error": "RuntimeError: cannot start tool server"}
    assert api._investigation_lock.acquire(blocking=False)
    api._investigation_lock.release()


def test_the_run_goes_on_and_frees_the_lock_after_the_client_stops_listening(client, fake_model, monkeypatch):
    import threading

    started, finish = threading.Event(), threading.Event()
    fake_model()

    def slow_investigation(*args, **kwargs):
        started.set()
        finish.wait(5)
        return {"stop_reason": "model_error"}

    monkeypatch.setattr(api.agent, "investigate", slow_investigation)
    response = api.loan_investigation("LN-5008", call_model=lambda *a: None)  # headers only; the body is never read
    assert started.wait(5) and api._investigation_lock.locked()  # still running with nobody listening

    finish.set()
    api._investigation_worker.submit(lambda: None).result(5)  # the single worker is free once the run is done
    assert not api._investigation_lock.locked() and response.status_code == 200


def test_ai_result_keeps_citations_trace_and_technical_details_in_collapsible_sections():
    page = (api.Path(api.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    ai_code = page[page.index("// --- AI investigation"):page.index('button.addEventListener("click", investigate);')]

    assert 'el("details")' in ai_code
    for title in ("Cited records", "Tool-call trace", "Technical details"):
        assert title in ai_code
    assert "technical.open = !complete" in ai_code  # an incomplete run shows its details straight away
    assert "Human review required" in ai_code and "did not finish" in ai_code and "No finding was produced" in ai_code
    assert "Stop reason: ${report.stop_reason}" in ai_code  # the raw code lives only in the technical details


def test_the_page_never_injects_html_strings():
    page = (api.Path(api.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in page


NODE_RENDER = r"""
const fs = require("fs"), vm = require("vm");
class Node_ {
  constructor(tag) { this.tag = tag; this.kids = []; this.className = ""; this._text = ""; }
  set textContent(v) { this._text = String(v); this.kids = []; }
  get textContent() { return this._text + this.kids.map((k) => k.textContent).join(""); }
  append(...items) { items.forEach((i) => this.kids.push(typeof i === "string" ? Object.assign(new Node_("#text"), { _text: i }) : i)); }
  replaceChildren(...items) { this.kids = []; this.append(...items); }
  addEventListener() {}
  set innerHTML(v) { throw new Error("innerHTML used"); }
}
const els = {};
const document = { getElementById: (id) => (els[id] ??= new Node_(id)), createElement: (tag) => new Node_(tag) };
const script = fs.readFileSync(process.argv[2], "utf8").match(/<script>([\s\S]*)<\/script>/)[1];
const context = vm.createContext({ document, fetch() {}, setInterval() {}, clearInterval() {}, Number, Date, encodeURIComponent });
vm.runInContext(script, context);
vm.runInContext(`renderComparison(${process.argv[3]})`, context);
const tags = new Set();
const walk = (node) => { tags.add(node.tag); node.kids.forEach(walk); };
walk(els["result"]);
console.log(JSON.stringify({ text: els["result"].textContent, tags: [...tags] }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_compare_balances_shows_backend_values_as_text_never_as_markup(tmp_path):
    page = api.Path(api.__file__).parent / "static" / "index.html"
    script = tmp_path / "render.js"
    script.write_text(NODE_RENDER)
    hostile = "<img src=x onerror=alert(1)>"
    data = {
        "status": "missing_in_a", "difference": "7000.00", "lower_balance_system": "<script>alert(1)</script>",
        "loan_system": {"amount": "115000.00", "currency": "EUR", "as_of_date": hostile}, "risk_system": None,
        "snapshot_dates_aligned": False,
    }

    done = subprocess.run(["node", str(script), str(page), json.dumps(data)], capture_output=True, text=True, timeout=30)

    assert done.returncode == 0, done.stderr
    rendered = json.loads(done.stdout)
    assert hostile in rendered["text"] and "<script>alert(1)</script>" in rendered["text"]  # shown literally
    assert set(rendered["tags"]) <= {"result", "span", "div", "table", "thead", "tbody", "tr", "th", "td", "p", "#text"}
    for expected in ("missing in a", "€7,000.00", "€115,000.00", "no snapshot", "dates not aligned"):
        assert expected in rendered["text"]
