"""Renders the agent's final state into a credit memo (.docx) for a credit officer to review.

The model may use **bold** and "- " bullet lines; they become real Word bold runs and bullets,
so no markdown reaches the document.
"""

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Cm, Pt, RGBColor

from .schemas import SECTIONS

OUT = Path(__file__).parent.parent / "out"
RED, GREEN, GREY, NAVY = (RGBColor(0xB0, 0x1E, 0x1E), RGBColor(0x1E, 0x7B, 0x34),
                          RGBColor(0x66, 0x66, 0x66), RGBColor(0x1F, 0x3A, 0x5F))
STYLE = "Light Grid Accent 1"


def unique(items):
    return list(dict.fromkeys(items))


def _shade(cell, hex_fill):
    cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:fill="{hex_fill}"/>'))


def _rich(par, text):
    """Add text to a paragraph, turning **x** into bold runs and dropping other markdown marks."""
    text = text.replace("`", "")
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            par.add_run(part.replace("*", "")).bold = i % 2 == 1
    return par


def _prose(doc, text):
    """LLM text -> Word paragraphs and bullets."""
    for line in text.splitlines():
        line = re.sub(r"^#+\s*", "", line.strip())
        if not line:
            continue
        bullet = re.match(r"^(?:[-•]|\*(?!\*))\s+(.*)", line)
        numbered = re.match(r"^\(?\d+[.)]\s+(.*)", line)
        if bullet or numbered:
            _rich(doc.add_paragraph(style="List Bullet"), (bullet or numbered).group(1))
        else:
            p = _rich(doc.add_paragraph(), line)
            p.paragraph_format.space_after = Pt(6)


def _table(doc, header, rows, color_col=None, bold_first=False, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = STYLE
    # explicit borders and header fill, so the table looks the same in Word, Google Docs and previews
    t._tbl.tblPr.append(parse_xml(f'<w:tblBorders {nsdecls("w")}>' + "".join(
        f'<w:{e} w:val="single" w:sz="4" w:color="C9D3DF"/>'
        for e in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>"))
    for cell, h in zip(t.rows[0].cells, header):
        _shade(cell, "1F3A5F")
        r = cell.paragraphs[0].add_run(h)
        r.bold, r.font.color.rgb = True, RGBColor(0xFF, 0xFF, 0xFF)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            run = cells[i].paragraphs[0].add_run(str(v))
            if i == color_col:
                run.bold = True
                run.font.color.rgb = GREEN if str(v) == "PASS" else RED
            elif i == 0 and bold_first:
                run.bold = True
    if widths:
        for row in t.rows:
            for cell, w in zip(row.cells, widths):
                cell.width = Cm(w)
    doc.add_paragraph()
    return t


def _note(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic, r.font.size, r.font.color.rgb = True, Pt(9), GREY
    return p


def _page_number(par):
    par.add_run("Page ")
    par._p.append(parse_xml(f'<w:fldSimple {nsdecls("w")} w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple>'))


def render(state: dict) -> str:
    plan, d = state["plan"], state["data"]
    profile = d.get("profile", {})
    name = profile.get("name", plan.borrower)
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2.2)
        hr = s.header.paragraphs[0].add_run(f"Credit Memo · {name} · DRAFT for review")
        hr.font.size, hr.font.color.rgb = Pt(8), GREY
        fp = s.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _page_number(fp)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    for lvl in (1, 2):
        doc.styles[f"Heading {lvl}"].font.color.rgb = NAVY

    doc.add_heading("Credit Memo", 0)
    doc.add_heading(name, 1)
    basis = d.get("ratio_year")
    status = d.get("financials", {}).get(basis, {}).get("status") if basis else None
    _table(doc, ["Field", "Detail"], [
        ["Facility", f"₹{plan.loan_amount_cr:g} Cr {plan.loan_type.replace('_', ' ')}"],
        ["Industry / location", f"{profile.get('industry', '-')}, {profile.get('city', '-')}"],
        ["Date", date.today().strftime("%d %b %Y")],
        ["Data basis", f"{basis} {status}" if basis else "Ratios not computed"],
        ["Prepared by", "Credit memo agent (AI draft)"],
        ["Status", "DRAFT for credit officer review"],
    ], bold_first=True, widths=[5, 11])

    # recommendation callout: one shaded cell
    rec = d.get("recommendation", "NO RECOMMENDATION: ratios could not be computed")
    ok = rec.startswith("RECOMMEND")
    cell = doc.add_table(rows=1, cols=1).rows[0].cells[0]
    _shade(cell, "E6F4EA" if ok else "FDECEA")
    p = cell.paragraphs[0]
    lab = p.add_run("RECOMMENDATION\n")
    lab.font.size, lab.font.color.rgb, lab.bold = Pt(8), GREY, True
    r = p.add_run(rec)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(14), GREEN if ok else RED
    rows = d.get("policy_rows", [])
    if rows:
        fails = [x["rule"] for x in rows if x["result"] == "FAIL"]
        sub = p.add_run(f"\n{len(rows) - len(fails)} of {len(rows)} policy rules pass"
                        + (f" · fails: {', '.join(fails)}" if fails else ""))
        sub.font.size, sub.font.color.rgb = Pt(9), GREY
    doc.add_paragraph()

    doc.add_heading("Assumptions, flags and open questions", 2)
    items = ([("Assumption", a) for a in plan.assumptions]
             + [("Flag", f) for f in unique(f["text"] for f in state["flags"] if f["level"] != "info")]
             + [("Open question", q) for q in unique(state["open_questions"])])
    for label, text in items:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(f"{label}: ").bold = True
        p.add_run(text)
    if not items:
        _note(doc, "None: audited data, no flags and no policy breaches.")

    for i, sec in enumerate(SECTIONS, 1):
        doc.add_heading(f"{i}. {sec}", 2)
        text = state["sections"].get(sec)
        if text:
            _prose(doc, text)
        else:
            _note(doc, "Not available: this section could not be completed.")
        if sec == "Financial Analysis":
            fin = d.get("financials", {})
            if fin:
                _table(doc, ["Year", "Status", "Revenue", "PAT", "Depreciation", "Interest"],
                       [[y, v["status"], f"{v['revenue']:.2f}", f"{v['pat']:.2f}", f"{v['depreciation']:.2f}",
                         f"{v['interest']:.2f}"] for y, v in sorted(fin.items())], bold_first=True)
            if "ratios" in d:
                rt = d["ratios"]
                _table(doc, ["Ratio", "Value"], [
                    ["DSCR", f"{rt['dscr']:.2f}"], ["Loan-to-value", f"{rt['ltv_pct']}%"],
                    ["Debt / equity", f"{rt['debt_equity']:.2f}"], ["Current ratio", f"{rt['current_ratio']:.2f}"],
                    ["GST turnover vs bank credits gap", f"{rt['gst_bank_gap_pct']}%"],
                    ["Annual cash accrual (₹ Cr)", f"{rt['cash_accrual_cr']:.2f}"],
                    ["Annual debt service incl. new loan (₹ Cr)", f"{rt['debt_service_cr']:.2f}"],
                ], bold_first=True, widths=[9, 7])
                _note(doc, "All amounts in ₹ crore. Ratios are computed in code, not by the language model.")
        if sec == "Policy Compliance" and rows:
            _table(doc, ["Rule", "Required", "Actual", "Result"],
                   [[x["rule"], x["required"], x["actual"], x["result"]] for x in rows], color_col=3, bold_first=True)

    c = state.get("check") or {}
    doc.add_heading("Agent self-check", 2)
    _table(doc, ["Check", "Result"], [
        ["Sections written", c.get("sections", "?")],
        ["Ratios computed", "yes" if c.get("ratios_computed") else "no"],
        ["Policy rules tested", f"{c.get('policy_rules_tested', 0)} ({c.get('policy_passed', 0)} pass, "
                                f"{c.get('policy_failed', 0)} fail)"],
        ["Figures checked against source data", "all grounded" if not c.get("ungrounded")
                                                else "; ".join(f"{k}: {v}" for k, v in c["ungrounded"].items())],
        ["Repairs made by the agent", c.get("repairs", 0)],
    ], bold_first=True, widths=[9, 7])

    doc.add_heading("Credit officer sign-off", 2)
    _table(doc, ["Field", "Entry"], [["Name", ""], ["Decision (approve / reject / refer)", ""],
                                     ["Conditions", ""], ["Signature and date", ""]], bold_first=True, widths=[7, 9])

    doc.add_page_break()
    doc.add_heading("Appendix: agent audit trail", 2)
    _table(doc, ["#", "Step", "By", "Status", "Result", "ms"],
           [[t["id"] or "", t["tool"] + (" *" if t["added_by"] == "self_check" else ""),
             t["kind"].upper(), t["status"], t["summary"], t["ms"]] for t in state["task_log"]],
           widths=[0.8, 3.6, 1.3, 1.4, 8.4, 1.2])
    _note(doc, "* added by the agent's self-check.")

    OUT.mkdir(exist_ok=True)
    path = OUT / f"{state['run_id']}.docx"
    doc.save(path)
    return str(path)
