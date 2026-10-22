import json
from pathlib import Path

from app.ratios import check_policy, compute_ratios, recommendation

DATA = json.loads((Path(__file__).parent.parent / "app" / "mock_data.json").read_text())


def run(key, amount, loan_type):
    b = DATA["borrowers"][key]
    r = compute_ratios(b["financials"]["FY25"], b["balance_sheet"], b["gst"], b["bank"], amount, loan_type)
    return r, check_policy(r, DATA["policy"], amount)


def test_patel_agro_fails_dscr_and_gst_gap():
    r, rows = run("patel-agro", 5, "term_loan")
    assert r["dscr"] == 1.08 and r["ltv_pct"] == 58.1 and r["debt_equity"] == 1.9
    assert r["current_ratio"] == 1.3 and r["gst_bank_gap_pct"] == 18.1
    assert [x["rule"] for x in rows if x["result"] == "FAIL"] == ["DSCR", "GST vs bank credits gap"]
    assert recommendation(rows, has_provisional=True) == "REFER TO COMMITTEE WITH CONDITIONS"


def test_shree_ganesh_passes_everything():
    r, rows = run("shree-ganesh-packaging", 2, "working_capital")
    assert r["dscr"] == 1.6 and r["ltv_pct"] == 50.0
    assert all(x["result"] == "PASS" for x in rows)
    assert recommendation(rows, has_provisional=False) == "RECOMMEND APPROVAL"
