import logging
from dataclasses import dataclass, field
from enum import Enum
from io import BytesIO

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel
from docling_core.types.io import DocumentStream
from PIL import Image

logger = logging.getLogger(__name__)


def _default_converter() -> DocumentConverter:
    # do_ocr=False: OCR fallback for scanned/low-confidence pages is FEAT-017
    # (Phase 4), deliberately out of scope here. Without this, Docling probes
    # every page for OCR need and downloads OCR models on first use even for
    # fully digital PDFs.
    # generate_picture_images=True: PictureItem.get_image() returns None
    # unless this is set — needed to satisfy "figures as PIL Image objects".
    options = PdfPipelineOptions(do_ocr=False, generate_picture_images=True)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


class ElementType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    LIST = "list"


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
    def __init__(self, converter: DocumentConverter | None = None):
        self._converter = converter or _default_converter()

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

        return ParsedDocument(elements=elements, dropped_elements=dropped_elements)
