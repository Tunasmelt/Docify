import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO

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


class GeminiOcrClient:
    """FEAT-017's OCR fallback — a page whose Docling parse yielded
    suspiciously little gets sent here as a rendered image, once, for a
    real vision-model transcription. Never raises: a failed OCR call
    degrades that one page back to "still low-yield," matching this
    project's established fail-safe discipline elsewhere (Verifier's
    fail-to-unsupported pattern) rather than taking down the whole parse
    over one page's bad luck."""

    def __init__(self, client: genai.Client | None = None):
        self._client = client or genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def transcribe_page(self, image: Image.Image) -> str | None:
        try:
            buf = BytesIO()
            image.save(buf, format="PNG")
            response = self._client.models.generate_content(
                model=OCR_MODEL,
                contents=[
                    types.Part.from_text(text=OCR_SYSTEM_PROMPT),
                    types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                ],
            )
            text = response.text
            return text.strip() if text and text.strip() else None
        except Exception:
            logger.warning("parser: OCR fallback call failed for a page — page remains unrecovered", exc_info=True)
            return None


def _default_ocr_client() -> GeminiOcrClient:
    return GeminiOcrClient()


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
    def __init__(self, converter: DocumentConverter | None = None, ocr_client: GeminiOcrClient | None = None):
        self._converter = converter or _default_converter()
        # Real by default, same as `converter` — OCR fallback is production
        # behavior, not an opt-in extra a caller has to remember to wire up.
        # routes/ingest.py constructs Parser() with no arguments and gets it
        # automatically. Tests that don't want a real Gemini call inject a
        # fake here explicitly (see test_parser.py).
        self._ocr_client = ocr_client or _default_ocr_client()

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

            # The try/except here is deliberate, on top of GeminiOcrClient's
            # own internal one: ocr_client is an injectable dependency (any
            # object with transcribe_page), so this loop can't assume every
            # possible implementation fails safe on its own. One page's OCR
            # call blowing up must never take down the rest of the parse.
            try:
                recovered_text = self._ocr_client.transcribe_page(page_image)
            except Exception:
                logger.warning(
                    "parser: OCR client raised for page %s — page remains low-yield", page_number, exc_info=True
                )
                continue
            if not recovered_text:
                logger.warning(
                    "parser: OCR fallback found no recoverable text on page %s — page remains low-yield",
                    page_number,
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
            logger.info("parser: OCR fallback recovered text on page %s", page_number)

        return ParsedDocument(elements=elements, dropped_elements=dropped_elements)
