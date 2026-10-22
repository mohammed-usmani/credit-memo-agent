"""The agent's tools. Each takes (args, state) and returns a ToolResult; only draft_section uses the LLM.

Data tools read app/mock_data.json, standing in for core banking, GST and bank-statement systems.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ratios as R
from .llm import write_section
from .schemas import SECTIONS

DB = json.loads((Path(__file__).parent / "mock_data.json").read_text())


class ToolError(Exception):
    """A tool could not run. The task is marked failed and the run continues."""


@dataclass
class ToolResult:
    summary: str
    data: dict = field(default_factory=dict)        # merged into state["data"]
    flags: list = field(default_factory=list)       # {"level": "warn"|"fail", "text": ...}
    questions: list = field(default_factory=list)   # open questions for the credit officer
    section: tuple[str, str] | None = None          # (name, text) for draft_section


def find_borrower(name: str) -> str:
    """Loose match: 'patel agro' finds 'Patel Agro Pvt Ltd'."""
    words = set(re.findall(r"[a-z]+", name.lower())) - {"pvt", "ltd", "the", "limited", "private"}
    best, score = None, 0
    for key, b in DB["borrowers"].items():
        overlap = len(words & set(re.findall(r"[a-z]+", b["profile"]["name"].lower())))
        if overlap > score:
            best, score = key, overlap
    if not best:
        raise ToolError(f"no borrower matches '{name}' in the loan system")
    return best


def _borrower(args: dict, state: dict) -> dict:
    return DB["borrowers"][find_borrower(args.get("borrower") or state["plan"].borrower)]


def get_borrower_profile(args, state):
    p = _borrower(args, state)["profile"]
    return ToolResult(f"{p['name']}, {p['industry']}, {p['years_in_business']} yrs, {p['city']}",
                      {"profile": p})


def get_financials(args, state):
    b = _borrower(args, state)
    year = args.get("year", "FY25").upper()
    if year not in b["financials"]:
        raise ToolError(f"no financials for {year}")
    fin = b["financials"][year]
    years = {**state["data"].get("financials", {}), year: fin}
    res = ToolResult(f"{year}: {fin['status']}, revenue ₹{fin['revenue']} Cr, PAT ₹{fin['pat']} Cr",
                     {"financials": years, "balance_sheet": b["balance_sheet"]})
    if fin["status"] != "audited":
        res.flags.append({"level": "warn", "text": f"{year} audited financials not available; {fin['status']} figures used"})
        res.questions.append(f"When will {year} audited financials be submitted?")
    return res


def get_gst_returns(args, state):
    g = _borrower(args, state)["gst"]
    return ToolResult(f"GST turnover ₹{g['turnover_12m']} Cr (12 m), {g['filings_on_time']} filed on time", {"gst": g})


def get_bank_statements(args, state):
    bk = _borrower(args, state)["bank"]
    res = ToolResult(f"Bank credits ₹{bk['credits_12m']} Cr (12 m), {bk['bounced_cheques']} bounced cheques",
                     {"bank": bk})
    if bk["bounced_cheques"]:
        res.flags.append({"level": "warn", "text": f"{bk['bounced_cheques']} bounced cheques in the last 12 months"})
    return res


def get_credit_policy(args, state):
    return ToolResult(DB["policy"]["name"], {"policy": DB["policy"]})


REQUIRED_FOR_RATIOS = {"financials": "get_financials", "gst": "get_gst_returns",
                       "bank": "get_bank_statements", "policy": "get_credit_policy"}


def compute_ratios(args, state):
    d, plan = state["data"], state["plan"]
    missing = [k for k in REQUIRED_FOR_RATIOS if k not in d]
    if missing:
        raise ToolError(f"cannot compute ratios, not fetched yet: {', '.join(missing)}")
    latest = max(d["financials"])                       # e.g. "FY25"
    fin = d["financials"][latest]
    r = R.compute_ratios(fin, d["balance_sheet"], d["gst"], d["bank"], plan.loan_amount_cr, plan.loan_type)
    rows = R.check_policy(r, d["policy"], plan.loan_amount_cr)
    rec = R.recommendation(rows, has_provisional=fin["status"] != "audited")
    res = ToolResult(f"DSCR {r['dscr']} · LTV {r['ltv_pct']}% · D/E {r['debt_equity']} · "
                     f"CR {r['current_ratio']} · GST gap {r['gst_bank_gap_pct']}% (from {latest})",
                     {"ratios": r, "policy_rows": rows, "recommendation": rec, "ratio_year": latest})
    for row in rows:
        if row["result"] == "FAIL":
            res.flags.append({"level": "fail", "text": f"{row['rule']} {row['actual']} breaches policy ({row['required']})"})
    if r["gst_bank_gap_pct"] > d["policy"]["max_gst_bank_gap_pct"]:
        res.questions.append(f"What explains the {r['gst_bank_gap_pct']}% gap between GST turnover and bank credits?")
    return res


def _facts(section: str, state: dict) -> dict:
    """Only the facts each section needs: shorter prompts and fewer chances to invent numbers."""
    d, plan = state["data"], state["plan"]
    loan = {"amount_cr": plan.loan_amount_cr, "type": plan.loan_type}
    flags = [f["text"] for f in state["flags"]]
    return {
        "Borrower Overview": {"loan_request": loan, "profile": d.get("profile")},
        "Financial Analysis": {"financials": d.get("financials"), "ratios": d.get("ratios"),
                               "ratio_year": d.get("ratio_year")},
        "Risk Assessment": {"ratios": d.get("ratios"), "gst": d.get("gst"), "bank": d.get("bank"), "flags": flags},
        "Policy Compliance": {"policy_checks": d.get("policy_rows")},
        "Recommendation": {"decision": d.get("recommendation"), "loan_request": loan,
                           "failed_rules": [r for r in d.get("policy_rows", []) if r["result"] == "FAIL"],
                           "flags": flags, "assumptions": plan.assumptions},
    }[section]


def draft_section(args, state):
    section = args.get("section", "")
    if section not in SECTIONS:
        raise ToolError(f"unknown section '{section}'; use one of {', '.join(SECTIONS)}")
    facts = _facts(section, state)
    if not any(facts.values()):
        raise ToolError(f"no facts available yet for {section}")
    text = write_section(section, facts)
    return ToolResult(f"{len(text.split())} words", section=(section, text))


REGISTRY = {
    "get_borrower_profile": (get_borrower_profile, "code"),
    "get_financials": (get_financials, "code"),
    "get_gst_returns": (get_gst_returns, "code"),
    "get_bank_statements": (get_bank_statements, "code"),
    "get_credit_policy": (get_credit_policy, "code"),
    "compute_ratios": (compute_ratios, "code"),
    "draft_section": (draft_section, "llm"),
}
