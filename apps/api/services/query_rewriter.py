"""Rewrites a follow-up question into a standalone search query.

Retrieval used to search on the raw latest question only, so a follow-up such
as "how does that compare to Q2?" retrieved poorly: the words that say what
"that" is live in earlier turns. When a conversation has history, the question
is rewritten with that context for RETRIEVAL ONLY — generation still receives
the user's own words plus the history. Any failure returns the original
question, so rewriting can only help, never block an answer.
"""

import logging
import os
import re
import time

from google import genai
from google.genai import types

from services.gemini_retry import call_with_retry

logger = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash-lite"  # same cheap model as citation verification
MAX_HISTORY_CHARS_PER_MESSAGE = 600

SYSTEM_INSTRUCTION = (
    "You turn a user's latest question in a conversation into ONE standalone search query "
    "for finding passages in their documents. Resolve pronouns and references ('it', 'that', "
    "'the second one', 'same for Q2') using the conversation. Keep every specific name, "
    "number, date, and term. If the question already stands on its own, return it unchanged. "
    "Output only the query — no quotes, no explanation."
)

_CITATION_BRACKET = re.compile(r"\[[^\[\]]*\]")


def _default_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


class QueryRewriter:
    def __init__(self, client: genai.Client | None = None, *, retry_sleep=time.sleep):
        self._client = client
        self._retry_sleep = retry_sleep

    def rewrite(self, question: str, history: list[dict] | None) -> str:
        if not history:
            return question
        try:
            client = self._client or _default_client()
            self._client = client
            response = call_with_retry(
                lambda: client.models.generate_content(
                    model=MODEL,
                    contents=[types.Part.from_text(text=_build_prompt(question, history))],
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.0),
                ),
                what="query rewriter",
                sleep_fn=self._retry_sleep,
            )
            rewritten = (response.text or "").strip().strip('"').strip()
        except Exception as exc:
            logger.warning("query rewriter failed (%s) — searching with the original question", type(exc).__name__)
            return question
        # An empty or runaway answer isn't a usable query.
        if not rewritten or len(rewritten) > 4 * len(question) + 300:
            return question
        return rewritten


def _build_prompt(question: str, history: list[dict]) -> str:
    lines = ["Conversation so far (oldest first):"]
    for message in history:
        speaker = "User" if message["role"] == "user" else "Assistant"
        text = _CITATION_BRACKET.sub("", message["content"]).strip()
        if len(text) > MAX_HISTORY_CHARS_PER_MESSAGE:
            text = text[:MAX_HISTORY_CHARS_PER_MESSAGE] + "…"
        lines.append(f"{speaker}: {text}")
    lines.append(f"\nLatest question: {question}")
    return "\n".join(lines)
