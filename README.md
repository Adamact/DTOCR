# DTOCR

Turn scanned or digital PDFs of tabular documents — invoices, delivery notes, statements — into structured spreadsheets.

You draw regions once on a sample PDF, DTOCR saves that as a reusable **template**, and every later document of the same shape is rendered, OCR'd, parsed into rows, and exported to Excel. Table regions get an automatic row/column grid so each cell is read individually instead of as one blob of text.

> **Status:** working prototype. The desktop app is the supported path; the HTTP API and worker are an early headless variant of the same pipeline.

---

## How it works

```
PDF ──> pypdfium2 render ──> region crops ──> grid detection ──> TrOCR ──> parser ──> .xlsx
          (configurable DPI)                    (OpenCV)                   (registry)
```

1. **Render** — `pypdfium2` rasterises each page at a configurable DPI.
2. **Crop** — regions saved on the template are cut out of the page image.
3. **Detect grid** — for table regions, OpenCV finds row and column boundaries from whitespace gaps, with a content-based fallback. Boundaries can be hand-adjusted in the editor and are cached on the template.
4. **Recognise** — cells are batched through [TrOCR](https://huggingface.co/microsoft/trocr-base-printed) (`microsoft/trocr-base-printed`).
5. **Parse** — a named parser turns recognised lines into typed records, carrying incomplete rows across page breaks.
6. **Export** — records are written to `.xlsx` via `openpyxl`, one sheet per parser.

## Features

- **Visual template editor** (Tkinter) — draw regions, label them, tune grid detection with live preview
- **Automatic table grid detection** with manual override and per-template caching
- **Versioned templates** in SQLite — every save is a new immutable version
- **GPU acceleration** — CUDA or DirectML, with automatic CPU fallback
- **Pluggable parsers** — register a function to support a new document layout
- **Excel export** with per-parser column ordering and row filtering
- **Headless pipeline** usable from a script, an HTTP API, or a background worker

---

## Requirements

- Python 3.10+
- ~2 GB disk for the TrOCR model weights (downloaded from Hugging Face on first run)
- Optional: an NVIDIA GPU (CUDA) or a DirectML-capable GPU on Windows

## Installation

DTOCR is imported as a package named `DTOCR`, so clone it into a directory of that name:

```bash
git clone https://github.com/Adamact/DTOCR.git
cd DTOCR

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e .
```

For the optional headless HTTP API and worker:

```bash
pip install -e ".[web]"
```

On Windows, for DirectML GPU acceleration:

```bash
pip install torch-directml
```

## Usage

### Desktop app

```bash
python -m DTOCR
```

1. **Load PDF** — open a representative document.
2. Draw regions and assign each a label. Use a **grid region** for tables.
3. Adjust detection settings until the row/column overlay matches the table.
4. **Save Template** — pick the parser that matches the layout.
5. **Run OCR Pipeline** — results are written next to the PDF as `.xlsx`.

The database is created automatically at `data/DTOCR.sqlite3` on first run.

### Headless

```bash
python -m DTOCR.scripts.test_headless_pipeline path/to/document.pdf --template-id 1
```

### HTTP API

```bash
uvicorn DTOCR.web.api.main:app --reload          # API
python -m DTOCR.web.worker.worker                # worker (needs Redis)
```

| Method | Endpoint             | Purpose                        |
| ------ | -------------------- | ------------------------------ |
| `GET`  | `/health`            | Liveness probe                 |
| `POST` | `/documents`         | Upload a PDF                   |
| `POST` | `/runs`              | Start a pipeline run           |
| `GET`  | `/runs/{id}`         | Run status                     |
| `GET`  | `/runs/{id}/results` | Extracted records              |
| `GET`  | `/runs/{id}/summary` | Row counts and detected fields |

## Configuration

| Variable           | Default                    | Purpose                                         |
| ------------------ | -------------------------- | ----------------------------------------------- |
| `DTOCR_DB_PATH`    | `data/DTOCR.sqlite3`       | SQLite template database location               |
| `DTOCR_OCR_DEVICE` | `auto`                     | `auto`, `cpu`, `cuda`, or `directml`            |
| `REDIS_URL`        | `redis://127.0.0.1:6379/0` | Queue backend for the worker                    |

`auto` prefers CUDA, then DirectML, then CPU.

---

## Adding a parser

Parsers live in `parsers/vendor_parsers.py` and are registered in `parsers/registry.py`. A parser receives OCR'd rows plus a `carry` record from the previous page, and returns completed records plus any still-open record:

```python
def parse_my_layout_with_carry(
    rows: list[list[str]], carry: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    ...
```

Register it so templates can select it by name:

```python
_PARSERS["my-layout"] = ParserDefinition(
    name="my-layout",
    parse_with_carry=vendor_parsers.parse_my_layout_with_carry,
    preferred_columns=["delivery_date", "vehicle_id", "amount"],
    sheet_name="Parsed_MyLayout",
    row_filter=lambda row: bool(row.get("vehicle_id")),
)
```

Two layout parsers ship as worked examples: **`layout-a`** (keyed `field: value` lines under a numbered item) and **`layout-b`** (positional columns with trailing surcharge rows). **`grid-only`** skips line-item parsing and exports detected cells as-is.

## Project layout

```
core/       rendering, grid detection, OCR pipeline, logging
gui/        Tkinter application and template editor
db/         SQLite access, schema, repositories
services/   template service and factory
parsers/    layout parsers and the parser registry
shared/     dataclass schemas for templates and runs
web/        FastAPI app and RQ worker (headless path)
scripts/    development and benchmarking utilities
```

## Development

```bash
pip install -e ".[dev]"

python -m pytest tests -q     # from the directory above the checkout: python -m pytest DTOCR/tests -q
ruff check .
python scripts/privacy_check.py
```

`scripts/privacy_check.py` fails if an absolute local path, a database file, or a
key/env file is tracked. It also reads optional terms from `.privacy-denylist`
(gitignored) so names that should never be committed can be checked without
writing them into the repository.

## Known limitations

- The desktop GUI is Tkinter — functional, not pretty.
- `trocr-base-printed` targets printed text; handwriting needs a different checkpoint.
- Grid detection assumes whitespace-separated tables; heavily ruled or shaded tables may need manual boundaries.
- Layout A reads quantity as the first number on the item line, so digits inside a
  product name (`Crushed stone 0-32`) are picked up instead of the real quantity.
- Layout A stops a `key: value` field at the first `;`, so trailing fields on the
  same line (a contact name after a site) are not captured.
- Only the parsers are covered by tests; the GUI and OCR pipeline are verified manually.

## License

[MIT](LICENSE) © 2026 Adam Älgamo
