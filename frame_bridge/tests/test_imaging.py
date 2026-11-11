import io

import pytest
from PIL import Image

from app.imaging import ImageError, prepare
from conftest import make_jpeg


def px(jpeg, xy):
    return Image.open(io.BytesIO(jpeg)).convert("RGB").getpixel(xy)


def test_fit_keeps_whole_image_portrait():
    p = prepare(make_jpeg(900, 1600, (200, 30, 30)), "fit")
    assert (p.width, p.height) == (3840, 2160)
    centre = px(p.jpeg, (1920, 1080))
    assert centre[0] > 150 and centre[1] < 80  # the picture sits in the middle
    edge = px(p.jpeg, (20, 1080))
    assert edge[0] < centre[0]  # sides are the darkened blur


def test_crop_fills_screen():
    p = prepare(make_jpeg(900, 1600, (30, 160, 30)), "crop")
    assert (p.width, p.height) == (3840, 2160)
    assert px(p.jpeg, (20, 20))[1] > 120


def test_png_with_alpha_is_flattened():
    buf = io.BytesIO()
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(buf, "PNG")
    p = prepare(buf.getvalue(), "crop")
    assert px(p.jpeg, (100, 100)) >= (240, 240, 240)


def test_thumb_small():
    p = prepare(make_jpeg(), "fit")
    assert Image.open(io.BytesIO(p.thumb)).width <= 640


@pytest.mark.parametrize("data", [b"", b"hello", make_jpeg(fmt="GIF"), make_jpeg(32, 32)])
def test_rejects(data):
    with pytest.raises(ImageError):
        prepare(data, "fit")
