#!/usr/bin/env python3
"""Build a compact 16:9 PDF from one workshop job's proof slides."""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from PIL import Image
from reportlab.pdfgen import canvas


def build(job_dir: Path, output: Path, *, width: int, quality: int) -> None:
    slide_paths = sorted((job_dir / "slides").glob("slide_*_proof.png"))
    if not slide_paths:
        raise SystemExit(f"No numbered proof slides found under {job_dir / 'slides'}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="pitch-synthase-pdf-"))
    try:
        page_width = 960
        page_height = 540
        pdf = canvas.Canvas(
            str(output),
            pagesize=(page_width, page_height),
            pageCompression=1,
        )
        for index, slide_path in enumerate(slide_paths, start=1):
            image = Image.open(slide_path).convert("RGB")
            target_height = round(width * 9 / 16)
            image.thumbnail((width, target_height), Image.Resampling.LANCZOS)
            jpeg_path = temp_dir / f"slide_{index:02d}.jpg"
            image.save(
                jpeg_path,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=2,
            )
            pdf.drawImage(
                str(jpeg_path),
                0,
                0,
                width=page_width,
                height=page_height,
                preserveAspectRatio=True,
                anchor="c",
            )
            pdf.showPage()
        pdf.save()
    finally:
        shutil.rmtree(temp_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--quality", type=int, default=72)
    args = parser.parse_args()
    build(args.job_dir, args.output, width=args.width, quality=args.quality)


if __name__ == "__main__":
    main()
