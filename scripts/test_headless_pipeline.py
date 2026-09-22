"""Smoke-test the headless pipeline against a PDF, without starting the GUI.

Usage:
    python -m DTOCR.scripts.test_headless_pipeline <pdf-path> [--template-id N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from DTOCR.services.service_factory import create_template_service  # type: ignore
from DTOCR.web.worker.pipeline_adapter import run_pipeline  # type: ignore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path, help="PDF to run through the pipeline.")
    parser.add_argument(
        "--template-id",
        type=int,
        default=None,
        help="Template to apply. Defaults to the first template in the database.",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--num-beams", type=int, default=1)
    parser.add_argument("--excel-path", type=Path, default=None)
    args = parser.parse_args()

    if not args.pdf_path.is_file():
        print(f"PDF not found: {args.pdf_path}", file=sys.stderr)
        return 2

    service = create_template_service()

    templates = service.list_templates()
    print("Templates in DB:", templates)

    if not templates:
        print(
            "No templates found in the DB used by service_factory.\n"
            "Create and save a template in the GUI first, or set DTOCR_DB_PATH "
            "to point at an existing database.",
            file=sys.stderr,
        )
        return 1

    if args.template_id is not None:
        template_id = args.template_id
    else:
        first = templates[0]
        template_id = int(first.get("id") or first.get("template_id"))
    print("Using template_id:", template_id)

    template_payload = service.load_latest_template_payload(template_id=template_id)
    if not template_payload:
        print(
            f"Template exists but has no saved payload versions. template_id={template_id}",
            file=sys.stderr,
        )
        return 1

    result = run_pipeline(
        template_payload=template_payload,
        pdf_path=str(args.pdf_path),
        excel_path=str(args.excel_path) if args.excel_path else None,
        save_crops=True,
        batch_size=args.batch_size,
        num_beams=args.num_beams,
    )

    print("OK. Keys:", list(result.keys()))
    print("OCR output path:", result.get("ocr_output_path"))
    print("Excel output path:", result.get("excel_output_path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
