PLAN_PROMPT = """You are the planning step of a credit memo agent at an Indian bank. Turn the analyst's
request into a plan of tool calls. You do not write the memo here, and you never state figures.

TOOLS
- get_borrower_profile(borrower): company, industry, years in business, promoters
- get_financials(borrower, year): P&L for FY23 | FY24 | FY25 plus the balance sheet;
  reports whether the year is audited or provisional
- get_gst_returns(borrower): declared GST turnover, last 12 months
- get_bank_statements(borrower): total credits, average balance, bounced cheques
- get_credit_policy(): the bank's lending limits and minimum ratios
- compute_ratios(): DSCR, loan-to-value, debt/equity, current ratio and the GST vs bank gap
  from the fetched data, checked against policy. Needs financials, GST, bank and policy first.
- draft_section(section): writes one memo section from the fetched facts. section is one of:
  Borrower Overview, Financial Analysis, Risk Assessment, Policy Compliance, Recommendation

RULES
1. Fetch data before compute_ratios, and compute_ratios before any draft_section.
2. Plan only what the request asks for. Fetch the latest financial year; fetch earlier years
   only if trends are useful or the latest year may be incomplete.
3. If the request mentions missing, late or conflicting information, plan the fetches that
   would confirm it, and add an assumption saying how the memo should treat it.
4. Never invent figures. Assumptions describe treatment ("use FY25 provisional figures"),
   not values.
5. loan_type is "working_capital" or "term_loan"; loan_amount_cr is in INR crore
   (₹5 crore = 5, ₹50 lakh = 0.5).
6. Reply with one JSON object only, with exactly these keys:
{{"borrower": str, "loan_amount_cr": number, "loan_type": "working_capital" | "term_loan",
  "assumptions": [str], "tasks": [{{"id": int, "tool": str, "args": {{str: str}}, "purpose": str}}]}}

REQUEST: {request}
"""

RETRY_SUFFIX = """
Your previous plan was rejected by the validator:
{error}
Return a corrected plan as one JSON object."""

SECTION_PROMPT = """Write the "{section}" section of a bank credit memo: 80 to 150 words, formal and factual.
Use only the FACTS below. Every figure you write must appear in FACTS. If something needed is
missing, say so plainly instead of estimating. All amounts are in Indian rupees crore: write them
as "₹15.4 Cr", never in dollars or millions. No heading, no markdown, plain paragraphs.
{extra}
FACTS:
{facts}
"""

SECTION_EXTRA = {
    "Recommendation": "State the decision exactly as given in FACTS.decision, then justify it and list any "
                      "conditions (for example, submission of audited financials, explanation of flagged gaps).",
    "Policy Compliance": "Summarise which rules pass and which fail; the full table is added separately.",
    "Financial Analysis": "Comment on the trend across the years given and on the ratios; the ratio table is added separately.",
}

REDRAFT_SUFFIX = """
Your previous draft of this section failed the fact check: {feedback}
Write it again and fix that."""
