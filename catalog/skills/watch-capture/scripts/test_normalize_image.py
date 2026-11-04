"""Tests for the canvas step (no rembg needed): ``uv run --no-project --with pillow --with pytest pytest``."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from normalize_image import NormalizeError, fit_on_canvas, main


def _subject(width: int, height: int) -> Image.Image:
    image = Image.new('RGBA', (width + 40, height + 10), (0, 0, 0, 0))
    image.paste(Image.new('RGBA', (width, height), (200, 10, 10, 255)), (30, 5))
    return image


def test_canvas_is_square_and_subject_centered() -> None:
    out = fit_on_canvas(_subject(300, 600), 1200, 0.1)
    assert out.size == (1200, 1200)
    left, top, right, bottom = out.getchannel('A').getbbox()
    assert (top, bottom) == (120, 1080)
    assert abs((left + right) / 2 - 600) <= 1


def test_wide_subject_fills_width() -> None:
    left, _, right, _ = fit_on_canvas(_subject(800, 200), 1000, 0.05).getchannel('A').getbbox()
    assert (left, right) == (50, 950)


def test_transparent_input_is_rejected() -> None:
    with pytest.raises(NormalizeError):
        fit_on_canvas(Image.new('RGBA', (10, 10), (0, 0, 0, 0)), 100, 0.1)


@pytest.mark.parametrize(('size', 'padding'), [(0, 0.1), (100, -0.1), (100, 0.5)])
def test_bad_options_are_rejected(size: int, padding: float) -> None:
    with pytest.raises(NormalizeError):
        fit_on_canvas(_subject(10, 10), size, padding)


def test_main_keep_background_writes_png(tmp_path: Path) -> None:
    source = tmp_path / 'in.png'
    _subject(50, 100).save(source)
    target = tmp_path / 'out' / 'w.png'
    assert main([str(source), str(target), '--size', '200', '--keep-background']) == 0
    with Image.open(target) as written:
        assert written.size == (200, 200)


def test_main_rejects_non_png_target(tmp_path: Path) -> None:
    assert main([str(tmp_path / 'in.png'), str(tmp_path / 'out.jpg')]) == 2


def test_main_reports_unreadable_source(tmp_path: Path) -> None:
    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not an image')
    assert main([str(bad), str(tmp_path / 'o.png'), '--keep-background']) == 1
