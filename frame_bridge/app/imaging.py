"""Turns an uploaded JPG/PNG into a 3840x2160 JPEG for The Frame, plus a gallery thumbnail."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

TARGET_SIZE = (3840, 2160)
THUMB_WIDTH = 640
MAX_SOURCE_PIXELS = 80_000_000  # about 10000x8000; larger files are refused
# MPO: JPEG with extra frames (iPhone HDR gain map, depth, camera stereo); we use the main frame
ALLOWED_FORMATS = {"JPEG", "PNG", "MPO"}
FIT_MODES = ("fit", "crop")

Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS


class ImageError(ValueError):
    """A problem with the uploaded file that the user can fix; the message is shown as is."""


@dataclass
class PreparedImage:
    jpeg: bytes
    thumb: bytes
    width: int
    height: int
    source_width: int
    source_height: int


def _open(data: bytes) -> Image.Image:
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()
    except Image.DecompressionBombError:
        raise ImageError("Das Bild ist zu groß (maximal ca. 80 Megapixel).") from None
    except Exception:
        raise ImageError("Die Datei ist kein lesbares JPG- oder PNG-Bild.") from None
    if fmt not in ALLOWED_FORMATS:
        raise ImageError(f"Format {fmt} wird nicht unterstützt, bitte JPG oder PNG verwenden.")
    img = Image.open(io.BytesIO(data))
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)  # main image of an MPO
    img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _fit(img: Image.Image) -> Image.Image:
    """Whole picture visible; the free space is filled with a blurred, darkened copy."""
    tw, th = TARGET_SIZE
    background = ImageOps.fit(img, (tw // 8, th // 8), Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(6)).resize(TARGET_SIZE, Image.Resampling.BICUBIC)
    background = Image.blend(background, Image.new("RGB", TARGET_SIZE, (0, 0, 0)), 0.35)
    fg = ImageOps.contain(img, TARGET_SIZE, Image.Resampling.LANCZOS)
    background.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
    return background


def _crop(img: Image.Image) -> Image.Image:
    """Fills the screen; edges are cut off around the centre."""
    return ImageOps.fit(img, TARGET_SIZE, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def prepare(data: bytes, mode: str = "fit") -> PreparedImage:
    if mode not in FIT_MODES:
        raise ImageError("Modus muss 'fit' (Einpassen) oder 'crop' (Zuschneiden) sein.")
    img = _open(data)
    sw, sh = img.size
    if sw < 64 or sh < 64:
        raise ImageError("Das Bild ist zu klein (mindestens 64×64 Pixel).")
    out = _fit(img) if mode == "fit" else _crop(img)

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92, optimize=True, progressive=False, subsampling=0)
    thumb = out.copy()
    thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH), Image.Resampling.LANCZOS)
    tbuf = io.BytesIO()
    thumb.save(tbuf, "JPEG", quality=82)
    return PreparedImage(buf.getvalue(), tbuf.getvalue(), out.width, out.height, sw, sh)
