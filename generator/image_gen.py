"""
Генератор изображений для новостей — формат TikTok/Reels (1080×1920, 9:16).
Два стиля: "world" (TIN THE GIOI) и "vietnam" (TIN VIET NAM).

Конвейер create_image:
  1. download_image(url)       — скачать оригинальное фото статьи
  2. get_fallback_image(title) — если нет фото → Google Images → Picsum (seed)
  3. prepare_background(img)   — FIT-resize + размытый фон
  4. add_overlay(img)          — градиентный overlay 80→220 alpha
  5. draw_badge(draw, type)    — бейдж типа новости (верх-лево)
  6. отрисовка заголовка       — авто-подбор шрифта 72→48px, макс. 4 строки
  7. отрисовка описания        — 38px, макс. 3 строки
"""

import base64
import hashlib
import io
import os
import sys
import random
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    IMAGE_WIDTH, IMAGE_HEIGHT,
    FONT_PATH, HEADERS, REQUEST_TIMEOUT,
)

# ── Константы ────────────────────────────────────────────────────────────────

W, H = IMAGE_WIDTH, IMAGE_HEIGHT          # 1080 × 1920

# Шрифты для create_image
TITLE_FONT_START = 72
TITLE_FONT_MIN   = 48
TITLE_FONT_STEP  = 4
TITLE_MAX_LINES  = 4
TITLE_LINE_H     = 85                     # межстрочный интервал заголовка (px)

DESC_FONT_SIZE   = 38
DESC_LINE_H      = 48
DESC_MAX_LINES   = 3

# Горизонтальный отступ текста
TEXT_PAD = 80

# Бейдж
BADGE_X      = 60
BADGE_Y      = 80
BADGE_PAD_X  = 20
BADGE_PAD_Y  = 12
BADGE_RADIUS = 12
BADGE_FONT_SZ = 36

# Стоп-слова для Unsplash-запроса (русские, вьетнамские, английские)
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were",
    "in", "on", "at", "to", "for", "of", "and", "or", "but",
    "with", "that", "this", "it", "he", "she", "they",
    # вьетнамские
    "voi", "cua", "va", "la", "co", "duoc", "cho", "trong",
    "mot", "cac", "nhung", "sau", "khi", "da",
}

_PICSUM_TIMEOUT = 15

# Пары градиента для фона-заглушки
_GRADIENT_PAIRS = [
    ((15, 20, 40), (5,  5,  15)),
    ((40, 10, 10), (10, 5,  5)),
    ((10, 30, 15), (5,  10, 5)),
    ((30, 15, 40), (10, 5,  15)),
    ((40, 30, 10), (15, 10, 5)),
]

# Кэш шрифтов
_font_cache: dict = {}

# Вьетнамские названия месяцев
_VI_MONTHS = {
    1:  "thang 1",  2:  "thang 2",  3:  "thang 3",
    4:  "thang 4",  5:  "thang 5",  6:  "thang 6",
    7:  "thang 7",  8:  "thang 8",  9:  "thang 9",
    10: "thang 10", 11: "thang 11", 12: "thang 12",
}


# ── Публичный API ─────────────────────────────────────────────────────────────

def create_image(news_item: dict, output_path: str) -> bool:
    """
    Создаёт PNG изображение в формате TikTok/Reels для одной новости.

    Args:
        news_item: {"title", "image_url", "description", "type" ("world"|"vietnam"), ...}
        output_path: куда сохранить PNG

    Returns:
        True — успех, False — ошибка
    """
    try:
        title     = (news_item.get("title")       or "").strip()
        image_url = (news_item.get("image_url")   or "").strip()
        desc      = (news_item.get("description") or "").strip()
        news_type = (news_item.get("type")        or "world").strip()

        # 1. Скачать оригинальное фото статьи
        img = download_image(image_url) if image_url else None
        if img:
            print(f"    [OK] Оригинальное фото загружено")

        # 2. Fallback → Picsum Photos (seed детерминирован по заголовку)
        if img is None:
            print(f"    [>] Нет фото, берём Picsum...")
            img = get_fallback_image(title, news_type)

        # 3. FIT-resize + размытый фон (или градиент если img=None)
        img = prepare_background(img, W, H)

        # 4. Градиентный overlay
        img = add_overlay(img)

        # 5. Рисуем поверх overlay
        draw = ImageDraw.Draw(img)

        # 6. Бейдж типа новости
        font_badge = _get_font(BADGE_FONT_SZ, bold=True)
        draw_badge(draw, news_type, font_badge)

        # 7. Заголовок (нижняя треть, авто-подбор шрифта)
        font_title, title_lines = _fit_title_font(draw, title, W - TEXT_PAD * 2)
        title_lines = title_lines[:TITLE_MAX_LINES]
        total_title_h = len(title_lines) * TITLE_LINE_H
        title_y = H - 350 - total_title_h

        for line in title_lines:
            # Тень
            draw.text((TEXT_PAD + 3, title_y + 3), line,
                      font=font_title, fill=(0, 0, 0))
            # Основной текст
            draw.text((TEXT_PAD, title_y), line,
                      font=font_title, fill=(255, 255, 255))
            title_y += TITLE_LINE_H

        # 8. Описание
        if desc:
            font_desc = _get_font(DESC_FONT_SIZE, bold=False)
            desc_lines = wrap_text(desc, font_desc, W - TEXT_PAD * 2, draw)
            desc_lines = desc_lines[:DESC_MAX_LINES]
            desc_y = title_y + 20

            for line in desc_lines:
                if desc_y + DESC_LINE_H > H - 60:
                    break
                draw.text((TEXT_PAD, desc_y), line,
                          font=font_desc, fill=(210, 210, 210))
                desc_y += DESC_LINE_H

        # 9. Сохранить
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
        return True

    except Exception as e:
        print(f"    [ImageGen] Ошибка: {e}")
        return False


# ── Загрузка изображений ──────────────────────────────────────────────────────

def download_image(url: str) -> Image.Image | None:
    """Скачивает изображение по URL или декодирует base64 data URI."""
    if not url or len(url) < 10:
        return None
    try:
        # Обработка base64 data URI (data:image/...;base64,<data>)
        if url.startswith("data:image"):
            if "," not in url:
                return None
            _, data = url.split(",", 1)
            img_bytes = base64.b64decode(data)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            if img.width > 200 and img.height > 200:
                return img
            return None

        # Обычный HTTP/HTTPS URL
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.content) > 5_000:
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            if img.width > 200 and img.height > 200:
                return img
    except Exception as e:
        print(f"    [ImageGen] Не загружена ({url[:55]}): {e}")
    return None


def get_fallback_image(title: str, news_type: str = "world") -> Image.Image | None:
    """
    Попытка 1 — Google Custom Search: ищет релевантное фото по заголовку.
    Попытка 2 — Picsum Photos: детерминированное фото по MD5-seed заголовка.

    Возвращает None только если обе попытки провалились (→ градиентный фон).
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    cx = os.environ.get("GOOGLE_CX", "")

    print(f"    [>] Нет фото, ищем через Google...")
    print(f"    [Debug] KEY={'SET:' + api_key[:4] if api_key else 'EMPTY'} CX={'SET' if cx else 'EMPTY'}")

    # Попытка 1 — Google Images (самое релевантное)
    if api_key and cx:
        img = get_google_image(title, api_key, cx)
        if img:
            return img
        print(f"    [Google] Не нашёл, пробуем Picsum...")
    else:
        print(f"    [Google] API ключи не заданы, пробуем Picsum...")

    # Попытка 2 — Picsum (запасной)
    seed = int(hashlib.md5(title.encode("utf-8", errors="replace")).hexdigest()[:8], 16) % 1000

    urls = [
        f"https://picsum.photos/seed/{seed}/1080/1920",
        f"https://picsum.photos/1080/1920?random={seed}",
        f"https://picsum.photos/1080/1920?random={seed + 1}",
    ]

    for url in urls:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=_PICSUM_TIMEOUT,
                allow_redirects=True,
            )
            if resp.status_code == 200 and len(resp.content) > 5_000:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                if img.width > 200 and img.height > 200:
                    print(f"    [Picsum] OK (seed={seed})")
                    return img
        except Exception as e:
            print(f"    [Picsum] Ошибка ({url[:55]}): {e}")
            continue

    return None


def get_google_image(
    title: str,
    api_key: str,
    cx: str,
) -> Image.Image | None:
    """
    Ищет релевантное фото через Google Custom Search API.

    Алгоритм:
      1. Берёт первые 5 слов заголовка как поисковый запрос.
      2. Делает запрос к Google Custom Search (searchType=image, imgSize=large).
      3. Пробует скачать до 3 результатов, возвращает первое успешное фото.
    """
    try:
        query = " ".join(title.split()[:5])
        encoded = urllib.parse.quote(query)

        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}"
            f"&cx={cx}"
            f"&q={encoded}"
            f"&searchType=image"
            f"&imgSize=large"
            f"&num=3"
            f"&safe=active"
        )

        resp = requests.get(url, timeout=10)
        data = resp.json()

        print(f"    [Google] Status: {resp.status_code}")
        print(f"    [Google] Query: {query!r}")

        if "error" in data:
            print(f"    [Google] API Error: {data['error'].get('message', '')}")
            return None

        items = data.get("items", [])
        print(f"    [Google] Найдено результатов: {len(items)}")

        for item in items:
            img_url = item.get("link", "")
            if not img_url:
                continue
            try:
                photo = requests.get(
                    img_url,
                    timeout=8,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if photo.status_code == 200 and len(photo.content) > 5_000:
                    img = Image.open(io.BytesIO(photo.content)).convert("RGB")
                    if img.width > 200 and img.height > 200:
                        print(f"    [Google] OK!")
                        return img
            except Exception as e:
                print(f"    [Google] Фото не загрузилось: {e}")
                continue

    except Exception as e:
        print(f"    [Google] Ошибка: {e}")

    return None


# ── Подготовка фона ───────────────────────────────────────────────────────────

def prepare_background(
    img: Image.Image | None,
    target_w: int = 1080,
    target_h: int = 1920,
) -> Image.Image:
    """
    Если img=None — генерирует тёмный градиентный фон.
    Иначе — режим FIT + размытый фон:
      1. Оригинал растягивается на весь холст → GaussianBlur(25) → затемняется на 55%.
      2. Поверх по центру вставляется чёткая FIT-версия (вписана без обрезки).
      3. Нижние 80px закрашиваются чёрным — скрывает логотип BBC и схожие ватермарки.
    """
    if img is None:
        return create_gradient_bg(target_w, target_h)

    if img.mode != "RGB":
        img = img.convert("RGB")

    src_w, src_h = img.size

    # Фон: оригинал на весь холст → размыт → затемнён
    bg      = img.copy().resize((target_w, target_h), Image.LANCZOS)
    bg      = bg.filter(ImageFilter.GaussianBlur(radius=25))
    overlay = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    bg      = Image.blend(bg, overlay, alpha=0.55)

    # FIT: вписываем оригинал целиком (без обрезки)
    scale       = min(target_w / src_w, target_h / src_h)
    new_w       = int(src_w * scale)
    new_h       = int(src_h * scale)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Вставляем по центру
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    bg.paste(img_resized, (offset_x, offset_y))

    # Скрываем логотип BBC и другие ватермарки
    bg = _remove_logo_area(bg, target_w, target_h)

    return bg


def _remove_logo_area(bg: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Скрывает логотип/ватермарку в нижней части картинки.

    Алгоритм:
      1. Берём полоску чистого контента чуть выше зоны логотипа.
      2. Растягиваем её на зону логотипа (нижние 12% высоты).
      3. Дополнительно размываем зону логотипа для естественности.
    """
    logo_h  = int(target_h * 0.12)          # высота зоны логотипа
    clean_y = target_h - logo_h - 20        # строка чуть выше логотипа

    # Вырезаем полоску чистого контента (40px) выше логотипа
    strip_top = max(0, clean_y - 40)
    clean_strip = bg.crop((0, strip_top, target_w, clean_y))

    # Растягиваем полоску на всю зону логотипа
    clean_strip = clean_strip.resize((target_w, logo_h + 20), Image.LANCZOS)
    bg.paste(clean_strip, (0, target_h - logo_h - 20))

    # Дополнительно размываем зону для маскировки артефактов
    logo_zone = bg.crop((0, target_h - logo_h, target_w, target_h))
    logo_zone = logo_zone.filter(ImageFilter.GaussianBlur(radius=15))
    bg.paste(logo_zone, (0, target_h - logo_h))

    return bg


def create_gradient_bg(w: int, h: int) -> Image.Image:
    """Генерирует тёмный двухцветный градиент сверху вниз."""
    top_color, bottom_color = random.choice(_GRADIENT_PAIRS)
    img  = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)

    for y in range(h):
        ratio = y / h
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    return img


def add_overlay(img: Image.Image) -> Image.Image:
    """
    Накладывает градиентный чёрный overlay:
    - Верх: alpha=80  (~31% непрозрачности) — фото просматривается
    - Низ:  alpha=220 (~86% непрозрачности) — текст читаем
    """
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    _w, _h  = img.size

    for y in range(_h):
        ratio = y / _h
        alpha = int(80 + 140 * ratio)
        draw.line([(0, y), (_w, y)], fill=(0, 0, 0, alpha))

    base = img.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


# ── Бейдж типа новости ────────────────────────────────────────────────────────

def draw_badge(
    draw: ImageDraw.Draw,
    news_type: str,
    font: ImageFont.FreeTypeFont,
) -> None:
    """
    Рисует бейдж типа новости в верхнем левом углу:
      world   → красный фон (#DC1E1E), белый текст  "TIN THE GIOI"
      vietnam → жёлтый фон (#FFC800), тёмно-красный "TIN VIET NAM"
    """
    if news_type == "vietnam":
        badge_color = (255, 200, 0)
        text_color  = (180, 0, 0)
        badge_text  = "TIN VIET NAM"
    else:
        badge_color = (220, 30, 30)
        text_color  = (255, 255, 255)
        badge_text  = "TIN THE GIOI"

    bbox   = draw.textbbox((0, 0), badge_text, font=font)
    bw     = bbox[2] - bbox[0] + BADGE_PAD_X * 2
    bh     = bbox[3] - bbox[1] + BADGE_PAD_Y * 2

    draw.rounded_rectangle(
        [BADGE_X, BADGE_Y, BADGE_X + bw, BADGE_Y + bh],
        radius=BADGE_RADIUS,
        fill=badge_color,
    )
    draw.text(
        (BADGE_X + BADGE_PAD_X, BADGE_Y + BADGE_PAD_Y),
        badge_text,
        font=font,
        fill=text_color,
    )


# ── Word-wrap ─────────────────────────────────────────────────────────────────

def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    draw: ImageDraw.Draw,
) -> list[str]:
    """Word-wrap по пикселям."""
    if not text:
        return []
    words   = text.split()
    lines:  list[str] = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines


# ── Шрифты ───────────────────────────────────────────────────────────────────

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Кастомный → системный Windows → Linux/Mac → default."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]

    candidates: list[str] = []

    if os.path.exists(FONT_PATH):
        candidates.append(FONT_PATH)

    if bold:
        candidates += [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "C:/Windows/Fonts/verdanab.ttf",
            "C:/Windows/Fonts/tahomabd.ttf",
            "C:/Windows/Fonts/georgiab.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/verdana.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue

    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _fit_title_font(
    draw: ImageDraw.Draw,
    text: str,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """
    Подбирает размер шрифта заголовка (TITLE_FONT_START→TITLE_FONT_MIN, шаг TITLE_FONT_STEP)
    так, чтобы текст влез в TITLE_MAX_LINES строк.
    """
    size = TITLE_FONT_START
    while size >= TITLE_FONT_MIN:
        font  = _get_font(size, bold=True)
        lines = wrap_text(text, font, max_width, draw)
        if len(lines) <= TITLE_MAX_LINES:
            return font, lines
        size -= TITLE_FONT_STEP

    # Минимальный шрифт, жёстко обрезаем
    font  = _get_font(TITLE_FONT_MIN, bold=True)
    lines = wrap_text(text, font, max_width, draw)
    return font, lines[:TITLE_MAX_LINES]


def _has_image_ext(url: str) -> bool:
    base = url.lower().split("?")[0]
    return any(base.endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


# ── Вспомогательный градиент для баннеров ────────────────────────────────────

def _dark_gradient(
    draw: ImageDraw.Draw, w: int, h: int,
    top: tuple, bottom: tuple,
) -> None:
    """Рисует вертикальный градиент на draw-объекте."""
    for y in range(h):
        t = y / (h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


# ── Вспомогательные для баннеров ─────────────────────────────────────────────

def _line_h(font: ImageFont.FreeTypeFont, draw: ImageDraw.Draw) -> int:
    bb = draw.textbbox((0, 0), "Аgy", font=font)
    return bb[3] - bb[1]


def _block_height(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.Draw,
    spacing: int,
) -> int:
    if not lines:
        return 0
    lh = _line_h(font, draw)
    return lh * len(lines) + spacing * (len(lines) - 1)


def _draw_text_centered(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.FreeTypeFont,
    y: int,
    canvas_w: int,
    color: tuple,
    shadow: bool = True,
) -> None:
    """Рисует одну строку по центру с опциональной тенью."""
    if shadow:
        draw.text((canvas_w // 2 + 3, y + 3), text,
                  font=font, anchor="mm", fill=(0, 0, 0))
    draw.text((canvas_w // 2, y), text, font=font, anchor="mm", fill=color)


# ── Вступительный баннер ──────────────────────────────────────────────────────

def create_intro_banner(date_str: str, news_count: int, output_path: str) -> str:
    """
    Создаёт вступительный баннер дайджеста (1080×1920) на вьетнамском языке.

    Args:
        date_str:   дата, напр. "ngay 5 thang 4 nam 2026"
        news_count: количество новостей
        output_path: путь для сохранения PNG
    """
    bw, bh = 1080, 1920

    img  = Image.new("RGB", (bw, bh), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    _dark_gradient(draw, bw, bh, (8, 12, 40), (0, 0, 0))

    # Декоративные линии по краям
    for x_off in (30, 50):
        draw.line([(x_off, 120), (x_off, bh - 120)],     fill=(220, 30, 30), width=2)
        draw.line([(bw - x_off, 120), (bw - x_off, bh - 120)], fill=(220, 30, 30), width=2)

    # Декоративные точки по краям (каждые 120px)
    for y_dot in range(200, bh - 200, 120):
        draw.ellipse([18, y_dot - 5, 28, y_dot + 5],           fill=(220, 30, 30))
        draw.ellipse([bw - 28, y_dot - 5, bw - 18, y_dot + 5], fill=(220, 30, 30))

    font_huge  = _get_font(100, bold=True)
    font_big   = _get_font(80,  bold=True)
    font_med   = _get_font(50,  bold=False)
    font_small = _get_font(40,  bold=False)
    font_xs    = _get_font(35,  bold=False)

    # Красный круг-иконка
    icon_cy, icon_r = 370, 130
    draw.ellipse(
        [bw // 2 - icon_r, icon_cy - icon_r, bw // 2 + icon_r, icon_cy + icon_r],
        fill=(220, 30, 30),
    )
    draw.text((bw // 2, icon_cy), "N", font=font_huge, anchor="mm", fill=(255, 255, 255))

    # Заголовок баннера
    _draw_text_centered(draw, "TIN TUC HOM NAY", font_huge, 580, bw, (255, 255, 255))

    # Дата
    draw.text((bw // 2, 700), date_str, font=font_med, anchor="mm", fill=(160, 160, 160))

    # Красная линия
    draw.rectangle([(80, 790), (bw - 80, 795)], fill=(220, 30, 30))

    # TOP SU KIEN
    _draw_text_centered(draw, "TOP SU KIEN", font_big, 910, bw, (255, 255, 255))

    # Количество
    draw.text((bw // 2, 1040), f"{news_count} tin tuc hom nay",
              font=font_med, anchor="mm", fill=(220, 30, 30))

    # Декоративные точки по центру
    for i, x_dot in enumerate(range(380, 720, 60)):
        color = (220, 30, 30) if i % 2 == 0 else (100, 100, 100)
        r = 8
        draw.ellipse([x_dot - r, 1160 - r, x_dot + r, 1160 + r], fill=color)

    # Горизонтальная линия
    draw.rectangle([(80, 1240), (bw - 80, 1245)], fill=(220, 30, 30))

    # Канал
    _draw_text_centered(draw, "@todayrealnews", font_small, 1700, bw, (255, 255, 255))

    # Призыв
    draw.text((bw // 2, 1790), "Dang ky de khong bo lo",
              font=font_xs, anchor="mm", fill=(140, 140, 140))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"    [OK] Вступительный баннер создан: {output_path}")
    return output_path


# ── Баннер подписки ───────────────────────────────────────────────────────────

def create_subscribe_banner(output_path: str) -> str:
    """
    Создаёт финальный рекламный баннер «DANG KY NGAY» (1080×1920) на вьетнамском.
    """
    bw, bh = 1080, 1920

    img  = Image.new("RGB", (bw, bh), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    _dark_gradient(draw, bw, bh, (5, 5, 20), (0, 0, 0))

    # Декоративные круги
    for _ in range(8):
        cx = random.randint(0, bw)
        cy = random.randint(0, bh)
        cr = random.randint(100, 400)
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr],
                     fill=None, outline=(255, 50, 50))

    font_big   = _get_font(90, bold=True)
    font_med   = _get_font(55, bold=True)
    font_small = _get_font(40, bold=False)

    # Иконка
    icon_y, icon_r = 400, 110
    draw.ellipse(
        [bw // 2 - icon_r, icon_y - icon_r, bw // 2 + icon_r, icon_y + icon_r],
        fill=(220, 30, 30),
    )
    draw.text((bw // 2, icon_y), "T", font=font_big, anchor="mm", fill=(255, 255, 255))

    # Разделитель
    draw.rectangle([(100, icon_y + icon_r + 50), (bw - 100, icon_y + icon_r + 55)],
                   fill=(220, 30, 30))

    # Текстовые блоки
    for text, font, color, y in [
        ("THEO DOI",  font_med, (180, 180, 180), 720),
        ("TIN TUC",   font_big, (255, 255, 255), 840),
        ("THE GIOI!", font_big, (255, 255, 255), 960),
        ("MOI NGAY",  font_big, (220,  30,  30), 1090),
    ]:
        _draw_text_centered(draw, text, font, y, bw, color)

    # Разделитель
    draw.rectangle([(100, 1210), (bw - 100, 1215)], fill=(220, 30, 30))

    # Кнопка
    btn_y = 1340
    draw.rounded_rectangle([150, btn_y - 65, bw - 150, btn_y + 65],
                            radius=42, fill=(220, 30, 30))
    draw.text((bw // 2, btn_y), "DANG KY NGAY",
              font=font_med, anchor="mm", fill=(255, 255, 255))

    # Канал
    draw.text((bw // 2, 1490), "@todayrealnews",
              font=font_med, anchor="mm", fill=(255, 255, 255))

    # Подпись
    draw.text((bw // 2, 1660), "Tin tuc moi ngay luc 10:00",
              font=font_small, anchor="mm", fill=(150, 150, 150))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"    [OK] Баннер подписки создан: {output_path}")
    return output_path
