"""The agent as a LangGraph state machine.

validate_request -> plan -> validate_plan --invalid (max 3)--> plan
                                 | valid
                                 v
                              execute <-- one task per step, loops while tasks remain
                                 v
                             self_check --gaps, first time--> repair -> execute
                                 v
                              render -> END
"""

import time
from operator import add
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from . import docx_writer
from .llm import plan_json
from .prompts import PLAN_PROMPT, RETRY_SUFFIX
from .schemas import SECTIONS, Plan, Task
from .tools import REGISTRY, REQUIRED_FOR_RATIOS, ToolError, find_borrower, ungrounded

MAX_PLAN_ATTEMPTS = 3
MAX_REPAIRS = 1


class AgentState(TypedDict, total=False):
    run_id: str
    request: str
    error: str | None                   # set when the run cannot continue
    plan: Plan | None
    raw_plan: str                       # the planner's JSON, before validation
    plan_error: str | None              # last validator message, fed back to the planner
    plan_attempts: int
    cursor: int                         # index of the next task to run
    added_by_check: list[int]           # task ids the self-check appended
    data: dict[str, Any]                # facts from tools: profile, financials, ratios, ...
    sections: dict[str, str]            # drafted memo sections
    flags: Annotated[list[dict], add]   # reducers: nodes append, never overwrite
    open_questions: Annotated[list[str], add]
    task_log: Annotated[list[dict], add]
    llm_calls: int
    check: dict | None
    check_loops: int
    doc_path: str | None


# ---- nodes -------------------------------------------------------------------

def validate_request(state: AgentState) -> dict:
    """Guardrail: the request must name a borrower the loan system knows, before any LLM call."""
    try:
        find_borrower(state["request"])
    except ToolError:
        return {"error": "The request doesn't name a borrower in the loan system "
                         "(known: Patel Agro, Shree Ganesh Packaging)."}
    return {"plan_attempts": 0, "cursor": 0, "data": {}, "sections": {}, "added_by_check": [],
            "llm_calls": 0, "check_loops": 0, "error": None}


def plan(state: AgentState) -> dict:
    prompt = PLAN_PROMPT.format(request=state["request"])
    if state.get("plan_error"):
        prompt += RETRY_SUFFIX.format(error=state["plan_error"])
    t = time.time()
    raw = plan_json(prompt)
    attempt = state["plan_attempts"] + 1
    return {"plan_attempts": attempt, "llm_calls": state["llm_calls"] + 1, "raw_plan": raw,
            "task_log": [{"id": 0, "tool": "plan", "kind": "llm", "purpose": f"plan the work (attempt {attempt})",
                          "status": "done", "summary": "plan drafted", "ms": int((time.time() - t) * 1000),
                          "added_by": "planner"}]}


def validate_plan(state: AgentState) -> dict:
    """Schema check (Pydantic) plus checks the schema can't express. Errors go back to the planner."""
    try:
        p = Plan.model_validate_json(state["raw_plan"])
        problems = []
        tools = [t.tool for t in p.tasks]
        if len({t.id for t in p.tasks}) != len(p.tasks):
            problems.append("task ids must be unique")
        if "compute_ratios" in tools and "draft_section" in tools and \
                tools.index("draft_section") < tools.index("compute_ratios"):
            problems.append("draft_section tasks must come after compute_ratios")
        try:
            find_borrower(p.borrower)
        except ToolError as e:
            problems.append(str(e))
        if problems:
            raise ValueError("; ".join(problems))
    except (ValidationError, ValueError) as e:
        msg = ("; ".join(f"{'.'.join(map(str, x['loc'])) or 'plan'}: {x['msg']}" for x in e.errors())
               if isinstance(e, ValidationError) else str(e))[:800]
        if state["plan_attempts"] >= MAX_PLAN_ATTEMPTS:
            return {"plan_error": msg, "error": f"No valid plan after {MAX_PLAN_ATTEMPTS} attempts: {msg}"}
        return {"plan_error": msg, "flags": [{"level": "info", "text": f"Plan attempt {state['plan_attempts']} "
                                              f"rejected by validator and retried: {msg[:160]}"}]}
    return {"plan": p, "plan_error": None,
            "task_log": [{"id": 0, "tool": "validate_plan", "kind": "code", "purpose": "check the plan",
                          "status": "done", "summary": f"valid: {len(p.tasks)} tasks, {len(p.assumptions)} assumptions",
                          "ms": 0, "added_by": "planner"}]}


def execute(state: AgentState) -> dict:
    """Run the next task. A failing tool is recorded and the run moves on."""
    task: Task = state["plan"].tasks[state["cursor"]]
    fn, kind = REGISTRY[task.tool]
    t = time.time()
    update: dict = {"cursor": state["cursor"] + 1}
    try:
        res = fn(task.args, state)
        status, summary = "done", res.summary
        update |= {"data": {**state["data"], **res.data}, "flags": res.flags, "open_questions": res.questions}
        if res.section:
            update["sections"] = {**state["sections"], res.section[0]: res.section[1]}
    except ToolError as e:
        status, summary = "failed", str(e)
    except Exception as e:  # an LLM or API error inside a tool: record it, keep going
        status, summary = "failed", f"{type(e).__name__}: {str(e)[:200]}"
    if kind == "llm":
        update["llm_calls"] = state["llm_calls"] + 1
    label = task.args.get("section") or task.args.get("year") or ""
    update["task_log"] = [{"id": task.id, "tool": task.tool, "kind": kind,
                           "purpose": task.purpose + (f" [{label}]" if label and label not in task.purpose else ""),
                           "status": status, "summary": summary, "ms": int((time.time() - t) * 1000),
                           "added_by": "self_check" if task.id in state["added_by_check"] else "planner"}]
    return update


def self_check(state: AgentState) -> dict:
    """Reflection in code: is the memo complete, is every figure grounded, was every policy rule tested?"""
    d, sections = state["data"], state["sections"]
    missing_sections = [s for s in SECTIONS if s not in sections]
    bad = {s: p for s in SECTIONS if s in sections and (p := ungrounded(s, sections[s], state))}
    rows = d.get("policy_rows", [])
    check = {
        "sections": f"{len(SECTIONS) - len(missing_sections)}/{len(SECTIONS)}",
        "missing_sections": missing_sections,
        "ungrounded": bad,
        "ratios_computed": "ratios" in d,
        "policy_rules_tested": len(rows),
        "policy_passed": sum(r["result"] == "PASS" for r in rows),
        "policy_failed": sum(r["result"] == "FAIL" for r in rows),
        "repairs": state["check_loops"],
    }
    check["passed"] = not missing_sections and not bad and check["ratios_computed"]
    update = {"check": check}
    if not check["passed"] and state["check_loops"] >= MAX_REPAIRS:
        gaps = missing_sections + [f"{k} ({v})" for k, v in bad.items()] + \
            ([] if check["ratios_computed"] else ["ratios"])
        update["flags"] = [{"level": "fail", "text": f"Memo incomplete after self-check repair: {', '.join(gaps)}"}]
    return update


def repair(state: AgentState) -> dict:
    """Append the tasks the self-check found missing. Deterministic: no LLM decides the fix."""
    p, d = state["plan"], state["data"]
    next_id = max(t.id for t in p.tasks) + 1
    new: list[Task] = []
    if "ratios" not in d:
        for key, tool in REQUIRED_FOR_RATIOS.items():
            if key not in d:
                new.append(Task(id=0, tool=tool, args={"borrower": p.borrower}, purpose=f"fetch {key} (needed for ratios)"))
        new.append(Task(id=0, tool="compute_ratios", purpose="compute ratios and check policy"))
    for s in state["check"]["missing_sections"]:
        new.append(Task(id=0, tool="draft_section", args={"section": s}, purpose=f"write missing section: {s}"))
    for s, problem in state["check"]["ungrounded"].items():
        new.append(Task(id=0, tool="draft_section", args={"section": s, "feedback": problem},
                        purpose=f"redraft {s}: {problem}"))
    for i, t in enumerate(new):
        t.id = next_id + i
    plan = p.model_copy(update={"tasks": p.tasks + new})
    return {"plan": plan, "check_loops": state["check_loops"] + 1,
            "added_by_check": state["added_by_check"] + [t.id for t in new]}


def render(state: AgentState) -> dict:
    return {"doc_path": docx_writer.render(state)}


# ---- edges -------------------------------------------------------------------

def after_validate_request(state):
    return END if state.get("error") else "plan"


def after_validate_plan(state):
    if state.get("error"):
        return END
    return "plan" if state.get("plan_error") else "execute"


def after_execute(state):
    return "execute" if state["cursor"] < len(state["plan"].tasks) else "self_check"


def after_self_check(state):
    return "repair" if not state["check"]["passed"] and state["check_loops"] < MAX_REPAIRS else "render"


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("validate_request", validate_request), ("plan", plan), ("validate_plan", validate_plan),
                     ("execute", execute), ("self_check", self_check), ("repair", repair), ("render", render)]:
        g.add_node(name, fn)
    g.set_entry_point("validate_request")
    g.add_conditional_edges("validate_request", after_validate_request, ["plan", END])
    g.add_edge("plan", "validate_plan")
    g.add_conditional_edges("validate_plan", after_validate_plan, ["plan", "execute", END])
    g.add_conditional_edges("execute", after_execute, ["execute", "self_check"])
    g.add_conditional_edges("self_check", after_self_check, ["repair", "render"])
    g.add_edge("repair", "execute")
    g.add_edge("render", END)
    return g.compile()


GRAPH = build_graph()


def run(request: str, run_id: str) -> dict:
    return GRAPH.invoke({"run_id": run_id, "request": request, "flags": [], "open_questions": [], "task_log": []},
                        {"recursion_limit": 80})
