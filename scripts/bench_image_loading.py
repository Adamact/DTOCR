"""Benchmark image loading: compare serial vs threaded loading using Pillow.

Usage:
    python scripts/bench_image_loading.py --count 200 --workers 8
"""
import argparse
import io
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

# Create a sample RGB image in memory
img = Image.new("RGB", (800, 600), color=(255, 255, 255))
buf = io.BytesIO()
img.save(buf, format="PNG")
image_bytes = buf.getvalue()


def load_image_from_bytes(crop):
    try:
        im = Image.open(io.BytesIO(crop["image_bytes"]))
        im = im.convert("RGB")
        return True
    except Exception:
        return False


def serial_load(crops):
    start = time.perf_counter()
    count = 0
    for c in crops:
        ok = load_image_from_bytes(c)
        if ok:
            count += 1
    end = time.perf_counter()
    return end - start, count


def threaded_load(crops, workers: int):
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(load_image_from_bytes, crops))
    end = time.perf_counter()
    return end - start, sum(1 for r in results if r)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=500, help="number of crops to simulate")
    p.add_argument("--workers", type=int, default=8, help="thread pool workers")
    args = p.parse_args()

    crops = [{"image_bytes": image_bytes} for _ in range(args.count)]

    t_serial, ok1 = serial_load(crops)
    t_threaded, ok2 = threaded_load(crops, args.workers)

    print(f"Serial load time: {t_serial:.3f}s, loaded: {ok1}")
    print(f"Threaded load time ({args.workers} workers): {t_threaded:.3f}s, loaded: {ok2}")
    speedup = t_serial / t_threaded if t_threaded > 0 else float('inf')
    print(f"Speedup: {speedup:.2f}x")