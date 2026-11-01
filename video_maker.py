"""
AI Video Maker - turns a JSON script into a vertical (9:16) Short.

Pipeline: script JSON -> AI voice per scene -> visuals + synced captions + sound effects -> MP4

Backgrounds:
    "gradient"  animated color background (default)
    "full"      your gameplay clip full screen, visuals on a glass panel
    "split"     visuals on top, gameplay clip on the bottom half

Command line use:
    python video_maker.py examples/moon.json
    python video_maker.py my_script.json --background backgrounds/parkour.mp4 --layout split
"""

import argparse
import asyncio
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import wave
from array import array

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
import imageio_ffmpeg

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT_BOLD = os.path.join(HERE, "fonts", "Poppins-Bold.ttf")
FONT_MED = os.path.join(HERE, "fonts", "Poppins-Medium.ttf")
IMAGES_DIR = os.path.join(HERE, "images")
BACKGROUNDS_DIR = os.path.join(HERE, "backgrounds")

SCENE_PAD = 0.25          # seconds of breathing room after each scene
VISUAL_CENTER_Y = 760     # middle of the visual area
CAPTION_Y = 1390          # middle of the caption area (gradient / full layouts)
SPLIT_Y = 960             # where the gameplay starts in split layout
HIGHLIGHT = "#FFE14D"     # color for *highlighted* words and the spoken caption word
ACCENTS = ["#FFE14D", "#4DE1FF", "#FF6B9A", "#7CFF8A", "#C49BFF", "#FFA94D"]
SR = 44100


# --------------------------------------------------------------------------- #
# Fonts and emoji
# --------------------------------------------------------------------------- #

_font_cache = {}


def font(path, size):
    key = (path, int(size))
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, int(size))
    return _font_cache[key]


EMOJI_FONTS = [
    ("/System/Library/Fonts/Apple Color Emoji.ttc", [160, 96, 64]),
    ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", [109]),
    ("/usr/share/fonts/noto/NotoColorEmoji.ttf", [109]),
]
_emoji_font = None
_emoji_cmap = set()
_emoji_img_cache = {}


def _load_emoji_font():
    global _emoji_font, _emoji_cmap
    if _emoji_font is not None:
        return _emoji_font
    for path, sizes in EMOJI_FONTS:
        if not os.path.exists(path):
            continue
        for s in sizes:
            try:
                f = ImageFont.truetype(path, s)
                _emoji_font = f
                try:
                    from fontTools.ttLib import TTFont, TTCollection
                    tt = TTCollection(path).fonts[0] if path.endswith(".ttc") else TTFont(path)
                    _emoji_cmap = set(tt.getBestCmap().keys())
                except Exception:
                    _emoji_cmap = set()
                return _emoji_font
            except Exception:
                continue
    _emoji_font = False
    return _emoji_font


def is_emoji_token(tok):
    if not tok:
        return False
    cp = ord(tok[0])
    if cp < 0x2000:
        return False
    _load_emoji_font()
    if _emoji_cmap:
        return cp in _emoji_cmap
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF


def emoji_image(tok, height):
    """Render an emoji (or emoji sequence) as an RGBA image of the given height."""
    key = (tok, int(height))
    if key in _emoji_img_cache:
        return _emoji_img_cache[key]
    f = _load_emoji_font()
    img = None
    if f:
        size = f.size
        canvas = Image.new("RGBA", (size * 2 * max(1, len(tok)), size * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        try:
            d.text((size // 4, size // 4), tok, font=f, embedded_color=True)
            bbox = canvas.getbbox()
            if bbox:
                img = canvas.crop(bbox)
        except Exception:
            img = None
    if img is None:  # fallback: plain text glyph
        img = render_text_token(tok, font(FONT_BOLD, height), "#FFFFFF", 0)
    scale = height / img.height
    img = img.resize((max(1, int(img.width * scale)), int(height)), Image.LANCZOS)
    _emoji_img_cache[key] = img
    return img


def strip_emoji(text):
    return " ".join(t for t in text.split() if not is_emoji_token(t)).strip()


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #

def hex_rgb(h, default=(40, 60, 140)):
    try:
        h = str(h).strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def mix(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def render_text_token(tok, f, fill, stroke):
    bbox = f.getbbox(tok, stroke_width=stroke)
    w, h = bbox[2] - bbox[0] + 4, bbox[3] - bbox[1] + 4
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((-bbox[0] + 2, -bbox[1] + 2), tok, font=f, fill=fill,
                             stroke_width=stroke, stroke_fill=(15, 15, 30))
    return img


def shadowed(img, blur=16, offset=12, opacity=0.5):
    """Return img with a soft drop shadow baked in (canvas grows evenly so centering is unchanged)."""
    pad = blur * 2 + offset
    out = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    a = img.getchannel("A").point(lambda v: int(v * opacity))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 255))
    sh.putalpha(a)
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    layer.paste(sh, (pad, pad + offset))
    out = Image.alpha_composite(out, layer.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(img, (pad, pad))
    return out


ARROW_TOKENS = {"→", "->", "➜", "⇒", "=>", "➡", "➔", "⟶", "➝"}


def clean_tokens(text):
    return " ".join("➡️" if t in ARROW_TOKENS else t for t in str(text).split())


def layout_rich(text, size, max_w, path=FONT_BOLD):
    """Word-wrap text that can mix words, emoji and *highlighted* words.
    Returns lines of (kind, token, width, highlighted)."""
    text = clean_tokens(text)
    f = font(path, size)
    space = f.getlength(" ")
    lines, cur, cur_w = [], [], 0
    hl_on = False
    for raw in text.split():
        tok, hl = raw, hl_on
        if tok.startswith("*") and len(tok) > 1:
            hl, hl_on, tok = True, True, tok.lstrip("*")
        if tok.endswith("*"):
            tok, hl_on = tok.rstrip("*"), False
        if not tok:
            continue
        if is_emoji_token(tok):
            w, kind = emoji_image(tok, size * 1.1).width, "emoji"
        else:
            w, kind = f.getlength(tok) + size * 0.12, "text"
        add = w if not cur else w + space
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w, add = [], 0, w
        cur.append((kind, tok, w, hl))
        cur_w += add
    if cur:
        lines.append(cur)
    return lines, space


def render_rich(text, max_w, max_h, max_size=150, min_size=40, color="#FFFFFF",
                path=FONT_BOLD, highlight=None, stroke=None, shadow=True):
    """Render wrapped text (with emoji) as large as fits in max_w x max_h. Returns RGBA.
    highlight=(word_index, color) colors one word (used for captions)."""
    size = max_size
    while True:
        lines, space = layout_rich(text, size, max_w, path)
        line_h = size * 1.2
        widest = max((sum(w for _, _, w, _ in ln) + space * (len(ln) - 1)) for ln in lines) if lines else 0
        if (len(lines) * line_h <= max_h and widest <= max_w) or size <= min_size:
            break
        size -= 6
    f = font(path, size)
    dark_text = sum(hex_rgb(color)) < 300
    if stroke is None:
        stroke = 0 if dark_text else max(3, size // 14)
    total_h = int(len(lines) * line_h) + 20
    img = Image.new("RGBA", (int(max_w), max(1, total_h)), (0, 0, 0, 0))
    y, word_i = 10, 0
    for ln in lines:
        lw = sum(w for _, _, w, _ in ln) + space * (len(ln) - 1)
        x = (max_w - lw) / 2
        for kind, tok, w, hl in ln:
            if kind == "emoji":
                e = emoji_image(tok, size * 1.1)
                img.alpha_composite(e, (int(x), int(y + (line_h - e.height) / 2 - size * 0.05)))
            else:
                fill = color
                if hl:
                    fill = "#E0457B" if dark_text else HIGHLIGHT
                if highlight and word_i == highlight[0]:
                    fill = highlight[1]
                t = render_text_token(tok, f, fill, stroke)
                img.alpha_composite(t, (int(x), int(y + (line_h - t.height) / 2)))
            x += w + space
            word_i += 1
        y += line_h
    bbox = img.getbbox()
    img = img.crop(bbox) if bbox else img
    return shadowed(img, blur=max(6, size // 10), offset=max(4, size // 16), opacity=0.55) \
        if (shadow and not dark_text) else img


def paste_scaled(frame, img, cx, cy, scale=1.0, alpha=1.0):
    if img is None or scale <= 0.01 or alpha <= 0.01:
        return
    if abs(scale - 1.0) > 0.005:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    if alpha < 0.999:
        a = img.getchannel("A").point(lambda v: int(v * alpha))
        img = img.copy()
        img.putalpha(a)
    frame.paste(img, (int(cx - img.width / 2), int(cy - img.height / 2)), img)


def rounded_box(w, h, fill, radius=36, outline=None, outline_w=6):
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill,
                                          outline=outline, width=outline_w if outline else 0)
    return img


def card(w, h, accent=None, radius=40):
    """White card with an accent stripe and a drop shadow."""
    body = Image.new("RGBA", (w, h), (255, 255, 255, 245))
    if accent:
        ImageDraw.Draw(body).rectangle((0, 0, w, 18), fill=hex_rgb(accent) + (255,))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    c.paste(body, (0, 0), mask)
    return c


_orb_cache = {}


def soft_orb(r, color, alpha=90):
    key = (int(r), color, alpha)
    if key not in _orb_cache:
        size = int(r * 3)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((r, r, size - r, size - r), fill=color + (alpha,))
        _orb_cache[key] = img.filter(ImageFilter.GaussianBlur(r * 0.45))
    return _orb_cache[key]


# --------------------------------------------------------------------------- #
# Photos: your images/ folder -> Pixabay (optional free key) -> Openverse (no key)
# --------------------------------------------------------------------------- #

DOWNLOAD_DIR = os.path.join(IMAGES_DIR, "downloaded")
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:60] or "image"


def _http_get(url, headers=None, timeout=20):
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    h = {"User-Agent": "ai-video-maker/1.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def _save_photo(data, out):
    """Check the bytes are a usable photo, then save as JPEG. Returns True on success."""
    import io
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        return False
    if min(img.size) < 400:
        return False
    img.save(out, "JPEG", quality=92)
    return True


def _search_pixabay(query, key):
    import urllib.parse
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": key, "q": query[:100], "image_type": "photo", "safesearch": "true",
        "per_page": 5, "min_width": 800})
    hits = json.loads(_http_get(url).decode()).get("hits", [])
    return [(h.get("largeImageURL") or h.get("webformatURL"), f"{h.get('user', 'Unknown')} (Pixabay)")
            for h in hits if h.get("largeImageURL") or h.get("webformatURL")]


def _search_openverse(query):
    import urllib.parse
    url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode({
        "q": query, "license_type": "commercial", "mature": "false", "page_size": 10})
    results = json.loads(_http_get(url).decode()).get("results", [])
    out = []
    for r in results:
        if not r.get("url") or (r.get("width") and r["width"] < 600):
            continue
        lic = str(r.get("license", "")).upper()
        lic = "CC0" if lic == "CC0" else ("Public domain" if lic == "PDM" else f"CC {lic} {r.get('license_version', '')}")
        title = (r.get("title") or "Photo").strip()[:60]
        out.append((r["url"], f'"{title}" by {r.get("creator") or "Unknown"} ({lic.strip()}, via Openverse)'))
    return out


def find_image(query, pixabay_key="", log=print, online=True):
    """Return (path, credit) for a photo matching query, or (None, None)."""
    query = strip_emoji(str(query)).strip()
    if not query:
        return None, None
    os.makedirs(IMAGES_DIR, exist_ok=True)
    direct = os.path.join(IMAGES_DIR, query)
    if os.path.isfile(direct):
        return direct, None
    # 1. your own photos, matched by file name; earlier downloads count too
    words = set(_slug(query).split("-"))
    best, best_score = None, 0
    for root, _, files in os.walk(IMAGES_DIR):
        for fn in files:
            if not fn.lower().endswith(IMG_EXTS):
                continue
            stem = set(_slug(os.path.splitext(fn)[0]).split("-"))
            score = len(words & stem) / max(1, len(words))
            if root.startswith(DOWNLOAD_DIR):
                score -= 0.01
            if score > best_score:
                best, best_score = os.path.join(root, fn), score
    if best and best_score >= 0.5:
        credit = None
        meta = os.path.splitext(best)[0] + ".credit.txt"
        if os.path.exists(meta):
            credit = open(meta, encoding="utf-8").read().strip() or None
        return best, credit
    if not online:
        return None, None
    # 2. download one (Pixabay first if you have a key, then Openverse)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    out = os.path.join(DOWNLOAD_DIR, _slug(query) + ".jpg")
    key = pixabay_key or os.environ.get("PIXABAY_API_KEY", "")
    sources = ([("Pixabay", lambda: _search_pixabay(query, key))] if key else []) + \
        [("Openverse", lambda: _search_openverse(query))]
    errors = []
    for name, search in sources:
        try:
            candidates = search()
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        for img_url, credit in candidates[:5]:
            try:
                if _save_photo(_http_get(img_url, timeout=30), out):
                    with open(os.path.splitext(out)[0] + ".credit.txt", "w", encoding="utf-8") as f:
                        f.write(credit)
                    return out, credit
            except Exception:
                continue
    if errors:
        log(f"Couldn't search for a photo of '{query}' ({'; '.join(errors)}). Showing text instead.")
    else:
        log(f"No photo found for '{query}'. Showing text instead. Tip: try simpler words, "
            f"or add your own photo to the images folder.")
    return None, None


def test_photos(pixabay_key=""):
    """Quick online check. Returns (ok, message)."""
    msgs, ok = [], False
    if pixabay_key:
        try:
            n = len(_search_pixabay("mountain", pixabay_key))
            msgs.append(f"Pixabay: working ({n} results)")
            ok = ok or n > 0
        except Exception as e:
            msgs.append(f"Pixabay: not working ({e})")
    try:
        n = len(_search_openverse("mountain"))
        msgs.append(f"Openverse: working ({n} results)")
        ok = ok or n > 0
    except Exception as e:
        msgs.append(f"Openverse: not working ({e})")
    return ok, "\n".join(msgs)


# --------------------------------------------------------------------------- #
# Visual types
# --------------------------------------------------------------------------- #

NUM_RE = re.compile(r"^(.*?)(-?\d[\d,]*\.?\d*)(.*)$")


class Visual:
    """draw(frame, t, dur) draws the visual t seconds into its scene.
    sfx(dur) returns [(seconds_into_scene, 'pop'|'ding'), ...] for sound effects."""

    def __init__(self, v):
        self.v = v
        self.content = str(v.get("content", "")).strip()

    def draw(self, frame, t, dur):
        raise NotImplementedError

    def sfx(self, dur):
        return [(0.02, "pop")]


class BigText(Visual):
    def __init__(self, v):
        super().__init__(v)
        self.img = render_rich(self.content or "...", 920, 620, max_size=160)

    def draw(self, frame, t, dur):
        s = 0.6 + 0.4 * ease_out_back(t / 0.35)
        bob = math.sin(t * 2.2) * 8
        paste_scaled(frame, self.img, W / 2, VISUAL_CENTER_Y + bob, s)


def emoji_badge(tok, size, accent):
    """An emoji inside a glowing glass circle."""
    e = emoji_image(tok, size)
    d = int(size * 1.5)
    pad = int(size * 0.35)
    total = d + pad * 2
    acc = hex_rgb(accent)
    glow = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((pad, pad, pad + d, pad + d), outline=acc + (200,), width=int(size * 0.12))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    g = ImageDraw.Draw(glow)
    g.ellipse((pad, pad, pad + d, pad + d), fill=(255, 255, 255, 38), outline=acc + (255,),
              width=max(4, int(size * 0.045)))
    glow.alpha_composite(e, ((total - e.width) // 2, (total - e.height) // 2))
    return glow


class EmojiRow(Visual):
    def __init__(self, v):
        super().__init__(v)
        toks = clean_tokens(self.content).split() or ["❓"]
        n = sum(1 for t in toks if t != "➡️")
        size = 280 if n <= 1 else 190 if n == 2 else 145 if n == 3 else 108 if n == 4 else 88
        self.items = []
        k = 0
        for tok in toks:
            if tok == "➡️":
                self.items.append(shadowed(emoji_image(tok, size * 0.55), 8, 6, 0.4))
            elif is_emoji_token(tok):
                self.items.append(emoji_badge(tok, size, ACCENTS[k % len(ACCENTS)]))
                k += 1
            else:
                self.items.append(render_rich(tok, 900, size, max_size=int(size * 0.75)))
        self.rows, row, row_w, gap = [], [], 0, 10
        for im in self.items:
            if row and row_w + gap + im.width > 1040:
                self.rows.append(row)
                row, row_w = [], 0
            row.append(im)
            row_w += im.width + (gap if len(row) > 1 else 0)
        if row:
            self.rows.append(row)
        self.gap = gap

    def draw(self, frame, t, dur):
        row_hs = [max(im.height for im in r) for r in self.rows]
        total_h = sum(row_hs) + 10 * (len(self.rows) - 1)
        y = VISUAL_CENTER_Y - total_h / 2
        idx = 0
        for r, rh in zip(self.rows, row_hs):
            rw = sum(im.width for im in r) + self.gap * (len(r) - 1)
            x = W / 2 - rw / 2
            for im in r:
                lt = t - idx * 0.22
                s = ease_out_back(lt / 0.35) if lt > 0 else 0
                bob = math.sin(t * 2.4 + idx) * 10
                paste_scaled(frame, im, x + im.width / 2, y + rh / 2 + bob, s)
                x += im.width + self.gap
                idx += 1
            y += rh + 10

    def sfx(self, dur):
        return [(0.02 + i * 0.22, "pop") for i in range(len(self.items))]


class NumberCount(Visual):
    def __init__(self, v):
        super().__init__(v)
        toks = self.content.split()
        emojis = [t for t in toks if is_emoji_token(t)]
        rest = " ".join(t for t in toks if not is_emoji_token(t))
        self.emoji = emoji_badge(emojis[0], 150, ACCENTS[1]) if emojis else None
        m = NUM_RE.match(rest)
        self.label = str(self.v.get("label", "")).strip()
        self.label_img = None
        if self.label:
            txt = render_rich(self.label, 820, 200, max_size=64, path=FONT_MED, color="#1C1C3A")
            pill = rounded_box(txt.width + 80, txt.height + 44, (255, 255, 255, 240), radius=(txt.height + 44) // 2)
            pill.alpha_composite(txt, (40, 22))
            self.label_img = shadowed(pill, 14, 10, 0.45)
        if not m:
            self.prefix, self.num, self.suffix = rest, None, ""
            self.final_img = render_rich(rest or "?", 960, 360, max_size=200, color=HIGHLIGHT)
            return
        self.prefix, raw, self.suffix = m.group(1), m.group(2), m.group(3)
        self.commas = "," in raw
        self.decimals = len(raw.split(".")[1]) if "." in raw else 0
        self.num = float(raw.replace(",", ""))
        size = 230
        while size > 60 and font(FONT_BOLD, size).getlength(rest) > 960:
            size -= 10
        self.size = size
        self.final_img = self._render(rest)
        self._cache = {}

    def _render(self, txt):
        return shadowed(render_text_token(txt, font(FONT_BOLD, self.size), HIGHLIGHT, max(4, self.size // 14)),
                        18, 12, 0.55)

    def _fmt(self, x):
        s = f"{x:,.{self.decimals}f}" if self.commas else f"{x:.{self.decimals}f}"
        return f"{self.prefix}{s}{self.suffix}"

    def draw(self, frame, t, dur):
        y = VISUAL_CENTER_Y
        if self.emoji is not None:
            paste_scaled(frame, self.emoji, W / 2, y - 270, ease_out_back(t / 0.35))
        if self.num is None or t >= 1.1:
            img = self.final_img
        else:
            txt = self._fmt(self.num * ease_out_cubic(t / 1.1))
            img = self._cache.get(txt)
            if img is None:
                img = self._cache[txt] = self._render(txt)
        if self.num is not None:
            pop = 1.0 + 0.1 * max(0.0, 1 - abs(t - 1.15) / 0.15)
        else:
            pop = ease_out_back(t / 0.35)
        paste_scaled(frame, img, W / 2, y, pop)
        if self.label_img is not None:
            a = ease_out_cubic((t - 0.3) / 0.4)
            paste_scaled(frame, self.label_img, W / 2, y + 200 + (1 - a) * 30, 1.0, a)

    def sfx(self, dur):
        return [(0.02, "pop"), (1.1, "ding")] if self.num is not None else [(0.02, "pop")]


class Comparison(Visual):
    def __init__(self, v):
        super().__init__(v)
        parts = [p.strip() for p in re.split(r"\s+vs\.?\s+|\s*\|\s*|\n", self.content, flags=re.I) if p.strip()]
        if len(parts) < 2:
            parts = (parts + ["?", "?"])[:2]
        self.items = []
        for p in parts[:5]:
            m = re.match(r"^(.*?)\s*[:=]\s*(.+)$", p)
            label, val = (m.group(1), m.group(2)) if m else (p, "")
            nm = re.search(r"-?\d[\d,]*\.?\d*", val)
            num = float(nm.group(0).replace(",", "")) if nm else None
            self.items.append({"label": label.strip(), "val": val.strip(), "num": num})
        self.numeric = all(i["num"] is not None for i in self.items) and max(i["num"] for i in self.items) > 0
        if self.numeric:
            n = len(self.items)
            self.bar_h = 110 if n <= 3 else 80
            self.row_h = self.bar_h + 110
            mx = max(i["num"] for i in self.items)
            for k, it in enumerate(self.items):
                it["frac"] = max(0.08, it["num"] / mx)
                it["color"] = hex_rgb(ACCENTS[k % len(ACCENTS)])
                it["label_img"] = render_rich(it["label"], 900, 90, max_size=64)
                it["val_img"] = render_rich(it["val"], 400, 80, max_size=56, color="#1C1C3A")
        else:
            self.cards = []
            for k, it in enumerate(self.items[:2] if len(self.items) == 2 else self.items[:4]):
                txt = (it["label"] + (" " + it["val"] if it["val"] else "")).strip()
                c = card(400, 520, ACCENTS[k % len(ACCENTS)])
                inner = render_rich(txt, 340, 440, max_size=90, color="#1C1C3A")
                c.alpha_composite(inner, ((400 - inner.width) // 2, (520 - inner.height) // 2 + 8))
                self.cards.append(shadowed(c, 20, 14, 0.5))
            vs = Image.new("RGBA", (170, 170), (0, 0, 0, 0))
            ImageDraw.Draw(vs).ellipse((0, 0, 169, 169), fill=(28, 28, 58, 255), outline=hex_rgb(HIGHLIGHT),
                                       width=8)
            t = render_text_token("VS", font(FONT_BOLD, 72), HIGHLIGHT, 0)
            vs.alpha_composite(t, ((170 - t.width) // 2, (170 - t.height) // 2 - 4))
            self.vs = shadowed(vs, 12, 8, 0.5)

    def draw(self, frame, t, dur):
        if self.numeric:
            n = len(self.items)
            top = VISUAL_CENTER_Y - (n * self.row_h) / 2
            d = ImageDraw.Draw(frame)
            for k, it in enumerate(self.items):
                lt = t - k * 0.35
                if lt <= 0:
                    continue
                y = top + k * self.row_h
                a = ease_out_cubic(lt / 0.3)
                lw = it["label_img"].width
                paste_scaled(frame, it["label_img"], 80 + lw / 2, y + 40, 1.0, a)
                full_w = 900 * it["frac"]
                bw = max(full_w * ease_out_cubic(lt / 0.9), self.bar_h)
                by = y + 85
                d.rounded_rectangle((90, by + 8, 90 + bw, by + self.bar_h + 8), radius=self.bar_h // 2,
                                    fill=(0, 0, 0))
                d.rounded_rectangle((90, by, 90 + bw, by + self.bar_h), radius=self.bar_h // 2, fill=it["color"])
                d.rounded_rectangle((90 + 14, by + 10, 90 + bw - 14, by + self.bar_h * 0.38), radius=self.bar_h // 5,
                                    fill=mix(it["color"], (255, 255, 255), 0.35))
                if lt > 0.6:
                    vi = it["val_img"]
                    vx = max(90 + full_w - vi.width / 2 - 30, 90 + vi.width / 2 + 24)
                    paste_scaled(frame, vi, vx, by + self.bar_h / 2, 1.0, ease_out_cubic((lt - 0.6) / 0.3))
        else:
            n = len(self.cards)
            if n == 2:
                for k, x in enumerate([W / 2 - 265, W / 2 + 265]):
                    lt = t - k * 0.3
                    s = 0.9 * ease_out_back(lt / 0.4) if lt > 0 else 0
                    tilt_y = VISUAL_CENTER_Y + (1 - ease_out_cubic(lt / 0.4)) * 80
                    paste_scaled(frame, self.cards[k], x, tilt_y, s)
                if t > 0.5:
                    paste_scaled(frame, self.vs, W / 2, VISUAL_CENTER_Y, ease_out_back((t - 0.5) / 0.3))
            else:
                pos = [(W / 2 - 245, VISUAL_CENTER_Y - 265), (W / 2 + 245, VISUAL_CENTER_Y - 265),
                       (W / 2 - 245, VISUAL_CENTER_Y + 265), (W / 2 + 245, VISUAL_CENTER_Y + 265)]
                for k, c in enumerate(self.cards):
                    lt = t - k * 0.25
                    s = 0.8 * ease_out_back(lt / 0.4) if lt > 0 else 0
                    paste_scaled(frame, c, pos[k][0], pos[k][1], s)

    def sfx(self, dur):
        if self.numeric:
            return [(0.02 + k * 0.35, "pop") for k in range(len(self.items))]
        return [(0.02, "pop"), (0.32, "pop"), (0.5, "ding")] if len(self.cards) == 2 else \
            [(0.02 + k * 0.25, "pop") for k in range(len(self.cards))]


class Diagram(Visual):
    def __init__(self, v):
        super().__init__(v)
        steps = [s.strip() for s in re.split(r"\s*(?:→|->|➜|⇒|➡️|➡|=>)\s*", self.content) if s.strip()]
        self.steps = steps[:6] or ["?"]
        n = len(self.steps)
        self.box_h = 170 if n <= 3 else 140 if n <= 4 else 110
        self.gap = 80 if n <= 4 else 60
        self.boxes = []
        for k, s in enumerate(self.steps):
            box = rounded_box(840, self.box_h, (255, 255, 255, 245), radius=self.box_h // 3)
            d = ImageDraw.Draw(box)
            r = int(self.box_h * 0.32)
            cx, cy = 30 + r, self.box_h // 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hex_rgb(ACCENTS[k % len(ACCENTS)]))
            num = render_text_token(str(k + 1), font(FONT_BOLD, int(r * 1.1)), "#1C1C3A", 0)
            box.alpha_composite(num, (cx - num.width // 2, cy - num.height // 2))
            inner = render_rich(s, 840 - (cx + r) - 50, self.box_h - 30, max_size=68, color="#1C1C3A")
            box.alpha_composite(inner, (cx + r + 25 + (840 - (cx + r) - 50 - inner.width) // 2,
                                        (self.box_h - inner.height) // 2))
            self.boxes.append(shadowed(box, 16, 10, 0.45))

    def _step_t(self, dur):
        return max(0.35, min(0.9, (dur * 0.6) / len(self.boxes)))

    def draw(self, frame, t, dur):
        n = len(self.boxes)
        total = n * self.box_h + (n - 1) * self.gap
        top = VISUAL_CENTER_Y - total / 2
        step_t = self._step_t(dur)
        d = ImageDraw.Draw(frame)
        for k, box in enumerate(self.boxes):
            lt = t - k * step_t
            if lt <= 0:
                continue
            cy = top + k * (self.box_h + self.gap) + self.box_h / 2
            slide = (1 - ease_out_cubic(lt / 0.35)) * 120
            paste_scaled(frame, box, W / 2 + slide, cy, 0.85 + 0.15 * ease_out_back(lt / 0.35),
                         ease_out_cubic(lt / 0.2))
            if k < n - 1 and lt > 0.25:
                ay = cy + self.box_h / 2 + self.gap / 2
                sz = 26 * ease_out_cubic((lt - 0.25) / 0.2)
                d.polygon([(W / 2 - sz, ay - sz * 0.6), (W / 2 + sz, ay - sz * 0.6), (W / 2, ay + sz * 0.7)],
                          fill=(255, 255, 255))

    def sfx(self, dur):
        st = self._step_t(dur)
        return [(0.02 + k * st, "pop") for k in range(len(self.boxes))]


class PhotoCard(Visual):
    """A real photo in a rounded frame with a slow zoom (Ken Burns)."""

    def __init__(self, v, path, max_w=900, max_h=980):
        super().__init__(v)
        src = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        ar = min(max(src.width / src.height, 0.78), 1.3)     # keep the frame between portrait and landscape
        if max_w / ar <= max_h:
            self.cw, self.ch = max_w, int(max_w / ar)
        else:
            self.cw, self.ch = int(max_h * ar), max_h
        self.base = ImageOps.fit(src, (int(self.cw * 1.15), int(self.ch * 1.15)), Image.LANCZOS)
        self.mask = Image.new("L", (self.cw, self.ch), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, self.cw - 1, self.ch - 1), radius=44, fill=255)
        b = 14
        fr = rounded_box(self.cw + 2 * b, self.ch + 2 * b, (255, 255, 255, 255), radius=44 + b)
        self.frame_img = shadowed(fr, 26, 18, 0.55)
        self.pad = (self.frame_img.width - fr.width) // 2
        self.b = b
        label = str(v.get("label", "")).strip()
        self.label_img = None
        if label:
            txt = render_rich(label, 800, 160, max_size=60, color="#1C1C3A")
            pill = rounded_box(txt.width + 70, txt.height + 40, (255, 222, 77, 255), radius=(txt.height + 40) // 2)
            pill.alpha_composite(txt, (35, 20))
            self.label_img = shadowed(pill, 12, 8, 0.45)
        self.direction = random.Random(path).choice([-1, 1])
        self.blur_bg = None

    def background(self):
        """Blurred, darkened full-screen version of the photo (used behind the card)."""
        if self.blur_bg is None:
            bg = ImageOps.fit(self.base, (W // 4, H // 4)).filter(ImageFilter.GaussianBlur(12))
            bg = bg.resize((W, H), Image.BILINEAR)
            self.blur_bg = Image.blend(bg, Image.new("RGB", (W, H), (10, 10, 25)), 0.45)
        return self.blur_bg

    def draw(self, frame, t, dur):
        z = 1.0 + 0.13 * ease_in_out(t / max(dur, 0.1))
        bw, bh = self.base.size
        cw, ch = bw / z, bh / z
        pan = (bw - cw) / 2 + self.direction * (bw - cw) / 2 * ease_in_out(t / max(dur, 0.1)) * 0.6
        crop = self.base.crop((int(pan), int((bh - ch) / 2), int(pan + cw), int((bh + ch) / 2)))
        photo = crop.resize((self.cw, self.ch), Image.BILINEAR)
        comp = self.frame_img.copy()
        comp.paste(photo, (self.pad + self.b, self.pad + self.b - 0), self.mask)
        s = 0.75 + 0.25 * ease_out_back(t / 0.4)
        cy = VISUAL_CENTER_Y - (40 if self.label_img else 0)
        paste_scaled(frame, comp, W / 2, cy, s)
        if self.label_img is not None:
            a = ease_out_cubic((t - 0.35) / 0.3)
            paste_scaled(frame, self.label_img, W / 2, cy + self.ch / 2 + 10, 0.9 + 0.1 * a, a)

    def sfx(self, dur):
        return [(0.02, "pop")] + ([(0.35, "pop")] if self.label_img else [])


def make_visual(v, pixabay_key="", log=print, credits=None, compact=False):
    kind = str(v.get("type", "big_text")).lower().strip()
    content = str(v.get("content", ""))
    try:
        if kind in ("image", "photo"):
            path, credit = find_image(content, pixabay_key, log)
            if path:
                if credit and credits is not None:
                    credits.append(credit)
                return PhotoCard(v, path, max_h=700 if compact else 980)
            fallback = dict(v)
            fallback["content"] = v.get("label") or content
            if any(is_emoji_token(t) for t in content.split()):
                return EmojiRow(v)
            return BigText(fallback)
        if kind == "emoji":
            return EmojiRow(v)
        if kind == "number":
            return NumberCount(v)
        if kind == "comparison":
            return Comparison(v)
        if kind == "diagram" and re.search(r"→|->|➜|⇒|➡|=>", content):
            return Diagram(v)
    except Exception as e:  # never let one odd visual kill the whole video
        log(f"Visual '{kind}' fell back to big text: {e}")
    return BigText(v)


# --------------------------------------------------------------------------- #
# Voice
# --------------------------------------------------------------------------- #

def _to_wav(src, dst, af=None):
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", src]
    if af:
        cmd += ["-af", af]
    subprocess.run(cmd + ["-ar", str(SR), "-ac", "2", "-sample_fmt", "s16", dst], check=True)


def _wav_duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


# Audio effects applied after the voice is generated. Each keeps the timing the same.
VOICE_EFFECTS = {
    "None": None,
    "Robot": "afftfilt=real='hypot(re\\,im)*sin(0)':imag='hypot(re\\,im)*cos(0)':win_size=512:overlap=0.75,"
             "volume=1.6",
    "Chipmunk": f"asetrate={SR}*1.4,aresample={SR},atempo=0.714",
    "Giant / monster": f"asetrate={SR}*0.72,aresample={SR},atempo=1.389",
    "Old radio": "highpass=f=350,lowpass=f=3400,acompressor=threshold=0.1:ratio=6,volume=1.4",
    "Megaphone": "highpass=f=600,lowpass=f=2800,acompressor=threshold=0.05:ratio=10,volume=2.5,"
                 "alimiter=limit=0.9",
    "Big hall echo": "aecho=0.8:0.8:70|140:0.3|0.15",
    "Alien": "flanger=delay=2:depth=4:speed=0.6,vibrato=f=6:d=0.4",
}

# Ready-made characters: a voice + speed + pitch + effect.
# engine "edge" = free Microsoft AI voices (online); "mac" = voices built into macOS (offline).
CHARACTERS = {
    "🎙️ Narrator": dict(engine="edge", voice="en-US-AndrewNeural", rate=8, pitch=0, effect="None",
                        desc="Warm, natural, works for anything."),
    "⚡ Hype Host": dict(engine="edge", voice="en-US-BrianNeural", rate=22, pitch=6, effect="None",
                        desc="Fast and excited, like a YouTuber on their third energy drink."),
    "🔥 Energetic Ava": dict(engine="edge", voice="en-US-AvaNeural", rate=18, pitch=5, effect="None",
                            desc="Bright, upbeat and quick."),
    "🎬 Movie Trailer": dict(engine="edge", voice="en-US-ChristopherNeural", rate=-6, pitch=-14,
                            effect="Big hall echo", desc="'In a world...' Deep and dramatic."),
    "🎓 The Professor": dict(engine="edge", voice="en-GB-RyanNeural", rate=2, pitch=-2, effect="None",
                            desc="British, clear, a little posh. Great for teaching."),
    "👩‍🏫 The Lecturer": dict(engine="edge", voice="en-GB-SoniaNeural", rate=4, pitch=0, effect="None",
                             desc="Confident British teacher voice."),
    "📰 News Anchor": dict(engine="edge", voice="en-US-GuyNeural", rate=6, pitch=-2, effect="None",
                          desc="Serious, authoritative, breaking-news energy."),
    "😌 Calm Explainer": dict(engine="edge", voice="en-US-EmmaNeural", rate=-6, pitch=-2, effect="None",
                             desc="Soft and soothing. Good for history and deep dives."),
    "🦘 Aussie Mate": dict(engine="edge", voice="en-AU-WilliamNeural", rate=12, pitch=0, effect="None",
                          desc="Relaxed Australian accent."),
    "☘️ Irish Storyteller": dict(engine="edge", voice="en-IE-ConnorNeural", rate=4, pitch=0, effect="None",
                                desc="Friendly Irish accent, great for stories."),
    "🧒 Kid Explainer": dict(engine="edge", voice="en-US-AnaNeural", rate=6, pitch=0, effect="None",
                            desc="A kid's voice. Funny for 'explained by a 7-year-old' bits."),
    "📢 Coach": dict(engine="edge", voice="en-US-GuyNeural", rate=16, pitch=2, effect="Megaphone",
                    desc="Shouting through a megaphone. Motivational chaos."),
    "📻 Old Radio Host": dict(engine="edge", voice="en-US-ChristopherNeural", rate=4, pitch=-4,
                             effect="Old radio", desc="Crackly 1940s radio broadcast."),
    "🤖 Robot": dict(engine="edge", voice="en-US-GuyNeural", rate=0, pitch=-6, effect="Robot",
                    desc="Monotone robot. Perfect for 'AI explains humans' jokes."),
    "🐿️ Chipmunk": dict(engine="edge", voice="en-US-BrianNeural", rate=10, pitch=0, effect="Chipmunk",
                       desc="Squeaky and ridiculous."),
    "👹 Monster": dict(engine="edge", voice="en-US-ChristopherNeural", rate=6, pitch=-8,
                      effect="Giant / monster", desc="Deep, slow, rumbly."),
    "👽 Alien": dict(engine="edge", voice="en-US-EricNeural", rate=4, pitch=10, effect="Alien",
                    desc="Wobbly and otherworldly."),
}

# Fallback list if the full voice list can't be downloaded.
EDGE_VOICES = [
    "en-US-AndrewNeural", "en-US-AndrewMultilingualNeural", "en-US-BrianNeural", "en-US-AvaNeural",
    "en-US-EmmaNeural", "en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-EricNeural",
    "en-US-RogerNeural", "en-US-SteffanNeural", "en-US-JennyNeural", "en-US-AriaNeural",
    "en-US-MichelleNeural", "en-US-AnaNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural",
    "en-GB-ThomasNeural", "en-GB-LibbyNeural", "en-GB-MaisieNeural", "en-AU-WilliamNeural",
    "en-AU-NatashaNeural", "en-IE-ConnorNeural", "en-IE-EmilyNeural", "en-IN-PrabhatNeural",
    "en-IN-NeerjaNeural", "en-CA-LiamNeural", "en-CA-ClaraNeural", "en-NZ-MitchellNeural",
    "en-ZA-LukeNeural",
]

# macOS novelty voices (only the ones installed on your Mac are shown).
MAC_FUN_VOICES = ["Zarvox", "Trinoids", "Bad News", "Good News", "Jester", "Superstar", "Bubbles", "Whisper",
                  "Wobble", "Cellos", "Organ", "Bells", "Boing", "Bahh", "Albert", "Fred", "Junior", "Ralph"]


def list_edge_voices():
    """All English Microsoft AI voices as {short_name: 'Name (Gender, Accent) - personality'}.
    Downloads the list once and caches it in voices.json."""
    cache = os.path.join(HERE, "voices.json")
    try:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    try:
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
        out = {}
        for v in sorted(voices, key=lambda v: (v["Locale"] != "en-US", v["Locale"], v["ShortName"])):
            if not v["Locale"].startswith("en-"):
                continue
            tag = ", ".join(v.get("VoiceTag", {}).get("VoicePersonalities", [])[:3])
            name = v["ShortName"].split("-")[-1].replace("Neural", "")
            out[v["ShortName"]] = f"{name} ({v['Gender']}, {v['Locale'][3:]})" + (f" - {tag}" if tag else "")
        if out:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=1)
            return out
    except Exception:
        pass
    return {v: v.split("-")[-1].replace("Neural", "") + f" ({v[3:5]})" for v in EDGE_VOICES}


def list_mac_voices():
    """English voices installed in macOS (novelty voices first). Empty list on other systems."""
    if sys.platform != "darwin":
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}(\w\w[_-]\w\w)", line)
        if m and m.group(2).lower().startswith("en"):
            names.append(m.group(1).strip())
    fun = [n for n in MAC_FUN_VOICES if n in names]
    return fun + [n for n in names if n not in fun]


def tts_edge(text, voice, rate, out_wav, attempts=4, pitch=0):
    """Microsoft's free AI voice. It sometimes refuses a request for a moment, so retry a few times."""
    import time
    import edge_tts
    mp3 = out_wav + ".mp3"

    async def go():
        await edge_tts.Communicate(text, voice, rate=f"{int(rate):+d}%", pitch=f"{int(pitch):+d}Hz").save(mp3)

    last = None
    for attempt in range(attempts):
        try:
            asyncio.run(go())
            if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
                _to_wav(mp3, out_wav)
                return
            last = RuntimeError("empty audio")
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def tts_mac(text, voice, rate, out_wav):
    aiff = out_wav + ".aiff"
    cmd = ["say", "-r", str(int(185 * (1 + rate / 100))), "-o", aiff, text]
    if voice and "Neural" not in voice:
        cmd[1:1] = ["-v", voice]
    subprocess.run(cmd, check=True)
    _to_wav(aiff, out_wav)


def tts_silent(text, voice, rate, out_wav):
    secs = max(1.2, len(text.split()) / 2.7 * (1 - rate / 100))
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"\x00\x00\x00\x00" * int(secs * SR))


def apply_effect(wav, effect, log=print):
    af = VOICE_EFFECTS.get(effect)
    if not af:
        return
    tmp = wav + ".fx.wav"
    try:
        _to_wav(wav, tmp, af)
        os.replace(tmp, wav)
    except Exception as e:
        log(f"Voice effect '{effect}' didn't work on this computer, using the plain voice ({e}).")
        VOICE_EFFECTS[effect] = None   # don't keep retrying it this run


class VoiceFailed(Exception):
    pass


def synthesize(text, out_wav, backend, voice, rate, log, pitch=0, effect="None"):
    if backend == "silent":
        tts_silent(text, voice, rate, out_wav)
    elif backend == "mac":
        tts_mac(text, voice, rate, out_wav)
    else:
        try:
            tts_edge(text, voice, rate, out_wav, pitch=pitch)
        except Exception as e:
            raise VoiceFailed(f"{e.__class__.__name__}: {e}")
    if backend != "silent":
        apply_effect(out_wav, effect, log)


def test_voice(voice, rate=8, pitch=0, effect="None", backend="auto",
               line="Hey! This is how your videos will sound."):
    """Say one short line with the chosen voice. Returns (ok, wav_path_or_error)."""
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "test.wav")
    try:
        if backend == "mac" or voice.startswith("Mac"):
            tts_mac(line, "" if voice.startswith("Mac") else voice, rate, out)
        else:
            tts_edge(line, voice, rate, out, attempts=2, pitch=pitch)
        apply_effect(out, effect, lambda m: None)
        return True, out
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


# --------------------------------------------------------------------------- #
# Sound effects (generated in code, no files needed)
# --------------------------------------------------------------------------- #

def _sfx_pop():
    n = int(SR * 0.12)
    out, ph = [], 0.0
    for i in range(n):
        t = i / SR
        f = 260 + 700 * math.exp(-t * 40)
        ph += 2 * math.pi * f / SR
        out.append(math.sin(ph) * math.exp(-t * 30) * 0.8)
    return out


def _sfx_whoosh():
    rnd = random.Random(3)
    n = int(SR * 0.42)
    out, y1, y2 = [], 0.0, 0.0
    for i in range(n):
        p = i / n
        env = math.sin(math.pi * p) ** 2
        a = 0.02 + 0.25 * math.sin(math.pi * p)        # filter opens then closes = "swoosh"
        x = rnd.uniform(-1, 1)
        y1 += a * (x - y1)
        y2 += a * (y1 - y2)
        out.append(y2 * env * 2.2)
    return out


def _sfx_ding():
    n = int(SR * 0.7)
    out = []
    for i in range(n):
        t = i / SR
        s = math.sin(2 * math.pi * 1568 * t) * 0.6 + math.sin(2 * math.pi * 2350 * t) * 0.25
        out.append(s * math.exp(-t * 6) * min(1.0, t * 400) * 0.5)
    return out


def build_sfx_track(events, total, path, volume=0.5):
    sounds = {"pop": _sfx_pop(), "whoosh": _sfx_whoosh(), "ding": _sfx_ding()}
    n = int(total * SR) + SR
    buf = array("f", bytes(4 * n))
    for t, kind in events:
        s = sounds.get(kind)
        start = int(max(0.0, t) * SR)
        if not s:
            continue
        for i, val in enumerate(s):
            j = start + i
            if j >= n:
                break
            buf[j] += val
    pcm = array("h", (int(max(-1.0, min(1.0, x * volume)) * 32767) for x in buf))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------- #
# Captions
# --------------------------------------------------------------------------- #

def caption_chunks(narration, start, dur):
    """Split narration into 1-3 word chunks with estimated word timings."""
    words = narration.split()
    if not words:
        return []
    weights = []
    for w in words:
        wt = len(re.sub(r"[^\w]", "", w)) + 2.5
        if w[-1] in ",;:":
            wt += 3
        elif w[-1] in ".!?":
            wt += 5
        weights.append(wt)
    total = sum(weights)
    speak = dur - SCENE_PAD
    times, acc = [], 0.0
    for wt in weights:
        times.append((start + speak * acc / total, start + speak * (acc + wt) / total))
        acc += wt
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if len(cur) == 3 or w[-1] in ",.!?;:" or (len(cur) == 2 and len(w) > 9):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out = [{"words": [words[i] for i in c], "times": [times[i] for i in c],
            "start": times[c[0]][0], "end": times[c[-1]][1]} for c in chunks]
    for a, b in zip(out, out[1:]):  # no gaps between chunks
        a["end"] = b["start"]
    out[-1]["end"] = start + dur
    return out


_cap_cache = {}


def caption_image(words, hi):
    key = (tuple(words), hi)
    if key not in _cap_cache:
        text = " ".join(w.strip("*") for w in words).upper()
        _cap_cache[key] = render_rich(text, 960, 260, max_size=92, min_size=60,
                                      highlight=(hi, HIGHLIGHT) if hi is not None else None)
    return _cap_cache[key]


# --------------------------------------------------------------------------- #
# Backgrounds
# --------------------------------------------------------------------------- #

def make_background(color):
    top = hex_rgb(color)
    bottom = mix(top, (8, 8, 20), 0.7)
    grad = Image.new("RGB", (1, 256))
    for y in range(256):
        grad.putpixel((0, y), mix(top, bottom, y / 255))
    bg = grad.resize((W, H), Image.BILINEAR)
    # soft light from the top, dark edges
    sw, sh = W // 8, H // 8
    glow = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(glow).ellipse((-sw * 0.3, -sh * 0.15, sw * 1.3, sh * 0.6), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(sw * 0.18)).resize((W, H), Image.BILINEAR)
    bg = Image.composite(Image.new("RGB", (W, H), mix(top, (255, 255, 255), 0.22)), bg, glow)
    vig = Image.new("L", (sw, sh), 255)
    ImageDraw.Draw(vig).ellipse((-sw * 0.25, -sh * 0.1, sw * 1.25, sh * 1.1), fill=0)
    vig = vig.filter(ImageFilter.GaussianBlur(sw * 0.15)).resize((W, H), Image.BILINEAR).point(lambda v: v * 0.6)
    return Image.composite(Image.new("RGB", (W, H), (5, 5, 12)), bg, vig)


class BackgroundVideo:
    """Streams frames from a gameplay clip, cropped to fill w x h, looping forever."""

    def __init__(self, path, w, h, seed=None):
        self.path, self.w, self.h = path, w, h
        probe = imageio_ffmpeg.read_frames(path)
        meta = next(probe)
        probe.close()
        dur = float(meta.get("duration") or 0)
        rnd = random.Random(seed)
        self.start = rnd.uniform(0, dur - 75) if dur > 90 else 0.0   # random spot so videos differ
        self._open()

    def _open(self):
        vf = f"fps={FPS},scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h}"
        self.gen = imageio_ffmpeg.read_frames(
            self.path, pix_fmt="rgb24",
            input_params=["-stream_loop", "-1", "-ss", f"{self.start:.2f}"],
            output_params=["-vf", vf, "-an"])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # size differs from the source on purpose (we crop)
            next(self.gen)

    def frame(self):
        try:
            b = next(self.gen)
        except StopIteration:
            self.start = 0.0
            self._open()
            b = next(self.gen)
        return Image.frombytes("RGB", (self.w, self.h), b)

    def close(self):
        try:
            self.gen.close()
        except Exception:
            pass


def list_backgrounds():
    if not os.path.isdir(BACKGROUNDS_DIR):
        return []
    return sorted(f for f in os.listdir(BACKGROUNDS_DIR)
                  if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".m4v")))


# --------------------------------------------------------------------------- #
# Main render
# --------------------------------------------------------------------------- #

def parse_script(raw):
    if isinstance(raw, dict):
        data = raw
    else:
        s = raw.strip()
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
        a, b = s.find("{"), s.rfind("}")
        if a == -1 or b == -1:
            raise ValueError("Couldn't find JSON in the script. Paste the whole thing, including { and }.")
        data = json.loads(s[a:b + 1])
    scenes = data.get("scenes") or []
    if not scenes:
        raise ValueError("The script has no scenes.")
    for i, sc in enumerate(scenes):
        if not str(sc.get("narration", "")).strip():
            raise ValueError(f"Scene {i + 1} has no narration.")
        sc.setdefault("visual", {"type": "big_text", "content": sc["narration"]})
    return data


def render_video(script, out_path, backend="auto", voice="en-US-AndrewNeural", rate=8,
                 pitch=0, effect="None",
                 music=None, music_volume=0.12, handle="", background=None, layout="full",
                 sfx=True, sfx_volume=0.5, pixabay_key="", progress=None, log=print):
    """background: path to a gameplay clip, or None for the animated color background.
    layout: 'full' (gameplay full screen) or 'split' (visuals top, gameplay bottom)."""
    data = parse_script(script)
    scenes = data["scenes"]
    progress = progress or (lambda p, msg="": None)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if background and not os.path.isabs(background) and not os.path.exists(background):
        background = os.path.join(BACKGROUNDS_DIR, background)
    mode = "gradient"
    if background and os.path.exists(background):
        mode = "split" if layout == "split" else "full"

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Voice (if the online voice fails, redo every scene in one voice so they never mix)
        def record_all(use_backend, use_voice):
            tl, t0 = [], 0.0
            full = os.path.join(tmp, "narration.wav")
            with wave.open(full, "wb") as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(SR)
                for i, sc in enumerate(scenes):
                    progress(0.25 * i / len(scenes), f"Recording voice for scene {i + 1}/{len(scenes)}")
                    text = strip_emoji(str(sc["narration"]).replace("*", ""))
                    wav = os.path.join(tmp, f"s{i}.wav")
                    synthesize(text, wav, use_backend, use_voice, rate, log, pitch=pitch, effect=effect)
                    with wave.open(wav) as w:
                        out.writeframes(w.readframes(w.getnframes()))
                    out.writeframes(b"\x00\x00\x00\x00" * int(SCENE_PAD * SR))
                    dur = _wav_duration(wav) + SCENE_PAD
                    tl.append({"start": t0, "dur": dur, "text": text, "sc": sc})
                    t0 += dur
            return tl, t0, full

        try:
            timeline, t0, full_wav = record_all(backend, voice)
        except VoiceFailed as e:
            if backend == "edge" or sys.platform != "darwin":
                raise RuntimeError(f"The AI voice isn't responding right now ({e}). Check your internet, "
                                   f"wait a minute and try again, or pick 'Mac built-in voice'.")
            log("The AI voice isn't responding right now, so this video uses the Mac's built-in voice "
                "for every scene. Try again in a few minutes to get the AI voice.")
            timeline, t0, full_wav = record_all("mac", "")
        total = t0

        # 2. Visuals, backgrounds, captions, sound effects
        rnd = random.Random(7)
        credits, events = [], []
        for i, item in enumerate(timeline):
            progress(0.25 + 0.08 * i / len(timeline), f"Preparing visuals {i + 1}/{len(timeline)}")
            v = item["sc"].get("visual") or {}
            color = v.get("color") or ["#2B3A8C", "#7A2B8C", "#0F6B5C", "#8C3A2B", "#1F5C8C"][i % 5]
            item["visual"] = make_visual(v, pixabay_key, log, credits, compact=(mode == "split"))
            if mode == "gradient" and isinstance(item["visual"], PhotoCard):
                item["bg"] = item["visual"].background()
            else:
                item["bg"] = make_background(color)
            orb_col = mix(hex_rgb(color), (255, 255, 255), 0.35)
            item["orbs"] = [(soft_orb(rnd.choice([70, 100, 140]), orb_col, rnd.choice([50, 70, 90])),
                             rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(-20, 20), rnd.uniform(-45, -15))
                            for _ in range(7)]
            item["caps"] = caption_chunks(item["text"], item["start"], item["dur"])
            if i > 0:
                events.append((item["start"] - 0.15, "whoosh"))
            events += [(item["start"] + dt, kind) for dt, kind in item["visual"].sfx(item["dur"])]
        handle_img = render_rich(handle, 700, 60, max_size=40, path=FONT_MED) if handle else None

        bgvid = None
        if mode == "full":
            bgvid = BackgroundVideo(background, W, H, seed=out_path)
            dim = Image.new("RGB", (W, H), (0, 0, 0))
            panel = shadowed(rounded_box(980, 980, (12, 12, 28, 165), radius=56, outline=(255, 255, 255, 40),
                                         outline_w=3), 24, 14, 0.4)
        elif mode == "split":
            bgvid = BackgroundVideo(background, W, H - SPLIT_Y, seed=out_path)
        cap_y = SPLIT_Y if mode == "split" else CAPTION_Y

        # 3. Audio mix + encoder
        audio_inputs = ["-i", full_wav]
        mix_parts, labels = [], ["[1:a]"]
        idx = 2
        if sfx and events:
            sfx_wav = os.path.join(tmp, "sfx.wav")
            build_sfx_track(events, total, sfx_wav, sfx_volume)
            audio_inputs += ["-i", sfx_wav]
            labels.append(f"[{idx}:a]")
            idx += 1
        if music and os.path.exists(music):
            audio_inputs += ["-stream_loop", "-1", "-i", music]
            mix_parts.append(f"[{idx}:a]volume={music_volume}[m]")
            labels.append("[m]")
            idx += 1
        cmd = [FFMPEG, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"] + audio_inputs
        if len(labels) > 1:
            fc = ";".join(mix_parts + ["".join(labels) +
                                       f"amix=inputs={len(labels)}:duration=first:normalize=0[a]"])
            cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

        # 4. Frames
        n_frames = int(math.ceil(total * FPS))
        si = 0
        try:
            for fi in range(n_frames):
                t = fi / FPS
                while si < len(timeline) - 1 and t >= timeline[si + 1]["start"]:
                    si += 1
                item = timeline[si]
                lt = t - item["start"]

                if mode == "full":
                    frame = Image.blend(bgvid.frame(), dim, 0.28)
                    s = 0.97 + 0.03 * ease_out_back(lt / 0.3)
                    paste_scaled(frame, panel, W / 2, VISUAL_CENTER_Y, s)
                else:
                    if si > 0 and lt < 0.2:
                        frame = Image.blend(timeline[si - 1]["bg"], item["bg"], lt / 0.2)
                    else:
                        frame = item["bg"].copy()
                    if not isinstance(item["visual"], PhotoCard):
                        for orb, bx, by, vx, vy in item["orbs"]:
                            x = (bx + vx * t) % (W + orb.width) - orb.width / 2
                            y = (by + vy * t) % (H + orb.height) - orb.height / 2
                            frame.paste(orb, (int(x - orb.width / 2), int(y - orb.height / 2)), orb)

                item["visual"].draw(frame, lt, item["dur"])

                if mode == "split":
                    # visuals sit in the upper part of the top half, leaving room for captions on the seam
                    top = frame.crop((0, VISUAL_CENTER_Y - 440, W, VISUAL_CENTER_Y - 440 + SPLIT_Y))
                    frame = Image.new("RGB", (W, H))
                    frame.paste(top, (0, 0))
                    frame.paste(bgvid.frame(), (0, SPLIT_Y))

                # progress bar (in split layout, the divider line is the progress bar)
                d = ImageDraw.Draw(frame)
                if mode == "split":
                    d.rectangle((0, SPLIT_Y - 7, W, SPLIT_Y + 7), fill=(20, 20, 30))
                    d.rectangle((0, SPLIT_Y - 7, int(W * t / total), SPLIT_Y + 7), fill=(255, 255, 255))
                else:
                    d.rounded_rectangle((60, 70, W - 60, 82), radius=6, fill=(0, 0, 0))
                    d.rounded_rectangle((60, 70, 60 + max(12, (W - 120) * t / total), 82), radius=6,
                                        fill=(255, 255, 255))

                # captions
                for ch in item["caps"]:
                    if ch["start"] <= t < ch["end"]:
                        hi = None
                        for k, (a, _) in enumerate(ch["times"]):
                            if a <= t:
                                hi = k
                        s = 0.85 + 0.15 * ease_out_back((t - ch["start"]) / 0.12)
                        paste_scaled(frame, caption_image(ch["words"], hi), W / 2, cap_y, s)
                        break

                if handle_img is not None:
                    paste_scaled(frame, handle_img, W / 2, H - 150, 1.0, 0.8)

                try:
                    proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    break
                if fi % 15 == 0:
                    progress(0.35 + 0.63 * fi / n_frames, f"Drawing frames {fi}/{n_frames}")
        finally:
            if bgvid:
                bgvid.close()
            proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"Video encoding failed: {err[-800:]}")

    # 5. Upload info next to the video
    info_path = os.path.splitext(out_path)[0] + ".txt"
    with open(info_path, "w", encoding="utf-8") as f:
        f.write(f"TITLE:\n{data.get('title', '')}\n\nDESCRIPTION:\n{data.get('description', '')}\n")
        if credits:
            f.write("\nPhotos:\n" + "\n".join(dict.fromkeys(credits)) + "\n")
        f.write("\nTAGS:\n" + ", ".join(data.get("tags", [])) + "\n")
    progress(1.0, "Done!")
    return out_path, total


def safe_name(title):
    s = re.sub(r"[^\w\s-]", "", title or "video").strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "video"


def main():
    ap = argparse.ArgumentParser(description="Turn a JSON script into a vertical video.")
    ap.add_argument("script", help="path to the script .json file")
    ap.add_argument("--out", help="output .mp4 path (default: output/<title>.mp4)")
    ap.add_argument("--backend", default="auto", choices=["auto", "edge", "mac", "silent"])
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--rate", type=int, default=8, help="speech speed change in percent, e.g. 10 or -5")
    ap.add_argument("--pitch", type=int, default=0, help="pitch change in Hz, e.g. 10 or -15")
    ap.add_argument("--effect", default="None", choices=list(VOICE_EFFECTS))
    ap.add_argument("--character", help="use a ready-made character, e.g. 'Robot' or 'Movie Trailer'")
    ap.add_argument("--music", help="optional background music file")
    ap.add_argument("--handle", default="", help="your @handle shown at the bottom")
    ap.add_argument("--background", help="gameplay clip (file in backgrounds/ or a path)")
    ap.add_argument("--layout", default="full", choices=["full", "split"])
    ap.add_argument("--no-sfx", action="store_true", help="turn off pop/whoosh sound effects")
    ap.add_argument("--pixabay-key", default="", help="optional free Pixabay API key for photos")
    a = ap.parse_args()
    with open(a.script, encoding="utf-8") as f:
        data = parse_script(f.read())
    out = a.out or os.path.join(HERE, "output", safe_name(data.get("title")) + ".mp4")
    if a.character:
        match = [k for k in CHARACTERS if a.character.lower() in k.lower()]
        if not match:
            sys.exit("Unknown character. Options: " + ", ".join(CHARACTERS))
        c = CHARACTERS[match[0]]
        a.voice, a.rate, a.pitch, a.effect = c["voice"], c["rate"], c["pitch"], c["effect"]

    def prog(p, msg=""):
        print(f"\r[{int(p * 100):3d}%] {msg:<45}", end="", flush=True)

    path, secs = render_video(data, out, a.backend, a.voice, a.rate, a.pitch, a.effect, music=a.music,
                              handle=a.handle,
                              background=a.background, layout=a.layout, sfx=not a.no_sfx,
                              pixabay_key=a.pixabay_key, progress=prog)
    print(f"\nSaved {path} ({secs:.1f}s)")


if __name__ == "__main__":
    main()
