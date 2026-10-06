"""Retrieval benchmark over the fixture documents (questions in questions.json).

Run from apps/api:

    uv run python -m eval.run                    # answerability only: parse + chunk, no services
    uv run python -m eval.run --retrieval fts    # + full-text ranking against local Supabase (no API keys)
    uv run python -m eval.run --retrieval full   # + real hybrid retrieval (Voyage/Gemini keys, uses quota)

Answerability asks whether parsing and chunking put the facts that answer
each question into a single chunk; if not, no retriever can find them.
Retrieval scores where the first answering chunk ranks when every
benchmark document is in scope at once: recall@1, recall@3, recall@k and
MRR. Options: --k (default 5), --rerank (full mode), --sleep SECONDS
between queries (Voyage's free tier allows about 3 requests a minute),
--json PATH to save per-question results for comparing runs.
"""

import argparse
import json
import mimetypes
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
FIXTURES = API_DIR / "tests" / "fixtures"
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"

_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html",
}


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def answers(content: str, expect: list[str]) -> bool:
    haystack = _normalize(content)
    return all(_normalize(needle) in haystack for needle in expect)


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS_PATH.read_text())["questions"]


@dataclass
class ParsedFixture:
    filename: str
    elements: list
    chunks: list = field(default_factory=list)


def parse_fixtures(filenames: set[str]) -> dict[str, ParsedFixture]:
    from services.chunker import Chunker
    from services.parser import Parser

    parsed = {}
    for name in sorted(filenames):
        document = Parser(ocr_tiers=[]).parse((FIXTURES / name).read_bytes(), filename=name)
        parsed[name] = ParsedFixture(name, document.elements, Chunker().chunk(document))
    return parsed


def answerability(questions: list[dict], parsed: dict[str, ParsedFixture]) -> list[dict]:
    return [
        {
            "id": q["id"],
            "answerable": any(answers(c.content, q["expect"]) for c in parsed[q["doc"]].chunks),
        }
        for q in questions
    ]


class _ConstantEmbedder:
    """FTS mode: chunk rows need some vector, but it is never searched."""

    def embed(self, chunks):
        from services.embedder import EmbeddedChunk

        return [EmbeddedChunk(vector=[1.0] + [0.0] * 1023, provider="voyage") for _ in chunks]


def retrieval(questions: list[dict], parsed: dict[str, ParsedFixture], mode: str, k: int, rerank: bool, sleep: float):
    from db import queries
    from services.retriever import Retriever
    from tests._local_supabase import admin_client, create_test_user, delete_test_user

    admin = admin_client()
    user_id, _email = create_test_user(admin)
    try:
        if mode == "full":
            from services.embedder import Embedder

            embedder = Embedder()
        else:
            embedder = _ConstantEmbedder()

        document_ids = []
        for fixture in parsed.values():
            doc = queries.create_document(
                admin,
                user_id=user_id,
                filename=fixture.filename,
                storage_path=f"eval/{user_id}/{fixture.filename}",
                mime_type=_MIME_TYPES.get(Path(fixture.filename).suffix) or mimetypes.guess_type(fixture.filename)[0],
                size_bytes=(FIXTURES / fixture.filename).stat().st_size,
            )
            rows = queries.build_chunk_rows(
                document_id=doc["id"],
                user_id=user_id,
                chunks=fixture.chunks,
                embedded_chunks=embedder.embed(fixture.chunks),
                figure_paths={},
                elements=fixture.elements,
            )
            queries.insert_chunks(admin, rows)
            queries.mark_ready(admin, doc["id"])
            document_ids.append(doc["id"])

        retriever = Retriever(client=admin)
        results = []
        for i, q in enumerate(questions):
            if i and sleep:
                time.sleep(sleep)
            if mode == "full":
                contents = [c.content for c in retriever.retrieve(q["question"], document_ids, user_id, k=k, rerank=rerank)]
            else:
                contents = [row["content"] for row in retriever._fts_search(q["question"], document_ids, user_id, k)]
            rank = next((r for r, content in enumerate(contents, start=1) if answers(content, q["expect"])), None)
            results.append({"id": q["id"], "rank": rank})
        return results
    finally:
        delete_test_user(admin, user_id)  # cascades to the documents and chunks


def summarize_retrieval(results: list[dict], k: int) -> dict:
    n = len(results)
    ranks = [r["rank"] for r in results]
    return {
        "recall@1": sum(1 for r in ranks if r == 1) / n,
        "recall@3": sum(1 for r in ranks if r is not None and r <= 3) / n,
        f"recall@{k}": sum(1 for r in ranks if r is not None) / n,
        "mrr": sum(1 / r for r in ranks if r is not None) / n,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrieval", choices=["fts", "full"])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if args.retrieval:
        # Point the app's clients at the local stack, as tests/conftest.py does.
        from tests._local_supabase import LOCAL_SUPABASE_SERVICE_ROLE_KEY, LOCAL_SUPABASE_URL

        os.environ["SUPABASE_URL"] = LOCAL_SUPABASE_URL
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = LOCAL_SUPABASE_SERVICE_ROLE_KEY

    questions = load_questions()
    by_id = {q["id"]: q for q in questions}
    parsed = parse_fixtures({q["doc"] for q in questions})

    report: dict = {"answerability": answerability(questions, parsed)}
    unanswerable = [r["id"] for r in report["answerability"] if not r["answerable"]]
    answerable_count = len(questions) - len(unanswerable)
    print(f"Answerability: {answerable_count}/{len(questions)} questions have an answering chunk")
    for qid in unanswerable:
        print(f"  MISSING  {qid}: {by_id[qid]['question']}  (expects {by_id[qid]['expect']})")

    if args.retrieval:
        results = retrieval(questions, parsed, args.retrieval, args.k, args.rerank, args.sleep)
        summary = summarize_retrieval(results, args.k)
        report["retrieval"] = {"mode": args.retrieval, "k": args.k, "rerank": args.rerank, "summary": summary, "results": results}
        label = args.retrieval + (" + rerank" if args.rerank else "")
        print(f"\nRetrieval ({label}, all {len(parsed)} documents in scope):")
        print("  " + "  ".join(f"{name} {value:.2f}" for name, value in summary.items()))
        for r in results:
            if r["rank"] != 1:
                where = f"rank {r['rank']}" if r["rank"] else f"not in top {args.k}"
                print(f"  {where:>14}  {r['id']}: {by_id[r['id']]['question']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nWrote {args.json}")
    return 1 if unanswerable else 0


if __name__ == "__main__":
    sys.exit(main())
