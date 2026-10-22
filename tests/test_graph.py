"""The graph with a fake LLM: plan retry, self-check repair and the borrower guardrail, no network."""

import json

from app import graph, tools

FETCH = [{"id": i, "tool": t, "args": {"borrower": "Patel Agro"}, "purpose": t} for i, t in enumerate(
    ["get_financials", "get_gst_returns", "get_bank_statements", "get_credit_policy", "compute_ratios"], 1)]
GOOD = {"borrower": "Patel Agro", "loan_amount_cr": 5, "loan_type": "term_loan", "assumptions": ["use provisional"],
        "tasks": FETCH + [{"id": 6, "tool": "draft_section", "args": {"section": "Recommendation"}, "purpose": "rec"}]}


def fake(monkeypatch, plans):
    replies = iter(plans)
    monkeypatch.setattr(graph, "plan_json", lambda prompt: next(replies))
    monkeypatch.setattr(tools, "write_section", lambda section, facts, feedback="": f"{section} text.")


def test_bad_plan_is_retried_then_missing_sections_are_repaired(monkeypatch, tmp_path):
    monkeypatch.setattr(graph.docx_writer, "OUT", tmp_path)
    fake(monkeypatch, ['{"borrower": "Patel Agro", "loan_amount_cr": 0}', json.dumps(GOOD)])
    s = graph.run("Credit memo for Patel Agro, 5 crore term loan", "t1")

    assert s["plan_attempts"] == 2
    assert s["check"]["passed"] and s["check"]["repairs"] == 1
    added = [t for t in s["task_log"] if t["added_by"] == "self_check"]
    assert [t["tool"] for t in added] == ["draft_section"] * 4
    assert s["data"]["recommendation"] == "REFER TO COMMITTEE WITH CONDITIONS"
    assert (tmp_path / "t1.docx").exists()


def test_unknown_borrower_is_refused_before_any_llm_call(monkeypatch):
    fake(monkeypatch, [])
    s = graph.run("Credit memo for Tata Motors, 10 crore", "t2")
    assert "doesn't name a borrower" in s["error"] and "plan" not in s


def test_ratios_before_fetch_fails_and_repair_fetches(monkeypatch, tmp_path):
    monkeypatch.setattr(graph.docx_writer, "OUT", tmp_path)
    plan = {**GOOD, "tasks": [{"id": 1, "tool": "compute_ratios", "args": {}, "purpose": "x"},
                              {"id": 2, "tool": "get_borrower_profile", "args": {}, "purpose": "x"},
                              {"id": 3, "tool": "get_credit_policy", "args": {}, "purpose": "x"}]}
    fake(monkeypatch, [json.dumps(plan)])
    s = graph.run("Credit memo for Patel Agro", "t3")
    assert s["task_log"][2]["status"] == "failed"        # compute_ratios with nothing fetched
    assert s["check"]["ratios_computed"] and s["check"]["sections"] == "5/5"


def test_invented_figures_and_dollars_are_redrafted(monkeypatch, tmp_path):
    monkeypatch.setattr(graph.docx_writer, "OUT", tmp_path)
    fake(monkeypatch, [json.dumps(GOOD)])
    monkeypatch.setattr(tools, "write_section", lambda section, facts, feedback="":
                        f"{section} fixed." if feedback else f"{section}: revenue of $42.7 million.")
    s = graph.run("Credit memo for Patel Agro, 5 crore term loan", "t4")
    redrafts = [t for t in s["task_log"] if "redraft" in t["purpose"]]
    assert [t["purpose"].split(":")[0] for t in redrafts] == ["redraft Recommendation"]
    assert "42.7" in redrafts[0]["purpose"] and "dollars" in redrafts[0]["purpose"]
    assert s["check"]["passed"] is False            # the 4 missing sections were also bad first time round
