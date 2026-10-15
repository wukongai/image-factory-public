#!/usr/bin/env python3
"""Compose a raw visual and deterministic text into a non-overlapping layout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from image_factory.errors import ImageFactoryError
from image_factory.mixed_layout import compose_from_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic adaptive image/text mixed layout")
    parser.add_argument("--manifest", required=True, help="Compiled image-asset-manifest.json")
    parser.add_argument("--input-image", required=True, help="Raw visual image without generated text")
    parser.add_argument("--output", required=True, help="Final PNG path")
    parser.add_argument("--font", default="", help="Optional Chinese font path")
    args = parser.parse_args()
    try:
        result_path = compose_from_manifest(
            manifest_path=Path(args.manifest).expanduser().resolve(),
            source_image=Path(args.input_image).expanduser().resolve(),
            output_path=Path(args.output).expanduser().resolve(),
            font_path=args.font,
        )
    except (ImageFactoryError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
