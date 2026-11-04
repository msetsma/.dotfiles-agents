"""Normalize a watch product photo: cut out the background and center it on a fixed canvas.

Every output has the same shape: a square transparent PNG with the watch
trimmed to its visible pixels, scaled to fit inside the padding, and centered.

Usage (the input is an untrusted download; run isolated with ``-I``):

    uv run --no-project --python 3.12 --with "rembg[cpu]" --with pillow \
        python -I normalize_image.py <input> <output.png> [--size 1200] [--padding 0.08]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PIL import Image


__all__ = ['fit_on_canvas', 'main', 'normalize']

log = logging.getLogger('normalize_image')

DEFAULT_SIZE = 1200
DEFAULT_PADDING = 0.08
MAX_PADDING = 0.4


class NormalizeError(Exception):
    """The image cannot be normalized (unreadable, empty after cutout, bad options)."""


def remove_background(image: Image.Image) -> Image.Image:
    """Cut the subject out with rembg; returns RGBA."""
    from rembg import remove  # heavy import (onnxruntime); load only when used

    result = remove(image)
    if not isinstance(result, Image.Image):
        raise NormalizeError('rembg returned no image')
    return result.convert('RGBA')


def fit_on_canvas(cutout: Image.Image, size: int, padding: float) -> Image.Image:
    """Trim to visible pixels, scale to fit inside the padding, center on a transparent square."""
    if size <= 0 or not 0 <= padding < MAX_PADDING:
        raise NormalizeError(f'bad canvas options: size={size}, padding={padding}')
    rgba = cutout.convert('RGBA')
    box = rgba.getchannel('A').getbbox()
    if box is None:
        raise NormalizeError('image is fully transparent after background removal')
    subject = rgba.crop(box)
    inner = round(size * (1 - 2 * padding))
    scale = min(inner / subject.width, inner / subject.height)
    resized = subject.resize(
        (max(1, round(subject.width * scale)), max(1, round(subject.height * scale))), Image.Resampling.LANCZOS
    )
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return canvas


def normalize(source: Path, target: Path, *, size: int, padding: float, cut: bool) -> Path:
    """Read ``source``, optionally remove its background, and write the canvas PNG to ``target``."""
    try:
        with Image.open(source) as opened:
            opened.load()
            image = opened.convert('RGBA')
    except (OSError, Image.DecompressionBombError) as exc:
        raise NormalizeError(f'cannot read image {source}: {exc}') from exc
    cutout = remove_background(image) if cut else image
    result = fit_on_canvas(cutout, size, padding)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target, format='PNG', optimize=True)
    log.info('wrote %s (%dx%d)', target, size, size)
    return target


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--size', type=int, default=DEFAULT_SIZE)
    parser.add_argument('--padding', type=float, default=DEFAULT_PADDING)
    parser.add_argument('--keep-background', action='store_true', help='skip rembg (already transparent)')
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.target.suffix.lower() != '.png':
        log.error('target must be a .png file: %s', args.target)
        return 2
    try:
        normalize(args.source, args.target, size=args.size, padding=args.padding, cut=not args.keep_background)
    except NormalizeError as exc:
        log.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
