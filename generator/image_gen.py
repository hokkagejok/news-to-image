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

# Автоподбор размера шрифта заголовка
TITLE_FONT_START = 72    # начальный размер
TITLE_FONT_MIN   = 48    # минимальный размер
TITLE_FONT_STEP  = 4     # шаг уменьшения
TITLE_MAX_LINES  = 4     # макс. строк заголовка

# Описание — фиксированный шрифт, обрезается до 3 строк
DESC_FONT_SIZE  = 36
DESC_MAX_LINES  = 3

TITLE_LINE_SPACING = 14
DESC_LINE_SPACING  = 8
BLOCK_GAP          = 28  # отступ между заголовком и описанием (px)

# Нижние границы: текст не должен заходить ниже этих Y-координат
TITLE_MAX_BOTTOM = IMAGE_HEIGHT - 400   # 1520
DESC_MAX_BOTTOM  = IMAGE_HEIGHT - 100   # 1820

# Заголовок начинается не выше этой Y-координаты (нижняя треть)
TEXT_ZONE_TOP = 1300

SHADOW_OFFSET = 4
SHADOW_COLOR  = (0, 0, 0, 230)
WHITE         = (255, 255, 255, 255)
DESC_COLOR    = (220, 220, 220, 255)

# Нижние 15 % обрезаются после первого resize, чтобы убрать логотип/watermark
LOGO_CROP_PERCENT = 0.15

# Пары (верхний_цвет, нижний_цвет) для градиентного фона-заглушки
_GRADIENT_PAIRS = [
    ((20, 20, 40),  (0, 0, 0)),
    ((40, 10, 10),  (0, 0, 0)),
    ((10, 30, 20),  (0, 0, 0)),
    ((30, 10, 40),  (0, 0, 0)),
    ((10, 30, 50),  (0, 0, 0)),
    ((50, 25, 10),  (0, 0, 0)),
]

_UNSPLASH_TIMEOUT = 12

# Кэш шрифтов
_font_cache: dict = {}

# Названия месяцев на русском для баннеров
_RU_MONTHS = {
    1: "января", 2: "февраля", 3: "марта",    4: "апреля",
    5: "мая",    6: "июня",    7: "июля",     8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


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
    """Генерирует тёмный двухцветный градиент сверху вниз."""
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
    """
    w, h = img.size
    cut  = int(h * LOGO_CROP_PERCENT)
    if cut < 5 or (h - cut) < 100:
        return img
    return img.crop((0, 0, w, h - cut))


def _apply_gradient_overlay(img: Image.Image) -> Image.Image:
    """
    Накладывает градиентный чёрный overlay:
    - сверху: ~45% непрозрачности
    - снизу:  ~85% непрозрачности
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

def _fit_title(
    draw: ImageDraw.Draw,
    text: str,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """
    Подбирает размер шрифта для заголовка так, чтобы текст влез в TITLE_MAX_LINES строк.
    Начинает с TITLE_FONT_START, уменьшает на TITLE_FONT_STEP до TITLE_FONT_MIN.
    """
    size = TITLE_FONT_START
    while size >= TITLE_FONT_MIN:
        font  = _get_font(size, bold=True)
        lines = _wrap(text, font, max_width, draw)
        if len(lines) <= TITLE_MAX_LINES:
            return font, lines
        size -= TITLE_FONT_STEP

    # Не влезло даже в минимальный размер — берём min-шрифт, обрезаем до MAX_LINES
    font  = _get_font(TITLE_FONT_MIN, bold=True)
    lines = _wrap(text, font, max_width, draw)
    return font, lines[:TITLE_MAX_LINES]


def _fit_desc(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """
    Переносит описание по словам. Если строк больше DESC_MAX_LINES — обрезает,
    добавляя «…» к последней строке.
    """
    if not text:
        return []

    lines = _wrap(text, font, max_width, draw)

    if len(lines) <= DESC_MAX_LINES:
        return lines

    # Обрезаем до лимита строк и добавляем "…" к последней
    lines = lines[:DESC_MAX_LINES]
    last  = lines[-1]
    while last:
        candidate = last.rstrip() + "…"
        w = draw.textbbox((0, 0), candidate, font=font)[2]
        if w <= max_width:
            lines[-1] = candidate
            break
        last = last[:-1]

    return lines


def _draw_all_text(img: Image.Image, title: str, description: str) -> Image.Image:
    """
    Рисует текст в нижней трети картинки:
      1. Заголовок — автоподбор шрифта 72→48px, макс. 4 строки
      2. Описание — 36px, макс. 3 строки с обрезкой и «…»

    Ограничения позиционирования:
      - Низ заголовка   ≤ TITLE_MAX_BOTTOM (y=1520)
      - Низ описания    ≤ DESC_MAX_BOTTOM  (y=1820)
      - Начало блока    ≥ TEXT_ZONE_TOP    (y=1300)
    """
    img_rgba   = img.convert("RGBA")
    text_layer = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    draw       = ImageDraw.Draw(text_layer)

    W, H       = img.size
    max_text_w = W - PADDING * 2   # 1080 - 160 = 920px

    # Подбираем шрифт и строки заголовка
    font_title, title_lines = _fit_title(draw, title, max_text_w)
    title_h = _block_height(title_lines, font_title, draw, TITLE_LINE_SPACING)

    # Описание — фиксированный шрифт, обрезка до 3 строк
    font_desc  = _get_font(DESC_FONT_SIZE, bold=False)
    desc_lines = _fit_desc(draw, description, font_desc, max_text_w)
    desc_h     = _block_height(desc_lines, font_desc, draw, DESC_LINE_SPACING)

    gap           = BLOCK_GAP if desc_lines else 0
    total_block_h = title_h + gap + desc_h

    # Вычисляем Y начала блока: прижимаем вниз, но соблюдаем ограничения
    # title_bottom = block_y + title_h <= TITLE_MAX_BOTTOM
    # desc_bottom  = block_y + total_block_h <= DESC_MAX_BOTTOM
    max_y_for_title = TITLE_MAX_BOTTOM - title_h
    max_y_for_desc  = DESC_MAX_BOTTOM  - total_block_h
    block_y = min(max_y_for_title, max_y_for_desc)
    block_y = max(block_y, TEXT_ZONE_TOP)   # не выше верхней границы зоны

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


def _dark_gradient(draw: ImageDraw.Draw, w: int, h: int,
                   top: tuple, bottom: tuple) -> None:
    """Рисует вертикальный градиент на уже созданном draw-объекте."""
    for y in range(h):
        t = y / (h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


# ── Вступительный баннер ──────────────────────────────────────────────────────

def create_intro_banner(date_str: str, news_count: int, output_path: str) -> str:
    """
    Создаёт вступительный баннер дайджеста (1080×1920).

    Args:
        date_str:   дата в виде "5 апреля 2026" (или любая строка)
        news_count: количество новостей в дайджесте
        output_path: куда сохранить PNG

    Returns:
        output_path
    """
    W, H = 1080, 1920

    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Градиентный фон: тёмно-синий → чёрный
    _dark_gradient(draw, W, H, (8, 12, 40), (0, 0, 0))

    # Декоративные тонкие линии по левому и правому краю
    for x_off in (30, 50):
        draw.line([(x_off, 120), (x_off, H - 120)], fill=(220, 30, 30), width=2)
        draw.line([(W - x_off, 120), (W - x_off, H - 120)], fill=(220, 30, 30), width=2)

    # Декоративные точки по краям (каждые 120px по вертикали)
    for y_dot in range(200, H - 200, 120):
        draw.ellipse([18, y_dot - 5, 28, y_dot + 5],  fill=(220, 30, 30))
        draw.ellipse([W - 28, y_dot - 5, W - 18, y_dot + 5], fill=(220, 30, 30))

    # Шрифты
    font_huge  = _get_font(100, bold=True)
    font_big   = _get_font(80,  bold=True)
    font_med   = _get_font(50,  bold=False)
    font_small = _get_font(40,  bold=False)
    font_xs    = _get_font(35,  bold=False)

    # ── Красный круг-иконка сверху ───────────────────────────────────────────
    icon_cy = 370
    icon_r  = 130
    draw.ellipse(
        [W // 2 - icon_r, icon_cy - icon_r, W // 2 + icon_r, icon_cy + icon_r],
        fill=(220, 30, 30),
    )
    # Иконка глобуса
    draw.text((W // 2, icon_cy), "🌍", font=font_huge, anchor="mm", fill=(255, 255, 255))

    # ── Надпись "НОВОСТИ" ─────────────────────────────────────────────────────
    y_news = 580
    draw.text((W // 2 + 4, y_news + 4), "НОВОСТИ", font=font_huge, anchor="mm", fill=(0, 0, 0))
    draw.text((W // 2,     y_news),     "НОВОСТИ", font=font_huge, anchor="mm", fill=(255, 255, 255))

    # ── Текущая дата ─────────────────────────────────────────────────────────
    y_date = 700
    draw.text((W // 2, y_date), date_str, font=font_med, anchor="mm", fill=(160, 160, 160))

    # ── Горизонтальная красная линия (разделитель) ────────────────────────────
    line_y = 790
    draw.rectangle([(80, line_y), (W - 80, line_y + 5)], fill=(220, 30, 30))

    # ── "ТОП СОБЫТИЙ" ────────────────────────────────────────────────────────
    y_top = 910
    draw.text((W // 2 + 4, y_top + 4), "ТОП СОБЫТИЙ", font=font_big, anchor="mm", fill=(0, 0, 0))
    draw.text((W // 2,     y_top),     "ТОП СОБЫТИЙ", font=font_big, anchor="mm", fill=(255, 255, 255))

    # ── Количество новостей ───────────────────────────────────────────────────
    y_count = 1040
    count_text = f"{news_count} новостей сегодня"
    draw.text((W // 2, y_count), count_text, font=font_med, anchor="mm", fill=(220, 30, 30))

    # ── Декоративные точки по центру ─────────────────────────────────────────
    for i, x_dot in enumerate(range(380, 720, 60)):
        color = (220, 30, 30) if i % 2 == 0 else (100, 100, 100)
        dot_r = 8
        draw.ellipse([x_dot - dot_r, 1160 - dot_r, x_dot + dot_r, 1160 + dot_r], fill=color)

    # ── Горизонтальная линия снизу ────────────────────────────────────────────
    draw.rectangle([(80, 1240), (W - 80, 1245)], fill=(220, 30, 30))

    # ── Название канала ───────────────────────────────────────────────────────
    y_channel = 1700
    draw.text((W // 2 + 3, y_channel + 3), "@todayrealnews",
              font=font_small, anchor="mm", fill=(0, 0, 0))
    draw.text((W // 2, y_channel), "@todayrealnews",
              font=font_small, anchor="mm", fill=(255, 255, 255))

    # ── Призыв подписаться ───────────────────────────────────────────────────
    y_sub = 1790
    draw.text((W // 2, y_sub), "Подпишись чтобы не пропустить",
              font=font_xs, anchor="mm", fill=(140, 140, 140))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"    ✅ Вступительный баннер создан: {output_path}")
    return output_path


# ── Баннер подписки ───────────────────────────────────────────────────────────

def create_subscribe_banner(output_path: str) -> str:
    """
    Создаёт финальный рекламный баннер «Подпишись на канал» в формате TikTok (1080×1920).
    Возвращает путь к сохранённому файлу.
    """
    W, H = 1080, 1920

    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Градиентный фон: тёмно-синий сверху → чёрный снизу
    _dark_gradient(draw, W, H, (5, 5, 20), (0, 0, 0))

    # Декоративные круги на фоне
    for _ in range(8):
        cx = random.randint(0, W)
        cy = random.randint(0, H)
        cr = random.randint(100, 400)
        draw.ellipse(
            [cx - cr, cy - cr, cx + cr, cy + cr],
            fill=None,
            outline=(255, 50, 50),
        )

    # Шрифты
    font_big   = _get_font(90, bold=True)
    font_med   = _get_font(55, bold=True)
    font_small = _get_font(40, bold=False)

    # Красный круг-иконка сверху
    icon_y = 400
    icon_r = 110
    draw.ellipse(
        [W // 2 - icon_r, icon_y - icon_r, W // 2 + icon_r, icon_y + icon_r],
        fill=(220, 30, 30),
    )
    draw.text((W // 2, icon_y), "📰", font=font_big, anchor="mm", fill=(255, 255, 255))

    # Горизонтальный разделитель под иконкой
    draw.rectangle([(100, icon_y + icon_r + 50), (W - 100, icon_y + icon_r + 55)],
                   fill=(220, 30, 30))

    # Главный текст
    text_blocks = [
        ("СЛЕДИ ЗА",   font_med, (180, 180, 180), 720),
        ("МИРОВЫМИ",   font_big, (255, 255, 255), 840),
        ("НОВОСТЯМИ",  font_big, (255, 255, 255), 960),
        ("ПЕРВЫМ!",    font_big, (220,  30,  30), 1090),
    ]
    for text, font, color, y in text_blocks:
        draw.text((W // 2 + 3, y + 3), text, font=font, anchor="mm", fill=(0, 0, 0))
        draw.text((W // 2, y),          text, font=font, anchor="mm", fill=color)

    # Горизонтальный разделитель перед кнопкой
    draw.rectangle([(100, 1210), (W - 100, 1215)], fill=(220, 30, 30))

    # Кнопка «ПОДПИСАТЬСЯ»
    btn_y = 1340
    draw.rounded_rectangle(
        [150, btn_y - 65, W - 150, btn_y + 65],
        radius=42,
        fill=(220, 30, 30),
    )
    draw.text((W // 2, btn_y), "👆 ПОДПИСАТЬСЯ",
              font=font_med, anchor="mm", fill=(255, 255, 255))

    # Название канала
    draw.text((W // 2, 1490), "@todayrealnews",
              font=font_med, anchor="mm", fill=(255, 255, 255))

    # Подпись снизу
    draw.text((W // 2, 1660), "Актуальные новости",
              font=font_small, anchor="mm", fill=(150, 150, 150))
    draw.text((W // 2, 1720), "каждый день в 10:00",
              font=font_small, anchor="mm", fill=(150, 150, 150))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"    ✅ Баннер подписки создан: {output_path}")
    return output_path
