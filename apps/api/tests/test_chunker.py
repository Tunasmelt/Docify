# Tests for [FEAT-005] Chunker
#
# Fixtures reused from FEAT-004/FEAT-027 (apps/api/tests/fixtures/). None
# of the three real fixtures contains a FIGURE with a nearby caption
# (table_heavy has no figures at all; scanned.pdf has none since FEAT-027's
# rewrite — see below), so "figure chunk with caption prepended" is tested
# against a hand-built ParsedDocument instead — the only way to exercise
# that path deterministically.
#
# Counts below updated 2026-08-01 (FEAT-027, Docling -> pdfplumber/
# python-docx/python-pptx/selectolax rewrite — .agent/reviews/2026-08-01-
# parser-research.md): table_heavy.pdf still has 29 real tables, but the
# new parser's own Tier 1 (text-prefix + bbox proximity) links captions to
# ALL 29 directly — beating the old Docling-based parser's 13 explicit +
# chunker's own Tier 2 resolving 6 more (19 total, 10 left uncaptioned).
# Tier 2's OWN logic is unchanged and still exercised by the hand-built
# tie-breaking tests further down this file — it simply has nothing left
# to do on this fixture now that Tier 1 alone resolves everything.
# scanned.pdf's one figure (a small logo, per the OLD Docling baseline) is
# no longer extracted at all under the new parser: it was a full-page-
# covering raster image (the scan itself), not a distinct embedded figure
# — filtered deliberately, see services/parser.py's _FIGURE_MAX_PAGE_COVERAGE.
#
# Each fixture is parsed once per test session (module-scoped fixtures).

import pytest
from PIL import Image

from services.chunker import MAX_CHUNK_TOKENS, Chunker, _approx_token_count
from services.parser import BBox, ElementType, ParsedDocument, ParsedElement, Parser

FIXTURES = "tests/fixtures"


def load(name: str) -> bytes:
    with open(f"{FIXTURES}/{name}", "rb") as f:
        return f.read()


def make_element(
    element_type,
    page_number=1,
    content="text",
    element_id="#/x/0",
    bbox=None,
    associated_caption_ids=None,
    association_method=None,
):
    return ParsedElement(
        element_type=element_type,
        page_number=page_number,
        bbox=bbox or BBox(0.0, 0.0, 10.0, 10.0),
        content=content,
        element_id=element_id,
        associated_caption_ids=associated_caption_ids or [],
        association_method=association_method,
    )


@pytest.fixture(scope="module")
def clean_digital_doc():
    return Parser().parse(load("clean_digital.pdf"))


@pytest.fixture(scope="module")
def table_heavy_doc():
    return Parser().parse(load("table_heavy.pdf"))


@pytest.fixture(scope="module")
def scanned_doc():
    return Parser().parse(load("scanned.pdf"))


# Acceptance criterion: Groups adjacent text/heading elements into chunks of ~500 tokens, respecting element boundaries (never splits a table row or a heading)
def test_groups_text_heading_list_elements_respecting_boundaries(clean_digital_doc):
    chunks = Chunker().chunk(clean_digital_doc)

    # clean_digital.pdf: 22 elements (6 heading + 6 text + 9 list + 1 table).
    # Since 2026-10-06 every heading starts a new chunk, so each section is
    # its own chunk; the "Table" heading folds into the table chunk as its
    # prefix; the closing paragraph is prefixed with its section heading.
    types = [c.element_type for c in chunks]
    assert types == [
        ElementType.TEXT, ElementType.LIST, ElementType.TEXT, ElementType.TEXT, ElementType.TABLE, ElementType.TEXT,
    ]
    assert [c.content.split("\n")[0] for c in chunks] == [
        "Sample Document for PDF Testing", "Text Formatting Examples", "Lists", "Quote", "Table", "Table",
    ]
    assert sum(len(c.source_element_indices) for c in chunks) == 22  # every element lands in exactly one chunk
    assert len({i for c in chunks for i in c.source_element_indices}) == 22


# FEAT-020 (2026-07-27) real bug, found via the real end-to-end /query
# test, not a unit test: grouping used to freely cross a page_number
# change, and db/queries.py's build_chunk_rows stores
# min(chunk.page_numbers) as the single page_number column — a chunk
# spanning two pages silently reported only the first one. Harmless-ish
# imprecision for PDF's continuous pagination; a real, confirmed-live
# wrong-slide-number bug for PPTX, where page_number is a genuinely
# discrete slide index. Fixed by treating a page/slide change as a flush
# boundary, same tier as TOKEN_BUDGET overflow.
def test_grouping_never_crosses_a_page_number_boundary_even_within_token_budget():
    elements = [
        make_element(ElementType.TEXT, page_number=1, content="page one, first paragraph", element_id="#/t/0"),
        make_element(ElementType.TEXT, page_number=1, content="page one, second paragraph", element_id="#/t/1"),
        make_element(ElementType.TEXT, page_number=2, content="page two, first paragraph", element_id="#/t/2"),
    ]
    doc = ParsedDocument(elements=elements)

    chunks = Chunker().chunk(doc)

    # All three elements together are nowhere near TOKEN_BUDGET (500
    # tokens) — without the page-boundary fix, this would produce ONE
    # chunk spanning both pages. Must now produce two: one per page.
    assert len(chunks) == 2
    assert chunks[0].page_numbers == [1]
    assert chunks[0].source_element_indices == [0, 1]
    assert chunks[1].page_numbers == [2]
    assert chunks[1].source_element_indices == [2]


def test_grouping_within_a_single_page_is_unaffected_by_the_page_boundary_fix():
    # Regression guard for the fix above: elements that all share the
    # SAME page_number must still group together exactly as before —
    # the fix must only trigger on an actual page change, not on every
    # element boundary.
    elements = [
        make_element(ElementType.TEXT, page_number=5, content="first", element_id="#/t/0"),
        make_element(ElementType.TEXT, page_number=5, content="second", element_id="#/t/1"),
        make_element(ElementType.TEXT, page_number=5, content="third", element_id="#/t/2"),
    ]
    doc = ParsedDocument(elements=elements)

    chunks = Chunker().chunk(doc)

    assert len(chunks) == 1
    assert chunks[0].page_numbers == [5]
    assert chunks[0].source_element_indices == [0, 1, 2]


def test_table_content_is_never_split_across_chunks(table_heavy_doc):
    chunks = Chunker().chunk(table_heavy_doc)

    table_chunks = [c for c in chunks if c.element_type == ElementType.TABLE]
    assert len(table_chunks) == 29
    for chunk in table_chunks:
        # Exactly one table's worth of source content per chunk (plus,
        # optionally, its caption(s)) — never more than one table folded in.
        table_source_count = sum(
            1 for i in chunk.source_element_indices if table_heavy_doc.elements[i].element_type == ElementType.TABLE
        )
        assert table_source_count == 1
        assert "|" in chunk.content  # markdown table syntax preserved whole


# Acceptance criterion: Preserves metadata: page numbers, source element indices, element_type of primary element
def test_preserves_page_numbers_source_indices_and_element_type(table_heavy_doc):
    chunks = Chunker().chunk(table_heavy_doc)

    assert len(chunks) > 0
    for chunk in chunks:
        assert isinstance(chunk.element_type, ElementType)
        assert isinstance(chunk.page_numbers, list) and len(chunk.page_numbers) > 0
        assert all(isinstance(p, int) for p in chunk.page_numbers)
        assert isinstance(chunk.source_element_indices, list) and len(chunk.source_element_indices) > 0
        for i in chunk.source_element_indices:
            assert 0 <= i < len(table_heavy_doc.elements)
        # page_numbers must actually match the source elements' own pages
        expected_pages = {table_heavy_doc.elements[i].page_number for i in chunk.source_element_indices}
        assert set(chunk.page_numbers) == expected_pages


# Acceptance criterion: Each chunk has a stable `chunk_index` (ordinal within document)
def test_chunk_index_is_stable_and_sequential(table_heavy_doc):
    chunks = Chunker().chunk(table_heavy_doc)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


# Acceptance criterion: Figure elements produce their own chunks (one figure = one chunk, with caption prepended if adjacent)
#
# FEAT-027: scanned.pdf's one figure under the old Docling-based parser
# (a small logo) is no longer extracted at all — it was actually a
# full-page-covering raster image (the scan itself), not a distinct
# embedded figure, filtered deliberately (services/parser.py's
# _FIGURE_MAX_PAGE_COVERAGE — see test_parser.py's
# test_scanned_pdf_full_page_images_are_filtered_not_treated_as_figures).
# No real fixture has a genuine standalone figure element for THIS
# specific "figure produces its own chunk, no caption" shape anymore —
# table.docx and slides.pptx both DO have one real figure each, but
# neither is uncaptioned in quite the same deterministic way, so this is
# now exercised by hand instead, same reasoning as the captioned-figure
# test directly below it.
def test_figure_produces_own_chunk_uncaptioned_hand_built():
    image = Image.new("RGB", (4, 4))
    figure = make_element(ElementType.FIGURE, content=image, element_id="#/pictures/0")
    doc = ParsedDocument(elements=[figure])

    chunks = Chunker().chunk(doc)

    figure_chunks = [c for c in chunks if c.element_type == ElementType.FIGURE]
    assert len(figure_chunks) == 1
    figure_chunk = figure_chunks[0]
    assert isinstance(figure_chunk.image, Image.Image)
    assert figure_chunk.content == ""  # no caption linked
    assert figure_chunk.merged_caption_ids == []
    assert len(figure_chunk.source_element_indices) == 1


def test_scanned_pdf_no_longer_produces_a_figure_chunk(scanned_doc):
    # Real, deliberate behavior change from the old Docling-based parser
    # (confirmed live, not assumed) — see the module docstring above.
    chunks = Chunker().chunk(scanned_doc)
    figure_chunks = [c for c in chunks if c.element_type == ElementType.FIGURE]
    assert figure_chunks == []


def test_figure_chunk_has_caption_prepended_when_explicitly_linked():
    # No real fixture has a captioned figure — built by hand to exercise
    # this specific path deterministically.
    image = Image.new("RGB", (4, 4))
    figure = make_element(
        ElementType.FIGURE,
        content=image,
        element_id="#/pictures/0",
        associated_caption_ids=["#/texts/0"],
    )
    caption = make_element(
        ElementType.CAPTION,
        content="Figure 1: a tiny test image",
        element_id="#/texts/0",
        association_method="explicit",
    )
    doc = ParsedDocument(elements=[caption, figure])

    chunks = Chunker().chunk(doc)

    figure_chunks = [c for c in chunks if c.element_type == ElementType.FIGURE]
    assert len(figure_chunks) == 1
    chunk = figure_chunks[0]
    assert chunk.content == "Figure 1: a tiny test image"
    assert chunk.image is image
    assert chunk.association_method == "explicit"
    assert chunk.merged_caption_ids == ["#/texts/0"]
    # The caption must not also produce its own separate standalone chunk.
    assert not any(c.element_type == ElementType.CAPTION for c in chunks)


# --- Caption association: Tier 1 (explicit) + Tier 2 (heuristic) -----------


def test_explicit_caption_associations_from_parser_are_preserved(table_heavy_doc):
    chunks = Chunker().chunk(table_heavy_doc)

    explicit_table_chunks = [
        c for c in chunks if c.element_type == ElementType.TABLE and c.association_method == "explicit"
    ]
    # FEAT-027: the new parser's own Tier 1 heuristic (text-prefix + bbox
    # proximity) links all 29 real tables directly — beating the old
    # Docling-based parser's 13 (.agent/reviews/2026-08-01-parser-
    # research.md, spot-checked individually in test_parser.py). Every
    # single table/figure<->caption link chunker.py sees for this fixture
    # is now "explicit" by construction, not "heuristic" — Tier 1 simply
    # never leaves anything for Tier 2 to do here anymore.
    assert len(explicit_table_chunks) == 29
    for chunk in explicit_table_chunks:
        assert len(chunk.merged_caption_ids) >= 1
        assert chunk.content.strip() != ""


def test_tier2_heuristic_has_nothing_left_to_resolve_in_table_heavy(table_heavy_doc):
    # FEAT-027: real, honest consequence of Tier 1 now resolving all 29
    # tables directly — chunker.py's OWN Tier 2 logic is unchanged (still
    # exercised directly by the hand-built tie-breaking tests elsewhere in
    # this file), it simply has zero "association_method == none" captions
    # left to work with on this specific fixture anymore.
    chunks = Chunker().chunk(table_heavy_doc)

    heuristic_table_chunks = [
        c for c in chunks if c.element_type == ElementType.TABLE and c.association_method == "heuristic"
    ]
    assert heuristic_table_chunks == []
    assert not any(c.element_type == ElementType.CAPTION and c.association_method == "unmatched" for c in chunks)
    # Confirms every caption element in this fixture was consumed into a
    # table/figure chunk (Tier 1) rather than surviving as its own
    # standalone chunk.
    assert not any(c.element_type == ElementType.CAPTION for c in chunks)


def test_tier2_heuristic_leaves_genuinely_unmatched_caption_standalone():
    # A caption with no table/figure at all on its page — Tier 2 must not
    # invent a match. Hand-built since no real fixture has this shape.
    caption = make_element(
        ElementType.CAPTION,
        page_number=1,
        content="Figure 99: orphaned caption with nothing nearby",
        element_id="#/texts/0",
        association_method="none",
    )
    unrelated_text = make_element(ElementType.TEXT, page_number=1, content="unrelated body text", element_id="#/texts/1")
    doc = ParsedDocument(elements=[caption, unrelated_text])

    chunks = Chunker().chunk(doc)

    standalone = [c for c in chunks if c.element_type == ElementType.CAPTION]
    assert len(standalone) == 1
    assert standalone[0].association_method == "unmatched"
    assert standalone[0].content == "Figure 99: orphaned caption with nothing nearby"
    assert standalone[0].merged_caption_ids == []


def test_uncaptioned_tables_have_no_association_method(table_heavy_doc):
    chunks = Chunker().chunk(table_heavy_doc)

    uncaptioned = [
        c for c in chunks if c.element_type == ElementType.TABLE and c.association_method is None
    ]
    # FEAT-027: 29 tables - 29 explicit (new parser's own Tier 1) - 0
    # heuristic = 0 tables with no caption at all — every real table in
    # this fixture does genuinely have a "Table N[:...]" caption line
    # somewhere on its page; the OLD Docling-based parser's 10
    # "uncaptioned" tables reflected a gap in ITS OWN layout model, not a
    # genuine absence of caption text in the source document.
    assert uncaptioned == []


def test_every_chunk_across_all_fixtures_has_a_valid_association_method(clean_digital_doc, table_heavy_doc, scanned_doc):
    allowed = {None, "explicit", "heuristic", "unmatched"}
    for doc in (clean_digital_doc, table_heavy_doc, scanned_doc):
        chunks = Chunker().chunk(doc)
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk.association_method in allowed
            assert isinstance(chunk.merged_caption_ids, list)


# --- Tier-2 edge cases (Codex review 2026-07-23; these were manually probed
# during that review but not captured as permanent tests — converted here) --


def test_tier2_equidistant_reading_order_candidates_broken_by_bbox_distance():
    # Caption at index 1; table_far at index 0 and table_near at index 2 —
    # both at reading-order distance 1 (a tie on the primary sort key) but
    # at different bbox distances. The nearer one by position must win.
    caption = make_element(
        ElementType.CAPTION, content="a caption", element_id="#/texts/0", bbox=BBox(0, 0, 10, 10), association_method="none"
    )
    table_far = make_element(ElementType.TABLE, content="far table", element_id="#/tables/0", bbox=BBox(1000, 1000, 1010, 1010))
    table_near = make_element(ElementType.TABLE, content="near table", element_id="#/tables/1", bbox=BBox(0, 0, 10, 10))
    doc = ParsedDocument(elements=[table_far, caption, table_near])

    chunks = Chunker().chunk(doc)

    matched = next(c for c in chunks if c.merged_caption_ids)
    assert matched.source_element_indices[0] == 2  # table_near, not table_far


def test_tier2_reading_order_beats_closer_position():
    # A candidate closer in reading order but farther by bbox must still
    # win over one closer by position but farther in reading order —
    # reading-order distance is the primary key, bbox distance only
    # breaks ties, it never overrides reading order.
    caption = make_element(
        ElementType.CAPTION, content="a caption", element_id="#/texts/0", bbox=BBox(0, 0, 10, 10), association_method="none"
    )
    table_near_reading_far_bbox = make_element(
        ElementType.TABLE, content="t0", element_id="#/tables/0", bbox=BBox(1000, 1000, 1010, 1010)
    )
    padding = [make_element(ElementType.TEXT, content=f"pad{i}", element_id=f"#/texts/pad{i}") for i in range(6)]
    table_far_reading_near_bbox = make_element(
        ElementType.TABLE, content="t1", element_id="#/tables/1", bbox=BBox(0, 0, 10, 10)
    )
    doc = ParsedDocument(elements=[caption, table_near_reading_far_bbox, *padding, table_far_reading_near_bbox])

    chunks = Chunker().chunk(doc)

    matched = next(c for c in chunks if c.merged_caption_ids)
    assert matched.source_element_indices[0] == 1  # table_near_reading_far_bbox, index 1 (reading-distance 1)


def test_tier2_first_caption_wins_when_two_captions_compete_for_one_target():
    # Two orphaned captions, only one unclaimed table on the page. The
    # first caption in document order claims it (greedy, not globally
    # optimal — see MEMORY.md); the second is left unmatched rather than
    # stealing the first's match or guessing at something implausible.
    caption_a = make_element(
        ElementType.CAPTION, content="caption A", element_id="#/texts/0", bbox=BBox(0, 0, 10, 10), association_method="none"
    )
    caption_b = make_element(
        ElementType.CAPTION, content="caption B", element_id="#/texts/1", bbox=BBox(0, 0, 10, 10), association_method="none"
    )
    table = make_element(ElementType.TABLE, content="the only table", element_id="#/tables/0", bbox=BBox(0, 0, 10, 10))
    doc = ParsedDocument(elements=[caption_a, caption_b, table])

    chunks = Chunker().chunk(doc)

    table_chunk = next(c for c in chunks if c.element_type == ElementType.TABLE)
    assert table_chunk.merged_caption_ids == ["#/texts/0"]  # caption_a won
    standalone_captions = [c for c in chunks if c.element_type == ElementType.CAPTION]
    assert len(standalone_captions) == 1
    assert standalone_captions[0].association_method == "unmatched"
    assert standalone_captions[0].content == "caption B"


def test_tier2_fully_equal_tie_falls_back_to_document_order():
    # Two candidates at identical reading-order distance AND identical
    # bbox distance from the caption — an unresolvable tie by the
    # documented sort keys. Falls back to whichever appears first in
    # `elements` order (Python's min() stability). Not a load-bearing
    # guarantee, just documented, deterministic behavior.
    same_bbox = BBox(0, 0, 10, 10)
    table_first = make_element(ElementType.TABLE, content="first", element_id="#/tables/0", bbox=same_bbox)
    caption = make_element(
        ElementType.CAPTION, content="a caption", element_id="#/texts/0", bbox=same_bbox, association_method="none"
    )
    table_second = make_element(ElementType.TABLE, content="second", element_id="#/tables/1", bbox=same_bbox)
    doc = ParsedDocument(elements=[table_first, caption, table_second])

    chunks = Chunker().chunk(doc)

    matched = next(c for c in chunks if c.merged_caption_ids)
    assert matched.source_element_indices[0] == 0  # table_first


# --- Oversized-element splitting (MAX_CHUNK_TOKENS) -------------------------


def test_oversized_table_splits_by_row_never_mid_row():

    rows = "\n".join(f"| row{i} | val{i} |" for i in range(2000))
    table_md = f"| A | B |\n|---|---|\n{rows}"
    assert _approx_token_count(table_md) > MAX_CHUNK_TOKENS  # sanity check the fixture is actually oversized

    table = make_element(ElementType.TABLE, content=table_md, element_id="#/tables/0")
    doc = ParsedDocument(elements=[table])

    chunks = Chunker().chunk(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert _approx_token_count(chunk.content) <= MAX_CHUNK_TOKENS
        assert chunk.content.startswith("| A | B |")  # header repeated on every part
        assert chunk.split_from_element_id == "#/tables/0"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))  # distinct, sequential
    # No row lost or duplicated across the split.
    assert sum(chunk.content.count("| row") for chunk in chunks) == 2000
    # Never split mid-row: every non-header, non-separator line in every
    # chunk is a complete "| rowN | valN |" row.
    for chunk in chunks:
        body_lines = chunk.content.split("\n")[2:]
        for line in body_lines:
            assert line.count("|") == 3  # a whole row, not a fragment


def test_oversized_table_split_parts_inherit_association_and_captions():
    rows = "\n".join(f"| row{i} | val{i} |" for i in range(2000))
    table_md = f"| A | B |\n|---|---|\n{rows}"

    table = make_element(
        ElementType.TABLE,
        content=table_md,
        element_id="#/tables/0",
        associated_caption_ids=["#/texts/0"],
    )
    caption = make_element(
        ElementType.CAPTION,
        content="Table 1: an oversized table",
        element_id="#/texts/0",
        association_method="explicit",
    )
    doc = ParsedDocument(elements=[caption, table])

    chunks = Chunker().chunk(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.association_method == "explicit"
        assert chunk.merged_caption_ids == ["#/texts/0"]
        assert chunk.split_from_element_id == "#/tables/0"
        assert chunk.source_element_indices == chunks[0].source_element_indices  # same parent element(s)


def test_oversized_text_splits_by_sentence_never_mid_sentence():

    sentence = "This is a test sentence with some words in it. "
    text = sentence * 1000
    assert _approx_token_count(text) > MAX_CHUNK_TOKENS

    element = make_element(ElementType.TEXT, content=text, element_id="#/texts/0")
    doc = ParsedDocument(elements=[element])

    chunks = Chunker().chunk(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert _approx_token_count(chunk.content) <= MAX_CHUNK_TOKENS
        assert chunk.content.rstrip().endswith(".")  # never cut mid-sentence
        assert chunk.split_from_element_id == "#/texts/0"
    assert sum(chunk.content.count("This is a test sentence") for chunk in chunks) == 1000


def test_elements_under_ceiling_are_never_split():
    # Sanity guard: splitting must only trigger for genuinely oversized
    # content, not as a side effect of normal-sized chunks.
    element = make_element(ElementType.TEXT, content="a short paragraph.", element_id="#/texts/0")
    doc = ParsedDocument(elements=[element])

    chunks = Chunker().chunk(doc)

    assert len(chunks) == 1
    assert chunks[0].split_from_element_id is None


# --- Reparse stability -------------------------------------------------------


def test_chunking_the_same_parsed_document_twice_produces_identical_output(table_heavy_doc):
    chunks_a = Chunker().chunk(table_heavy_doc)
    chunks_b = Chunker().chunk(table_heavy_doc)

    assert len(chunks_a) == len(chunks_b)
    for a, b in zip(chunks_a, chunks_b):
        assert a.chunk_index == b.chunk_index
        assert a.element_type == b.element_type
        assert a.page_numbers == b.page_numbers
        assert a.source_element_indices == b.source_element_indices
        assert a.content == b.content
        assert a.association_method == b.association_method
        assert a.merged_caption_ids == b.merged_caption_ids


def test_reparsing_the_same_file_bytes_produces_identical_chunk_order():
    # Stronger version: parse the same file from scratch twice (not just
    # re-chunking the same in-memory ParsedDocument), confirming stability
    # holds through Docling's own parse, not just the chunker.
    pdf_bytes = load("clean_digital.pdf")
    doc_a = Parser().parse(pdf_bytes)
    doc_b = Parser().parse(pdf_bytes)

    chunks_a = Chunker().chunk(doc_a)
    chunks_b = Chunker().chunk(doc_b)

    assert [c.chunk_index for c in chunks_a] == [c.chunk_index for c in chunks_b]
    assert [c.content for c in chunks_a] == [c.content for c in chunks_b]
    assert [c.element_type for c in chunks_a] == [c.element_type for c in chunks_b]


# ── Section headings (2026-10-06) ─────────────────────────────────────────────
# Chunks used to carry no section context: a chunk starting mid-section had no
# heading, a heading could be stranded as the last line of the previous chunk,
# and a grouped chunk took the type of its first element (so most text chunks
# were labelled "heading").


def _h(text, page=1, eid=None):
    return make_element(ElementType.HEADING, page_number=page, content=text, element_id=eid or f"h-{text}")


def _t(text, page=1, eid=None):
    return make_element(ElementType.TEXT, page_number=page, content=text, element_id=eid or f"t-{text[:12]}")


def _chunk(elements):
    return Chunker().chunk(ParsedDocument(elements=elements, dropped_elements=0))


def test_a_heading_starts_a_new_chunk_instead_of_trailing_the_previous_one():
    chunks = _chunk([_h("Intro"), _t("Intro body."), _h("Results"), _t("Results body.")])

    assert [c.content for c in chunks] == ["Intro\nIntro body.", "Results\nResults body."]


def test_consecutive_headings_stay_together_with_their_body():
    chunks = _chunk([_h("Report title"), _h("Introduction"), _t("Body text.")])

    assert [c.content for c in chunks] == ["Report title\nIntroduction\nBody text."]


def test_continuation_chunk_is_prefixed_with_its_section_heading():
    long_paragraph = "word " * 1200  # ~1500 tokens: more than one TOKEN_BUDGET chunk
    elements = [_h("Quarterly Results"), _t(long_paragraph, eid="p1"), _t("Second paragraph.", eid="p2")]

    chunks = _chunk(elements)

    assert len(chunks) == 2
    assert chunks[0].content.startswith("Quarterly Results\n")
    assert chunks[1].content == "Quarterly Results\nSecond paragraph."
    assert chunks[1].section_heading == "Quarterly Results"


def test_section_heading_carries_across_a_page_break():
    chunks = _chunk([_h("Methods", page=1), _t("Page one text.", page=1), _t("Page two text.", page=2)])

    assert [c.content for c in chunks] == ["Methods\nPage one text.", "Methods\nPage two text."]
    assert chunks[1].page_numbers == [2]


def test_tables_and_uncaptioned_figures_get_their_section_heading():
    figure_image = Image.new("RGB", (4, 4))
    elements = [
        _h("Revenue"),
        make_element(ElementType.TABLE, content="| Q | $ |\n|---|---|\n| Q3 | 4.2 |", element_id="tbl"),
        make_element(ElementType.FIGURE, content=figure_image, element_id="fig"),
    ]

    table, figure = [c for c in _chunk(elements) if c.element_type in (ElementType.TABLE, ElementType.FIGURE)]

    assert table.content.startswith("Revenue\n\n| Q | $ |")
    assert figure.content == "Revenue"


def test_text_before_any_heading_gets_no_prefix():
    chunks = _chunk([_t("Preamble with no heading.")])

    assert chunks[0].content == "Preamble with no heading."
    assert chunks[0].section_heading is None


def test_grouped_chunk_type_reflects_its_body_not_its_first_heading():
    heading_only = _chunk([_h("Lonely heading")])
    with_body = _chunk([_h("Section"), _t("Body.")])
    list_only = _chunk([make_element(ElementType.LIST, content="- a", element_id="l1")])

    assert heading_only[0].element_type == ElementType.HEADING
    assert with_body[0].element_type == ElementType.TEXT
    assert list_only[0].element_type == ElementType.LIST


def test_heading_directly_above_a_table_becomes_its_prefix_not_a_lone_chunk():
    elements = [
        _h("Table"),
        make_element(ElementType.TABLE, content="| A |\n|---|\n| 1 |", element_id="tbl"),
        _t("After the table."),
    ]

    chunks = _chunk(elements)

    assert [c.element_type for c in chunks] == [ElementType.TABLE, ElementType.TEXT]
    assert chunks[0].content == "Table\n\n| A |\n|---|\n| 1 |"
    assert chunks[0].source_element_indices[0] == 1  # the table stays the primary source element
    assert chunks[1].content == "Table\nAfter the table."
