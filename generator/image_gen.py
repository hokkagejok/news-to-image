"""
Генератор изображений для новостей — формат TikTok/Reels (1080×1920, 9:16).
Фото на весь фон, тёмный overlay (плотнее снизу), заголовок + описание в нижней трети.

Порядок получения фона:
  1. image_url из новости
  2. Unsplash Source API по первым словам заголовка
  3. Красивый тёмный градиент (не серый)

Борьба с логотипами: после resize обрезаем нижние 15% кадра и снова resize.
"""

import io
import os
import sys
import random
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    IMAGE_WIDTH, IMAGE_HEIGHT,
    FONT_PATH, HEADERS, REQUEST_TIMEOUT,
)

# ── Константы дизайна ────────────────────────────────────────────────────────

PADDING = 80
OVERLAY_ALPHA_TOP    = int(0.45 * 255)   # ~115 — верх светлее
OVERLAY_ALPHA_BOTTOM = int(0.85 * 255)   # ~217 — низ темнее (текст читаем)

MAX_TITLE_CHARS = 150
MAX_DESC_CHARS  = 250

TITLE_FONT_SIZE = 68
DESC_FONT_SIZE  = 38

TITLE_LINE_SPACING = 14
DESC_LINE_SPACING  = 8
BLOCK_GAP          = 28   # отступ между заголовком и описанием (px)

# Заголовок начинается не выше этой Y-координаты (нижняя треть)
TEXT_ZONE_TOP = 1300
# Заголовок не опускается ниже этой Y-координаты (с учётом PADDING снизу)
TEXT_ZONE_BOTTOM = IMAGE_HEIGHT - PADDING

SHADOW_OFFSET = 4
SHADOW_COLOR  = (0, 0, 0, 230)
WHITE         = (255, 255, 255, 255)
DESC_COLOR    = (220, 220, 220, 255)

# Нижние 15 % обрезаются после первого resize, чтобы убрать логотип/watermark
LOGO_CROP_PERCENT = 0.15

# Пары (верхний_цвет, нижний_цвет) для градиентного фона-заглушки
_GRADIENT_PAIRS = [
    ((20, 20, 40),  (0, 0, 0)),    # тёмно-синий → чёрный
    ((40, 10, 10),  (0, 0, 0)),    # тёмно-красный → чёрный
    ((10, 30, 20),  (0, 0, 0)),    # тёмно-зелёный → чёрный
    ((30, 10, 40),  (0, 0, 0)),    # тёмно-фиолетовый → чёрный
    ((10, 30, 50),  (0, 0, 0)),    # тёмно-бирюзовый → чёрный
    ((50, 25, 10),  (0, 0, 0)),    # тёмно-коричневый → чёрный
]

_UNSPLASH_TIMEOUT = 12

# Кэш шрифтов
_font_cache: dict = {}


# ── Публичный API ─────────────────────────────────────────────────────────────

def create_image(news_item: dict, output_path: str) -> bool:
    """
    Создаёт PNG изображение в формате TikTok/Reels для одной новости.

    Args:
        news_item: {"title", "image_url", "description" (optional), ...}
        output_path: куда сохранить PNG

    Returns:
        True — успех, False — ошибка
    """
    try:
        title       = (news_item.get("title")       or "").strip()
        image_url   = (news_item.get("image_url")   or "").strip()
        description = (news_item.get("description") or "").strip()

        if len(title) > MAX_TITLE_CHARS:
            title = title[:MAX_TITLE_CHARS - 1].rstrip() + "…"
        if len(description) > MAX_DESC_CHARS:
            description = description[:MAX_DESC_CHARS - 1].rstrip() + "…"

        # 1. Загружаем фоновое изображение
        base = _load_image(image_url, title)

        # 2. Первый resize: cover до целевого размера
        base = _resize_and_crop(base, IMAGE_WIDTH, IMAGE_HEIGHT)

        # 3. Обрезаем нижние 15% (логотип/watermark)
        base = _remove_logo(base)

        # 4. Второй resize: снова до 1080×1920 после кропа
        base = _resize_and_crop(base, IMAGE_WIDTH, IMAGE_HEIGHT)

        # 5. Градиентный тёмный overlay (светлее сверху, темнее снизу)
        base = _apply_gradient_overlay(base)

        # 6. Рисуем текст в нижней трети
        base = _draw_all_text(base, title, description)

        # 7. Сохраняем
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        base.save(output_path, "PNG", optimize=True)
        return True

    except Exception as e:
        print(f"    [ImageGen] Ошибка: {e}")
        return False


# ── Загрузка / генерация фона ─────────────────────────────────────────────────

def _load_image(image_url: str, title: str) -> Image.Image:
    """
    Цепочка fallback:
      1. Скачивает image_url из парсера
      2. Ищет фото на Unsplash по первым словам заголовка
      3. Генерирует тёмный градиент
    """
    if image_url:
        img = _download_image(image_url)
        if img:
            return img
        print(f"    [ImageGen] Прямой URL не сработал, пробую Unsplash...")

    if title:
        img = _unsplash_image(title)
        if img:
            print(f"    [ImageGen] Картинка получена с Unsplash.")
            return img
        print(f"    [ImageGen] Unsplash не ответил, генерирую градиент...")

    return _generate_gradient_bg()


def _download_image(url: str) -> Image.Image | None:
    """Скачивает изображение по URL. Возвращает None при любой ошибке."""
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "image" in content_type or _has_image_ext(url):
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        print(f"    [ImageGen] Не загружена ({url[:55]}…): {e}")
    return None


def _unsplash_image(title: str) -> Image.Image | None:
    """
    Запрашивает случайное фото с Unsplash по первым 3 словам заголовка.
    Использует бесплатный Unsplash Source API (редирект на CDN-фото).
    """
    try:
        words = [w.strip(".,!?:;\"'()") for w in title.split()[:3] if len(w) > 2]
        if not words:
            return None

        query = urllib.parse.quote(" ".join(words))
        url   = f"https://source.unsplash.com/{IMAGE_WIDTH}x{IMAGE_HEIGHT}/?{query}"

        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            },
            timeout=_UNSPLASH_TIMEOUT,
            allow_redirects=True,
        )

        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            return Image.open(io.BytesIO(resp.content)).convert("RGB")

    except Exception as e:
        print(f"    [ImageGen] Unsplash ошибка: {e}")

    return None


def _generate_gradient_bg() -> Image.Image:
    """
    Генерирует тёмный двухцветный градиент сверху вниз.
    """
    top_color, bottom_color = random.choice(_GRADIENT_PAIRS)
    img  = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT))
    draw = ImageDraw.Draw(img)

    for y in range(IMAGE_HEIGHT):
        t = y / (IMAGE_HEIGHT - 1)
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        draw.line([(0, y), (IMAGE_WIDTH, y)], fill=(r, g, b))

    return img


# ── Обработка изображения ─────────────────────────────────────────────────────

def _resize_and_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Cover-масштабирование: заполняет весь холст, кроп по центру."""
    src_w, src_h = img.size
    if src_w / src_h > w / h:
        new_h, new_w = h, int(src_w * h / src_h)
    else:
        new_w, new_h = w, int(src_h * w / src_w)

    img  = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _remove_logo(img: Image.Image) -> Image.Image:
    """
    Обрезает нижние LOGO_CROP_PERCENT % кадра после первого resize,
    чтобы убрать логотип/watermark сайта.
    После этого вызывающий код делает повторный resize до 1080×1920.
    """
    w, h = img.size
    cut  = int(h * LOGO_CROP_PERCENT)
    if cut < 5 or (h - cut) < 100:
        return img
    return img.crop((0, 0, w, h - cut))


def _apply_gradient_overlay(img: Image.Image) -> Image.Image:
    """
    Накладывает градиентный чёрный overlay:
    - сверху: OVERLAY_ALPHA_TOP (~45% непрозрачности)
    - снизу:  OVERLAY_ALPHA_BOTTOM (~85% непрозрачности)
    Это делает нижнюю треть достаточно тёмной для читаемого текста,
    не перекрывая верхнюю часть фотографии полностью.
    """
    base    = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    h = base.height
    for y in range(h):
        t     = y / (h - 1)
        alpha = int(OVERLAY_ALPHA_TOP + (OVERLAY_ALPHA_BOTTOM - OVERLAY_ALPHA_TOP) * t)
        draw.line([(0, y), (base.width, y)], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(base, overlay).convert("RGB")


# ── Отрисовка текста ──────────────────────────────────────────────────────────

def _draw_all_text(img: Image.Image, title: str, description: str) -> Image.Image:
    """
    Рисует текст в нижней трети картинки:
      1. Заголовок — крупный жирный белый
      2. Описание — под заголовком, светло-серый
    Текстовый блок прижат к низу зоны TEXT_ZONE_TOP..TEXT_ZONE_BOTTOM.
    """
    img_rgba   = img.convert("RGBA")
    text_layer = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    draw       = ImageDraw.Draw(text_layer)

    W, H = img.size
    max_text_w = W - PADDING * 2

    font_title = _get_font(TITLE_FONT_SIZE, bold=True)
    font_desc  = _get_font(DESC_FONT_SIZE,  bold=False)

    # Вычисляем высоту всего текстового блока
    title_lines = _wrap(title, font_title, max_text_w, draw)
    title_h     = _block_height(title_lines, font_title, draw, TITLE_LINE_SPACING)

    desc_lines = _wrap(description, font_desc, max_text_w, draw) if description else []
    desc_h     = _block_height(desc_lines, font_desc, draw, DESC_LINE_SPACING) if desc_lines else 0

    gap            = BLOCK_GAP if desc_lines else 0
    total_block_h  = title_h + gap + desc_h

    # Прижимаем блок к низу, но не выше TEXT_ZONE_TOP
    block_y = TEXT_ZONE_BOTTOM - total_block_h
    if block_y < TEXT_ZONE_TOP:
        block_y = TEXT_ZONE_TOP

    # Рисуем заголовок
    _draw_text_block(
        draw, title_lines, font_title,
        x=PADDING, y=block_y,
        max_w=max_text_w,
        color=WHITE,
        shadow_color=SHADOW_COLOR,
        shadow_offset=SHADOW_OFFSET,
        line_spacing=TITLE_LINE_SPACING,
        align="left",
        canvas_w=W,
    )

    # Рисуем описание
    if desc_lines:
        _draw_text_block(
            draw, desc_lines, font_desc,
            x=PADDING, y=block_y + title_h + BLOCK_GAP,
            max_w=max_text_w,
            color=DESC_COLOR,
            shadow_color=SHADOW_COLOR,
            shadow_offset=3,
            line_spacing=DESC_LINE_SPACING,
            align="left",
            canvas_w=W,
        )

    return Image.alpha_composite(img_rgba, text_layer).convert("RGB")


def _draw_text_block(
    draw: ImageDraw.Draw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x: int, y: int,
    max_w: int,
    color: tuple,
    shadow_color: tuple,
    shadow_offset: int,
    line_spacing: int,
    align: str,
    canvas_w: int,
) -> None:
    """Рисует блок строк с тенью. align='left' | 'center'."""
    lh    = _line_h(font, draw)
    cur_y = y

    for line in lines:
        bbox   = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        cur_x  = x + (max_w - line_w) // 2 if align == "center" else x

        draw.text((cur_x + shadow_offset, cur_y + shadow_offset),
                  line, font=font, fill=shadow_color)
        draw.text((cur_x, cur_y), line, font=font, fill=color)

        cur_y += lh + line_spacing


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _wrap(text: str, font: ImageFont.FreeTypeFont,
          max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Word-wrap по пикселям."""
    if not text:
        return []
    words   = text.split()
    lines:  list[str] = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip()
        w = draw.textbbox((0, 0), candidate, font=font)[2]
        if w <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines


def _line_h(font: ImageFont.FreeTypeFont, draw: ImageDraw.Draw) -> int:
    bb = draw.textbbox((0, 0), "Аgy", font=font)
    return bb[3] - bb[1]


def _block_height(lines: list[str], font: ImageFont.FreeTypeFont,
                  draw: ImageDraw.Draw, spacing: int) -> int:
    if not lines:
        return 0
    lh = _line_h(font, draw)
    return lh * len(lines) + spacing * (len(lines) - 1)


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


def _has_image_ext(url: str) -> bool:
    base = url.lower().split("?")[0]
    return any(base.endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
