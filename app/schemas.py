"""Request, plan and response models. The plan schema is the contract the LLM must meet."""

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolName = Literal[
    "get_borrower_profile",
    "get_financials",
    "get_gst_returns",
    "get_bank_statements",
    "get_credit_policy",
    "compute_ratios",
    "draft_section",
]

SECTIONS = (
    "Borrower Overview",
    "Financial Analysis",
    "Risk Assessment",
    "Policy Compliance",
    "Recommendation",
)
SectionName = Literal[
    "Borrower Overview",
    "Financial Analysis",
    "Risk Assessment",
    "Policy Compliance",
    "Recommendation",
]


class Task(BaseModel):
    id: int
    tool: ToolName
    args: dict[str, str] = Field(default_factory=dict)
    purpose: str


class Plan(BaseModel):
    borrower: str
    loan_amount_cr: float = Field(gt=0)
    loan_type: Literal["working_capital", "term_loan"]
    assumptions: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(min_length=3)


class AgentRequest(BaseModel):
    request: str = Field(min_length=10, max_length=2000)


class TaskRecord(BaseModel):
    id: int
    tool: str
    kind: Literal["llm", "code"]
    purpose: str
    status: Literal["done", "failed"]
    summary: str
    ms: int
    added_by: Literal["planner", "self_check"] = "planner"


class AgentResponse(BaseModel):
    run_id: str
    status: Literal["completed", "failed"]
    error: str | None = None
    borrower: str | None = None
    recommendation: str | None = None
    plan_attempts: int = 0
    tasks: list[TaskRecord] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    flags: list[dict[str, str]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    check: dict[str, Any] | None = None
    document_url: str | None = None
    llm_calls: int = 0
    total_ms: int = 0
