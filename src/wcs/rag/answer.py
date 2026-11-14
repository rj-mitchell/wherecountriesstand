"""What the model is asked for a briefing: this site's instructions, its question to the model and its sources.

The call itself (the streaming, the citations as they come, the prompt cache, the retry without the fallback beta)
is the Where They Stand sites' shared model client (wts-core, ``wts_core.adapters``). What is here is what is this
site's own: which model, how hard it is asked to think, and the words around the reader's question.
"""

from __future__ import annotations

from datetime import date

from wts_core.ports import ModelRequest

from wcs.config import Settings
from wcs.rag.prompts import system_prompt, user_instruction
from wcs.rag.sources import Source


def briefing_request(settings: Settings, question: str, sources: list[Source], mode: str = "summary",
                     plan_note: str = "", history: list[dict[str, str]] | None = None, today: date | None = None,
                     group_by: str = "region", compare: list[dict[str, str]] | None = None) -> ModelRequest:
    """The request for one briefing: the sources as documents the model can cite, then the question in the site's
    own words. The instructions are the same for every request (apart from the date), so they are sent to be kept
    in the provider's prompt cache (``[answer] cache_instructions``: "short", as always; "long" or "off")."""
    cfg = settings.answer
    history_note = ""
    if history:
        history_note = "If this question follows on from an earlier one, answer what it asks, building on the " \
            "earlier briefing rather than repeating it (do not restate points already made unless the new " \
            "records change them). Earlier in this conversation (for context only; cite only the search " \
            "results below):\n" + \
            "\n".join(f"Q: {h['question']}\nA (summary): {h.get('answer', '')[:600]}" for h in history[-3:])
    return ModelRequest(
        model=cfg.get("model", "claude-sonnet-5-5"),
        instructions=system_prompt(today or date.today()),
        question=user_instruction(question, mode, plan_note, history_note, group_by, compare),
        documents=tuple(s.document() for s in sources),
        max_tokens=int(cfg.get("max_tokens", 16000)),
        effort=cfg.get("comprehensive_effort" if mode == "comprehensive" else "summary_effort", "medium"),
        cache_instructions=cfg.get("cache_instructions", "short"),
        fallbacks=bool(cfg.get("use_fallbacks", True)))
