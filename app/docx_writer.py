"""Renders the agent's final state into a credit memo (.docx) for a credit officer to review."""

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from .schemas import SECTIONS

OUT = Path(__file__).parent.parent / "out"
RED, GREEN, GREY = RGBColor(0xB0, 0x1E, 0x1E), RGBColor(0x1E, 0x7B, 0x34), RGBColor(0x66, 0x66, 0x66)
STYLE = "Light Grid Accent 1"


def _table(doc, header, rows, color_col=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = STYLE
    for cell, h in zip(t.rows[0].cells, header):
        cell.text = h
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            run = cells[i].paragraphs[0].add_run(str(v))
            if i == color_col:
                run.bold = True
                run.font.color.rgb = GREEN if str(v) == "PASS" else RED
    doc.add_paragraph()
    return t


def unique(items):
    return list(dict.fromkeys(items))


def render(state: dict) -> str:
    plan, d = state["plan"], state["data"]
    profile = d.get("profile", {})
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    doc.add_heading("Credit Memo", 0)
    doc.add_heading(profile.get("name", plan.borrower), 1)
    basis = d.get("ratio_year")
    status = d.get("financials", {}).get(basis, {}).get("status") if basis else None
    _table(doc, ["Field", "Detail"], [
        ["Facility", f"₹{plan.loan_amount_cr} Cr {plan.loan_type.replace('_', ' ')}"],
        ["Date", date.today().strftime("%d %b %Y")],
        ["Data basis", f"{basis} {status}" if basis else "Ratios not computed"],
        ["Prepared by", "Credit memo agent (AI draft)"],
        ["Status", "DRAFT for credit officer review"],
    ])

    rec = d.get("recommendation", "NO RECOMMENDATION: ratios could not be computed")
    p = doc.add_paragraph()
    r = p.add_run(f"Recommendation: {rec}")
    r.bold, r.font.size = True, Pt(13)
    r.font.color.rgb = GREEN if rec.startswith("RECOMMEND") else RED

    doc.add_heading("Assumptions, flags and open questions", 2)
    for a in plan.assumptions:
        doc.add_paragraph(f"Assumption: {a}", style="List Bullet")
    for f in unique(f["text"] for f in state["flags"] if f["level"] != "info"):
        doc.add_paragraph(f"Flag: {f}", style="List Bullet")
    for q in unique(state["open_questions"]):
        doc.add_paragraph(f"Open question: {q}", style="List Bullet")

    for i, name in enumerate(SECTIONS, 1):
        doc.add_heading(f"{i}. {name}", 2)
        text = state["sections"].get(name)
        if text:
            for para in text.split("\n\n"):
                doc.add_paragraph(para.strip())
        else:
            doc.add_paragraph("Not available: this section could not be completed.").runs[0].italic = True
        if name == "Financial Analysis":
            fin = d.get("financials", {})
            if fin:
                _table(doc, ["Year", "Status", "Revenue", "PAT", "Depreciation", "Interest"],
                       [[y, v["status"], v["revenue"], v["pat"], v["depreciation"], v["interest"]]
                        for y, v in sorted(fin.items())])
            if "ratios" in d:
                rt = d["ratios"]
                _table(doc, ["Ratio", "Value"], [
                    ["DSCR", rt["dscr"]], ["Loan-to-value", f"{rt['ltv_pct']}%"],
                    ["Debt / equity", rt["debt_equity"]], ["Current ratio", rt["current_ratio"]],
                    ["GST turnover vs bank credits gap", f"{rt['gst_bank_gap_pct']}%"],
                    ["Cash accrual / debt service (₹ Cr)", f"{rt['cash_accrual_cr']} / {rt['debt_service_cr']}"],
                ])
                doc.add_paragraph("All amounts in ₹ crore. Ratios are computed in code, not by the language model."
                                  ).runs[0].font.color.rgb = GREY
        if name == "Policy Compliance" and d.get("policy_rows"):
            _table(doc, ["Rule", "Required", "Actual", "Result"],
                   [[x["rule"], x["required"], x["actual"], x["result"]] for x in d["policy_rows"]], color_col=3)

    c = state.get("check") or {}
    doc.add_heading("Agent self-check", 2)
    doc.add_paragraph(f"Sections {c.get('sections', '?')} · ratios computed: {'yes' if c.get('ratios_computed') else 'no'}"
                      f" · policy rules tested: {c.get('policy_rules_tested', 0)} "
                      f"({c.get('policy_passed', 0)} pass, {c.get('policy_failed', 0)} fail)"
                      f" · repairs made: {c.get('repairs', 0)}")

    doc.add_heading("Credit officer sign-off", 2)
    _table(doc, ["Field", "Entry"], [["Name", ""], ["Decision (approve / reject / refer)", ""],
                                     ["Conditions", ""], ["Signature and date", ""]])

    doc.add_page_break()
    doc.add_heading("Appendix: agent audit trail", 2)
    _table(doc, ["#", "Step", "By", "Status", "Result", "ms"],
           [[t["id"] if t["id"] else "", t["tool"] + (" *" if t["added_by"] == "self_check" else ""),
             t["kind"].upper(), t["status"], t["summary"], t["ms"]] for t in state["task_log"]])
    note = doc.add_paragraph("* added by the agent's self-check.")
    note.runs[0].font.color.rgb = GREY
    note.alignment = WD_ALIGN_PARAGRAPH.LEFT

    OUT.mkdir(exist_ok=True)
    path = OUT / f"{state['run_id']}.docx"
    doc.save(path)
    return str(path)
