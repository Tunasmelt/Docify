# Tests for [FEAT-010] Gemini generator wrapper
#
# Two tiers, same split as test_retriever.py:
#
# 1. FAST/MOCKED — a fake genai client records exactly what Generator sent
#    it and returns a caller-controlled response, so citation parsing,
#    multimodal content assembly, hallucinated-marker handling, and
#    metadata plumbing can all be tested precisely and cheaply.
#
# 2. REAL — gated behind env vars (RUN_REAL_GEMINI_TEST, then
#    RUN_GENERATION_QUALITY_TEST), same pattern as
#    RUN_REAL_VOYAGE_TEST/RUN_RETRIEVAL_QUALITY_TEST. The quality test
#    reuses FEAT-009's exact table_heavy.pdf fixture and questions, since
#    this is the first point where retrieval quality and generation
#    quality compound — a correct retrieval result is wasted if the
#    generated answer doesn't actually cite it.

import os
import time

import pytest
from google.genai import types

from services.generator import (
    MODEL,
    GenerateResult,
    Generator,
    GeneratorChunk,
    GenerationError,
    _parse_citations,
)


# --- Fakes --------------------------------------------------------------


class FakeUsageMetadata:
    def __init__(self, prompt_token_count=100, candidates_token_count=20):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class FakeResponse:
    def __init__(self, text="an answer [1].", model_version="gemini-3.6-flash-001", usage_metadata=None):
        self.text = text
        self.model_version = model_version
        self.usage_metadata = usage_metadata if usage_metadata is not None else FakeUsageMetadata()
        self.prompt_feedback = None


class FakeModels:
    """Stands in for client.models — records every call's kwargs so tests
    can assert on exactly what was sent to Gemini, and returns a
    caller-controlled canned response."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response if response is not None else FakeResponse()

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class FakeClient:
    def __init__(self, response=None):
        self.models = FakeModels(response)


def _chunk(chunk_id="c1", content="some content", element_type="text", page_number=1, document_name="doc.pdf", image=None):
    return GeneratorChunk(
        chunk_id=chunk_id,
        content=content,
        element_type=element_type,
        page_number=page_number,
        document_name=document_name,
        image=image,
    )


# --- Part 1: fast/mocked --------------------------------------------------


# Acceptance criterion: Generator.generate(question, chunks) -> GenerateResult
# returns answer text + parsed citation markers
def test_generator_generate_question_chunks_generateresult_returns_an():
    client = FakeClient(response=FakeResponse(text="Revenue grew 12% [1], per the filing [2]."))
    generator = Generator(client=client)

    result = generator.generate("What was revenue growth?", [_chunk(chunk_id="a"), _chunk(chunk_id="b")])

    assert isinstance(result, GenerateResult)
    assert result.answer == "Revenue grew 12% [1], per the filing [2]."
    assert result.cited_indices == [1, 2]
    assert result.hallucinated_markers == []


# Acceptance criterion: System prompt instructs Gemini 3.6 Flash to cite chunk IDs inline as `[N]`
def test_system_prompt_instructs_gemini_3_6_flash_to_cite_chunk_ids_i():
    client = FakeClient()
    generator = Generator(client=client)

    generator.generate("question", [_chunk()])

    assert len(client.models.calls) == 1
    call = client.models.calls[0]
    assert call["model"] == MODEL == "gemini-3.6-flash"
    system_instruction = call["config"].system_instruction
    assert "[N]" in system_instruction
    assert "position" in system_instruction.lower() or "number printed immediately before" in system_instruction

    # The marker is positional (1-indexed into `chunks`), not the chunk's
    # database id — the system prompt must say so explicitly, since a
    # model that assumed otherwise would produce citations FEAT-012 could
    # never resolve back to a real chunk_id.
    assert "not any database id" in system_instruction.lower() or "not any" in system_instruction.lower()


# Acceptance criterion: Multimodal — figure chunks pass their image content to Gemini
def test_multimodal_figure_chunks_pass_their_image_content_to_gemini():
    client = FakeClient()
    generator = Generator(client=client)

    fake_png_bytes = b"\x89PNG\r\n\x1a\nfake-image-data"
    figure_chunk = _chunk(chunk_id="fig1", content="Figure 3: revenue chart", element_type="figure", image=fake_png_bytes)
    text_chunk = _chunk(chunk_id="txt1", content="Revenue rose steadily.", element_type="text")

    generator.generate("What does the chart show?", [figure_chunk, text_chunk])

    contents = client.models.calls[0]["contents"]
    image_parts = [p for p in contents if isinstance(p, types.Part) and p.inline_data is not None]
    assert len(image_parts) == 1
    assert image_parts[0].inline_data.data == fake_png_bytes
    assert image_parts[0].inline_data.mime_type == "image/png"

    # The text-only chunk must NOT produce an image part.
    text_chunk_texts = [p.text for p in contents if p.text is not None]
    assert any("Revenue rose steadily." in t for t in text_chunk_texts)
    assert any("Figure 3: revenue chart" in t for t in text_chunk_texts)


def test_non_figure_chunks_never_produce_an_image_part_even_with_content():
    client = FakeClient()
    generator = Generator(client=client)

    generator.generate("question", [_chunk(content="a table rendered as markdown", element_type="table")])

    contents = client.models.calls[0]["contents"]
    image_parts = [p for p in contents if isinstance(p, types.Part) and p.inline_data is not None]
    assert image_parts == []


# --- Conversation memory (2026-07-27 follow-up) ----------------------------


def _history_text_parts(contents):
    return [p.text for p in contents if p.text is not None]


def test_generate_with_no_history_produces_no_history_block_at_all():
    # Regression guard: history=None (the default, and every pre-2026-07-27
    # call site) must produce byte-identical contents to before this
    # feature existed — no empty "Conversation history:" block appended.
    client = FakeClient()
    generator = Generator(client=client)

    generator.generate("question", [_chunk(content="some content")])

    texts = _history_text_parts(client.models.calls[0]["contents"])
    assert not any("Conversation history" in t for t in texts)


def test_generate_folds_history_into_the_prompt_before_the_chunks():
    client = FakeClient()
    generator = Generator(client=client)
    history = [
        {"role": "user", "content": "What was Q3 revenue?"},
        {"role": "assistant", "content": "Q3 revenue was $5M [1]."},
    ]

    generator.generate("How does that compare to Q2?", [_chunk(content="Q2 revenue was $4M.")], history=history)

    contents = client.models.calls[0]["contents"]
    texts = _history_text_parts(contents)
    assert any("Conversation history" in t for t in texts)
    assert any("What was Q3 revenue?" in t for t in texts)
    assert any("Q3 revenue was $5M" in t for t in texts)

    # History must appear BEFORE the numbered chunks in the prompt, and
    # the chunk numbering must be completely unaffected by history's
    # presence — chunk [1] is still the only real chunk, at position 1.
    history_index = next(i for i, p in enumerate(contents) if p.text and "Conversation history" in p.text)
    chunk_index = next(i for i, p in enumerate(contents) if p.text and "Q2 revenue was $4M" in p.text)
    assert history_index < chunk_index
    assert any(t.startswith("[1] (page") and "Q2 revenue was $4M" in t for t in texts)


def test_generate_strips_citation_markers_from_historical_assistant_answers():
    # A prior turn's [N] markers were positions into THAT turn's own
    # chunks list, which no longer exists — leaving them in the folded-in
    # history text would be meaningless noise at best. Confirms they're
    # actually stripped, not just documented as stripped.
    client = FakeClient()
    generator = Generator(client=client)
    history = [
        {"role": "user", "content": "What was Q3 revenue?"},
        {"role": "assistant", "content": "Q3 revenue was $5M [1], up from [2]."},
    ]

    generator.generate("follow-up", [_chunk(content="filler")], history=history)

    texts = _history_text_parts(client.models.calls[0]["contents"])
    history_text = next(t for t in texts if "Conversation history" in t)
    assert "[1]" not in history_text
    assert "[2]" not in history_text
    assert "Q3 revenue was $5M, up from" in history_text  # content preserved, only brackets removed


def test_citation_numbering_in_new_answer_is_unaffected_by_marker_like_text_in_history():
    # Citation numbering resets every turn: even if history contains text
    # that looks marker-shaped, _parse_citations only ever scans the
    # model's actual returned answer text (response.text), never the
    # prompt it was given — this locks that in as an explicit regression
    # test rather than leaving it as an implicit consequence of the code
    # structure.
    client = FakeClient(response=FakeResponse(text="A fresh answer [1]."))
    generator = Generator(client=client)
    history = [
        {"role": "user", "content": "earlier question mentioning [1] and [9] as plain text"},
        {"role": "assistant", "content": "an earlier answer citing [1], [2], and even [99]"},
    ]

    result = generator.generate("new question", [_chunk()], history=history)

    # Only the CURRENT answer's own [1] is parsed — none of history's
    # [1]/[2]/[9]/[99] leak into cited_indices or hallucinated_markers.
    assert result.cited_indices == [1]
    assert result.hallucinated_markers == []


# Acceptance criterion: Returns metadata: model, input_tokens, output_tokens, latency_ms
def test_returns_metadata_model_input_tokens_output_tokens_latency_ms():
    response = FakeResponse(
        text="answer, no citations needed here.",
        model_version="gemini-3.6-flash-002",
        usage_metadata=FakeUsageMetadata(prompt_token_count=543, candidates_token_count=87),
    )
    client = FakeClient(response=response)
    generator = Generator(client=client)

    result = generator.generate("question", [_chunk()])

    assert result.model == "gemini-3.6-flash-002"
    assert result.input_tokens == 543
    assert result.output_tokens == 87
    assert isinstance(result.latency_ms, float)
    assert result.latency_ms >= 0


# Item 5 — a hallucinated citation index (a marker referencing a chunk
# position that was never provided) must fail safely: dropped from
# cited_indices, flagged in hallucinated_markers, never a crash and never
# silently treated as a valid reference.
def test_hallucinated_citation_marker_is_dropped_and_flagged_not_crashed():
    client = FakeClient(response=FakeResponse(text="The answer is X [1], allegedly also [7]."))
    generator = Generator(client=client)

    # Only ONE chunk provided — [7] cannot correspond to anything real.
    result = generator.generate("question", [_chunk()])

    assert result.cited_indices == [1]
    assert result.hallucinated_markers == [7]
    assert result.answer == "The answer is X [1], allegedly also [7]."  # raw text left untouched


def test_parse_citations_directly_handles_zero_and_negative_and_duplicate_markers():
    # Unit-level check of the parsing helper itself: [0] is out of range
    # (markers are 1-indexed), duplicates collapse, order is preserved.
    cited, hallucinated = _parse_citations("[2] then [1] then [2] again, also [0] and [99]", num_chunks=2)

    assert cited == [2, 1]
    assert hallucinated == [0, 99]


# 2026-07-24 self-audit: `[-1]` and `[0]` must fail the same safe way —
# neither corresponds to a real 1-indexed chunk. An earlier version of
# the number-extraction regex (`\d+`, no sign) silently stripped the
# minus sign from `[-1]` and extracted "1" as a VALID citation, treating
# it differently from `[0]` (correctly flagged hallucinated). Fixed by
# capturing an optional leading `-` so `int("-1") == -1` fails the same
# range check `0` already fails.
def test_parse_citations_treats_negative_and_zero_markers_identically():
    cited_zero, hallucinated_zero = _parse_citations("[0]", num_chunks=5)
    cited_negative, hallucinated_negative = _parse_citations("[-1]", num_chunks=5)

    assert cited_zero == cited_negative == []
    assert hallucinated_zero == [0]
    assert hallucinated_negative == [-1]


def test_parse_citations_ignores_brackets_with_no_digits_rather_than_flagging_them():
    # A bracket containing no digits at all ("[citation needed]", a
    # literal "[N]" placeholder, empty "[]") is not citation-shaped —
    # silently producing neither a cited nor a hallucinated entry is the
    # correct behavior here, not the same failure class as a numeric
    # marker outside the valid range.
    for text in ("[citation needed]", "[N]", "[]", "[  ]"):
        cited, hallucinated = _parse_citations(text, num_chunks=5)
        assert cited == [], text
        assert hallucinated == [], text


# Found live: FEAT-010's real generation-quality test against
# table_heavy.pdf produced real Gemini answers using a grouped-bracket
# form, `[2, 3]` and `[1, 2, 4]`, despite the system prompt asking for
# the singular `[N]` form. A regex matching only `[N]` would silently
# drop every number inside a grouped bracket — neither cited nor
# flagged. Locked in as a permanent regression test.
def test_parse_citations_handles_gemini_s_real_world_grouped_bracket_form():
    cited, hallucinated = _parse_citations("Per the tables [2, 3] and also [1, 2, 4] and [7, 8].", num_chunks=4)

    assert cited == [2, 3, 1, 4]
    assert hallucinated == [7, 8]


# 2026-07-24 self-audit of the grouped-bracket fix above: the first fix
# only handled comma-separated digits (`\[(\d+(?:\s*,\s*\d+)*)\]`) — a
# semicolon variant, `[2; 3]`, reproduced the identical silent-drop
# failure under a different delimiter. Redesigned to match ANY
# non-nested bracket span and pull every integer out of its contents
# regardless of delimiter. Each case below was independently verified
# live against the parser before being locked in here.
@pytest.mark.parametrize(
    "text,expected_cited",
    [
        ("[2,3]", [2, 3]),  # no space after comma
        ("[2 , 3]", [2, 3]),  # space before comma
        ("[10, 2]", [10, 2]),  # multi-digit mixed with single-digit
        ("[2][3]", [2, 3]),  # separate adjacent brackets, not one grouped bracket
        ("[2; 3]", [2, 3]),  # semicolon — the delimiter the first fix missed
        ("[2 and 3]", [2, 3]),  # natural-language separator
    ],
)
def test_parse_citations_handles_bracket_variants_beyond_the_one_captured_case(text, expected_cited):
    cited, hallucinated = _parse_citations(text, num_chunks=10)

    assert cited == expected_cited
    assert hallucinated == []


# Deliberate, accepted gap — not a regression, a documented boundary.
# Nested/malformed brackets are judged reasonably out-of-scope: Gemini
# has never been observed producing them, and guessing at malformed
# structure risks worse outcomes than declining to parse it. The inner
# `[3]` still parses as its own bracket; content preceding the nested
# `[` (the "2") is silently dropped rather than guessed at. If this
# shape is ever observed live, this test documents today's behavior as
# the starting point to revisit, not an endorsement that it's ideal.
def test_parse_citations_documents_the_accepted_gap_for_nested_malformed_brackets():
    cited, hallucinated = _parse_citations("[2, [3]]", num_chunks=10)

    assert cited == [3]
    assert hallucinated == []


def test_generate_raises_generation_error_rather_than_calling_gemini_with_no_chunks():
    client = FakeClient()
    generator = Generator(client=client)

    with pytest.raises(GenerationError):
        generator.generate("question", [])

    assert client.models.calls == []


def test_generate_raises_generation_error_when_response_has_no_text():
    client = FakeClient(response=FakeResponse(text=None))
    generator = Generator(client=client)

    with pytest.raises(GenerationError):
        generator.generate("question", [_chunk()])


# 2026-07-24 self-audit item 6: without this, a FEAT-012 implementation
# that forgets to fetch a figure's image from Storage before constructing
# a GeneratorChunk would fail silently — the chunk just gets treated as
# text-only, no error, no signal anything was missing. Since parser.py
# already drops any figure element whose image extraction failed (it
# never becomes a chunk), a figure-typed GeneratorChunk with image=None
# can only mean the caller's fetch step is broken.
def test_generator_chunk_refuses_construction_for_a_figure_with_no_image():
    with pytest.raises(ValueError, match="figure"):
        GeneratorChunk(
            chunk_id="fig-1",
            content="Figure 4: revenue chart",
            element_type="figure",
            page_number=2,
            document_name="doc.pdf",
            # image intentionally omitted (defaults to None)
        )


def test_generator_chunk_allows_non_figure_chunks_with_no_image():
    # Only element_type == "figure" is constrained — everything else
    # legitimately has no image, and must not be rejected.
    for element_type in ("text", "heading", "table", "caption", "list"):
        GeneratorChunk(
            chunk_id="c1", content="content", element_type=element_type, page_number=1, document_name="doc.pdf"
        )


# --- Part 2: real API (opt-in only) --------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_GEMINI_TEST") != "1",
    reason="set RUN_REAL_GEMINI_TEST=1 to run a real Gemini generation call",
)
def test_real_gemini_api_call_cites_the_only_provided_chunk():
    generator = Generator()  # real client, reads GEMINI_API_KEY from env

    chunk = _chunk(
        chunk_id="real-1",
        content="Docify's integration test constant is exactly 8675309.",
        document_name="integration-test.txt",
    )

    started = time.perf_counter()
    result = generator.generate("What is Docify's integration test constant?", [chunk])
    wall_ms = (time.perf_counter() - started) * 1000

    print("\n" + "=" * 90)
    print("FEAT-010 real Gemini API call — single-chunk smoke test")
    print("=" * 90)
    print(f"answer: {result.answer!r}")
    print(f"cited_indices={result.cited_indices} hallucinated_markers={result.hallucinated_markers}")
    print(f"model={result.model} input_tokens={result.input_tokens} output_tokens={result.output_tokens}")
    print(f"latency_ms={result.latency_ms:.1f} (wall-clock around call: {wall_ms:.1f})")
    print("=" * 90)

    assert "8675309" in result.answer
    assert result.cited_indices == [1]
    assert result.hallucinated_markers == []
    assert result.input_tokens > 0
    assert result.output_tokens > 0


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# Same 4 questions FEAT-009's retrieval-quality test proved find the right
# chunk in the top-5 — reused verbatim so this test measures ONLY whether
# generation correctly cites what retrieval already found, not a new
# retrieval scenario.
GENERATION_QUALITY_QUESTIONS = [
    {
        "question": "What is Angola's Human Development Index value in 2010?",
        "expect_substring": "Angola",
    },
    {
        "question": "Does Respondent C have a driving licence?",
        "expect_substring": "driving licence",
    },
    {
        "question": "What was the balance in the 2011 accounts?",
        "expect_substring": "Balance",
    },
    {
        "question": "What courses does Institution X offer in Mathematics?",
        "expect_substring": "Mathematics",
    },
]


def _fetch_figure_bytes(admin, storage_path: str) -> bytes:
    return admin.storage.from_("figures").download(storage_path)


def _to_generator_chunks(admin, retrieved_chunks) -> list[GeneratorChunk]:
    """Adapts FEAT-009's RetrievedChunk rows into Generator's own input
    type. RetrievedChunk does not carry figure_path (the RPC functions
    it calls never SELECT it — see FEAT-009's self-audit item 3 note on
    FEAT-012's acceptance criteria), so this test queries the chunks
    table directly by id to fetch it, then downloads the real image
    bytes from Storage for any figure chunk. This exact adaptation is
    what FEAT-012 will need to do for real when it wires Retriever's
    output into Generator's input — done here only for this test's own
    purposes, not a change to retriever.py."""
    ids = [c.chunk_id for c in retrieved_chunks]
    rows = admin.table("chunks").select("id,figure_path").in_("id", ids).execute().data
    figure_paths = {r["id"]: r["figure_path"] for r in rows}

    out = []
    for c in retrieved_chunks:
        path = figure_paths.get(c.chunk_id)
        image = _fetch_figure_bytes(admin, path) if path else None
        out.append(
            GeneratorChunk(
                chunk_id=c.chunk_id,
                content=c.content,
                element_type=c.element_type,
                page_number=c.page,
                document_name=c.document_name,
                image=image,
            )
        )
    return out


@pytest.mark.skipif(
    os.environ.get("RUN_GENERATION_QUALITY_TEST") != "1",
    reason="set RUN_GENERATION_QUALITY_TEST=1 to run real Docling+Voyage+Gemini retrieval+generation quality checks (slow, uses quota)",
)
def test_generation_cites_the_chunk_retrieval_already_proved_relevant(admin, user_a):
    from services.chunker import Chunker
    from services.embedder import Embedder
    from services.parser import Parser
    from services.retriever import Retriever

    user_id, _token = user_a

    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)
    vectors = Embedder().embed(chunks)

    document_id = (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "table_heavy.pdf",
                "storage_path": f"uploads/{user_id}/table_heavy.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(pdf_bytes),
            }
        )
        .execute()
        .data[0]["id"]
    )

    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "content": chunk.content,
                "embedding": vector,
            }
        )
    admin.table("chunks").insert(rows).execute()

    for image in (c.image for c in chunks if c.image is not None):
        image.close()

    retriever = Retriever(client=admin)
    generator = Generator()

    # Same real Voyage free-tier pacing constraint FEAT-009 hit (3 RPM,
    # no payment method on this account) — see test_retriever.py. The
    # ingestion embed() call above already used one slot.
    time.sleep(25)

    print("\n" + "=" * 90)
    print("FEAT-010 generation quality — real retrieval + real generation, table_heavy.pdf")
    print("=" * 90)

    all_cited = True
    for i, spec in enumerate(GENERATION_QUALITY_QUESTIONS):
        if i > 0:
            time.sleep(25)

        retrieved = retriever.retrieve(spec["question"], [document_id], user_id, k=5)
        expected_position = next(
            (pos for pos, r in enumerate(retrieved, start=1) if spec["expect_substring"].lower() in r.content.lower()),
            None,
        )

        generator_chunks = _to_generator_chunks(admin, retrieved)
        result = generator.generate(spec["question"], generator_chunks)

        cited_the_expected_chunk = expected_position is not None and expected_position in result.cited_indices

        print(f"\nQ: {spec['question']}")
        print(f"   expected chunk retrieved at position: {expected_position}")
        print(f"   answer: {result.answer!r}")
        print(f"   cited_indices={result.cited_indices} hallucinated_markers={result.hallucinated_markers}")
        print(f"   model={result.model} input_tokens={result.input_tokens} output_tokens={result.output_tokens} " f"latency_ms={result.latency_ms:.1f}")
        print(f"   -> {'PASS' if cited_the_expected_chunk else 'FAIL'}: expected chunk " f"{'was' if cited_the_expected_chunk else 'was NOT'} cited")

        all_cited = all_cited and cited_the_expected_chunk

    print("\n" + "=" * 90)
    print(f"Overall: {'ALL' if all_cited else 'NOT ALL'} answers cited the chunk retrieval proved relevant")
    print("=" * 90)

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()

    assert all_cited
