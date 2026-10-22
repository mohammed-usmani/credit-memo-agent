"""The only module that talks to the model. Groq, free tier, OpenAI gpt-oss models."""

import json
import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from .prompts import REDRAFT_SUFFIX, SECTION_EXTRA, SECTION_PROMPT

load_dotenv()

MODEL = os.getenv("AGENT_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.getenv("AGENT_FALLBACK_MODEL", "openai/gpt-oss-20b")


@lru_cache(maxsize=4)
def _chat(model: str, json_mode: bool) -> ChatGroq:
    kwargs = {"model_kwargs": {"response_format": {"type": "json_object"}}} if json_mode else {}
    return ChatGroq(model=model, temperature=0, reasoning_effort="low", max_retries=2, timeout=60, **kwargs)


def _invoke(prompt: str, json_mode: bool) -> str:
    """Primary model first; on an API error (rate limit, outage), the smaller fallback model."""
    try:
        return _chat(MODEL, json_mode).invoke(prompt).content
    except Exception:
        return _chat(FALLBACK_MODEL, json_mode).invoke(prompt).content


def plan_json(prompt: str) -> str:
    """Raw JSON text for the plan. Validation is the graph's job, so its retry is visible there."""
    return _invoke(prompt, json_mode=True)


def write_section(section: str, facts: dict, feedback: str = "") -> str:
    prompt = SECTION_PROMPT.format(section=section, extra=SECTION_EXTRA.get(section, ""),
                                   facts=json.dumps(facts, ensure_ascii=False, indent=1))
    if feedback:
        prompt += REDRAFT_SUFFIX.format(feedback=feedback)
    return _invoke(prompt, json_mode=False).strip()
