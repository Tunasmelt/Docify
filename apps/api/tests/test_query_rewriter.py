# Unit tests for services/query_rewriter.py (2026-10-06).

import httpx
from google.genai.errors import ServerError

from services.query_rewriter import MODEL, QueryRewriter

HISTORY = [
    {"role": "user", "content": "What was Q3 revenue?"},
    {"role": "assistant", "content": "Q3 revenue was $4.2M [1]."},
]


class _Response:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


class _Client:
    def __init__(self, outcomes):
        self.models = _Models(outcomes)


def _rewriter(outcomes):
    client = _Client(outcomes)
    return QueryRewriter(client=client, retry_sleep=lambda _s: None), client.models


def test_no_history_returns_the_question_without_calling_gemini():
    rewriter, models = _rewriter([])

    assert rewriter.rewrite("What was Q3 revenue?", []) == "What was Q3 revenue?"
    assert models.calls == []


def test_follow_up_is_rewritten_using_the_conversation():
    rewriter, models = _rewriter(['"What was Q2 revenue?"'])

    assert rewriter.rewrite("And for Q2?", HISTORY) == "What was Q2 revenue?"
    assert models.calls[0]["model"] == MODEL
    prompt = models.calls[0]["contents"][0].text
    assert "Q3 revenue was $4.2M" in prompt and "[1]" not in prompt  # citation markers stripped
    assert prompt.rstrip().endswith("Latest question: And for Q2?")


def test_any_failure_falls_back_to_the_original_question():
    unavailable = ServerError(503, httpx.Response(503, json={"error": {"message": "x", "status": "UNAVAILABLE"}}))
    rewriter, models = _rewriter([unavailable, unavailable, unavailable])

    assert rewriter.rewrite("And for Q2?", HISTORY) == "And for Q2?"
    assert len(models.calls) == 3  # transient errors were retried first


def test_empty_or_runaway_output_falls_back_to_the_original_question():
    empty, _ = _rewriter([""])
    runaway, _ = _rewriter(["word " * 500])

    assert empty.rewrite("And for Q2?", HISTORY) == "And for Q2?"
    assert runaway.rewrite("And for Q2?", HISTORY) == "And for Q2?"
