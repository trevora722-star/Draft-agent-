"""Keepsake book generator — a print-ready PDF album of an event.

Composes a square 8x8" book from the album's photos and metadata:
cover (accent-colored, venue-aware), a foreword page carrying the AI-written
story of the day, one photo per page with its caption and uploader credit,
and a closing thank-you page naming everyone who contributed. The couple
downloads the PDF to send to family and the wedding party, or hands it to
any print shop / photo-book service.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

PAGE = 576  # 8in x 8in in points
MARGIN = 54
MAX_PHOTOS = 150


# Photos from modern phones decode to ~70 MB of raw RGB each; embedding them
# untouched holds every page's raster until save and can OOM a small server.
# Decode downscaled (JPEG draft mode), fix EXIF rotation, and hand reportlab
# a small JPEG buffer instead - it embeds JPEG bytes directly.
BOOK_EDGE = 1800  # ~225 dpi on an 8x8" page


def _photo_reader(path: Path, max_edge: int = BOOK_EDGE):
    try:
        with Image.open(path) as img:
            img.draft("RGB", (max_edge, max_edge))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_edge, max_edge))
            rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=85)
        size = rgb.size
        rgb.close()
        buf.seek(0)
        return ImageReader(buf), size
    except Exception:
        return None


def _hex_rgb(color: str) -> tuple[float, float, float]:
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (ValueError, IndexError):
        return (0.91, 0.36, 0.54)


def _diamonds(c: canvas.Canvas, y: float, color, size: float = 5) -> None:
    c.setFillColorRGB(*color)
    for dx in (-24, 0, 24):
        x = PAGE / 2 + dx
        p = c.beginPath()
        p.moveTo(x, y + size)
        p.lineTo(x + size, y)
        p.lineTo(x, y - size)
        p.lineTo(x - size, y)
        p.close()
        c.drawPath(p, fill=1, stroke=0)


def _wrapped(c: canvas.Canvas, text: str, font: str, size: float, y: float,
             leading: float, color, max_width: float | None = None) -> float:
    c.setFont(font, size)
    c.setFillColorRGB(*color)
    for line in simpleSplit(text, font, size, max_width or (PAGE - 2 * MARGIN)):
        c.drawCentredString(PAGE / 2, y, line)
        y -= leading
    return y


def generate_book(dest: Path, title: str, event_date: str, photos: list[dict],
                  photos_dir: Path, recap: str | None = None,
                  venue_name: str = "", accent: str = "#7d8c6f",
                  credit: str = "") -> int:
    """Write the PDF to dest; returns the number of photo pages."""
    photos = [p for p in photos if p.get("type") != "video"]
    photos.sort(key=lambda p: p.get("uploaded_at", 0))
    if len(photos) > MAX_PHOTOS:
        # Keep the best-scored photos but preserve chronological order.
        ranked = sorted(photos, key=lambda p: p.get("quality", 5), reverse=True)
        keep = {p["id"] for p in ranked[:MAX_PHOTOS]}
        photos = [p for p in photos if p["id"] in keep]

    accent_rgb = _hex_rgb(accent)
    ink = (0.17, 0.15, 0.13)
    soft = (0.45, 0.42, 0.4)
    cream = (0.992, 0.984, 0.969)

    c = canvas.Canvas(str(dest), pagesize=(PAGE, PAGE))
    c.setTitle(title)

    # ---- cover -------------------------------------------------------------
    c.setFillColorRGB(*accent_rgb)
    c.rect(0, 0, PAGE, PAGE, fill=1, stroke=0)
    _diamonds(c, PAGE - 150, (1, 1, 1))
    y = _wrapped(c, title, "Times-Italic", 34, PAGE - 220, 40, (1, 1, 1))
    if event_date:
        c.setFont("Helvetica", 13)
        c.setFillColorRGB(1, 1, 1)
        c.drawCentredString(PAGE / 2, y - 8, event_date)
    c.setFont("Helvetica", 11)
    c.setFillColorRGB(1, 1, 1)
    c.drawCentredString(PAGE / 2, 120, "A  K E E P S A K E  A L B U M")
    if venue_name:
        c.setFont("Times-Italic", 12)
        c.drawCentredString(PAGE / 2, 92, f"Hosted at {venue_name}")
    c.showPage()

    # ---- foreword ----------------------------------------------------------
    if recap:
        c.setFillColorRGB(*cream)
        c.rect(0, 0, PAGE, PAGE, fill=1, stroke=0)
        _diamonds(c, PAGE - 90, accent_rgb, 4)
        _wrapped(c, "The Story of the Day", "Times-Italic", 22, PAGE - 140, 26, ink)
        _wrapped(c, recap, "Times-Roman", 11.5, PAGE - 190, 17, ink,
                 PAGE - 2 * MARGIN - 30)
        c.showPage()

    # ---- photo pages -------------------------------------------------------
    pages = 0
    for index, meta in enumerate(photos):
        matches = list(photos_dir.glob(f"{meta['id']}.*"))
        if not matches:
            continue
        loaded = _photo_reader(matches[0])
        if loaded is None:
            continue
        reader, (width, height) = loaded
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, PAGE, PAGE, fill=1, stroke=0)
        box = PAGE - 2 * MARGIN
        caption_room = 40
        scale = min(box / width, (box - caption_room) / height)
        w, h = width * scale, height * scale
        x = (PAGE - w) / 2
        y = MARGIN + caption_room + (box - caption_room - h) / 2
        try:
            c.drawImage(reader, x, y, w, h,
                        preserveAspectRatio=True, anchor="c")
        except Exception:
            continue
        finally:
            del reader, loaded
        bits = []
        if meta.get("caption"):
            bits.append(meta["caption"])
        if meta.get("uploader"):
            bits.append(f"shared by {meta['uploader']}")
        if bits:
            c.setFont("Times-Italic", 10.5)
            c.setFillColorRGB(*soft)
            line = " — ".join(bits)
            for wrapped_line in simpleSplit(line, "Times-Italic", 10.5, box)[:2]:
                c.drawCentredString(PAGE / 2, y - 20, wrapped_line)
                y -= 14
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*soft)
        c.drawCentredString(PAGE / 2, 24, str(index + 1))
        c.showPage()
        pages += 1

    # ---- thanks ------------------------------------------------------------
    c.setFillColorRGB(*cream)
    c.rect(0, 0, PAGE, PAGE, fill=1, stroke=0)
    _diamonds(c, PAGE - 120, accent_rgb, 4)
    _wrapped(c, "With love and thanks", "Times-Italic", 22, PAGE - 170, 26, ink)
    _wrapped(c, "to everyone who captured and shared these moments",
             "Times-Roman", 12, PAGE - 205, 17, soft)
    uploaders = sorted({p["uploader"] for p in photos if p.get("uploader")})
    if uploaders:
        _wrapped(c, " · ".join(uploaders), "Times-Roman", 11.5, PAGE - 260, 17, ink,
                 PAGE - 2 * MARGIN - 20)
    if credit:
        c.setFont("Helvetica", 8.5)
        c.setFillColorRGB(*soft)
        c.drawCentredString(PAGE / 2, 40, credit)
    c.showPage()

    c.save()
    return pages
