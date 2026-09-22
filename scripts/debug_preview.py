"""Debug helper: run TemplateService._split_data_field_region on a generated image and print outputs.

Usage: python scripts/debug_preview.py
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

# Ensure project root is importable when running the script directly
# Insert the parent directory of project root so 'DTOCR' package dir is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None

from DTOCR.core.logging import configure_logging
from DTOCR.services.template_service import TemplateService


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Debug preprocessing and annotation on a generated image")
    parser.add_argument("--divisor", default=10, type=int, help="Optional word kernel divisor to pass in (smaller = more merging)")
    parser.add_argument("--no-save", action="store_true", help="Do not save cell images to disk; return image bytes in metadata instead")
    args = parser.parse_args()

    configure_logging(logging.DEBUG)
    # create a simple test image containing a few words
    if Image is None:
        print("Pillow not installed - please install pillow to run this test")
        return 2
    img = Image.new("L", (800, 200), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "Hello world this is a test", fill=0)
    draw.text((10, 60), "Another line with words here", fill=0)

    outdir = Path(tempfile.mkdtemp(prefix="dtocr_debug_preview_"))
    img_path = outdir / "test.png"
    img.save(img_path)
    print("Wrote test image to", img_path)

    service = TemplateService(template_repo=None)  # type: ignore[arg-type]
    try:
        subcrops = service._split_data_field_region(str(img_path), "data_field_debug", 1, outdir, word_kernel_divisor=args.divisor, save_crops=(not args.no_save))
    except Exception as exc:
        print("_split_data_field_region raised:", exc)
        import traceback

        traceback.print_exc()
        return 1

    print("Returned subcrops:")
    for s in subcrops:
        print(s)

    print("Files in outdir:")
    for p in sorted(outdir.rglob("*")):
        print(p.relative_to(outdir))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
