"""Parser's data contract (element/document shapes), split out from
services/parser.py (FEAT-027, 2026-08-01). parser.py itself imports
pdfplumber/docx/pptx/pytesseract/selectolax/google.genai — real, heavy
libraries a document only needs to pay for during actual ingestion.
chunker.py, however, needs ElementType/ParsedElement/ParsedDocument at
RUNTIME (not just as type hints — e.g. `element.element_type ==
ElementType.TABLE`), so it can't defer that import behind TYPE_CHECKING
the way db/queries.py and services/embedder.py do. This module holds
just the shapes, with no dependency on the parsing libraries, so
chunker.py (and anything that imports chunker.py — every route, per
FEAT-027's decoupling requirement) never pays their import cost."""

from dataclasses import dataclass, field
from enum import Enum

from PIL import Image


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


class ParseError(Exception):
    def __init__(self, message: str, page_number: int | None = None):
        super().__init__(message)
        self.page_number = page_number


class DocumentLimitError(ParseError):
    """The document is too big to process (size, pages, or pages needing
    OCR). Permanent: retrying can't help. The message is shown to the user."""


class MissingSourceFileError(Exception):
    """The uploaded file isn't in Storage. Permanent: it won't reappear."""


class IngestTimeoutError(Exception):
    """Processing ran past its time limit. Permanent: the next attempt would
    take just as long."""


def check_deadline(deadline: float | None, limit_minutes: float | None = None) -> None:
    """Raises IngestTimeoutError once time.monotonic() passes `deadline`."""
    import time

    if deadline is not None and time.monotonic() > deadline:
        suffix = f" ({limit_minutes:g} minutes)" if limit_minutes else ""
        raise IngestTimeoutError(f"Processing took longer than the time limit{suffix} and was stopped.")


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
    # caption this parser's own Tier-1 heuristic (text-prefix + bbox
    # proximity, FEAT-027) linked to this table/figure. Empty for every
    # other element type, and empty (not an error) for a table/figure with
    # no plausible caption nearby.
    associated_caption_ids: list[str] = field(default_factory=list)
    # Populated only for CAPTION elements: "explicit" if this parser's own
    # Tier-1 heuristic linked it to a table/figure, "none" if nothing
    # plausible was found. None (not "none") for every non-caption element
    # type, since the field doesn't apply to them. A caption Tier 1 leaves
    # unclaimed can still be picked up by chunker.py's own Tier-2 proximity
    # heuristic — unchanged by this rewrite.
    association_method: str | None = None


@dataclass
class ParsedDocument:
    """Result of Parser.parse().

    Figure ownership: each FIGURE element's `content` is a live PIL Image.
    The parser does not close these. Callers must close every figure Image
    after persisting it (e.g. after uploading to storage) to release the
    underlying buffer.
    """

    elements: list[ParsedElement]
    dropped_elements: int = 0
