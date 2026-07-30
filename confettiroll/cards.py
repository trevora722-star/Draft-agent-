"""Printable QR table cards — four per US-Letter page, with cut guides.

Each card carries an optional round photo (the couple, or a corporate
logo), the event title in the event's accent color, a QR code to the
gallery, and the join instructions. Built with reportlab + qrcode and
returned as PDF bytes for direct download.
"""

from __future__ import annotations

import io
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = letter  # 612 x 792 pt


def _hex_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _circle_photo(photo_path: Path, size_px: int = 600) -> ImageReader:
    """Center-crop the photo into a circle with transparent corners."""
    img = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
    img = ImageOps.fit(img, (size_px, size_px), Image.LANCZOS)
    mask = Image.new("L", (size_px, size_px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size_px, size_px], fill=255)
    out = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    buf.seek(0)
    return ImageReader(buf)


def _qr_image(url: str) -> ImageReader:
    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return ImageReader(buf)


def generate_cards(title: str, url: str, accent: str,
                   note_lines: list[str], photo_path: Path | None = None,
                   footer: str = "") -> bytes:
    """Render one Letter page with four identical cards; returns PDF bytes."""
    accent_rgb = _hex_rgb(accent or "#7d8c6f")
    ink = (0.20, 0.18, 0.16)
    soft = (0.48, 0.45, 0.42)

    qr_reader = _qr_image(url)
    photo_reader = _circle_photo(photo_path) if photo_path else None

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"{title} — table cards")

    half_w, half_h = PAGE_W / 2, PAGE_H / 2

    # dashed cut guides
    c.setDash(3, 4)
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.line(half_w, 0, half_w, PAGE_H)
    c.line(0, half_h, PAGE_W, half_h)
    c.setDash()

    for ox, oy in ((0, half_h), (half_w, half_h), (0, 0), (half_w, 0)):
        inset = 22
        # accent frame
        c.setStrokeColorRGB(*accent_rgb)
        c.setLineWidth(1.4)
        c.roundRect(ox + inset, oy + inset, half_w - 2 * inset,
                    half_h - 2 * inset, 10)

        cx = ox + half_w / 2
        top = oy + half_h - inset
        y = top - 18

        if photo_reader is not None:
            photo_size = 86
            c.drawImage(photo_reader, cx - photo_size / 2, y - photo_size,
                        photo_size, photo_size, mask="auto")
            y -= photo_size + 12
        else:
            c.setFillColorRGB(*accent_rgb)
            c.setFont("Times-Italic", 15)
            c.drawCentredString(cx, y - 14, "✦ ✦ ✦")
            y -= 32

        c.setFillColorRGB(*ink)
        c.setFont("Times-Italic", 17 if len(title) <= 26 else 13)
        c.drawCentredString(cx, y, title)
        y -= 20

        c.setFillColorRGB(*accent_rgb)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawCentredString(cx, y, "S C A N   T O   S H A R E   Y O U R   P H O T O S")
        y -= 10

        qr_size = 96
        c.drawImage(qr_reader, cx - qr_size / 2, y - qr_size, qr_size, qr_size)
        y -= qr_size + 14

        c.setFillColorRGB(*soft)
        c.setFont("Helvetica", 9)
        for line in note_lines:
            c.drawCentredString(cx, y, line)
            y -= 12

        if footer:
            c.setFont("Helvetica", 6.5)
            c.setFillColorRGB(0.65, 0.62, 0.60)
            c.drawCentredString(cx, oy + inset + 8, footer)

    c.showPage()
    c.save()
    return buf.getvalue()
