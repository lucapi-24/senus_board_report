"""AI commentary and chat for the Company view, grounded in the ingested document."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_HISTORY = 8
MAX_MESSAGE_CHARS = 1500

_SYSTEM = (
    "You are an analyst assistant for the Board of Senus PLC. Answer in the language of the question, "
    "concisely. Use ONLY the CONTEXT below (narrative facts and financial figures extracted from the "
    "company's half-year report). If the answer is not in the context, say the report does not cover it. "
    "Never invent figures, forecasts or probabilities; label any calculation you make as your own calculation. "
    "Respect consistency_notes: if a note says figure A is within figure B, A is part of B, so never call A \"additional\" to B and never sum them."
)

_cache: Dict[str, List[Dict[str, str]]] = {}


class AIUnavailable(Exception):
    pass


def _client():
    if not settings.GROQ_API_KEY:
        raise AIUnavailable("GROQ_API_KEY is not configured")
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=settings.GROQ_BASE_URL, api_key=settings.GROQ_API_KEY)


def build_context(narrative: Dict[str, Any], figures: Dict[str, Dict[str, float]]) -> str:
    """Compact text context: narrative facts without quotes/pages plus statement line items."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in ("quote", "page", "pages")}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    keep = {k: v for k, v in narrative.items() if k not in ("checks", "document")}
    notes = [c["message"] for c in narrative.get("checks", []) if c.get("message")]
    return json.dumps(
        {"narrative": strip(keep), "consistency_notes": notes, "financial_statements_eur": figures},
        ensure_ascii=False,
    )


async def _complete(messages: List[Dict[str, str]], *, json_mode: bool = False, max_tokens: int = 900) -> str:
    kwargs: Dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = await _client().chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
            extra_body={"reasoning_effort": "low"},
            **kwargs,
        )
    except AIUnavailable:
        raise
    except Exception as exc:  # provider errors surface as a generic unavailability
        logger.warning("Company AI request failed: %s", exc)
        raise AIUnavailable("The AI provider could not answer right now") from exc
    return (response.choices[0].message.content or "").strip()


async def generate_insights(cache_key: str, context: str, force: bool = False) -> List[Dict[str, str]]:
    if not force and cache_key in _cache:
        return _cache[cache_key]
    prompt = (
        "Write exactly 3 insights for the Board about the company's direction (strategy, commercial "
        "momentum, financial position). Each must be an interpretation that connects facts in the context, "
        "not a quotation. Return JSON: {\"insights\": [{\"title\": \"max 6 words\", \"text\": \"max 45 words\"}]}.\n\n"
        f"CONTEXT:\n{context}"
    )
    raw = await _complete(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        json_mode=True,
        max_tokens=1200,
    )
    try:
        items = json.loads(raw).get("insights", [])
    except (ValueError, AttributeError) as exc:
        raise AIUnavailable("The AI returned an unreadable answer") from exc
    insights = [
        {"title": str(i.get("title", "")).strip(), "text": str(i.get("text", "")).strip()}
        for i in items
        if isinstance(i, dict) and i.get("text")
    ][:3]
    if not insights:
        raise AIUnavailable("The AI returned no insights")
    _cache[cache_key] = insights
    return insights


async def chat(context: str, message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    turns = [
        {"role": h["role"], "content": h["content"][:MAX_MESSAGE_CHARS]}
        for h in (history or [])[-MAX_HISTORY:]
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    messages = [
        {"role": "system", "content": f"{_SYSTEM}\n\nCONTEXT:\n{context}"},
        *turns,
        {"role": "user", "content": message[:MAX_MESSAGE_CHARS]},
    ]
    answer = await _complete(messages, max_tokens=1200)
    if not answer:
        raise AIUnavailable("The AI returned an empty answer")
    return answer
