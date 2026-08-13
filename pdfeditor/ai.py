"""AI Assistant: summarize and answer questions about a PDF's text.

Uses the official Anthropic SDK (Claude). Requires the ``anthropic`` package
and an ``ANTHROPIC_API_KEY`` in the environment. All network use is opt-in —
nothing here runs unless the user explicitly invokes an AI action.
"""

from __future__ import annotations

import os

from .external import ANTHROPIC, require

# Default to the latest Opus model; overridable via env for advanced users.
DEFAULT_MODEL = os.environ.get("PDFEDITOR_AI_MODEL", "claude-opus-5")

# Keep prompts within a sane bound; PDFs can be huge.
_MAX_CHARS = 200_000


class AIError(Exception):
    """Raised for AI configuration or request failures."""


def _client():
    require(ANTHROPIC)
    import anthropic

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise AIError(
            "No API key found. Set ANTHROPIC_API_KEY in your environment, then "
            "restart the app."
        )
    return anthropic.Anthropic()


def _ask_claude(system: str, user_text: str, max_tokens: int = 1500) -> str:
    client = _client()
    try:
        message = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception as exc:  # surface a clean message to the UI
        raise AIError(str(exc)) from exc
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    ).strip()


def summarize(document_text: str) -> str:
    """Summarize the document's text into key points."""
    text = document_text[:_MAX_CHARS]
    if not text.strip():
        raise AIError("The document has no extractable text (it may be a scan — run OCR first).")
    return _ask_claude(
        system=(
            "You are a concise assistant that summarizes documents. Produce a short "
            "executive summary followed by the key points as a bulleted list."
        ),
        user_text=f"Summarize the following document:\n\n{text}",
    )


def ask(document_text: str, question: str) -> str:
    """Answer a question grounded in the document's text."""
    text = document_text[:_MAX_CHARS]
    if not text.strip():
        raise AIError("The document has no extractable text (it may be a scan — run OCR first).")
    return _ask_claude(
        system=(
            "You answer questions strictly based on the provided document. If the "
            "answer is not in the document, say so plainly rather than guessing."
        ),
        user_text=f"Document:\n\n{text}\n\n---\nQuestion: {question}",
    )
