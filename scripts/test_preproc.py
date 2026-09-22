"""Run preprocessing/annotation steps on a single crop image (no OCR).

Usage:
  python scripts/test_preproc.py --image <image_path> [--label data_field_1] [--outdir <output_dir>]

This will call TemplateService._split_data_field_region and save preproc/ and annotated/ images
into the chosen output_dir (or a temp dir) and print the paths of created files and returned metadata.
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from DTOCR.core.logging import configure_logging
from DTOCR.services.template_service import TemplateService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test preprocessing and annotation on a single image")
    parser.add_argument("--image", required=True, help="Path to crop image to preprocess")
    parser.add_argument("--label", default="data_field_test", help="Label to use (used for filenames)")
    parser.add_argument("--outdir", default=None, help="Directory to write outputs (default: temp)")
    parser.add_argument("--divisor", default=None, type=int, help="Optional word kernel divisor to pass in (smaller = more merging)")
    parser.add_argument("--no-save", action="store_true", help="Do not save cell images to disk; return image bytes in metadata instead")
    args = parser.parse_args(argv)

    configure_logging(logging.DEBUG)

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"ERROR: image '{img_path}' not found")
        return 2

    outdir = Path(args.outdir) if args.outdir else Path(tempfile.mkdtemp(prefix="dtocr_preproc_test_"))
    outdir.mkdir(parents=True, exist_ok=True)

    # TemplateService requires a template_repo but _split_data_field_region doesn't use it; pass None
    service = TemplateService(template_repo=None)  # type: ignore[arg-type]

    print(f"Running preprocessing on: {img_path}")
    print(f"Writing outputs to: {outdir}")

    subcrops = service._split_data_field_region(str(img_path), args.label, 1, outdir, word_kernel_divisor=args.divisor, save_crops=(not args.no_save))

    print("\nReturned subcrops metadata:")
    for i, s in enumerate(subcrops, start=1):
        print(f"  {i}: label={s.get('label')} row={s.get('row')} col={s.get('col')} path={s.get('path')}")
        if s.get("preproc_images"):
            print(f"     preproc_images: {s.get('preproc_images')}")
        if s.get("annotated_image"):
            print(f"     annotated_image: {s.get('annotated_image')}")

    # List files in outdir
    print("\nFiles created in output directory:")
    for p in sorted(outdir.rglob("*")):
        if p.is_file():
            try:
                size = p.stat().st_size
            except Exception:
                size = None
            print(f"  {p.relative_to(outdir)} ({size} bytes)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
