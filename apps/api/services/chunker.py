import re
from dataclasses import dataclass, field

from PIL import Image

from services.parser import ElementType, ParsedDocument, ParsedElement

# ~500 tokens is the target chunk size (SCOPE.md, FEATURES.md FEAT-005). No
# tokenizer dependency exists yet, so token count is approximated at ~4
# characters/token (a standard rough estimate for English) rather than
# pulling in tiktoken/transformers just for a budget heuristic. Swap for a
# real tokenizer if chunk-size accuracy becomes a problem in practice.
TOKEN_BUDGET = 500
_CHARS_PER_TOKEN = 4

# Hard ceiling, enforced after chunking/association is resolved. Voyage's
# real per-input limit is 32,000 tokens (verified .agent/api-docs/voyage.md,
# 2026-07-23, two independent sources). This is set at ~1/8 of that —
# comfortably below it, not hugging it, because our char/4 proxy is only a
# rough estimate of Voyage's actual tokenization and shouldn't be trusted
# near the real boundary. TOKEN_BUDGET groups elements toward ~500 tokens
# in the common case; this ceiling is the backstop for the rare element
# (a huge table, a huge single paragraph with no internal boundaries) that
# TOKEN_BUDGET's grouping logic can't shrink because splitting mid-element
# was previously disallowed. Splitting an oversized element is different
# from splitting normal grouping: it happens by row (tables) or sentence/
# paragraph (text), never arbitrarily, and only when content would
# otherwise be too large to safely embed.
MAX_CHUNK_TOKENS = 4000

_GROUPABLE_TYPES = (ElementType.TEXT, ElementType.HEADING, ElementType.LIST)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _approx_token_count(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


@dataclass
class Chunk:
    chunk_index: int
    element_type: ElementType
    page_numbers: list[int]
    # Index of each source ParsedElement within ParsedDocument.elements —
    # matches FEATURES.md's "source element indices" acceptance criterion
    # literally, and is stable for the lifetime of a given parse.
    source_element_indices: list[int]
    content: str
    image: Image.Image | None = None
    # "explicit": table/figure caption Tier-1-linked by the parser (Docling).
    # "heuristic": table/figure caption Tier-2-matched here by proximity.
    # "unmatched": a caption that Tier-2 could not plausibly place anywhere,
    #   left as its own standalone chunk.
    # None: not applicable — either a table/figure with no caption at all
    #   involved, or a plain text/heading/list chunk.
    association_method: str | None = None
    # element_id(s) (ParsedElement.element_id, i.e. Docling's self_ref) of
    # any caption(s) whose text was folded into `content`. Empty if none.
    # All split parts of one oversized element share the same list.
    merged_caption_ids: list[str] = field(default_factory=list)
    # Set only on chunks produced by splitting one oversized chunk into
    # several (see MAX_CHUNK_TOKENS). Points at the element_id of the
    # chunk's primary source element, so every split part is traceably
    # "originally one element" even though they now have distinct
    # chunk_index values. None for every chunk that wasn't split.
    split_from_element_id: str | None = None


def _bbox_center(element: ParsedElement) -> tuple[float, float]:
    b = element.bbox
    return ((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)


def _bbox_distance(a: ParsedElement, b: ParsedElement) -> float:
    ax, ay = _bbox_center(a)
    bx, by = _bbox_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _resolve_tier2_captions(
    elements: list[ParsedElement],
) -> dict[str, str | None]:
    """Tier-2 heuristic caption matching, for captions the parser couldn't
    explicitly link (association_method == "none").

    Only used when Docling gave no explicit link (Tier 1) — see
    parser.py's ParsedElement.association_method. Scope is deliberately
    narrow: same page, prefer adjacency in reading order, break ties by
    bbox distance. No cross-page matching, no fuzzy text matching against
    caption content ("Table 3:" etc.) — those would be reasonable
    extensions but are out of scope for this pass.

    A fully equal tie (same reading-order distance AND same bbox distance
    — only possible with >=2 equidistant candidates) falls back to
    Python's min() stability, i.e. whichever candidate appears first in
    `elements` order. Not documented as a deliberate design choice beyond
    this comment; revisit if it ever produces a wrong match in practice.

    Multiple captions competing for the same unclaimed target resolve
    greedily: whichever caption is processed first (document order) claims
    it via `claimed_targets`, and later captions no longer see that target
    as a candidate. This is not a globally optimal assignment — see
    .agent/MEMORY.md §Anti-patterns for why that's an acceptable
    simplification for now.

    Returns a dict of caption element_id -> matched table/figure element_id
    (or None if nothing plausible was found).
    """
    resolution: dict[str, str | None] = {}
    claimed_targets: set[str] = set()

    # Only elements with no Tier-1 caption already are eligible Tier-2
    # targets — a table/figure Docling already linked a caption to doesn't
    # need (or want) a second, guessed one.
    candidates_by_page: dict[int, list[tuple[int, ParsedElement]]] = {}
    for index, element in enumerate(elements):
        if element.element_type in (ElementType.TABLE, ElementType.FIGURE) and not element.associated_caption_ids:
            candidates_by_page.setdefault(element.page_number, []).append((index, element))

    for caption_index, caption in enumerate(elements):
        if caption.element_type != ElementType.CAPTION or caption.association_method != "none":
            continue

        page_candidates = [
            (index, el) for index, el in candidates_by_page.get(caption.page_number, []) if el.element_id not in claimed_targets
        ]
        if not page_candidates:
            resolution[caption.element_id] = None
            continue

        # Primary key: distance in reading order (adjacency). Secondary
        # key (tie-break): bbox distance. This naturally prefers an
        # immediately-adjacent table/figure over a same-page one several
        # elements away, and falls back to physical proximity only when
        # reading-order distance alone can't decide.
        def sort_key(candidate: tuple[int, ParsedElement]) -> tuple[int, float]:
            index, el = candidate
            return (abs(index - caption_index), _bbox_distance(caption, el))

        best_index, best_element = min(page_candidates, key=sort_key)
        resolution[caption.element_id] = best_element.element_id
        claimed_targets.add(best_element.element_id)

    return resolution


def _split_by_sentence(text: str, max_tokens: int) -> list[str]:
    sentences = _SENTENCE_BOUNDARY.split(text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}" if current else sentence
        if current and _approx_token_count(candidate) > max_tokens:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    # A single sentence that alone exceeds max_tokens is kept whole — never
    # split mid-sentence, even if that means this one part stays oversized.
    return parts or [text]


def _split_text_by_paragraph_or_sentence(text: str, max_tokens: int) -> list[str]:
    """Split text into parts each <= max_tokens (approx), preferring
    paragraph (blank-line) boundaries; falls back to sentence boundaries
    only for a paragraph that alone is still oversized. Never splits
    mid-sentence."""
    if _approx_token_count(text) <= max_tokens:
        return [text]

    paragraphs = text.split("\n\n")
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and _approx_token_count(candidate) > max_tokens:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
        if _approx_token_count(current) > max_tokens:
            sentence_parts = _split_by_sentence(current, max_tokens)
            parts.extend(sentence_parts[:-1])
            current = sentence_parts[-1]
    if current:
        parts.append(current)
    return parts or [text]


def _split_markdown_table_by_rows(markdown: str, max_tokens: int) -> list[str]:
    """Split a markdown table (optionally preceded by caption/title lines)
    into row groups, each <= max_tokens (approx). The header + separator
    row (and any caption/preamble lines before the table) are repeated on
    every part so each split chunk is a self-contained, valid markdown
    table on its own — never splits a row."""
    if _approx_token_count(markdown) <= max_tokens:
        return [markdown]

    lines = markdown.split("\n")
    header_idx = next((i for i, line in enumerate(lines) if line.strip().startswith("|")), None)
    if header_idx is None or header_idx + 1 >= len(lines):
        # Not a recognizable markdown table (shouldn't happen for our own
        # table content) — fail safe rather than crash.
        return _split_text_by_paragraph_or_sentence(markdown, max_tokens)

    preamble = lines[:header_idx]
    header_line = lines[header_idx]
    separator_line = lines[header_idx + 1]
    body_lines = [line for line in lines[header_idx + 2 :] if line.strip()]

    prefix_lines = [*preamble, header_line, separator_line]
    parts: list[str] = []
    current_rows: list[str] = []
    for row in body_lines:
        candidate = "\n".join([*prefix_lines, *current_rows, row])
        if current_rows and _approx_token_count(candidate) > max_tokens:
            parts.append("\n".join([*prefix_lines, *current_rows]))
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_rows:
        parts.append("\n".join([*prefix_lines, *current_rows]))
    # A single row (plus header) that alone exceeds max_tokens is kept
    # whole — never split mid-row.
    return parts or [markdown]


def _split_oversized(chunk: Chunk, elements: list[ParsedElement]) -> list[Chunk]:
    if _approx_token_count(chunk.content) <= MAX_CHUNK_TOKENS:
        return [chunk]

    if chunk.element_type == ElementType.TABLE:
        parts = _split_markdown_table_by_rows(chunk.content, MAX_CHUNK_TOKENS)
    else:
        parts = _split_text_by_paragraph_or_sentence(chunk.content, MAX_CHUNK_TOKENS)

    if len(parts) <= 1:
        return [chunk]

    parent_element_id = elements[chunk.source_element_indices[0]].element_id
    return [
        Chunk(
            chunk_index=0,  # reassigned in the final numbering pass
            element_type=chunk.element_type,
            page_numbers=chunk.page_numbers,
            source_element_indices=chunk.source_element_indices,
            content=part,
            image=chunk.image,
            association_method=chunk.association_method,
            merged_caption_ids=chunk.merged_caption_ids,
            split_from_element_id=parent_element_id,
        )
        for part in parts
    ]


class Chunker:
    def chunk(self, parsed_document: ParsedDocument) -> list[Chunk]:
        elements = parsed_document.elements
        tier2_resolution = _resolve_tier2_captions(elements)

        # element_id -> list of (index, caption element) merged into it,
        # combining both tiers. Built up front so the main pass can just
        # look up "does this table/figure have caption(s) to merge" once.
        captions_for_target: dict[str, list[tuple[int, ParsedElement]]] = {}
        caption_methods: dict[str, str] = {}
        for index, element in enumerate(elements):
            if element.element_type != ElementType.CAPTION:
                continue
            if element.association_method == "explicit":
                # Tier 1: the parser already knows which table/figure(s)
                # claimed this caption (the reverse of associated_caption_ids).
                for target in elements:
                    if element.element_id in target.associated_caption_ids:
                        captions_for_target.setdefault(target.element_id, []).append((index, element))
                        caption_methods[target.element_id] = "explicit"
            else:
                matched = tier2_resolution.get(element.element_id)
                if matched is not None:
                    captions_for_target.setdefault(matched, []).append((index, element))
                    caption_methods.setdefault(matched, "heuristic")

        chunks: list[Chunk] = []
        pending_indices: list[int] = []
        pending_texts: list[str] = []
        pending_pages: list[int] = []
        pending_type: ElementType | None = None

        def flush_pending() -> None:
            if not pending_indices:
                return
            chunks.append(
                Chunk(
                    chunk_index=0,  # reassigned in the final numbering pass
                    element_type=pending_type,
                    page_numbers=sorted(set(pending_pages)),
                    source_element_indices=list(pending_indices),
                    content="\n".join(pending_texts),
                )
            )
            pending_indices.clear()
            pending_texts.clear()
            pending_pages.clear()

        already_merged_caption_ids: set[str] = set()

        for index, element in enumerate(elements):
            if element.element_type == ElementType.CAPTION:
                if element.element_id in already_merged_caption_ids:
                    continue  # consumed by a table/figure chunk below
                if element.association_method == "none" and tier2_resolution.get(element.element_id) is None:
                    # Tier 2 found nothing plausible — standalone chunk.
                    flush_pending()
                    chunks.append(
                        Chunk(
                            chunk_index=0,  # reassigned in the final numbering pass
                            element_type=ElementType.CAPTION,
                            page_numbers=[element.page_number],
                            source_element_indices=[index],
                            content=element.content,
                            association_method="unmatched",
                        )
                    )
                # else: this caption will be consumed when its matched
                # table/figure is processed below (it appears either
                # before or after that element in document order).
                continue

            if element.element_type in (ElementType.TABLE, ElementType.FIGURE):
                flush_pending()
                caption_items = captions_for_target.get(element.element_id, [])
                caption_texts = [c.content for _, c in caption_items]
                caption_ids = [c.element_id for _, c in caption_items]
                for cid in caption_ids:
                    already_merged_caption_ids.add(cid)

                source_indices = [index] + [ci for ci, _ in caption_items]
                pages = sorted({element.page_number, *(c.page_number for _, c in caption_items)})

                if element.element_type == ElementType.FIGURE:
                    content = "\n".join(caption_texts)  # image itself carried separately, in `image`
                    image = element.content
                else:
                    content = "\n\n".join([*caption_texts, element.content]) if caption_texts else element.content
                    image = None

                chunks.append(
                    Chunk(
                        chunk_index=0,  # reassigned in the final numbering pass
                        element_type=element.element_type,
                        page_numbers=pages,
                        source_element_indices=source_indices,
                        content=content,
                        image=image,
                        association_method=caption_methods.get(element.element_id),
                        merged_caption_ids=caption_ids,
                    )
                )
                continue

            if element.element_type not in _GROUPABLE_TYPES:
                continue  # not modeled for chunking (shouldn't occur — parser already filters)

            candidate_texts = pending_texts + [element.content]
            if pending_indices and _approx_token_count("\n".join(candidate_texts)) > TOKEN_BUDGET:
                flush_pending()

            if not pending_indices:
                pending_type = element.element_type
            pending_indices.append(index)
            pending_texts.append(element.content)
            pending_pages.append(element.page_number)

        flush_pending()

        # Splitting happens after association is resolved, not before — a
        # table/figure that got a caption merged (Tier 1 or Tier 2) above
        # is only now checked against MAX_CHUNK_TOKENS and split if needed,
        # so caption-merging logic above never has to know about splitting.
        expanded: list[Chunk] = []
        for c in chunks:
            expanded.extend(_split_oversized(c, elements))

        for i, c in enumerate(expanded):
            c.chunk_index = i

        return expanded
