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
