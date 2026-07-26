# Tests for [FEAT-004] Docling parser service + [FEAT-017] OCR fallback
# (Gemini tier 1, extended with OCR.space tier 2 + Tesseract tier 3 —
# 2026-07-26 follow-up)
#
# Fixtures (apps/api/tests/fixtures/): clean_digital.pdf (plain formatted
# doc, one table), table_heavy.pdf (29 tables across 11 pages), scanned.pdf
# (3-page scan, no text layer except a real-text title on page 1).
#
# No single fixture exercises all six element types, so coverage is spread
# across fixtures rather than asserted on one. Most tests below inject a
# single-tier `ocr_tiers=[("gemini", FakeOcrClient())]` (always returns
# None) even though FEAT-017's real OCR fallback is a real 3-tier chain
# by default (Parser()'s bare constructor uses real Gemini -> OCR.space ->
# Tesseract clients, matching the existing `converter` default) — this
# keeps Docling-only regression guards deterministic and fast. The
# OCR-specific tests near the end of this file are the ones that actually
# exercise real/fake OCR recovery, and the full tier-chain behavior, on
# purpose.
#
# Extended 2026-07-22 per Codex review of parser.py: the try/except around
# converter.convert() didn't cover the element-iteration loop, so a failure
# during iteration/provenance/bbox access could raise unwrapped instead of
# ParseError; a caption with missing provenance was silently dropped with
# no signal at all. See ParsedDocument.dropped_elements and the WARN logs
# added for both silent-drop cases (missing provenance, get_image() -> None).

import httpx
from docling_core.types.doc import DocItemLabel
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
    proved (Docling's own extraction) without a real, non-deterministic
    Gemini call on every run. Also counts calls (and confirms a real
    image was passed each time), for the cost/scope-guard test — a
    normal digital PDF must trigger zero of them."""

    def __init__(self):
        self.call_count = 0

    def transcribe_page(self, image):
        self.call_count += 1
        assert isinstance(image, Image.Image)
        return None


# Acceptance criterion: `Parser.parse(pdf_bytes) -> ParsedDocument` returns typed elements: text, heading, table, figure, caption, list
def test_parse_returns_expected_element_types_across_fixtures():
    # FakeOcrClient here: this test is about Docling's own type coverage,
    # not FEAT-017's OCR behavior (which has its own dedicated tests below)
    # — a real OCR call would add TEXT elements to scanned.pdf and make
    # the pinned scanned_types assertion below meaningless noise.
    parser = Parser(ocr_tiers=[("gemini", FakeOcrClient())])

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
        pages = {}  # FEAT-017's OCR-fallback pass reads doc.pages — none here, none expected

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
    # FakeOcrClient (returns None): this pins Docling's OWN extraction —
    # FEAT-017's OCR fallback has its own separate pinned-output test below
    # (test_ocr_fallback_recovers_real_text_on_scanned_pdf) using the real
    # client. Without the fake here, scanned.pdf's real OCR recovery would
    # make this specific regression guard non-deterministic and conflate
    # two different things it's supposed to catch independently.
    parser = Parser(ocr_tiers=[("gemini", FakeOcrClient())])

    clean = parser.parse(load("clean_digital.pdf"))
    assert len(clean.elements) == 21
    assert clean.dropped_elements == 0

    table_heavy = parser.parse(load("table_heavy.pdf"))
    assert len(table_heavy.elements) == 66
    assert table_heavy.dropped_elements == 0

    scanned = parser.parse(load("scanned.pdf"))
    assert len(scanned.elements) == 2
    assert scanned.dropped_elements == 0


# --- Scanned-PDF degradation behavior, OCR unavailable/found-nothing path --
#
# This pins Docling's OWN behavior with OCR fallback present but not
# recovering anything (FakeOcrClient always returns None) — the real
# recovery case is test_ocr_fallback_recovers_real_text_on_scanned_pdf
# below. Docling alone still produces almost nothing on this fixture: 1
# heading (real embedded text on page 1's title, not OCR'd — this specific
# PDF has a hybrid layer) and 1 tiny figure (a logo); pages 2 and 3 produce
# zero elements each from Docling. The invariant this test actually
# guards: OCR finding nothing on every low-yield page must still degrade
# gracefully (no crash, no fabricated content) rather than being FEAT-017
# scoping information about a since-fixed gap.
def test_scanned_pdf_degrades_gracefully_when_ocr_recovers_nothing():
    parser = Parser(ocr_tiers=[("gemini", FakeOcrClient())])

    doc = parser.parse(load("scanned.pdf"))

    assert len(doc.elements) == 2
    pages_with_content = {e.page_number for e in doc.elements}
    assert pages_with_content == {1}  # pages 2-3: Docling found nothing, OCR recovered nothing either


# --- FEAT-017: OCR fallback ---------------------------------------------


# Acceptance criterion: real scanned.pdf, real recovered content per page —
# real output required, not a pass/fail assertion (task brief, item 6). Uses
# the REAL 3-tier chain (Parser()'s bare-constructor default: real Gemini
# -> real OCR.space -> real Tesseract) — the one true integration proof for
# this feature, same discipline as every other real-API-call test
# elsewhere in this suite. Which tier actually recovers each page is
# reported, not assumed — if Gemini's daily quota (.agent/MEMORY.md,
# 2026-07-26) is still exhausted from earlier same-day testing, this is
# real, live proof the chain itself is what recovers the content, not a
# specific tier succeeding.
def test_ocr_fallback_recovers_real_text_on_scanned_pdf(capsys, caplog):
    parser = Parser()  # real converter and the real 3-tier chain — no fakes

    with caplog.at_level("INFO"):
        doc = parser.parse(load("scanned.pdf"))

    by_page: dict[int, list] = {}
    for element in doc.elements:
        by_page.setdefault(element.page_number, []).append(element)

    tier_log_lines = [r.message for r in caplog.records if "via tier=" in r.message]

    with capsys.disabled():
        print("\n" + "=" * 90)
        print("FEAT-017 real OCR fallback — actual recovered content, scanned.pdf")
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

    # Page 1 has real embedded text (the title) — Docling already extracted
    # it, so this page must NOT trigger OCR at all (no ocr-page-1 element).
    assert not any(e.element_id == "ocr-page-1" for e in doc.elements)

    # Pages 2 and 3 (zero elements pre-FEAT-017 — the actual bug this
    # feature fixes) must now carry real, non-trivial recovered text.
    page_2_ocr = [e for e in by_page.get(2, []) if e.element_id == "ocr-page-2"]
    page_3_ocr = [e for e in by_page.get(3, []) if e.element_id == "ocr-page-3"]
    assert len(page_2_ocr) == 1
    assert len(page_3_ocr) == 1
    assert page_2_ocr[0].element_type == ElementType.TEXT
    assert page_3_ocr[0].element_type == ElementType.TEXT
    assert len(page_2_ocr[0].content.strip()) > 50
    assert len(page_3_ocr[0].content.strip()) > 50

    # OCR-recovered elements are ordinary ParsedElements — chunker.py
    # consumes them identically to Docling-native text, no parallel shape.
    for element in (page_2_ocr[0], page_3_ocr[0]):
        assert isinstance(element.content, str)
        assert isinstance(element.bbox, BBox)
        assert element.associated_caption_ids == []
        assert element.association_method is None


# Acceptance criterion: cost/scope guard — a normal digital PDF must
# trigger zero OCR calls, not one per page of every document. Now covers
# all THREE tiers explicitly (not just tier 1) — the trigger heuristic
# lives above the tier loop, so if it correctly never fires, none of the
# three tiers should ever see a call either.
def test_ocr_fallback_never_fires_on_high_yield_fixtures():
    tier1, tier2, tier3 = FakeOcrClient(), FakeOcrClient(), FakeOcrClient()
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    parser.parse(load("clean_digital.pdf"))
    assert (tier1.call_count, tier2.call_count, tier3.call_count) == (0, 0, 0)

    parser.parse(load("table_heavy.pdf"))
    assert (tier1.call_count, tier2.call_count, tier3.call_count) == (0, 0, 0)


# A Gemini call failure during OCR degrades that one page gracefully —
# logged, no crash, page stays unrecovered, parse completes for the rest
# of the document — never taken down the whole parse (acceptance
# criterion). Same class of check FEAT-011's audit required for
# Verifier's own fail-safe behavior (test_verifier.py's
# test_verify_fails_safe_to_unsupported_when_gemini_api_call_raises):
# raises the REAL exception type and shape a Gemini quota/rate-limit
# failure actually takes (google.genai.errors.ClientError, 429) — not a
# generic stand-in — and this is now a PERMANENT, deterministic
# regression test for exactly the failure FEAT-017 hit for real
# (.agent/MEMORY.md, 2026-07-26): a real 429 must never be the only
# proof this path works.
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
        doc = parser.parse(load("scanned.pdf"))

    # No crash: parse() returned normally with the pre-existing page-1
    # elements intact — a raised OCR call did not propagate out of parse().
    assert len(doc.elements) == 2
    assert {e.page_number for e in doc.elements} == {1}

    # Page stays unrecovered: no fabricated content for either page OCR
    # failed on.
    assert not any(e.element_id.startswith("ocr-page-") for e in doc.elements)

    # Parse completes for the REST of the document: both low-yield pages
    # (2 and 3) were independently attempted — one page's failure didn't
    # abort the loop early and skip the other.
    assert ocr_client.calls == 2

    # Logged warning: the real failure must be visible in logs, not just
    # silently swallowed — checked for content, not just presence.
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("OCR" in w and ("raised" in w.lower() or "fail" in w.lower()) for w in warnings)
    assert sum(1 for w in warnings if "page" in w.lower()) >= 2  # one per low-yield page


# --- 3-tier chain: full deterministic combination matrix (2026-07-26) ------
#
# Gemini (tier 1) -> OCR.space (tier 2, independent vendor) -> Tesseract
# (tier 3, self-hosted last resort) -> unrecovered. Each of the 4 real
# combinations the chain can land in, confirmed via call-counting fakes —
# same pattern as FakeOcrClient above, just parametrized per tier so each
# test can independently control every tier's behavior.


class TierFake:
    """One configurable fake tier: "succeed" returns canned text, "raise"
    simulates an exception/timeout/quota error, "none" simulates a tier
    that ran but found nothing (not an exception) — both real failure
    shapes a tier can take. Counts calls so each combination test can
    assert exactly which tiers were (and weren't) invoked."""

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


# Combination 1: tier 1 succeeds -> tiers 2/3 never called.
def test_ocr_chain_tier1_succeeds_tiers_2_and_3_never_called():
    tier1 = TierFake("succeed", text="gemini recovered this")
    tier2 = TierFake("succeed")
    tier3 = TierFake("succeed")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2  # pages 2 and 3
    assert tier2.calls == 0
    assert tier3.calls == 0
    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "gemini recovered this" for e in recovered)


# Combination 2: tier 1 fails, tier 2 succeeds -> tier 3 never called.
def test_ocr_chain_tier1_fails_tier2_succeeds_tier3_never_called():
    tier1 = TierFake("raise")
    tier2 = TierFake("succeed", text="ocrspace recovered this")
    tier3 = TierFake("succeed")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2
    assert tier2.calls == 2
    assert tier3.calls == 0
    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "ocrspace recovered this" for e in recovered)


# Combination 3: tiers 1+2 fail, tier 3 (Tesseract, last resort) succeeds.
def test_ocr_chain_tiers_1_and_2_fail_tier3_succeeds():
    tier1 = TierFake("none")  # ran, found nothing — not an exception
    tier2 = TierFake("raise")  # exception/timeout/quota error
    tier3 = TierFake("succeed", text="tesseract recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2
    assert tier2.calls == 2
    assert tier3.calls == 2
    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "tesseract recovered this" for e in recovered)


# Combination 4: all three tiers fail — existing fail-safe still holds:
# logged, no crash, page unrecovered, rest of document completes.
def test_ocr_chain_all_three_tiers_fail_page_stays_unrecovered(caplog):
    tier1 = TierFake("raise")
    tier2 = TierFake("none")
    tier3 = TierFake("raise")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", tier3)])

    with caplog.at_level("WARNING"):
        doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2
    assert tier2.calls == 2
    assert tier3.calls == 2

    assert len(doc.elements) == 2  # page 1's pre-existing elements only
    assert {e.page_number for e in doc.elements} == {1}
    assert _recovered_elements(doc) == []

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert sum(1 for w in warnings if "exhausted all tiers" in w) == 2  # one per low-yield page


# --- Real 3-way tier comparison (task brief item 7) -------------------------
#
# Forces tier 1 (Gemini) to fail so tier 2 (OCR.space, real demo key) does
# the real recovery, and separately runs the real Tesseract client against
# the exact same page image, so all three tiers' real output on the same
# real page can be honestly compared — not just asserted as "non-empty".
def test_real_ocrspace_recovery_and_three_way_quality_comparison(capsys):
    class AlwaysFailsGemini:
        def transcribe_page(self, image):
            raise RuntimeError("forcing tier 1 to fail for this test")

    parser = Parser(ocr_tiers=[("gemini", AlwaysFailsGemini()), ("ocrspace", OcrSpaceClient()), ("tesseract", TesseractOcrClient())])
    doc = parser.parse(load("scanned.pdf"))

    page_2_ocr = [e for e in doc.elements if e.element_id == "ocr-page-2"]
    assert len(page_2_ocr) == 1
    ocrspace_text = page_2_ocr[0].content
    assert len(ocrspace_text.strip()) > 50

    # Real Tesseract, real Gemini (if quota allows), against the identical
    # page image — for an honest side-by-side, not a re-run through the
    # chain (which would stop at whichever tier succeeds first).
    from io import BytesIO

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.io import DocumentStream

    options = PdfPipelineOptions(do_ocr=False, generate_picture_images=True, generate_page_images=True)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    stream = DocumentStream(name="document.pdf", stream=BytesIO(load("scanned.pdf")))
    docling_result = converter.convert(stream)
    page_2_image = docling_result.document.pages[2].image.pil_image

    tesseract_text = TesseractOcrClient().transcribe_page(page_2_image)
    gemini_text = GeminiOcrClient().transcribe_page(page_2_image)

    with capsys.disabled():
        print("\n" + "=" * 90)
        print("REAL 3-WAY OCR QUALITY COMPARISON — scanned.pdf, page 2")
        print("=" * 90)
        print("\n[OCR.space, tier 2, real recovery via the chain]:")
        print(repr(ocrspace_text[:400]))
        print("\n[Tesseract, tier 3, real local binary, same page image]:")
        print(repr(tesseract_text[:400]) if tesseract_text else "None (tesseract not available in this environment)")
        print("\n[Gemini, tier 1, same page image — quota permitting]:")
        print(repr(gemini_text[:400]) if gemini_text else "None (real API call failed — see .agent/MEMORY.md's 2026-07-26 quota entry)")


# --- Audit follow-up (2026-07-26): IsErroredOnProcessing + empty-success --
#
# Two real gap classes the original 4-combination matrix didn't cover:
# (a) OCR.space's own documented "200 OK but IsErroredOnProcessing: true"
# shape was never exercised against the REAL OcrSpaceClient class — only
# generic fakes. (b) "the API call succeeded" and "the API call actually
# recovered something useful" are different claims — a tier returning
# empty/whitespace text (not an exception) must be treated as failure by
# the chain, not accepted as valid recovery.


def _ocrspace_response(status_code: int, json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.ocr.space/parse/image")
    return httpx.Response(status_code, json=json_body, request=request)


class _MockOcrSpaceHttp:
    """Stands in for OcrSpaceClient's httpx.Client — records the call and
    returns a caller-controlled canned response, same FakeClient/FakeModels
    pattern as test_generator.py's Gemini fake, just for httpx.Client.post
    instead of client.models.generate_content."""

    def __init__(self, response: httpx.Response):
        self._response = response
        self.calls = 0

    def post(self, url, *, headers, data):
        self.calls += 1
        return self._response


def test_ocrspace_client_treats_is_errored_on_processing_as_failure():
    # Real shape OCR.space actually documents/returns for a processing
    # failure — a normal 200 OK, not a 4xx/5xx raise_for_status() would
    # catch.
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

    doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2
    assert mock_http.calls == 2  # the real OcrSpaceClient, not a fake, actually got called for both pages
    assert tier3.calls == 2
    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "tesseract recovered this" for e in recovered)


class _RawTierFake:
    """Unlike TierFake above, this does NOT self-normalize — it returns
    exactly what it's told, including a raw whitespace-only string. Used
    to prove the CHAIN itself (not just each well-behaved real client)
    refuses to accept whitespace-only text as valid recovery — a real gap
    found live during audit: a bare `if text:` check treats a non-empty
    whitespace string as truthy, silently accepting garbage as "recovered"
    from any tier that didn't normalize on its own."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe_page(self, image):
        self.calls += 1
        return self.text


def test_chain_treats_whitespace_only_success_as_failure_not_valid_recovery():
    tier1 = _RawTierFake("   \n\t  ")  # "succeeded" but recovered nothing useful
    tier2 = TierFake("succeed", text="tier2 recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", TierFake("succeed"))])

    doc = parser.parse(load("scanned.pdf"))

    assert tier1.calls == 2  # tier 1 was tried on both low-yield pages
    assert tier2.calls == 2  # and correctly fell through to tier 2 each time
    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "tier2 recovered this" for e in recovered)  # never the whitespace


def test_chain_treats_empty_string_success_as_failure_not_valid_recovery():
    tier1 = _RawTierFake("")
    tier2 = TierFake("succeed", text="tier2 recovered this")
    parser = Parser(ocr_tiers=[("gemini", tier1), ("ocrspace", tier2), ("tesseract", TierFake("succeed"))])

    doc = parser.parse(load("scanned.pdf"))

    recovered = _recovered_elements(doc)
    assert len(recovered) == 2
    assert all(e.content == "tier2 recovered this" for e in recovered)


# Each real client's OWN empty/whitespace handling, independent of the
# chain-level defense above (defense in depth, same reasoning as the
# try/except layering) — a mocked "successful" call returning nothing
# useful for each of the three real client classes.
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
    # A real, genuinely blank image through the real local Tesseract
    # binary — no text at all to recognize, a real "successful but empty"
    # OCR call, not a mock standing in for one.
    blank_image = Image.new("RGB", (200, 200), color="white")
    result = TesseractOcrClient().transcribe_page(blank_image)
    assert result is None


# --- Audit follow-up (2026-07-26): timeout coverage on all 3 tiers --------
#
# OcrSpaceClient already had an explicit 60s timeout; GeminiOcrClient and
# TesseractOcrClient did not — confirmed by reading the installed SDK/
# pytesseract source directly (genai passes timeout=None to httpx with no
# http_options set, which httpx treats as "wait forever"; pytesseract's
# own default timeout=0 skips subprocess.communicate()'s timeout
# entirely). Both now set one explicitly; these tests confirm the real
# constructed objects actually carry it, not just that the code compiles.


def test_gemini_client_sets_an_explicit_http_timeout_by_default(monkeypatch):
    from services.parser import OCR_TIMEOUT_MS

    # A fake, syntactically-valid key — genai.Client() construction never
    # validates it over the network (confirmed earlier: construction is
    # cheap/local), so this test stays self-contained regardless of
    # whether a real GEMINI_API_KEY happens to be set in the environment
    # it runs in (it deliberately isn't, in the fresh-clone check this
    # exact gap was found through).
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-this-test-only")
    client = GeminiOcrClient()
    # Real client construction is lazy (audit finding, 2026-07-26 — see
    # GeminiOcrClient.__init__'s own comment: resolving GEMINI_API_KEY at
    # Parser()-construction time crashed the whole Parser() over a missing
    # key, even for a document that would never touch OCR). _get_client()
    # forces the same real construction transcribe_page() would trigger
    # lazily, without making an actual network call — genai.Client() itself
    # only builds an HTTP client object, it doesn't call out.
    real_client = client._get_client()
    # (_http_options is a plain dict on the installed SDK version here,
    # confirmed by direct inspection, not a typed object with attribute
    # access.)
    assert real_client._api_client._http_options["timeout"] == OCR_TIMEOUT_MS


def test_tesseract_client_passes_a_nonzero_timeout_to_pytesseract(monkeypatch):
    calls = []

    def fake_image_to_string(image, timeout=0):
        calls.append(timeout)
        return "some text"

    monkeypatch.setattr("pytesseract.image_to_string", fake_image_to_string)

    TesseractOcrClient().transcribe_page(Image.new("RGB", (10, 10)))

    assert len(calls) == 1
    assert calls[0] > 0  # not the library's own hangs-forever default of 0


def test_ocrspace_client_still_has_an_explicit_timeout():
    client = OcrSpaceClient(api_key="test-key")
    assert client._http.timeout.connect is not None
    assert client._http.timeout.connect > 0
