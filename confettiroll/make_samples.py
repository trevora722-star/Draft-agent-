"""Generate the three sample Heirloom keepsake books with Gemini images.

Creates photorealistic sample photos for three demo events (a vineyard
wedding, a 50th birthday pool party, and a high school prom), then composes
each into a real keepsake book PDF with book.py — the same code paying
customers use. The PDFs land in static/samples/ and the landing page links
them automatically when they exist.

Usage:
    GEMINI_API_KEY=... python make_samples.py            # all three books
    GEMINI_API_KEY=... python make_samples.py wedding    # just one

Images are cached in samples_work/ (gitignored) so re-runs only generate
what's missing. Model override: GEMINI_IMAGE_MODEL (default
gemini-2.5-flash-image).
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

import book as book_maker

BASE_DIR = Path(__file__).resolve().parent
WORK = BASE_DIR / "samples_work"
OUT = BASE_DIR / "static" / "samples"

MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

STYLE = (
    "Photorealistic candid event photograph, shot on a full-frame camera with a "
    "35mm lens, natural skin tones, authentic joyful expressions, tasteful "
    "documentary wedding-photographer style. No text, no watermarks, no borders. "
)

EVENTS = {
    "wedding": {
        "slug": "vineyard-wedding",
        "title": "Anna & James",
        "date": "Saturday, June 20th",
        "accent": "#7d8c6f",
        "venue": "Silver Oak Vineyard",
        "recap": (
            "The vines were heavy with early summer when Anna and James said "
            "their vows beneath the white tent, and by the time the string "
            "lights came on, nobody was sitting down. There was the cake - "
            "and the cake on faces - the fathers and mothers who danced, the "
            "friends who toasted a little too long and laughed a lot too "
            "loud. These are the photos everyone took: the day as the people "
            "who love them saw it."
        ),
        "setting": (
            "Setting: an elegant summer wedding at a vineyard, rows of grapevines, "
            "a large white marquee tent, warm string lights. Guests are mostly "
            "caucasian with a few Black and East Asian guests, a natural variety "
            "of ages and body types, dressed in summer formal attire. "
        ),
        "cast": (
            "THE WEDDING PARTY (the same individuals in every photo): "
            "GROOM James - early 30s, tall and lean, short dark-brown hair, "
            "neatly trimmed short beard, navy-blue suit, white boutonniere. "
            "BRIDE Anna - early 30s, honey-blonde hair in a loose updo, ivory "
            "fitted lace gown, long veil. "
            "FIVE GROOMSMEN in matching light-grey suits with blush ties, each "
            "clearly different: a stocky broad-shouldered man with ginger hair "
            "and a full red beard; a tall slim Black man with a short fade "
            "haircut; a medium-build man with a shaved head and round glasses; "
            "an East Asian man with side-parted black hair; a heavyset "
            "clean-shaven man with light-brown curls. "
            "FIVE BRIDESMAIDS in mismatched dusty-rose gowns, each clearly "
            "different: a Black woman with a braided updo; an East Asian woman "
            "with long straight black hair; a curvy blonde woman with "
            "shoulder-length waves; a petite brunette with a pixie cut; a tall "
            "red-haired woman with freckles. "
        ),
        "anchor": "The whole wedding party",
        "shots": [
            ("The ceremony among the vines", "Wide shot of an outdoor wedding ceremony between rows of grapevines at golden hour, guests seated on white chairs, the couple at a floral arch, white tent in the background."),
            ("First look under the oaks", "A bride in a lace gown and a groom in a navy suit sharing an emotional first look at the edge of a vineyard, soft afternoon light."),
            ("The groom and his groomsmen", "A groom in a navy suit laughing with his five groomsmen in matching grey suits among the vineyard rows, varied body types, candid laughter, one adjusting his boutonniere."),
            ("The bride and her bridesmaids", "A bride with her five bridesmaids in mismatched dusty-rose gowns, walking together along a vineyard path holding bouquets, laughing, one Black bridesmaid and one Asian bridesmaid among them, varied body types."),
            ("The whole wedding party", "Full wedding party group photo, bride and groom in the center with groomsmen and bridesmaids, in front of a white tent at a vineyard, relaxed and joyful, diverse ages and body types."),
            ("Golden hour on the terrace", "The bride and groom alone among the grapevines at sunset, warm backlight, her veil catching the breeze."),
            ("Cutting the cake", "A bride and groom cutting a three-tier white wedding cake together under string lights inside a white tent, guests blurred in the background raising glasses."),
            ("A bite of cake, mostly on target", "A laughing bride playfully feeding wedding cake to the groom, a smudge of frosting on his cheek, string lights bokeh behind them."),
            ("The father-daughter dance", "A bride dancing with her silver-haired father under warm string lights in a white tent, both smiling, guests watching softly out of focus."),
            ("The mother-son dance", "A groom dancing with his mother in an elegant blue dress under string lights, tender candid moment, tent reception."),
            ("The first dance", "A bride and groom's first dance alone on the dance floor under a canopy of string lights in a white tent at night, guests holding sparklers around the edge."),
            ("The toasts", "A best man giving a toast with a raised champagne glass at a long harvest table inside a white tent, guests of varied ages and ethnicities laughing, warm candlelight."),
            ("The bouquet flies", "A bride tossing her bouquet over her shoulder to a laughing crowd of guests with arms raised, inside a warmly lit white tent at night."),
            ("Dancing under the lights", "A packed dance floor at a vineyard wedding at night, guests of all ages and a few different ethnicities dancing joyfully under string lights, motion and laughter."),
        ],
    },
    "birthday": {
        "slug": "fiftieth-birthday",
        "title": "Maria's 50th",
        "date": "Saturday, August 8th",
        "accent": "#2a7fa8",
        "venue": "",
        "recap": (
            "Fifty candles, one pool, and a backyard full of the people Maria "
            "loves most. There was the cannonball nobody saw coming (and "
            "everybody has a photo of), the grill that never went cold, the "
            "toast that turned into three. This is the whole afternoon, from "
            "every phone at the party - proof that fifty looks a lot like "
            "laughing until it hurts."
        ),
        "setting": (
            "Setting: a lively 50th birthday party at a suburban house with a "
            "backyard swimming pool and a large green lawn, summer afternoon. "
            "Guests are a warm mix of family and friends, mostly caucasian with "
            "a few Black and Latino guests, all ages and a natural variety of "
            "body types, casual summer party clothes. "
        ),
        "shots": [
            ("The backyard, ready to go", "Wide shot of a backyard birthday party: swimming pool, large lawn, gold '50' balloons over a drinks table, guests mingling in the sun."),
            ("Fifty candles", "A joyful 50-year-old woman blowing out candles on a birthday cake with a gold '50' number topper, family leaning in around her at a patio table, warm afternoon light. The cake decoration shows only the number 50 - no names or written words anywhere."),
            ("The cannonball", "A man mid-air in a cannonball jump over a backyard pool, huge splash rising, guests laughing and shielding themselves at the pool edge, summer party."),
            ("Poolside laughter", "Friends of varied ages and body types laughing hard together on pool loungers with drinks, one wiping tears of laughter, backyard summer party."),
            ("At the grill", "A smiling man grilling burgers at a backyard barbecue, smoke rising, a friend leaning in with a plate, pool and balloons in the background."),
            ("The toast", "A circle of guests raising mixed drinks and lemonade toward the birthday woman in the middle of a green lawn, golden hour light, genuine laughter."),
            ("Kids own the pool", "Children playing on colorful floaties in a backyard pool, splashing, adults chatting at the edge, bright summer day."),
            ("Four generations", "A relaxed multi-generation family group photo on a green lawn, from grandparents to toddlers, a few Black and Latino family members, everyone laughing between poses."),
            ("The gift that got a scream", "A woman laughing with her hand over her mouth as she opens a gift, friends around her reacting with delight, backyard party table with cake and balloons."),
            ("Lawn games at dusk", "Guests playing cornhole on a large lawn at dusk, string lights coming on over the patio, pool glowing behind them, relaxed summer evening."),
        ],
    },
    "prom": {
        "slug": "grad-gala-prom",
        "title": "Riverside High Prom",
        "date": "Friday, May 29th",
        "accent": "#5b3b8c",
        "venue": "",
        "recap": (
            "One night, one ballroom, and four years of friendships dressed to "
            "the nines. The corsages survived the pinning, the king and queen "
            "got their crowns, and the dance floor did not rest. Every photo "
            "here was taken by the chaperones and picked by the students - "
            "each one keeping their own night, their own way."
        ),
        "setting": (
            "Setting: a high school prom in a decorated ballroom with purple and "
            "silver balloons, a lit dance floor, and a photo backdrop. Students "
            "are 18-year-old high school seniors, a healthy mix of ethnicities - "
            "Black, white, Asian, Latino, South Asian - in a variety of dress "
            "styles: ball gowns, sleek modern dresses, classic tuxedos, colorful "
            "suits. All attire is formal and tasteful. "
        ),
        "shots": [
            ("The grand entrance", "Students arriving at prom on a red carpet into a decorated ballroom, a diverse group in gowns, tuxedos and colorful suits, excited faces, evening light."),
            ("The squad", "A group of eight diverse high school seniors posing together at prom - ball gowns, modern dresses, classic tuxedos and a burgundy suit - laughing between formal poses in front of a balloon arch."),
            ("The corsage moment", "A close-up of a young man in a tuxedo carefully pinning a white corsage on his date's wrist, her sequined gown catching the light, friends watching and smiling."),
            ("Backdrop portraits", "Two prom couples posing at a glittering photo backdrop with purple and silver balloons, one couple striking a dramatic pose, the other laughing."),
            ("The dance floor", "A packed prom dance floor under colored lights, diverse high school seniors dancing with arms up, gowns and suits in motion, confetti in the air."),
            ("Crowning the king and queen", "A prom king and queen being crowned on stage, sashes and crowns, cheering diverse crowd of students in formal wear below the stage lights."),
            ("The table that wouldn't stop laughing", "A round banquet table of diverse students in formal wear laughing hard together over sparkling juice, ballroom lights bokeh behind them."),
            ("The slow dance", "Couples slow dancing under a mirror ball, soft purple light, a diverse group of high school seniors, tender and formal."),
            ("Photo booth chaos", "A group of students in formal wear crammed into a photo booth frame with feather boas and oversized glasses, mid-laugh, prom decorations around."),
            ("The jump", "A group jump shot of diverse students in gowns and tuxedos outside the venue at dusk, everyone mid-air, city lights behind, pure joy."),
        ],
    },
}


def _extract_image(payload: dict) -> bytes | None:
    for cand in payload.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def generate_image(prompt: str, api_key: str, retries: int = 3,
                   reference: bytes | None = None) -> bytes | None:
    url = API.format(model=MODEL)
    parts: list = []
    if reference is not None:
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(reference).decode(),
        }})
    parts.append({"text": prompt})
    body = {"contents": [{"parts": parts}]}
    for attempt in range(retries):
        try:
            res = httpx.post(
                url, params={"key": api_key}, json=body, timeout=120,
            )
            if res.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            res.raise_for_status()
            img = _extract_image(res.json())
            if img:
                return img
        except httpx.HTTPError as exc:
            print(f"    retry after error: {exc}")
            time.sleep(5 * (attempt + 1))
    return None


def build_event(key: str, api_key: str) -> None:
    spec = EVENTS[key]
    photos_dir = WORK / spec["slug"]
    photos_dir.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # Character consistency: generate the anchor shot (the full group photo)
    # first, then pass it as a reference image to every other shot so the
    # same people appear on every page.
    reference = None
    cast = spec.get("cast", "")
    anchor_caption = spec.get("anchor")
    if anchor_caption:
        idx = next(i for i, (c, _) in enumerate(spec["shots"], 1) if c == anchor_caption)
        caption, scene = spec["shots"][idx - 1]
        dest = photos_dir / f"{spec['slug']}-{idx:02d}.jpg"
        if not dest.exists():
            print(f"  [anchor] {caption}…")
            raw = generate_image(STYLE + spec["setting"] + cast + scene, api_key)
            if raw is None:
                sys.exit("couldn't generate the anchor group shot - try again")
            Image.open(io.BytesIO(raw)).convert("RGB").save(dest, "JPEG", quality=90)
        reference = dest.read_bytes()

    photos = []
    for idx, (caption, scene) in enumerate(spec["shots"], 1):
        photo_id = f"{spec['slug']}-{idx:02d}"
        dest = photos_dir / f"{photo_id}.jpg"
        if not dest.exists():
            print(f"  [{idx}/{len(spec['shots'])}] {caption}…")
            if reference is not None:
                prompt = (
                    STYLE + spec["setting"] + cast +
                    "The reference photo shows this exact wedding party. Photograph "
                    "THE SAME PEOPLE - identical faces, hairstyles, and outfits as "
                    "the reference - in this new scene: " + scene
                )
            else:
                prompt = STYLE + spec["setting"] + scene
            raw = generate_image(prompt, api_key, reference=reference)
            if raw is None:
                print("    ! couldn't generate, skipping")
                continue
            Image.open(io.BytesIO(raw)).convert("RGB").save(dest, "JPEG", quality=90)
        photos.append({
            "id": photo_id,
            "type": "photo",
            "caption": caption,
            "uploader": "",
            "uploaded_at": idx,  # keeps the story order
            "quality": 8,
        })

    if len(photos) < 4:
        print(f"  !! only {len(photos)} images for {key} - not building the book")
        return
    pdf = OUT / f"{spec['slug']}-sample-book.pdf"
    pages = book_maker.generate_book(
        pdf, spec["title"], spec["date"], photos, photos_dir,
        recap=spec["recap"], venue_name=spec["venue"], accent=spec["accent"],
        credit="A sample Heirloom keepsake book — made with AI-generated demonstration photos",
    )
    print(f"  -> {pdf.name}: {pages} photo pages")


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY to generate the sample books.")
    wanted = sys.argv[1:] or list(EVENTS)
    for key in wanted:
        if key not in EVENTS:
            sys.exit(f"Unknown event '{key}' - choose from {', '.join(EVENTS)}")
        print(f"== {key} ==")
        build_event(key, api_key)


if __name__ == "__main__":
    main()
