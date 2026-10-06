# Tests for [FEAT-027] Parser rewrite (pdfplumber/python-docx/python-pptx/
# selectolax, replacing Docling) + [FEAT-017] OCR fallback.
#
# Fixtures (apps/api/tests/fixtures/): clean_digital.pdf (plain formatted
# doc, one table), table_heavy.pdf (29 tables across 11 pages), scanned.pdf
# (3-page scan, one real embedded text line on page 1), table.docx,
# slides.pptx, page.html.
#
# The OCR-tier classes (GeminiOcrClient/OcrSpaceClient/TesseractOcrClient)
# and their 3-tier-chain contract are UNCHANGED by the rewrite — those
# tests are carried forward essentially as-is. Everything that depended on
# Docling's own internals (fake ConversionResult/DocItemLabel objects,
# content-sniffing behavior, Docling's specific element counts) is
# rewritten against the new implementation's real, measured behavior — see
# .agent/reviews/2026-08-01-parser-research.md and CHANGELOG.md 2026-08-01
# for the full investigation and acceptance-bar comparison this is based
# on.

import httpx
from google.genai.errors import ClientError
from PIL import Image

from services.parser import BBox, ElementType, GeminiOcrClient, OcrSpaceClient, ParseError, Parser, TesseractOcrClient

FIXTURES = "tests/fixtures"


def load(name: str) -> bytes:
    with open(f"{FIXTURES}/{name}", "rb") as f:
        return f.read()


def _fake_client_error(code: int, message: str) -> ClientError:
    # Matches test_verifier.py's own helper exactly — same real exception
    # type (google.genai.errors.ClientError) FEAT-017's live 429 quota
    # exhaustion (2026-07-26, .agent/MEMORY.md) actually raised, not a
    # generic stand-in.
    response = httpx.Response(code, json={"error": {"message": message, "status": "SIMULATED"}})
    return ClientError(code, response)


class FakeOcrClient:
    """Always returns None (simulates 'OCR unavailable / found nothing')
    — used by every test below that isn't specifically about FEAT-017's
    OCR behavior, so those tests keep proving exactly what they always
    proved (the parser's own direct extraction) without a real,
    non-deterministic Gemini call on every run. Also counts calls (and
    confirms a real image was passed each time), for the cost/scope-guard
    test — a normal digital PDF must trigger zero of them."""

    def __init__(self):
        self.call_count = 0

    def transcribe_page(self, image):
        self.call_count += 1
        assert isinstance(image, Image.Image)
        return None


# --- Contract: ElementType, BBox, ParsedElement, ParsedDocument, ParseError -


# Acceptance criterion: `Parser.parse(pdf_bytes) -> ParsedDocument` returns typed elements: text, heading, table, figure, caption, list
def test_parse_returns_expected_element_types_across_fixtures():
    parser = Parser(ocr_tiers=[("fake", FakeOcrClient())])

    clean = parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")
    clean_types = {e.element_type for e in clean.elements}
    assert clean_types == {ElementType.HEADING, ElementType.TEXT, ElementType.LIST, ElementType.TABLE}

    table_heavy = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")
    table_heavy_types = {e.element_type for e in table_heavy.elements}
    assert {ElementType.TABLE, ElementType.CAPTION, ElementType.HEADING, ElementType.LIST} <= table_heavy_types


# Acceptance criterion: Each element has: page_number, bbox (x0,y0,x1,y1), content, element_type
def test_each_element_has_page_number_bbox_content_and_type():
    parser = Parser()
    doc = parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert isinstance(element.element_type, ElementType)
        assert isinstance(element.page_number, int) and element.page_number >= 1
        assert isinstance(element.bbox, BBox)
        assert all(isinstance(v, float) for v in (element.bbox.x0, element.bbox.y0, element.bbox.x1, element.bbox.y1))
        assert isinstance(element.content, (str, Image.Image))
        if isinstance(element.content, str):
            assert element.content.strip() != ""


def test_each_element_has_valid_bbox_table_heavy():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert isinstance(element.bbox, BBox)
        assert all(isinstance(v, float) for v in (element.bbox.x0, element.bbox.y0, element.bbox.x1, element.bbox.y1))


# --- Table/figure <-> caption association (FEAT-027 Tier 1: text-prefix + bbox proximity) --
#
# Real, live-measured comparison against Docling (temporarily reinstalled
# outside the lockfile purely to re-derive its exact per-table linkage,
# then removed again — .agent/reviews/2026-08-01-parser-research.md):
# Docling explicitly linked exactly 13 of 29 tables (Table 1, 2, 3, 10, 11,
# 12, 13, 16, 17, 21, 22, 27, 28). This parser's own Tier 1 heuristic
# (caption text-prefix + bbox proximity to the nearest table, same greedy
# reading-order-then-distance algorithm chunker.py's own Tier 2 already
# used) links all 29 — spot-checked individually below, not just counted.
def test_table_caption_association_covers_all_29_tables_beating_docling_13():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")

    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    captions = [e for e in doc.elements if e.element_type == ElementType.CAPTION]
    assert len(tables) == 29
    assert len(captions) == 29

    # Every caption must be labeled one way or the other — never left unset.
    assert all(c.association_method in ("explicit", "none") for c in captions)
    linked_tables = [t for t in tables if t.associated_caption_ids]
    assert len(linked_tables) == 29

    caption_ids_by_ref = {c.element_id: c for c in captions}
    for table in linked_tables:
        for caption_id in table.associated_caption_ids:
            assert caption_id in caption_ids_by_ref
            assert caption_ids_by_ref[caption_id].association_method == "explicit"

    # Non-table/figure elements never get associated_caption_ids populated.
    for element in doc.elements:
        if element.element_type not in (ElementType.TABLE, ElementType.FIGURE):
            assert element.associated_caption_ids == []

    # association_method only applies to captions.
    for element in doc.elements:
        if element.element_type != ElementType.CAPTION:
            assert element.association_method is None


def test_table_caption_spot_check_five_real_instances_beyond_docling():
    """Task item 6's own explicit requirement: at least 5 spot-checked,
    per-instance-verified captions that Docling's real 13-table baseline
    did NOT link, confirmed correct by real content (not just presence of
    a link). See test_parser_rewrite.py's mirrored, more detailed version
    of this same check for the full per-instance write-up.

    Locating each table by caption PREFIX is fine (caption numbers are
    unique, so a prefix match is unambiguous) — the bug the 2026-08-02
    audit found was that no test ever checked a caption's OWN full text
    for completeness, only substrings of the TABLE's content. Every
    caption below is now also asserted for exact full-string equality,
    not just prefix/substring containment, so a truncated caption (this
    fixture's real captions can wrap onto a 2nd or 3rd visual line) can't
    silently pass a prefix-only check again."""
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")
    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    captions = {c.element_id: c for c in doc.elements if c.element_type == ElementType.CAPTION}

    def find_by_caption_prefix(prefix: str):
        for t in tables:
            for cid in t.associated_caption_ids:
                if captions[cid].content.startswith(prefix):
                    return t, captions[cid].content
        return None, None

    docling_linked = {"Table 1", "Table 2", "Table 3", "Table 10", "Table 11", "Table 12", "Table 13", "Table 16", "Table 17", "Table 21", "Table 22", "Table 27", "Table 28"}

    t4, cap4 = find_by_caption_prefix("Table 4:")
    assert t4 is not None and "Table 4" not in docling_linked
    assert cap4 == "Table 4: table 3 with column headers added"
    assert "Role" in t4.content and "Daniel Radcliffe" in t4.content

    t7, cap7 = find_by_caption_prefix("Table 7:")
    assert t7 is not None and "Table 7" not in docling_linked
    assert cap7 == "Table 7: year-end statement, non-current assets (£, thousands)"
    assert "Non-current assets" in t7.content and "Property" in t7.content

    t14, cap14 = find_by_caption_prefix("Table 14:")
    assert t14 is not None and "Table 14" not in docling_linked
    assert cap14 == "Table 14: symbols replaced by real text"
    assert "Question" in t14.content and "Respondent" in t14.content

    t19, cap19 = find_by_caption_prefix("Table 19:")
    assert t19 is not None and "Table 19" not in docling_linked
    # Real 3-line-wrapped caption (2026-08-02 audit finding, fixed
    # 2026-08-02): would have silently truncated to "Table 19: Human
    # Development Index (HDI)" under the pre-fix parser.
    assert cap19 == "Table 19: Human Development Index (HDI) trends, 1980 to 2010. Source: Barro-Lee March, 2010"
    assert "Afghanistan" in t19.content

    t23, cap23 = find_by_caption_prefix("Table 23:")
    assert t23 is not None and "Table 23" not in docling_linked
    # Real 2-line-wrapped caption — pre-fix, this truncated to "Table 23:
    # simulated table created using tabs and containing no", silently
    # dropping the word "structure" that completes the sentence.
    assert cap23 == "Table 23: simulated table created using tabs and containing no structure"
    assert "Bob" in t23.content and "Sue" in t23.content and "Entered" in t23.content and "Completed" in t23.content


# Regression test for the 2026-08-02 independent-audit finding: captions
# spanning more than one visual line in the source PDF were silently cut
# to their first line (the caption -> table LINK was always correct; only
# the caption element's own text content was incomplete). Not caught by
# the spot-check test above in its original form because it only ever
# asserted `startswith()`/substring containment on caption text, which
# passes regardless of trailing truncation — every caption below is
# checked for exact full-string equality instead.
def test_multiline_captions_are_not_truncated_to_first_line():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")
    captions = {c.content.split(":")[0]: c.content for c in doc.elements if c.element_type == ElementType.CAPTION}

    assert captions["Table 10"] == "Table 10: self-contained year-end statement (£, thousands) (multiple layout problems)"
    assert captions["Table 11"] == "Table 11: self-contained year-end statement (£, thousands) (multiple problems resolved)"
    assert captions["Table 15"] == (
        "Table 15: courses offered by Institution X. A = Bachelor of Science, "
        "B = Bachelor of Arts, C = Masters, D = Doctorate, E = Diploma"
    )
    assert captions["Table 23"] == "Table 23: simulated table created using tabs and containing no structure"
    assert captions["Table 26"] == (
        "Table 26: courses offered by Institution X. A = Bachelor of Science, "
        "B = Bachelor of Arts, C = Masters, D = Doctorate, E = Diploma"
    )
    assert captions["Table 28"] == "Table 28: year-end financial table (£, thousands) – headings problem revisited"


# Acceptance criterion: Tables are extracted as markdown-formatted content
def test_tables_are_extracted_as_markdown_formatted_content():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")

    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    assert len(tables) == 29  # table_heavy.pdf's real table count — matches Docling's own count exactly

    for table in tables:
        assert isinstance(table.content, str)
        assert "|" in table.content  # markdown pipe-table syntax


# Real test — the specific "hardest case" the research report flagged:
# Table 23's caption literally describes it as "containing no structure"
# (describing the SOURCE document's tab-stop layout, not this parser's
# extraction) — despite that, real row/column structure is correctly
# reconstructed, matching Docling's own correct reconstruction exactly.
def test_table_23_extracted_content_matches_doclings_correct_reconstruction():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")
    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]

    table_23 = next((t for t in tables if "Bob" in t.content and "Entered" in t.content), None)
    assert table_23 is not None, "Table 23's real content (Bob/Sue rows) was not found"

    for expected in ("Bob", "Sue", "22", "21", "20", "19", "44", "12", "10"):
        assert expected in table_23.content
    assert "|" in table_23.content


# A real, live false positive found during implementation: pdfplumber's
# (and PyMuPDF's, per the research comparison) geometric table detector
# misreads a styled 1-row block-quote as a "table." Filtered by requiring
# >= 2 rows — confirmed here it doesn't cost the real 1-table count.
def test_single_row_false_positive_table_is_filtered_clean_digital():
    parser = Parser()
    doc = parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")
    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    assert len(tables) == 1  # matches Docling's own count — the blockquote false positive never surfaces


# Acceptance criterion: Figures are returned as PIL Image objects for downstream storage
def test_figures_are_returned_as_pil_image_objects():
    parser = Parser()
    doc = parser.parse(load("table.docx"), filename="table.docx")

    figures = [e for e in doc.elements if e.element_type == ElementType.FIGURE]
    assert len(figures) == 1
    assert isinstance(figures[0].content, Image.Image)
    figures[0].content.close()


# Real, deliberate finding from implementation (not a regression): a
# full-page scanned image (essentially the entire page, confirmed live via
# pdfplumber's own page.images reporting ~100% page-area coverage on every
# page of scanned.pdf) is filtered out as "this page IS a scan," not a
# meaningful embedded figure — extracting it as a FIGURE chunk would be
# redundant with what OCR fallback already recovers as real text for that
# same page. Docling's own model found one small figure here (a logo); the
# rewrite trades that one small win for a real, principled filter that
# also avoids emitting 3 giant page-sized "figures" that would need to be
# uploaded/stored for no retrieval benefit.
def test_scanned_pdf_full_page_images_are_filtered_not_treated_as_figures():
    parser = Parser(ocr_tiers=[("fake", FakeOcrClient())])
    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    figures = [e for e in doc.elements if e.element_type == ElementType.FIGURE]
    assert figures == []


# --- ParseError ---------------------------------------------------------


def test_parse_error_carries_page_number():
    error = ParseError("boom", page_number=5)

    assert error.page_number == 5
    assert str(error) == "boom"


def test_parse_error_page_number_defaults_to_none():
    error = ParseError("boom")
    assert error.page_number is None


def test_invalid_pdf_bytes_raises_parse_error():
    parser = Parser()

    try:
        parser.parse(b"this is not a pdf", filename="document.pdf")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_empty_pdf_bytes_raises_parse_error():
    parser = Parser()

    try:
        parser.parse(b"", filename="document.pdf")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_truncated_pdf_raises_parse_error():
    full = load("clean_digital.pdf")
    truncated = full[: len(full) // 2]
    parser = Parser()

    try:
        parser.parse(truncated, filename="document.pdf")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_invalid_docx_bytes_raises_parse_error():
    parser = Parser()
    try:
        parser.parse(b"this is not a docx", filename="document.docx")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_invalid_pptx_bytes_raises_parse_error():
    parser = Parser()
    try:
        parser.parse(b"this is not a pptx", filename="document.pptx")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_unsupported_extension_raises_parse_error():
    parser = Parser()
    try:
        parser.parse(b"whatever content", filename="document.xyz")
        assert False, "expected ParseError"
    except ParseError:
        pass


# --- Well-formed-fixture regression guard, real measured counts --------
#
# Pin exact counts so any future change to these fixtures' extraction is
# caught immediately. Compared directly against the Docling baseline in
# .agent/reviews/2026-08-01-parser-research.md and CHANGELOG.md
# 2026-08-01 — real, honestly-reported differences are explained inline
# where they occur, not silently forced to match.
def test_element_counts_pinned_clean_digital():
    parser = Parser(ocr_tiers=[("fake", FakeOcrClient())])
    doc = parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")

    assert len(doc.elements) == 22  # Docling: 21 — within 1, see per-type counts below
    assert doc.dropped_elements == 0
    type_counts = {}
    for e in doc.elements:
        type_counts[e.element_type.value] = type_counts.get(e.element_type.value, 0) + 1
    # heading and list match Docling's own count EXACTLY (6 and 9 — the
    # fixture item 7's own explicit "confirm 9/9" requirement). table
    # matches exactly (1). text is 6 vs Docling's 5 — a real, minor
    # paragraph-grouping granularity difference (this parser's line-gap
    # merge heuristic doesn't perfectly replicate Docling's own ML-based
    # paragraph boundaries), not a content loss.
    assert type_counts == {"table": 1, "heading": 6, "text": 6, "list": 9}


def test_element_counts_pinned_table_heavy():
    parser = Parser(ocr_tiers=[("fake", FakeOcrClient())])
    doc = parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")

    assert doc.dropped_elements == 0
    type_counts = {}
    for e in doc.elements:
        type_counts[e.element_type.value] = type_counts.get(e.element_type.value, 0) + 1
    # table matches Docling exactly (29). list matches Docling exactly
    # (6 — the parenthesized-footnote-marker pattern, "(1) Provisional
    # total..."). caption is 29 vs Docling's 19 — the real, deliberate
    # improvement this rewrite's Tier 1 heuristic achieves (see the
    # spot-check test above). heading is 3 (2026-08-02 audit fix: the
    # multi-line-caption-merge bug meant a caption's own second line —
    # e.g. "layout problems)" continuing "Table 10: ... (multiple" — was
    # misread as its own short-bold HEADING element; this count was
    # previously pinned to >= 8, which was measuring that bug's own
    # byproduct, not real headings. All 7 of the "missing" headings were
    # exactly those orphaned caption fragments; verified none were a real
    # document heading before lowering this bound). This fixture has 0
    # standalone `text` elements vs Docling's 4 — nearly all of this
    # heavily-tabular document's real content is inside a table, a
    # caption, a heading, or a footnote-list item; what little remained
    # "loose" text in Docling's own extraction falls inside this parser's
    # (deliberately generous, to avoid re-duplicating table content) table
    # bbox exclusion zone instead.
    assert type_counts["table"] == 29
    assert type_counts["caption"] == 29
    assert type_counts["list"] == 6
    assert type_counts.get("heading", 0) == 3


def test_element_counts_pinned_scanned_no_ocr_recovery():
    # FakeOcrClient (returns None): pins this parser's OWN direct
    # extraction — real OCR recovery is tested separately below.
    parser = Parser(ocr_tiers=[("fake", FakeOcrClient())])
    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert doc.dropped_elements == 0
    # Docling found 2 (1 heading + 1 tiny figure). This parser finds 1: the
    # same real embedded title text, but classified TEXT not HEADING (a
    # real, explained edge case — see below) — no other real text exists
    # on this page to establish a body-size baseline against, so nothing
    # can register as "larger than body," and whole-line-bold-short
    # doesn't fire either since the title isn't bold in this fixture. The
    # figure (Docling's "tiny logo") is deliberately not extracted here —
    # see test_scanned_pdf_full_page_images_are_filtered_not_treated_as_figures.
    # Functionally near-equivalent: chunker.py groups TEXT and HEADING
    # identically (both in _GROUPABLE_TYPES), so this reclassification has
    # no retrieval-quality impact.
    assert len(doc.elements) == 1
    assert doc.elements[0].element_type == ElementType.TEXT
    assert doc.elements[0].page_number == 1


# --- Format dispatch — real behavior change from Docling, reported honestly -
#
# Docling content-sniffed real magic bytes for PDF/DOCX/PPTX (robust to a
# wrong extension) but genuinely needed the right extension for HTML. This
# rewrite dispatches PURELY by filename extension for every format, with
# no content sniffing at all — routes/ingest.py always passes the real
# uploaded filename already (storage_path's own trailing segment), so
# production ingestion is unaffected; a caller of Parser() directly that
# gets the extension wrong now fails for EVERY format, not just HTML.


def test_extension_determines_format_pdf_bytes_with_wrong_extension_fails():
    pdf_bytes = load("clean_digital.pdf")
    parser = Parser()
    try:
        parser.parse(pdf_bytes, filename="wrong.docx")
        assert False, "expected ParseError — no content sniffing in this implementation"
    except ParseError:
        pass


def test_extension_determines_format_correct_extension_succeeds():
    parser = Parser()
    result = parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")
    assert len(result.elements) > 0


# Cost/scope guard: existing PDF fixtures produce sensible, non-degenerate
# results — no cross-format leakage from adding DOCX/PPTX/HTML support.
def test_pdf_fixtures_are_unaffected_by_docx_pptx_html_support():
    clean = Parser().parse(load("clean_digital.pdf"), filename="clean_digital.pdf")
    assert clean.dropped_elements == 0
    assert all(isinstance(e.page_number, int) and e.page_number >= 1 for e in clean.elements)


# --- FEAT-017: OCR fallback, re-derived against this parser's real behavior -
#
# Task item 8's explicit requirement: verified directly against pdfplumber's
# own real silent-failure shape, not assumed to transfer from Docling.
# Real finding: pdfplumber never raises and, like Docling, simply produces
# zero elements for a page with no extractable text — the SAME trigger
# shape (zero TEXT/HEADING/TABLE/LIST elements for a page_number) still
# applies. The concrete mechanical difference: page-image rendering for
# the OCR call itself now goes through this parser's own
# `page.to_image(resolution=150)` (pdfplumber) instead of Docling's
# `generate_page_images=True` pipeline option — confirmed live below to
# produce a real, usable image FakeOcrClient/GeminiOcrClient can both
# consume identically.


def test_ocr_fallback_recovers_real_text_on_scanned_pdf(capsys, caplog):
    parser = Parser()  # real converter-equivalent (pdfplumber) and the real 3-tier chain — no fakes

    with caplog.at_level("INFO"):
        doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    by_page: dict[int, list] = {}
    for element in doc.elements:
        by_page.setdefault(element.page_number, []).append(element)

    tier_log_lines = [r.message for r in caplog.records if "via tier=" in r.message]

    with capsys.disabled():
        print("\n" + "=" * 90)
        print("FEAT-017 real OCR fallback — actual recovered content, scanned.pdf (new parser)")
        print("=" * 90)
        print("\nWhich tier recovered each page (real, not assumed):")
        for line in tier_log_lines:
            print(f"  {line}")
        for page_number in sorted(by_page):
            print(f"\n--- page {page_number} ---")
            for element in by_page[page_number]:
                if isinstance(element.content, str):
                    print(f"  [{element.element_type.value}] {element.content[:300]!r}")
                else:
                    print(f"  [{element.element_type.value}] <image {element.content.size}>")

    # Page 1 has a tiny real text layer (the 13-character title) over a
    # full-page scan. Since 2026-10-06 that still counts as low-yield, so
    # the scanned body is OCR'd too and replaces the thin text layer.
    page_1_ocr = [e for e in by_page.get(1, []) if e.element_id == "ocr-page-1"]
    assert len(page_1_ocr) == 1
    assert len(page_1_ocr[0].content.strip()) > 50

    # Pages 2 and 3 (zero elements from direct extraction — the actual bug
    # FEAT-017 fixes) must carry real, non-trivial recovered text.
    page_2_ocr = [e for e in by_page.get(2, []) if e.element_id == "ocr-page-2"]
    page_3_ocr = [e for e in by_page.get(3, []) if e.element_id == "ocr-page-3"]
    assert len(page_2_ocr) == 1
    assert len(page_3_ocr) == 1
    assert page_2_ocr[0].element_type == ElementType.TEXT
    assert page_3_ocr[0].element_type == ElementType.TEXT
    assert len(page_2_ocr[0].content.strip()) > 50
    assert len(page_3_ocr[0].content.strip()) > 50

    for element in (page_2_ocr[0], page_3_ocr[0]):
        assert isinstance(element.content, str)
        assert isinstance(element.bbox, BBox)
        assert element.associated_caption_ids == []
        assert element.association_method is None


def test_ocr_fallback_never_fires_on_high_yield_fixtures():
    tier1, tier2, tier3 = FakeOcrClient(), FakeOcrClient(), FakeOcrClient()
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    parser.parse(load("clean_digital.pdf"), filename="clean_digital.pdf")
    assert (tier1.call_count, tier2.call_count, tier3.call_count) == (0, 0, 0)

    parser.parse(load("table_heavy.pdf"), filename="table_heavy.pdf")
    assert (tier1.call_count, tier2.call_count, tier3.call_count) == (0, 0, 0)


def test_ocr_fallback_call_failure_degrades_gracefully_not_a_crash(caplog):
    class RaisingOcrClient:
        def __init__(self):
            self.calls = 0

        def transcribe_page(self, image):
            self.calls += 1
            raise _fake_client_error(429, "quota exceeded — simulated")

    ocr_client = RaisingOcrClient()
    parser = Parser(ocr_tiers=[("gemini", ocr_client)])

    with caplog.at_level("WARNING"):
        doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert len(doc.elements) == 1  # page 1's pre-existing element only
    assert {e.page_number for e in doc.elements} == {1}
    assert not any(e.element_id.startswith("ocr-page-") for e in doc.elements)
    assert ocr_client.calls == 3  # all three low-yield pages independently attempted (page 1 is a partly scanned page)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("OCR" in w and ("raised" in w.lower() or "fail" in w.lower()) for w in warnings)
    assert sum(1 for w in warnings if "page" in w.lower()) >= 3


# --- 3-tier chain: full deterministic combination matrix (unchanged contract) -


class TierFake:
    def __init__(self, mode: str, text: str = "recovered text"):
        assert mode in ("succeed", "raise", "none")
        self.mode = mode
        self.text = text
        self.calls = 0

    def transcribe_page(self, image):
        self.calls += 1
        if self.mode == "succeed":
            return self.text
        if self.mode == "raise":
            raise RuntimeError("simulated tier failure")
        return None


def _recovered_elements(doc):
    return [e for e in doc.elements if e.element_id.startswith("ocr-page-")]


def test_ocr_chain_tier1_succeeds_tiers_2_and_3_never_called():
    tier1 = TierFake("succeed", text="gemini recovered this")
    tier2 = TierFake("succeed")
    tier3 = TierFake("succeed")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3  # pages 1-3 (page 1 is partly scanned)
    assert tier2.calls == 0
    assert tier3.calls == 0
    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "gemini recovered this" for e in recovered)


def test_ocr_chain_tier1_fails_tier2_succeeds_tier3_never_called():
    tier1 = TierFake("raise")
    tier2 = TierFake("succeed", text="ocrspace recovered this")
    tier3 = TierFake("succeed")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3
    assert tier2.calls == 3
    assert tier3.calls == 0
    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "ocrspace recovered this" for e in recovered)


def test_ocr_chain_tiers_1_and_2_fail_tier3_succeeds():
    tier1 = TierFake("none")
    tier2 = TierFake("raise")
    tier3 = TierFake("succeed", text="tesseract recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3
    assert tier2.calls == 3
    assert tier3.calls == 3
    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "tesseract recovered this" for e in recovered)


def test_ocr_chain_all_three_tiers_fail_page_stays_unrecovered(caplog):
    tier1 = TierFake("raise")
    tier2 = TierFake("none")
    tier3 = TierFake("raise")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    with caplog.at_level("WARNING"):
        doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3
    assert tier2.calls == 3
    assert tier3.calls == 3

    assert len(doc.elements) == 1
    assert {e.page_number for e in doc.elements} == {1}
    assert _recovered_elements(doc) == []

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert sum(1 for w in warnings if "exhausted all tiers" in w) == 3


# --- Real 3-way tier comparison (task brief item 7, FEAT-017 original) ------


def test_real_ocrspace_recovery_and_three_way_quality_comparison(capsys):
    class AlwaysFailsGemini:
        def transcribe_page(self, image):
            raise RuntimeError("forcing tier 1 to fail for this test")

    parser = Parser(ocr_tiers=[("gemini", AlwaysFailsGemini()), ("ocrspace", OcrSpaceClient()), ("tesseract", TesseractOcrClient())])
    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    page_2_ocr = [e for e in doc.elements if e.element_id == "ocr-page-2"]
    assert len(page_2_ocr) == 1
    ocrspace_text = page_2_ocr[0].content
    assert len(ocrspace_text.strip()) > 50

    # Real Tesseract, real Gemini (if quota allows), against the identical
    # page image — rendered via this parser's own pdfplumber-based helper
    # now, not Docling's page.image.pil_image.
    from services.parser import _render_pdf_page_image

    page_2_image = _render_pdf_page_image(load("scanned.pdf"), 2)
    assert page_2_image is not None

    tesseract_text = TesseractOcrClient().transcribe_page(page_2_image)
    gemini_text = GeminiOcrClient().transcribe_page(page_2_image)

    with capsys.disabled():
        print("\n" + "=" * 90)
        print("REAL 3-WAY OCR QUALITY COMPARISON — scanned.pdf, page 2 (new parser's page render)")
        print("=" * 90)
        print("\n[OCR.space, tier 2, real recovery via the chain]:")
        print(repr(ocrspace_text[:400]))
        print("\n[Tesseract, tier 3, real local binary, same page image]:")
        print(repr(tesseract_text[:400]) if tesseract_text else "None (tesseract not available in this environment)")
        print("\n[Gemini, tier 1, same page image — quota permitting]:")
        print(repr(gemini_text[:400]) if gemini_text else "None (real API call failed — see .agent/MEMORY.md's 2026-07-26 quota entry)")


# --- Audit-style follow-ups (unchanged contract, carried forward) ----------


def _ocrspace_response(status_code: int, json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.ocr.space/parse/image")
    return httpx.Response(status_code, json=json_body, request=request)


class _MockOcrSpaceHttp:
    def __init__(self, response: httpx.Response):
        self._response = response
        self.calls = 0

    def post(self, url, *, headers, data):
        self.calls += 1
        return self._response


def test_ocrspace_client_treats_is_errored_on_processing_as_failure():
    mock_http = _MockOcrSpaceHttp(
        _ocrspace_response(200, {"IsErroredOnProcessing": True, "ErrorMessage": ["simulated processing error"]})
    )
    client = OcrSpaceClient(api_key="test-key", http_client=mock_http)

    result = client.transcribe_page(Image.new("RGB", (10, 10)))

    assert result is None
    assert mock_http.calls == 1


def test_ocrspace_is_errored_on_processing_makes_the_chain_fall_through_to_tier3():
    mock_http = _MockOcrSpaceHttp(
        _ocrspace_response(200, {"IsErroredOnProcessing": True, "ErrorMessage": ["simulated processing error"]})
    )
    real_ocrspace = OcrSpaceClient(api_key="test-key", http_client=mock_http)
    tier1 = TierFake("raise")
    tier3 = TierFake("succeed", text="tesseract recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", real_ocrspace), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3
    assert mock_http.calls == 3
    assert tier3.calls == 3
    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "tesseract recovered this" for e in recovered)


class _RawTierFake:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe_page(self, image):
        self.calls += 1
        return self.text


def test_chain_treats_whitespace_only_success_as_failure_not_valid_recovery():
    tier1 = _RawTierFake("   \n\t  ")
    tier2 = TierFake("succeed", text="tier2 recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", TierFake("succeed"))])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    assert tier1.calls == 3
    assert tier2.calls == 3
    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "tier2 recovered this" for e in recovered)


def test_chain_treats_empty_string_success_as_failure_not_valid_recovery():
    tier1 = _RawTierFake("")
    tier2 = TierFake("succeed", text="tier2 recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", TierFake("succeed"))])

    doc = parser.parse(load("scanned.pdf"), filename="scanned.pdf")

    recovered = _recovered_elements(doc)
    assert len(recovered) == 3
    assert all(e.content == "tier2 recovered this" for e in recovered)


def test_gemini_client_normalizes_empty_response_text_to_none():
    class EmptyResponse:
        text = "   "

    class EmptyModels:
        def generate_content(self, *, model, contents):
            return EmptyResponse()

    class EmptyGeminiClient:
        models = EmptyModels()

    client = GeminiOcrClient(client=EmptyGeminiClient())
    assert client.transcribe_page(Image.new("RGB", (10, 10))) is None


def test_ocrspace_client_normalizes_empty_parsed_text_to_none():
    mock_http = _MockOcrSpaceHttp(
        _ocrspace_response(
            200,
            {"IsErroredOnProcessing": False, "ParsedResults": [{"ParsedText": "   ", "FileParseExitCode": 1}]},
        )
    )
    client = OcrSpaceClient(api_key="test-key", http_client=mock_http)
    assert client.transcribe_page(Image.new("RGB", (10, 10))) is None


def test_tesseract_client_normalizes_blank_image_to_none():
    blank_image = Image.new("RGB", (200, 200), color="white")
    result = TesseractOcrClient().transcribe_page(blank_image)
    assert result is None


def test_gemini_client_sets_an_explicit_http_timeout_by_default(monkeypatch):
    from services.parser import OCR_TIMEOUT_MS

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-this-test-only")
    client = GeminiOcrClient()
    real_client = client._get_client()
    assert real_client._api_client._http_options["timeout"] == OCR_TIMEOUT_MS


def test_tesseract_client_passes_a_nonzero_timeout_to_pytesseract(monkeypatch):
    calls = []

    def fake_image_to_string(image, timeout=0):
        calls.append(timeout)
        return "some text"

    monkeypatch.setattr("pytesseract.image_to_string", fake_image_to_string)

    TesseractOcrClient().transcribe_page(Image.new("RGB", (10, 10)))

    assert len(calls) == 1
    assert calls[0] > 0


def test_ocrspace_client_still_has_an_explicit_timeout():
    client = OcrSpaceClient(api_key="test-key")
    assert client._http.timeout.connect is not None
    assert client._http.timeout.connect > 0


# --- DOCX/PPTX/HTML ingestion (real native structure, no heuristics needed) -


def test_docx_real_parse_produces_expected_element_types():
    doc = Parser().parse(load("table.docx"), filename="table.docx")

    element_types = {e.element_type for e in doc.elements}
    assert ElementType.HEADING in element_types
    assert ElementType.TEXT in element_types
    assert ElementType.TABLE in element_types
    assert ElementType.FIGURE in element_types
    # Real finding, different from Docling: this parser's caption
    # detection (Word's real "Caption" style OR the text-prefix
    # convention) DOES catch table.docx's two real caption-shaped lines
    # ("Table 1: Quarterly revenue...", "Figure 1: a real embedded PNG
    # chart image.") even though neither is actually styled as "Caption"
    # in this specific fixture (confirmed live, .agent/reviews/2026-08-01-
    # parser-research.md) — Docling's DOCX backend never produced a
    # CAPTION element at all, for any reason.
    assert ElementType.CAPTION in element_types
    assert doc.dropped_elements == 0


def test_docx_elements_get_sentinel_page_number_and_zero_bbox():
    doc = Parser().parse(load("table.docx"), filename="table.docx")

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert element.page_number == 1
        assert element.bbox == BBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0)


def test_docx_figure_extracts_as_a_real_usable_image():
    doc = Parser().parse(load("table.docx"), filename="table.docx")

    figures = [e for e in doc.elements if e.element_type == ElementType.FIGURE]
    assert len(figures) == 1
    assert isinstance(figures[0].content, Image.Image)
    assert figures[0].content.size[0] > 0 and figures[0].content.size[1] > 0
    figures[0].content.close()


def test_docx_word_caption_style_is_recognized_when_present():
    # table.docx doesn't happen to use Word's real built-in "Caption"
    # style (both its captions are plain "Normal" paragraphs, confirmed
    # live) — this constructs a tiny synthetic docx that DOES use it,
    # closing the exact gap the research report flagged as untested by
    # the real fixture. Real, live-confirmed capability: FEAT-020 found
    # Docling could NOT read this style at all.
    import io

    import docx as docx_lib

    d = docx_lib.Document()
    d.add_paragraph("Some body text.")
    p = d.add_paragraph("A synthetic caption using Word's real built-in Caption style")
    p.style = d.styles["Caption"]
    buf = io.BytesIO()
    d.save(buf)

    doc = Parser().parse(buf.getvalue(), filename="synthetic.docx")
    captions = [e for e in doc.elements if e.element_type == ElementType.CAPTION]
    assert len(captions) == 1
    assert "synthetic caption" in captions[0].content


def test_pptx_real_parse_produces_expected_element_types():
    doc = Parser().parse(load("slides.pptx"), filename="slides.pptx")

    element_types = {e.element_type for e in doc.elements}
    assert ElementType.HEADING in element_types  # slide titles
    assert ElementType.TEXT in element_types or ElementType.LIST in element_types
    assert ElementType.FIGURE in element_types  # the chart image on slide 3
    assert doc.dropped_elements == 0


# Regression test for the 2026-08-02 independent-audit finding: a real
# slide-1 subtitle placeholder was misclassified as HEADING because
# `"TITLE" in str(placeholder_format.type)` also matches SUBTITLE
# (python-pptx's str() for that enum member is "SUBTITLE (4)", which
# contains "TITLE" as a substring). Fixed via exact enum-member comparison
# against PP_PLACEHOLDER.TITLE/CENTER_TITLE only.
def test_pptx_subtitle_placeholder_is_not_misclassified_as_heading():
    doc = Parser().parse(load("slides.pptx"), filename="slides.pptx")

    title = next(e for e in doc.elements if e.content == "Docify PPTX Fixture")
    subtitle = next(e for e in doc.elements if e.content == "A real PPTX created for testing Docling ingestion support")

    assert title.element_type == ElementType.HEADING
    assert subtitle.element_type == ElementType.TEXT


def test_pptx_page_number_is_the_real_slide_index_not_a_sentinel():
    doc = Parser().parse(load("slides.pptx"), filename="slides.pptx")

    page_numbers = {e.page_number for e in doc.elements}
    assert page_numbers == {1, 2, 3}


def test_pptx_elements_have_real_nonzero_bbox():
    doc = Parser().parse(load("slides.pptx"), filename="slides.pptx")

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert element.bbox != BBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0)


def test_pptx_figure_extracts_as_a_real_usable_image():
    doc = Parser().parse(load("slides.pptx"), filename="slides.pptx")

    figures = [e for e in doc.elements if e.element_type == ElementType.FIGURE]
    assert len(figures) == 1
    assert isinstance(figures[0].content, Image.Image)
    assert figures[0].content.size[0] > 0 and figures[0].content.size[1] > 0
    figures[0].content.close()


def test_html_real_parse_produces_expected_element_types():
    doc = Parser().parse(load("page.html"), filename="page.html")

    element_types = {e.element_type for e in doc.elements}
    assert ElementType.HEADING in element_types
    assert ElementType.TEXT in element_types
    assert ElementType.TABLE in element_types
    assert doc.dropped_elements == 0


def test_html_elements_get_sentinel_page_number_and_zero_bbox():
    doc = Parser().parse(load("page.html"), filename="page.html")

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert element.page_number == 1
        assert element.bbox == BBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0)


# ── Reading order (2026-10-06): HTML and DOCX elements must come out in
# document order. Before this fix, HTML emitted every heading first (CSS
# selector-group order, not DOM order) and DOCX emitted all paragraphs, then
# all tables, then all images — detaching every section/table/figure from
# its surrounding text before chunking ever saw it.

def _contents(doc) -> list[str]:
    return [e.content if isinstance(e.content, str) else "<figure>" for e in doc.elements]


def _index_of(contents: list[str], needle: str) -> int:
    return next(i for i, c in enumerate(contents) if needle in c)


def test_html_elements_come_out_in_document_order():
    contents = _contents(Parser().parse(load("page.html"), filename="page.html"))

    order = [
        _index_of(contents, "Docify HTML Fixture"),
        _index_of(contents, "This is a real HTML page"),
        _index_of(contents, "Quarterly Results"),
        _index_of(contents, "Table 1 below shows"),
        _index_of(contents, "| Quarter |"),
        _index_of(contents, "Table 1: Quarterly revenue"),
        _index_of(contents, "Conclusion"),
        _index_of(contents, "Revenue grew steadily"),
    ]
    assert order == sorted(order)


def test_html_nested_matching_tags_are_not_emitted_twice():
    html = (
        b"<html><body><ul><li><p>Outer item text</p></li></ul>"
        b"<table><caption>Table 9: nested caption</caption>"
        b"<tr><th>A</th></tr><tr><td>1</td></tr></table></body></html>"
    )
    doc = Parser().parse(html, filename="nested.html")
    contents = _contents(doc)

    assert sum("Outer item text" in c for c in contents) == 1
    assert sum("Table 9: nested caption" in c for c in contents) == 1
    caption_idx = _index_of(contents, "Table 9: nested caption")
    table_idx = _index_of(contents, "| A |")
    assert caption_idx < table_idx
    assert doc.elements[caption_idx].element_type == ElementType.CAPTION


def test_docx_tables_and_figures_stay_in_document_order():
    doc = Parser().parse(load("table.docx"), filename="table.docx")
    contents = _contents(doc)

    order = [
        _index_of(contents, "Quarterly Results"),
        _index_of(contents, "Table 1 below shows"),
        _index_of(contents, "| Quarter |"),
        _index_of(contents, "Revenue Chart"),
        _index_of(contents, "<figure>"),
        _index_of(contents, "Conclusion"),
    ]
    assert order == sorted(order)
    for element in doc.elements:
        if element.element_type == ElementType.FIGURE:
            element.content.close()



# ── OCR for partly scanned pages (2026-10-06) ─────────────────────────────────
# OCR used to run only on pages with ZERO text. scanned.pdf's page 1 has a
# 13-character text layer ("SAMPLE LETTER") over a full-page scan, so its
# scanned body was silently lost.


class _FixedTextOcrClient:
    def __init__(self, text: str):
        self._text = text
        self.pages_seen = 0

    def transcribe_page(self, image):
        self.pages_seen += 1
        return self._text


def _minimal_text_only_pdf(text: str) -> bytes:
    """A one-page PDF with a single short line of real text and no images."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_partly_scanned_page_is_ocrd_and_replaces_its_thin_text_layer():
    ocr = _FixedTextOcrClient("SAMPLE LETTER\nDear customer, thank you for your order of 12 widgets.")
    doc = Parser(ocr_tiers=[("fake", ocr)]).parse(load("scanned.pdf"), filename="scanned.pdf")

    page_1 = [e for e in doc.elements if e.page_number == 1]
    assert [e.element_id for e in page_1] == ["ocr-page-1"]
    assert "thank you for your order" in page_1[0].content
    assert ocr.pages_seen == 3


def test_ocr_elements_stay_in_page_order_with_bbox_in_pdf_points():
    ocr = _FixedTextOcrClient("recovered text")
    doc = Parser(ocr_tiers=[("fake", ocr)]).parse(load("scanned.pdf"), filename="scanned.pdf")

    pages = [e.page_number for e in doc.elements]
    assert pages == sorted(pages)
    page_1 = next(e for e in doc.elements if e.element_id == "ocr-page-1")
    # scanned.pdf pages are US Letter (612 x 792 pt), not 150-dpi pixels.
    assert (round(page_1.bbox.x1), round(page_1.bbox.y1)) == (612, 792)


def test_partly_scanned_page_keeps_its_text_layer_when_every_ocr_tier_fails():
    doc = Parser(ocr_tiers=[("fake", FakeOcrClient())]).parse(load("scanned.pdf"), filename="scanned.pdf")

    page_1 = [e for e in doc.elements if e.page_number == 1]
    assert len(page_1) == 1
    assert page_1[0].content == "SAMPLE LETTER"


def test_short_digital_page_without_a_scan_image_is_not_ocrd():
    ocr = _FixedTextOcrClient("should never be used")
    doc = Parser(ocr_tiers=[("fake", ocr)]).parse(_minimal_text_only_pdf("Short page"), filename="short.pdf")

    assert ocr.pages_seen == 0
    assert [e.content for e in doc.elements] == ["Short page"]
