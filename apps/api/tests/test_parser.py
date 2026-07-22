# Tests for [FEAT-004] Docling parser service
#
# Fixtures (apps/api/tests/fixtures/): clean_digital.pdf (plain formatted
# doc, one table), table_heavy.pdf (29 tables across 11 pages), scanned.pdf
# (3-page scan, no text layer except a real-text title on page 1).
#
# No single fixture exercises all six element types, so coverage is spread
# across fixtures rather than asserted on one. OCR is intentionally off
# (FEAT-017, Phase 4, out of scope) — see the scanned.pdf tests below for
# what that means in practice.
#
# Extended 2026-07-22 per Codex review of parser.py: the try/except around
# converter.convert() didn't cover the element-iteration loop, so a failure
# during iteration/provenance/bbox access could raise unwrapped instead of
# ParseError; a caption with missing provenance was silently dropped with
# no signal at all. See ParsedDocument.dropped_elements and the WARN logs
# added for both silent-drop cases (missing provenance, get_image() -> None).

from docling_core.types.doc import DocItemLabel
from PIL import Image

from services.parser import BBox, ElementType, ParseError, Parser

FIXTURES = "tests/fixtures"


def load(name: str) -> bytes:
    with open(f"{FIXTURES}/{name}", "rb") as f:
        return f.read()


# Acceptance criterion: `Parser.parse(pdf_bytes) -> ParsedDocument` returns typed elements: text, heading, table, figure, caption, list
def test_parse_returns_expected_element_types_across_fixtures():
    parser = Parser()

    clean = parser.parse(load("clean_digital.pdf"))
    clean_types = {e.element_type for e in clean.elements}
    assert clean_types == {ElementType.HEADING, ElementType.TEXT, ElementType.LIST, ElementType.TABLE}

    table_heavy = parser.parse(load("table_heavy.pdf"))
    table_heavy_types = {e.element_type for e in table_heavy.elements}
    assert {ElementType.TABLE, ElementType.CAPTION, ElementType.HEADING} <= table_heavy_types

    scanned = parser.parse(load("scanned.pdf"))
    scanned_types = {e.element_type for e in scanned.elements}
    assert scanned_types == {ElementType.FIGURE, ElementType.HEADING}


# Acceptance criterion: Each element has: page_number, bbox (x0,y0,x1,y1), content, element_type
def test_each_element_has_page_number_bbox_content_and_type():
    parser = Parser()
    doc = parser.parse(load("clean_digital.pdf"))

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert isinstance(element.element_type, ElementType)
        assert isinstance(element.page_number, int) and element.page_number >= 1
        assert isinstance(element.bbox, BBox)
        assert all(isinstance(v, float) for v in (element.bbox.x0, element.bbox.y0, element.bbox.x1, element.bbox.y1))
        assert isinstance(element.content, (str, Image.Image))
        if isinstance(element.content, str):
            assert element.content.strip() != ""


# Codex review (2026-07-23): bbox was only asserted against clean_digital.pdf.
def test_each_element_has_valid_bbox_table_heavy():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"))

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert isinstance(element.bbox, BBox)
        assert all(isinstance(v, float) for v in (element.bbox.x0, element.bbox.y0, element.bbox.x1, element.bbox.y1))


def test_each_element_has_valid_bbox_scanned():
    parser = Parser()
    doc = parser.parse(load("scanned.pdf"))

    assert len(doc.elements) > 0
    for element in doc.elements:
        assert isinstance(element.bbox, BBox)
        assert all(isinstance(v, float) for v in (element.bbox.x0, element.bbox.y0, element.bbox.x1, element.bbox.y1))


# --- Table/figure <-> caption association (Codex review, Tier 1 only) -------
#
# Docling exposes an explicit captions link on TableItem/PictureItem
# (verified empirically against the installed version — see MEMORY.md /
# CHANGELOG for how this was checked). This is "Tier 1": use what Docling
# already knows. Associating a caption with a nearby table/figure by
# position when Docling didn't link one (Tier 2) is deliberately NOT
# implemented here — that heuristic is FEAT-005's job. A caption with no
# explicit link gets association_method="none", not a guess.
def test_table_caption_association_uses_docling_explicit_links():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"))

    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    captions = [e for e in doc.elements if e.element_type == ElementType.CAPTION]
    assert len(tables) == 29
    assert len(captions) == 19

    # Every caption must be labeled one way or the other — never left unset.
    assert all(c.association_method in ("explicit", "none") for c in captions)

    explicit = [c for c in captions if c.association_method == "explicit"]
    none_ = [c for c in captions if c.association_method == "none"]
    # Observed directly against this fixture: 13 of 19 captions are
    # explicitly linked by Docling, 6 are not (multi-table groups sharing
    # one caption, or captions Docling didn't associate for other reasons).
    assert len(explicit) == 13
    assert len(none_) == 6

    # associated_caption_ids only appears on table/figure elements, and
    # every id it lists must point at a caption element that actually
    # exists and is marked "explicit".
    caption_ids_by_ref = {c.element_id: c for c in captions}
    tables_with_captions = [t for t in tables if t.associated_caption_ids]
    assert len(tables_with_captions) == 13
    for table in tables_with_captions:
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


# Acceptance criterion: Tables are extracted as markdown-formatted content
def test_tables_are_extracted_as_markdown_formatted_content():
    parser = Parser()
    doc = parser.parse(load("table_heavy.pdf"))

    tables = [e for e in doc.elements if e.element_type == ElementType.TABLE]
    assert len(tables) == 29  # table_heavy.pdf's actual table count, observed directly

    for table in tables:
        assert isinstance(table.content, str)
        assert "|" in table.content  # markdown pipe-table syntax


# Acceptance criterion: Figures are returned as PIL Image objects for downstream storage
def test_figures_are_returned_as_pil_image_objects():
    parser = Parser()
    doc = parser.parse(load("scanned.pdf"))

    figures = [e for e in doc.elements if e.element_type == ElementType.FIGURE]
    assert len(figures) == 1
    assert isinstance(figures[0].content, Image.Image)


# Acceptance criterion: Parse failures raise `ParseError` with the source page number
def test_parse_error_carries_page_number():
    error = ParseError("boom", page_number=5)

    assert error.page_number == 5
    assert str(error) == "boom"


def test_invalid_pdf_bytes_raises_parse_error():
    parser = Parser()

    try:
        parser.parse(b"this is not a pdf")
        assert False, "expected ParseError"
    except ParseError:
        pass


# --- Malformed-input regression tests (Codex review) ------------------------


def test_empty_pdf_bytes_raises_parse_error():
    parser = Parser()

    try:
        parser.parse(b"")
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_truncated_pdf_raises_parse_error():
    # Real fixture cut to ~half length: still starts with a valid %PDF-1.x
    # header (initially recognizable) but the trailer/xref table is gone,
    # so pdfium refuses to open it at all. This fails at the
    # converter.convert() stage — already covered by the original narrow
    # try/except — but wasn't previously tested against a real truncated
    # PDF (only against b"this is not a pdf", a different failure shape).
    # Kept as a permanent regression test in its own right.
    full = load("clean_digital.pdf")
    truncated = full[: len(full) // 2]
    parser = Parser()

    try:
        parser.parse(truncated)
        assert False, "expected ParseError"
    except ParseError:
        pass


def test_exception_during_element_iteration_raises_parse_error_with_last_known_page():
    # This is the actual gap Codex found: a failure during element
    # iteration/provenance/bbox extraction (as opposed to during
    # converter.convert() itself) previously propagated unwrapped instead
    # of becoming a ParseError. Real malformed PDFs couldn't be made to
    # reliably reproduce this exact failure point (pdfium either refuses to
    # open truncated/corrupted files outright — caught by the pre-existing
    # try/except around convert() — or silently tolerates mid-file
    # corruption and returns fewer elements with no error at all). A fake
    # converter/document is used instead to deterministically exercise the
    # specific code path the fix changed.
    class FakeBBox:
        l = 0.0
        t = 0.0
        r = 0.0
        b = 0.0

    class FakeProv:
        page_no = 3
        bbox = FakeBBox()

    class FakeItem:
        label = DocItemLabel.TEXT
        prov = [FakeProv()]
        text = "this item parses fine"
        self_ref = "#/texts/0"

    class FakeDoc:
        def iterate_items(self):
            yield FakeItem(), 0
            raise RuntimeError("simulated corruption mid-iteration")

    class FakeResult:
        document = FakeDoc()

    class FakeConverter:
        def convert(self, stream):
            return FakeResult()

    parser = Parser(converter=FakeConverter())

    try:
        parser.parse(b"irrelevant, converter is faked")
        assert False, "expected ParseError"
    except ParseError as e:
        # "Best-available" page number: the last element successfully
        # processed before the failure, not None.
        assert e.page_number == 3


# --- Silent-drop visibility (Codex review) -----------------------------------


def test_element_with_missing_provenance_is_counted_and_logged(caplog):
    class FakeItem:
        label = DocItemLabel.CAPTION
        prov = []  # missing provenance — this is the exact case Codex flagged

    class FakeDoc:
        def iterate_items(self):
            yield FakeItem(), 0

    class FakeResult:
        document = FakeDoc()

    class FakeConverter:
        def convert(self, stream):
            return FakeResult()

    parser = Parser(converter=FakeConverter())

    with caplog.at_level("WARNING"):
        doc = parser.parse(b"irrelevant, converter is faked")

    assert doc.elements == []
    assert doc.dropped_elements == 1
    assert any("dropped" in record.message.lower() for record in caplog.records)


# --- Well-formed-fixture regression guard ------------------------------------
#
# The changes above (extended try/except, dropped_elements tracking) must be
# a no-op on well-formed input. Pin exact counts so any future change to
# these three fixtures' output is caught immediately.
def test_element_counts_unchanged_on_well_formed_fixtures():
    parser = Parser()

    clean = parser.parse(load("clean_digital.pdf"))
    assert len(clean.elements) == 21
    assert clean.dropped_elements == 0

    table_heavy = parser.parse(load("table_heavy.pdf"))
    assert len(table_heavy.elements) == 66
    assert table_heavy.dropped_elements == 0

    scanned = parser.parse(load("scanned.pdf"))
    assert len(scanned.elements) == 2
    assert scanned.dropped_elements == 0


# --- Scanned-PDF degradation behavior (informs FEAT-017 scoping) -----------
#
# With OCR off, scanned.pdf (3 pages, no text layer) does NOT crash, but
# produces almost nothing: 1 heading (real embedded text on page 1's title,
# not OCR'd — this specific PDF has a hybrid layer) and 1 tiny figure (a
# logo). Pages 2 and 3 produce zero elements each. This is "graceful" in
# that it doesn't raise, but it's a near-total, silent data loss on a
# fully-scanned document — FEAT-017 needs a trigger heuristic (e.g. very
# low element count relative to page count) since nothing here signals
# that anything went wrong.
def test_scanned_pdf_degrades_gracefully_without_crashing():
    parser = Parser()

    doc = parser.parse(load("scanned.pdf"))

    assert len(doc.elements) == 2
    pages_with_content = {e.page_number for e in doc.elements}
    assert pages_with_content == {1}  # pages 2-3 produced nothing at all
