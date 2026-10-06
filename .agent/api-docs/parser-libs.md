# Parser libraries: pdfplumber, python-docx, python-pptx, selectolax, pytesseract

**Status:** inventory only. Versions come from `apps/api/uv.lock` and usage from `apps/api/services/parser.py` (2026-10-06). **Not yet checked against live upstream docs.** Run `/api-check` for the library you're touching before changing parser code, then replace this note with a `**Verified:**` line.

These are local libraries, not network APIs, so the risk is a signature or behaviour change on upgrade, not an outage. `pyproject.toml` caps each one below its next minor/major version.

---

## Locked versions

| Package | Locked | Range in `pyproject.toml` | License |
|---|---|---|---|
| `pdfplumber` | 0.11.10 (on `pdfminer.six` 20260107) | `>=0.11,<0.12` | MIT |
| `python-docx` | 1.2.0 | `>=1.1,<2.0` | MIT |
| `python-pptx` | 1.0.2 | `>=1.0,<2.0` | MIT |
| `selectolax` | 0.4.11 | `>=0.4,<0.5` | MIT |
| `pytesseract` | 0.3.13 | `>=0.3.13,<0.4` | Apache-2.0 |

`pytesseract` also needs the system `tesseract-ocr` binary (installed by `apps/api/Dockerfile`; locally see `docs/DEVELOPMENT.md`). Set `TESSERACT_CMD` if it isn't on `PATH`.

---

## API surface this project depends on

### pdfplumber (PDF)
- `pdfplumber.open(BytesIO(bytes))` as a context manager; `pdf.pages`, `page.page_number` (1-indexed)
- `page.chars`: per-character dicts (text, font size/name, x0/top/x1/bottom). The heading and line heuristics are built on these because pdfplumber has no line or paragraph primitive.
- `page.find_tables()` → each `table.extract()` (rows of cells) and `table.bbox`. Detection is per page, with no merging of tables that continue across pages (MEMORY.md §Open questions 2026-08-02).
- `page.images`: image bboxes for figure detection
- `page.crop(bbox).to_image(resolution=150).original`: a PIL image of a figure, or of the whole page for OCR

### python-docx (DOCX)
- `docx.Document(BytesIO(bytes))`, `document.paragraphs`, `paragraph.text`, `paragraph.style.name` (heading, list, and caption detection by style name), and `document.tables`
- DOCX has no pagination: every element gets `page_number = 1` and a zero-size bbox (see SCHEMA.md)

### python-pptx (PPTX)
- `pptx.Presentation(BytesIO(bytes))`, `presentation.slides`, `slide.shapes`
- `shape.shape_type` (picture detection), `shape.image.blob`, `shape.has_table` / `shape.table`, `shape.has_text_frame` / `shape.text_frame.paragraphs`
- `shape.is_placeholder` and `shape.placeholder_format.type` compared against `PP_PLACEHOLDER` TITLE/CENTER_TITLE for headings. SUBTITLE is deliberately treated as body text.
- `shape.left/top/width/height` are in EMU and converted to points (÷ 12700)

### selectolax (HTML)
- `HTMLParser(bytes)`, `tree.css("h1, …, p, li, table, figcaption, caption")`, `node.css("tr")`, `node.text(deep=True, separator=" ")`
- Images in HTML are not fetched, so HTML documents produce no figure chunks

### pytesseract (OCR tier 3)
- `pytesseract.image_to_string(pil_image, timeout=seconds)`. The default `timeout=0` means no timeout, so the parser always passes one (`OCR_TIMEOUT_MS`).

---

## Upgrade checklist
1. Read the upstream changelog for the target version.
2. `cd apps/api && uv lock --upgrade-package <name>`, then run `uv run pytest tests/test_parser.py tests/test_parser_rewrite.py tests/test_chunker.py`. The rewrite tests include peak-memory assertions that guard Render's 512MB limit.
3. Update the table above and add a CHANGELOG entry.
