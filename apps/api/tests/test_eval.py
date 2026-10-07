"""The retrieval benchmark's offline half (eval/run.py) as a regression gate:
every benchmark question must have a chunk that answers it. A parser or
chunker change that splits a fact from its labels (a table row from its
header, a value from its caption) fails here before it reaches retrieval."""

import pytest

from eval.run import FIXTURES, answerability, answers, load_questions, parse_fixtures


@pytest.fixture(scope="module")
def questions():
    return load_questions()


@pytest.fixture(scope="module")
def parsed(questions):
    return parse_fixtures({q["doc"] for q in questions})


def test_benchmark_questions_are_well_formed(questions):
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    for q in questions:
        assert (FIXTURES / q["doc"]).exists(), q["doc"]
        assert q["question"].strip() and q["expect"], q["id"]


def test_every_benchmark_question_has_an_answering_chunk(questions, parsed):
    missing = [r["id"] for r in answerability(questions, parsed) if not r["answerable"]]
    assert missing == []


def test_answer_matching_ignores_case_and_whitespace_but_needs_every_string():
    content = "| Region | Units |\n|---|---|\n| South   3 | 203 |"
    assert answers(content, ["units", "South 3", "203"])
    assert not answers(content, ["Units", "South 3", "999"])


@pytest.mark.parametrize("answer,document_name,quote,expected", [
    ("Revenue was 4.2M [1].", "a.pdf", "Revenue was 4.2M.", True),
    ("Revenue was 4.2M [1].", "unrelated.pdf", "Revenue was 4.2M.", False),
    ("Revenue was 9.9M [1].", "a.pdf", "Revenue was 4.2M.", False),
    ("Revenue was 4.2M [1].", "a.pdf", "not in the source", False),
    ("Revenue was 4.2M [99].", "a.pdf", "Revenue was 4.2M.", False),
])
def test_answer_quality_checks_facts_sources_and_grounding_during_provider_fallback(monkeypatch, answer, document_name, quote, expected):
    import json
    from eval.run import evaluate_generated_answer
    from services import cohere_client
    from services.generator import Generator
    from services.verifier import Verifier
    from tests.test_cohere_fallback import _GeminiDown, _gen_chunks, _no_sleep

    monkeypatch.setenv("COHERE_API_KEY", "test-key")
    replies = iter([answer, json.dumps({"verdict": "supported", "quote": quote})])
    monkeypatch.setattr(cohere_client, "chat", lambda *a, **k: cohere_client.ChatResult(
        text=next(replies), model="command-a-03-2025", input_tokens=1, output_tokens=1))
    chunks = _gen_chunks()
    chunks[0].document_name = document_name
    question = {"question": "What was revenue?", "doc": "a.pdf", "expect": ["Revenue", "4.2M"], "answer_expect": ["4.2M"]}
    result = evaluate_generated_answer(question, chunks,
        Generator(client=_GeminiDown(), retry_sleep=_no_sleep),
        Verifier(client=_GeminiDown(), retry_sleep=_no_sleep))
    assert result["passed"] is expected
    assert result["model"] == "command-a-03-2025"


def test_benchmark_has_explicit_expected_answer_facts(questions):
    assert all(q["answer_expect"] and all(f.strip() for f in q["answer_expect"]) for q in questions)



def test_generation_benchmark_uses_real_fts_results_and_production_fallback(monkeypatch, admin):
    import json
    from eval.run import retrieval
    from services import cohere_client, generator as generator_module, verifier as verifier_module
    from services.generator import Generator
    from services.verifier import Verifier
    from tests.test_cohere_fallback import _GeminiDown, _no_sleep

    monkeypatch.setenv("COHERE_API_KEY", "test-key")
    replies = iter(["Revenue in Q2 2026 was 1,350,000 [1].", json.dumps({"verdict": "supported", "quote": "1,350,000"})])
    monkeypatch.setattr(cohere_client, "chat", lambda *a, **k: cohere_client.ChatResult(
        text=next(replies), model="command-a-03-2025", input_tokens=1, output_tokens=1))
    monkeypatch.setattr(generator_module, "Generator", lambda: Generator(client=_GeminiDown(), retry_sleep=_no_sleep))
    monkeypatch.setattr(verifier_module, "Verifier", lambda: Verifier(client=_GeminiDown(), retry_sleep=_no_sleep))
    question = next(q for q in load_questions() if q["id"] == "docx-01")
    result = retrieval([question], parse_fixtures({question["doc"]}), "fts", 5, False, 0, generation=True)
    assert result[0]["rank"] == 1
    assert result[0]["generation"]["passed"] is True, result
    assert result[0]["generation"]["model"] == "command-a-03-2025"
