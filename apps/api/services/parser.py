import base64
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO

import httpx
import pytesseract
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel
from docling_core.types.io import DocumentStream
from google import genai
from google.genai import types
from PIL import Image

logger = logging.getLogger(__name__)

# FEAT-017: gemini-2.5-flash, not the 3.6/3.5 models used for generation/
# verification — this is Flash proper (vision-native, free tier 1,500
# req/day), unrelated to the generate/verify model choice. Live-confirmed
# callable against the real API before use (2026-07-25), not assumed from
# .agent/api-docs/gemini.md's model table alone.
OCR_MODEL = "gemini-2.5-flash"

OCR_SYSTEM_PROMPT = (
    "Transcribe all readable text from this scanned document page, in reading "
    "order, as plain text. Do not describe the image or add commentary — output "
    "only the transcribed text. If the page has no readable text at all, output "
    "nothing."
)


def _default_converter() -> DocumentConverter:
    # do_ocr=False: Docling's own OCR probes every page and downloads OCR
    # models on first use even for fully digital PDFs — both slow and
    # redundant now that low-yield pages get a targeted Gemini fallback
    # instead (FEAT-017) rather than Docling attempting OCR everywhere.
    # generate_picture_images=True: PictureItem.get_image() returns None
    # unless this is set — needed to satisfy "figures as PIL Image objects".
    # generate_page_images=True (FEAT-017): the OCR fallback below needs each
    # low-yield page's own rendered image to send to Gemini — this is the
    # only way to get it without a second, separate Docling conversion.
    options = PdfPipelineOptions(do_ocr=False, generate_picture_images=True, generate_page_images=True)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


# Audit finding (2026-07-26): with no explicit http_options.timeout, the
# genai SDK passes timeout=None straight through to httpx — confirmed
# directly against the installed SDK source (_api_client.py), not
# assumed — which httpx treats as "no timeout at all," not "use a
# default." A hung connection would block this call, and therefore the
# whole per-page OCR step (and the pages after it), indefinitely. Same
# reasoning as OcrSpaceClient's explicit 60s below.
OCR_TIMEOUT_MS = 60_000


class GeminiOcrClient:
    """Tier 1 of FEAT-017's OCR fallback chain — a page whose Docling
    parse yielded suspiciously little gets sent here first, as a rendered
    image, for a real vision-model transcription. Never raises: a failed
    call returns None so the chain (in Parser.parse()) moves on to tier
    2, matching this project's established fail-safe discipline elsewhere
    (Verifier's fail-to-unsupported pattern)."""

    def __init__(self, client: genai.Client | None = None):
        # Deliberately NOT resolved here (audit finding, 2026-07-26): the
        # original eager `client or genai.Client(api_key=os.environ[...])`
        # read GEMINI_API_KEY at Parser()-construction time — meaning a
        # missing key crashed the whole Parser(), even for a document that
        # would never once trigger OCR. Resolved lazily instead, inside
        # transcribe_page()'s own try/except below, so a missing/bad key
        # becomes an ordinary per-call tier failure (logged, chain moves
        # to tier 2) — never a Parser()-construction-time crash.
        self._client = client

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                api_key=os.environ["GEMINI_API_KEY"],
                http_options=types.HttpOptions(timeout=OCR_TIMEOUT_MS),
            )
        return self._client

    def transcribe_page(self, image: Image.Image) -> str | None:
        try:
            client = self._get_client()
            buf = BytesIO()
            image.save(buf, format="PNG")
            response = client.models.generate_content(
                model=OCR_MODEL,
                contents=[
                    types.Part.from_text(text=OCR_SYSTEM_PROMPT),
                    types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                ],
            )
            text = response.text
            return text.strip() if text and text.strip() else None
        except Exception:
            logger.warning("parser: Gemini OCR tier call failed for a page", exc_info=True)
            return None


# Tier 2: OCR.space — a plain REST API, not an SDK (.agent/api-docs/ocrspace.md,
# verified live 2026-07-26). Deliberately a second, independent vendor: a
# Gemini-side outage or quota exhaustion (a real, hit-live constraint —
# see .agent/MEMORY.md's 2026-07-26 entry) has zero chance of also taking
# out this tier, since it's a different company's infrastructure entirely.
OCR_SPACE_URL = "https://api.ocr.space/parse/image"
OCR_SPACE_ENGINE = 2  # the newer/more-accurate of OCR.space's two engines — see ocrspace.md


class OcrSpaceClient:
    """Tier 2. Same never-raises contract as GeminiOcrClient — a failure
    here (network, auth, or the API's own IsErroredOnProcessing flag,
    which can come back on a 200 OK) returns None so the chain falls
    through to tier 3."""

    def __init__(self, api_key: str | None = None, http_client: httpx.Client | None = None):
        # api_key resolution deliberately deferred to transcribe_page()'s
        # own try/except (same reasoning as GeminiOcrClient, audit finding
        # 2026-07-26) — os.environ["OCR_SPACE_API_KEY"] here at
        # construction time would crash Parser() itself if unset, not just
        # this one tier. The httpx.Client itself is safe to build eagerly;
        # it doesn't need the key.
        self._api_key = api_key
        self._http = http_client or httpx.Client(timeout=OCR_TIMEOUT_MS / 1000.0)

    def _get_api_key(self) -> str:
        if self._api_key is None:
            self._api_key = os.environ["OCR_SPACE_API_KEY"]
        return self._api_key

    def transcribe_page(self, image: Image.Image) -> str | None:
        try:
            api_key = self._get_api_key()
            buf = BytesIO()
            image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            response = self._http.post(
                OCR_SPACE_URL,
                headers={"apikey": api_key},
                data={"base64Image": f"data:image/png;base64,{b64}", "OCREngine": OCR_SPACE_ENGINE},
            )
            response.raise_for_status()
            body = response.json()
            # A processing failure comes back as a normal 200 OK with this
            # flag set — raise_for_status() above does not catch it.
            if body.get("IsErroredOnProcessing"):
                logger.warning("parser: OCR.space reported a processing error: %s", body.get("ErrorMessage"))
                return None
            parsed_results = body.get("ParsedResults") or []
            if not parsed_results:
                return None
            text = parsed_results[0].get("ParsedText")
            return text.strip() if text and text.strip() else None
        except Exception:
            logger.warning("parser: OCR.space tier call failed for a page", exc_info=True)
            return None


class TesseractOcrClient:
    """Tier 3, the last resort: self-hosted, no network call, no vendor
    quota of any kind to exhaust — always available as long as the
    container has the tesseract-ocr system binary installed (Docker-only
    on Render; see ARCHITECTURE.md's deploy constraint). TESSERACT_CMD
    lets local dev point at a binary that isn't on PATH (e.g. a Windows
    install) without affecting the Docker/Linux production path, where
    `tesseract` is already on PATH after the apt-get install."""

    def __init__(self, tesseract_cmd: str | None = None):
        cmd = tesseract_cmd or os.environ.get("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd

    def transcribe_page(self, image: Image.Image) -> str | None:
        try:
            # timeout=0 (pytesseract's own default) means "no timeout at
            # all" — confirmed directly against the installed source
            # (pytesseract.py's timeout_manager: a falsy value skips
            # subprocess.communicate()'s own timeout entirely). A hung
            # tesseract process (a real, documented failure mode on
            # certain pathological images) would otherwise block this
            # call, and the rest of the parse, indefinitely.
            text = pytesseract.image_to_string(image, timeout=OCR_TIMEOUT_MS // 1000)
            return text.strip() if text and text.strip() else None
        except RuntimeError as exc:
            logger.warning("parser: Tesseract OCR tier timed out for a page: %s", exc)
            return None
        except Exception:
            logger.warning("parser: Tesseract OCR tier call failed for a page", exc_info=True)
            return None


def _default_ocr_tiers() -> list[tuple[str, object]]:
    return [
        ("gemini", GeminiOcrClient()),
        ("ocrspace", OcrSpaceClient()),
        ("tesseract", TesseractOcrClient()),
    ]


class ElementType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    LIST = "list"


# FEAT-017's trigger set: a page with zero elements of these types is
# "low-yield" regardless of how many FIGURE/CAPTION elements it has. A lone
# figure with no text around it is still consistent with an unread scanned
# page — CAPTION is excluded too since a caption never appears without a
# table/figure it belongs to, so it carries no independent signal either.
_TEXTUAL_ELEMENT_TYPES = {ElementType.TEXT, ElementType.HEADING, ElementType.TABLE, ElementType.LIST}


# Matches .agent/SCHEMA.md's `element_type` enum exactly. Docling labels not
# listed here (page_header, page_footer, footnote, formula, code, ...) are
# deliberately dropped during parsing rather than mapped to a catch-all —
# they have no column to live in downstream. This filtering is expected and
# not logged; it's not the same thing as an element we DO model failing to
# extract (see dropped_elements below).
_LABEL_TO_ELEMENT_TYPE = {
    DocItemLabel.TEXT: ElementType.TEXT,
    DocItemLabel.PARAGRAPH: ElementType.TEXT,
    DocItemLabel.TITLE: ElementType.HEADING,
    DocItemLabel.SECTION_HEADER: ElementType.HEADING,
    DocItemLabel.TABLE: ElementType.TABLE,
    DocItemLabel.PICTURE: ElementType.FIGURE,
    DocItemLabel.CHART: ElementType.FIGURE,
    DocItemLabel.CAPTION: ElementType.CAPTION,
    DocItemLabel.LIST_ITEM: ElementType.LIST,
}


class ParseError(Exception):
    def __init__(self, message: str, page_number: int | None = None):
        super().__init__(message)
        self.page_number = page_number


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class ParsedElement:
    element_type: ElementType
    page_number: int
    bbox: BBox
    content: str | Image.Image
    element_id: str
    # Populated only for TABLE/FIGURE elements — the element_id of each
    # caption Docling explicitly linked to this table/figure (via
    # TableItem.captions / PictureItem.captions). Empty for every other
    # element type, and empty (not an error) for a table/figure Docling
    # simply didn't link a caption to.
    associated_caption_ids: list[str] = field(default_factory=list)
    # Populated only for CAPTION elements: "explicit" if some table/figure's
    # `captions` list pointed at this caption (Tier 1 — Docling-provided),
    # "none" if no table/figure claimed it. None (not "none") for every
    # non-caption element type, since the field doesn't apply to them.
    # Associating an unclaimed caption with a nearby table/figure by
    # position/proximity is a Tier 2 heuristic — explicitly FEAT-005's job,
    # not this parser's.
    association_method: str | None = None


@dataclass
class ParsedDocument:
    """Result of Parser.parse().

    Figure ownership: each FIGURE element's `content` is a live PIL Image
    backed by Docling's rendered page/crop data. The parser does not close
    these. Callers must close every figure Image after persisting it (e.g.
    after uploading to storage) to release the underlying buffer.
    """

    elements: list[ParsedElement]
    dropped_elements: int = 0


class Parser:
    def __init__(self, converter: DocumentConverter | None = None, ocr_tiers: list[tuple[str, object]] | None = None):
        self._converter = converter or _default_converter()
        # Real 3-tier chain by default, same reasoning as `converter` — OCR
        # fallback is production behavior, not an opt-in extra a caller has
        # to remember to wire up. routes/ingest.py constructs Parser() with
        # no arguments and gets all three tiers automatically. Tests that
        # don't want real calls inject their own (name, fake) tier list
        # (see test_parser.py) — each tier is just anything with a
        # transcribe_page(image) -> str | None method, same contract
        # GeminiOcrClient/OcrSpaceClient/TesseractOcrClient all share.
        self._ocr_tiers = ocr_tiers if ocr_tiers is not None else _default_ocr_tiers()

    def parse(self, pdf_bytes: bytes) -> ParsedDocument:
        stream = DocumentStream(name="document.pdf", stream=BytesIO(pdf_bytes))
        try:
            result = self._converter.convert(stream)
        except Exception as exc:
            raise ParseError(f"Docling conversion failed: {exc}") from exc

        doc = result.document
        elements: list[ParsedElement] = []
        dropped_elements = 0
        last_page_number: int | None = None
        explicitly_claimed_caption_ids: set[str] = set()

        try:
            for item, _level in doc.iterate_items():
                element_type = _LABEL_TO_ELEMENT_TYPE.get(getattr(item, "label", None))
                if element_type is None:
                    continue  # not one of our six types — expected filtering, not a drop

                if not item.prov:
                    dropped_elements += 1
                    logger.warning(
                        "parser: dropped %s element — no provenance (page/bbox unavailable)",
                        element_type.value,
                    )
                    continue

                prov = item.prov[0]
                page_number = prov.page_no
                last_page_number = page_number
                bbox = BBox(x0=prov.bbox.l, y0=prov.bbox.t, x1=prov.bbox.r, y1=prov.bbox.b)

                if element_type == ElementType.TABLE:
                    content = item.export_to_markdown(doc)
                elif element_type == ElementType.FIGURE:
                    image = item.get_image(doc)
                    if image is None:
                        dropped_elements += 1
                        logger.warning(
                            "parser: dropped figure element on page %s — get_image() returned None",
                            page_number,
                        )
                        continue
                    content = image
                else:
                    content = item.text

                associated_caption_ids: list[str] = []
                if element_type in (ElementType.TABLE, ElementType.FIGURE):
                    for caption_ref in getattr(item, "captions", []):
                        try:
                            resolved = caption_ref.resolve(doc)
                        except Exception:
                            logger.warning(
                                "parser: table/figure on page %s references a caption that failed to resolve (%s)",
                                page_number,
                                caption_ref,
                            )
                            continue
                        if _LABEL_TO_ELEMENT_TYPE.get(getattr(resolved, "label", None)) != ElementType.CAPTION:
                            logger.warning(
                                "parser: table/figure on page %s's caption ref resolved to a non-caption item (%s)",
                                page_number,
                                getattr(resolved, "label", None),
                            )
                            continue
                        associated_caption_ids.append(resolved.self_ref)
                        explicitly_claimed_caption_ids.add(resolved.self_ref)

                elements.append(
                    ParsedElement(
                        element_type=element_type,
                        page_number=page_number,
                        bbox=bbox,
                        content=content,
                        element_id=item.self_ref,
                        associated_caption_ids=associated_caption_ids,
                    )
                )

            # Second pass over already-extracted elements (no further Docling
            # calls): a caption can be referenced by a table/figure that's
            # iterated either before or after it, so association_method can
            # only be finalized once every table/figure has been seen.
            for element in elements:
                if element.element_type == ElementType.CAPTION:
                    element.association_method = (
                        "explicit" if element.element_id in explicitly_claimed_caption_ids else "none"
                    )
        except Exception as exc:
            raise ParseError(
                f"Failed while processing document elements: {exc}", page_number=last_page_number
            ) from exc

        # FEAT-017: OCR fallback for low-yield pages. Runs after Docling's
        # own extraction is fully done (elements above are final) and
        # outside the try/except above on purpose — a failed OCR call must
        # never become a ParseError for the whole document (GeminiOcrClient
        # itself never raises; this loop only needs to survive a page whose
        # rendered image is unexpectedly missing).
        pages_with_textual_content = {
            e.page_number for e in elements if e.element_type in _TEXTUAL_ELEMENT_TYPES
        }
        for page_number, page in doc.pages.items():
            if page_number in pages_with_textual_content:
                continue
            page_image = page.image.pil_image if page.image else None
            if page_image is None:
                logger.warning(
                    "parser: page %s has no textual elements and no rendered page image "
                    "(generate_page_images produced nothing) — cannot attempt OCR fallback",
                    page_number,
                )
                continue

            # Walk the tier chain in order — each tier only attempted if
            # every prior one failed (raised, returned nothing, or returned
            # only whitespace — a "successful" call that recovered nothing
            # useful is treated exactly like a failure, not a valid
            # recovery), never in parallel and never speculatively. The
            # try/except here is deliberate, on top of each tier client's
            # own internal one: a tier is an injectable dependency (anything
            # with transcribe_page), so this loop can't assume every
            # possible implementation fails safe — or normalizes
            # empty/whitespace results — on its own. `text and text.strip()`
            # (not bare `text`) is deliberate defense-in-depth: every real
            # client here already self-normalizes empty/whitespace text to
            # None before returning, but a bare truthiness check would
            # silently accept a raw whitespace-only string as "recovered"
            # from any tier that didn't (a real gap, found live during
            # audit — a plain `if text:` treats a non-empty whitespace
            # string as truthy). One tier blowing up must never take down
            # the rest of the chain, let alone the rest of the parse.
            recovered_text: str | None = None
            recovered_tier = "none"
            for tier_name, tier_client in self._ocr_tiers:
                try:
                    text = tier_client.transcribe_page(page_image)
                except Exception:
                    logger.warning(
                        "parser: OCR tier=%s raised for page %s — trying next tier",
                        tier_name,
                        page_number,
                        exc_info=True,
                    )
                    continue
                if text and text.strip():
                    recovered_text = text.strip()
                    recovered_tier = tier_name
                    break
                logger.warning(
                    "parser: OCR tier=%s found no recoverable text on page %s — trying next tier",
                    tier_name,
                    page_number,
                )

            if not recovered_text:
                logger.warning(
                    "parser: OCR fallback exhausted all tiers for page %s — page remains low-yield", page_number
                )
                continue

            width, height = page_image.size
            elements.append(
                ParsedElement(
                    element_type=ElementType.TEXT,
                    page_number=page_number,
                    bbox=BBox(x0=0.0, y0=0.0, x1=float(width), y1=float(height)),
                    content=recovered_text,
                    element_id=f"ocr-page-{page_number}",
                )
            )
            # Which tier recovered this page — established as necessary
            # for debugging retrieval quality (a page recovered by
            # Tesseract, the weakest tier, may need a closer look if
            # something downstream looks off).
            logger.info("parser: OCR fallback recovered text on page %s via tier=%s", page_number, recovered_tier)

        return ParsedDocument(elements=elements, dropped_elements=dropped_elements)
