"""Credit ratios and policy checks. Pure functions, no LLM: every number in the memo comes from here."""

RATE = 0.10        # assumed interest rate on the new loan
TERM_YEARS = 5     # assumed tenure for a term loan


def new_loan_debt_service(amount_cr: float, loan_type: str) -> float:
    """First-year debt service on the proposed loan: interest, plus principal for a term loan."""
    interest = amount_cr * RATE
    return interest + amount_cr / TERM_YEARS if loan_type == "term_loan" else interest


def compute_ratios(fin: dict, bs: dict, gst: dict, bank: dict, amount_cr: float, loan_type: str) -> dict:
    """fin is the latest year's figures. Returns ratios rounded for display."""
    cash_accrual = fin["pat"] + fin["depreciation"] + fin["interest"]
    debt_service = bs["existing_debt_service"] + new_loan_debt_service(amount_cr, loan_type)
    return {
        "dscr": round(cash_accrual / debt_service, 2),
        "ltv_pct": round(amount_cr / bs["collateral_value"] * 100, 1),
        "debt_equity": round((bs["existing_debt"] + amount_cr) / bs["net_worth"], 2),
        "current_ratio": round(bs["current_assets"] / bs["current_liabilities"], 2),
        "gst_bank_gap_pct": round((gst["turnover_12m"] - bank["credits_12m"]) / gst["turnover_12m"] * 100, 1),
        "cash_accrual_cr": round(cash_accrual, 2),
        "debt_service_cr": round(debt_service, 2),
    }


def check_policy(ratios: dict, policy: dict, amount_cr: float) -> list[dict]:
    """One row per rule: rule, required, actual, result (PASS/FAIL)."""
    rules = [
        ("DSCR", f">= {policy['min_dscr']}", ratios["dscr"], ratios["dscr"] >= policy["min_dscr"]),
        ("Loan-to-value", f"<= {policy['max_ltv_pct']}%", f"{ratios['ltv_pct']}%",
         ratios["ltv_pct"] <= policy["max_ltv_pct"]),
        ("Debt/equity", f"<= {policy['max_debt_equity']}", ratios["debt_equity"],
         ratios["debt_equity"] <= policy["max_debt_equity"]),
        ("Current ratio", f">= {policy['min_current_ratio']}", ratios["current_ratio"],
         ratios["current_ratio"] >= policy["min_current_ratio"]),
        ("GST vs bank credits gap", f"<= {policy['max_gst_bank_gap_pct']}%", f"{ratios['gst_bank_gap_pct']}%",
         abs(ratios["gst_bank_gap_pct"]) <= policy["max_gst_bank_gap_pct"]),
        ("Loan amount", f"<= ₹{policy['max_loan_cr']} Cr", f"₹{amount_cr} Cr", amount_cr <= policy["max_loan_cr"]),
    ]
    return [{"rule": r, "required": req, "actual": str(act), "result": "PASS" if ok else "FAIL"}
            for r, req, act, ok in rules]


def recommendation(policy_rows: list[dict], has_provisional: bool) -> str:
    """The decision is made here, never by the LLM."""
    if any(r["result"] == "FAIL" for r in policy_rows):
        return "REFER TO COMMITTEE WITH CONDITIONS"
    if has_provisional:
        return "RECOMMEND APPROVAL, SUBJECT TO AUDITED FINANCIALS"
    return "RECOMMEND APPROVAL"
