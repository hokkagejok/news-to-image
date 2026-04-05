"""
news_to_image — главный модуль.
Парсит новости → фильтрует кэш → дедуплицирует → переводит на русский
→ генерирует PNG → создаёт PDF → сохраняет отчёт
→ отправляет интро-баннер → отправляет новости в Telegram
→ отправляет баннер подписки → обновляет кэш.

Запуск: python main.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, date
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    IMAGES_DIR, OUTPUT_DIR, PDF_DIR, PDF_FILENAME,
    IMAGE_WIDTH, IMAGE_HEIGHT,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from parsers import parse_lenta, parse_ria, parse_bbc
from generator import create_image
from generator.image_gen import create_intro_banner, create_subscribe_banner
from telegram_sender import send_all, send_banner
from cache_manager import filter_new_news, add_to_cache

# ── Настройки ─────────────────────────────────────────────────────────────────

# Порог схожести заголовков (0.0–1.0): >= порога → дубль
DEDUP_THRESHOLD = 0.60

# Задержка между запросами к Google Translate (секунды)
TRANSLATE_DELAY = 0.3

# Источники, чьи заголовки/описания уже на русском — пропускаем перевод
RUSSIAN_SOURCES = {"Lenta.ru", "RIA Novosti"}

# Пути к баннерам
INTRO_PATH   = os.path.join(IMAGES_DIR, "000_intro_banner.png")
BANNER_PATH  = os.path.join(IMAGES_DIR, "000_subscribe_banner.png")

# Русские названия месяцев для интро-баннера
_RU_MONTHS = {
    1: "января", 2: "февраля", 3: "марта",    4: "апреля",
    5: "мая",    6: "июня",    7: "июля",     8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


# ── Вспомогательные ───────────────────────────────────────────────────────────

def ensure_directories() -> None:
    for directory in [IMAGES_DIR, PDF_DIR]:
        os.makedirs(directory, exist_ok=True)
    print("[Setup] Директории готовы: output/images, output/pdf")


def sanitize_filename(text: str, max_length: int = 60) -> str:
    invalid_chars = r'<>:"/\|?*'
    for ch in invalid_chars:
        text = text.replace(ch, "_")
    text = text.strip(". ").replace("  ", " ")
    return text[:max_length]


def _ru_date(d: date) -> str:
    """Возвращает дату в формате «5 апреля 2026»."""
    return f"{d.day} {_RU_MONTHS[d.month]} {d.year}"


# ── Сбор новостей ─────────────────────────────────────────────────────────────

def collect_all_news() -> list[dict]:
    """Запускает все парсеры и возвращает объединённый список."""
    print("\n" + "=" * 60)
    print("  СБОР НОВОСТЕЙ")
    print("=" * 60)

    all_news: list[dict] = []
    parsers = [
        ("Lenta.ru",    parse_lenta),
        ("RIA Novosti", parse_ria),
        ("BBC News",    parse_bbc),
    ]

    for source_name, parser_func in parsers:
        print(f"\n→ Парсинг {source_name}...")
        try:
            news = parser_func()
            all_news.extend(news)
            print(f"  Получено: {len(news)} новостей")
        except Exception as e:
            print(f"  [ОШИБКА] {source_name}: {e}")
        time.sleep(1)

    print(f"\n[Итого до фильтрации] {len(all_news)} новостей.")
    return all_news


# ── Дедупликация ──────────────────────────────────────────────────────────────

def _title_key(title: str) -> str:
    """Нормализует заголовок: lowercase, без пунктуации."""
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", " ", title)
    return " ".join(title.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def deduplicate(news_list: list[dict]) -> list[dict]:
    """
    Убирает дублирующиеся новости.
    При совпадении оставляет ту, у которой есть картинка.
    """
    if not news_list:
        return []

    keys = [_title_key(n.get("title", "")) for n in news_list]
    n = len(news_list)
    excluded = [False] * n

    for i in range(n):
        if excluded[i]:
            continue
        for j in range(i + 1, n):
            if excluded[j]:
                continue
            if _similarity(keys[i], keys[j]) >= DEDUP_THRESHOLD:
                i_has_img = bool(news_list[i].get("image_url"))
                j_has_img = bool(news_list[j].get("image_url"))
                if j_has_img and not i_has_img:
                    excluded[i] = True
                    break
                else:
                    excluded[j] = True

    unique = [item for idx, item in enumerate(news_list) if not excluded[idx]]
    removed = n - len(unique)
    if removed:
        print(f"[Дедупликация] Удалено {removed} дублей (порог: {DEDUP_THRESHOLD:.0%}).")
    else:
        print("[Дедупликация] Дубли не найдены.")
    print(f"[Дедупликация] Уникальных новостей: {len(unique)}")
    return unique


# ── Перевод на русский ────────────────────────────────────────────────────────

def _make_translator():
    """
    Возвращает функцию перевода через GoogleTranslator.
    Если deep-translator не установлен — возвращает заглушку (текст без изменений).
    """
    try:
        from deep_translator import GoogleTranslator

        def translate(text: str) -> str:
            if not text or not text.strip():
                return text
            try:
                result = GoogleTranslator(source="auto", target="ru").translate(text)
                return result if result else text
            except Exception as e:
                print(f"    [Translate] Ошибка: {e}")
                return text

        return translate

    except ImportError:
        print("[Translate] ВНИМАНИЕ: deep-translator не установлен.")
        print("            Запустите: pip install deep-translator")
        print("            Перевод пропускается, заголовки останутся на оригинальном языке.")
        return lambda text: text


def translate_all_news(news_list: list[dict]) -> list[dict]:
    """
    Переводит title и description каждой новости на русский язык.
    Новости из русскоязычных источников (Lenta, RIA) — пропускаются.
    """
    print("\n" + "=" * 60)
    print("  ПЕРЕВОД НА РУССКИЙ")
    print("=" * 60)

    translate = _make_translator()
    total = len(news_list)
    translated_count = 0
    skipped_count = 0

    for idx, item in enumerate(news_list, 1):
        source = item.get("source", "")

        if source in RUSSIAN_SOURCES:
            skipped_count += 1
            continue

        title_orig = item.get("title", "")
        desc_orig  = item.get("description", "")

        print(f"  [{idx}/{total}] {source}: {title_orig[:60]}...")

        title_ru = translate(title_orig)
        if title_ru != title_orig:
            item["title"] = title_ru
            translated_count += 1

        if desc_orig:
            desc_ru = translate(desc_orig)
            if desc_ru != desc_orig:
                item["description"] = desc_ru

        time.sleep(TRANSLATE_DELAY)

    print(f"\n[Перевод] Переведено: {translated_count}, пропущено (уже RU): {skipped_count}")
    return news_list


# ── Генерация изображений ─────────────────────────────────────────────────────

def generate_images(news_list: list[dict]) -> list[str]:
    """Создаёт PNG для каждой новости. Возвращает список путей."""
    print("\n" + "=" * 60)
    print("  ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ")
    print("=" * 60)

    created_paths: list[str] = []
    total = len(news_list)

    for idx, news_item in enumerate(news_list, 1):
        title  = news_item.get("title",  "untitled")
        source = news_item.get("source", "unknown")

        print(f"\n[{idx}/{total}] {source}: {title[:70]}...")

        safe_title  = sanitize_filename(title)
        safe_source = sanitize_filename(source).replace(" ", "_")
        filename    = f"{idx:03d}_{safe_source}_{safe_title}.png"
        output_path = os.path.join(IMAGES_DIR, filename)

        success = create_image(news_item, output_path)

        if success:
            print(f"    ✓ Сохранено: {filename}")
            created_paths.append(output_path)
            # Сохраняем путь к картинке в новость для отчёта и Telegram
            news_item["image_path"] = os.path.relpath(output_path)
        else:
            print(f"    ✗ Ошибка при создании изображения")
            news_item["image_path"] = ""

    print(f"\n[Изображения] Создано {len(created_paths)} из {total}.")
    return created_paths


# ── Создание PDF ──────────────────────────────────────────────────────────────

def create_pdf(image_paths: list[str]) -> str:
    """Создаёт PDF, каждая страница = одна картинка. Возвращает путь к файлу."""
    print("\n" + "=" * 60)
    print("  СОЗДАНИЕ PDF")
    print("=" * 60)

    if not image_paths:
        print("[PDF] Нет изображений.")
        return ""

    try:
        from fpdf import FPDF
    except ImportError:
        print("[PDF] ОШИБКА: fpdf2 не установлен. Запустите: pip install fpdf2")
        return ""

    today_str    = date.today().strftime("%Y-%m-%d")
    pdf_filename = f"{today_str}_{PDF_FILENAME}"
    pdf_path     = os.path.join(PDF_DIR, pdf_filename)

    page_w, page_h = 297.0, 210.0   # A4 landscape, мм

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_title(f"News Digest {today_str}")
    pdf.set_author("news_to_image")

    img_ratio  = IMAGE_WIDTH / IMAGE_HEIGHT
    page_ratio = page_w / page_h

    total = len(image_paths)
    for idx, img_path in enumerate(image_paths, 1):
        if not os.path.exists(img_path):
            print(f"  [{idx}/{total}] Пропускаю (не найден): {img_path}")
            continue
        try:
            pdf.add_page()
            if img_ratio > page_ratio:
                draw_w = page_w
                draw_h = page_w / img_ratio
            else:
                draw_h = page_h
                draw_w = page_h * img_ratio
            x = (page_w - draw_w) / 2
            y = (page_h - draw_h) / 2
            pdf.image(img_path, x=x, y=y, w=draw_w, h=draw_h)
            print(f"  [{idx}/{total}] Добавлено: {os.path.basename(img_path)}")
        except Exception as e:
            print(f"  [{idx}/{total}] Ошибка: {e}")
            continue

    try:
        pdf.output(pdf_path)
        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        print(f"\n[PDF] Создан: {pdf_filename} ({size_mb:.1f} МБ)")
        return pdf_path
    except Exception as e:
        print(f"[PDF] Ошибка при сохранении: {e}")
        return ""


# ── Отчёт ─────────────────────────────────────────────────────────────────────

def save_news_report(news_list: list[dict]) -> None:
    """
    Создаёт два файла отчёта:
      - output/news_report.txt — читаемый текстовый дайджест
      - output/news_report.json — те же данные в формате JSON
    """
    now       = datetime.now()
    date_str  = now.strftime("%d.%m.%Y")
    time_str  = now.strftime("%H:%M")
    sep_long  = "=" * 60
    sep_short = "-" * 60
    total     = len(news_list)

    txt_path  = os.path.join(OUTPUT_DIR, "news_report.txt")
    json_path = os.path.join(OUTPUT_DIR, "news_report.json")

    lines: list[str] = []
    lines.append(sep_long)
    lines.append(f"ДАЙДЖЕСТ НОВОСТЕЙ — {date_str}")
    lines.append(sep_long)
    lines.append("")

    json_records: list[dict] = []

    for idx, item in enumerate(news_list, 1):
        title       = item.get("title",       "").strip() or "Нет данных"
        source      = item.get("source",      "").strip() or "Нет данных"
        description = item.get("description", "").strip() or "Нет данных"
        image_path  = item.get("image_path",  "").strip() or "Нет данных"
        url         = item.get("url",         "").strip() or "Нет данных"

        lines.append(f"📰 НОВОСТЬ #{idx}")
        lines.append(sep_short)
        lines.append(f"Заголовок:    {title}")
        lines.append(f"Источник:     {source}")
        lines.append(f"Описание:     {description}")
        lines.append(f"Картинка:     {image_path}")
        lines.append(f"Ссылка:       {url}")
        lines.append(sep_short)
        lines.append("")

        json_records.append({
            "number":      idx,
            "title":       title,
            "source":      source,
            "description": description,
            "image_path":  image_path,
            "url":         url,
        })

    lines.append(sep_long)
    lines.append(f"ИТОГО: {total} новостей")
    lines.append(f"Сгенерировано: {date_str} в {time_str}")
    lines.append(sep_long)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    json_data = {
        "date":  date_str,
        "time":  time_str,
        "total": total,
        "news":  json_records,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    print(f"📄 Отчёт сохранён: output/news_report.txt")
    print(f"📄 JSON сохранён:  output/news_report.json")


# ── Итог ──────────────────────────────────────────────────────────────────────

def print_summary(
    raw_count: int,
    unique_count: int,
    images_count: int,
    pdf_path: str,
) -> None:
    print("\n" + "=" * 60)
    print("  ГОТОВО")
    print("=" * 60)
    print(f"  Собрано новостей:     {raw_count}")
    print(f"  После фильтрации:     {unique_count}")
    print(f"  Создано изображений:  {images_count}")
    if pdf_path:
        print(f"  PDF сохранён:         {os.path.relpath(pdf_path)}")
    else:
        print(f"  PDF:                  не создан")
    print(f"  Папка изображений:    output/images/")
    print("=" * 60)


# ── Точка входа ───────────────────────────────────────────────────────────────

def main() -> None:
    start    = time.time()
    today    = date.today()
    date_ru  = _ru_date(today)

    print("\n" + "=" * 60)
    print("  NEWS TO IMAGE — Новостной дайджест")
    print(f"  Дата: {today.strftime('%d.%m.%Y')}")
    print("=" * 60)

    # 1. Директории
    ensure_directories()

    # 2. Сбор
    all_news = collect_all_news()
    if not all_news:
        print("\n[ВНИМАНИЕ] Новости не найдены. Проверьте подключение к интернету.")
        sys.exit(1)

    # 3. Фильтрация по кэшу (убираем уже опубликованные)
    print("\n" + "=" * 60)
    print("  ФИЛЬТРАЦИЯ КЭША")
    print("=" * 60)
    all_news = filter_new_news(all_news)

    if len(all_news) == 0:
        print("\n📭 Нет новых новостей для публикации!")
        sys.exit(0)

    # 4. Дедупликация внутри текущей выборки
    print("\n" + "=" * 60)
    print("  ДЕДУПЛИКАЦИЯ")
    print("=" * 60)
    unique_news = deduplicate(all_news)

    # 5. Перевод на русский (только иноязычные источники)
    unique_news = translate_all_news(unique_news)

    # 6. Генерация изображений
    image_paths = generate_images(unique_news)

    # 7. PDF
    pdf_path = create_pdf(image_paths)

    # 8. Отчёт
    print("\n" + "=" * 60)
    print("  СОХРАНЕНИЕ ОТЧЁТА")
    print("=" * 60)
    save_news_report(unique_news)

    # 9. Вступительный баннер — создаём и отправляем ПЕРВЫМ
    print("\n" + "=" * 60)
    print("  ВСТУПИТЕЛЬНЫЙ БАННЕР")
    print("=" * 60)
    create_intro_banner(date_ru, len(unique_news), INTRO_PATH)
    intro_caption = (
        f"🗞 Доброе утро! Сегодня {date_ru}\n\n"
        f"⚡️ {len(unique_news)} главных новостей дня\n\n"
        f"📲 @todayrealnews"
    )
    send_banner(INTRO_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, caption=intro_caption)

    # 10. Отправка всех новостей в Telegram
    print("\n" + "=" * 60)
    print("  ОТПРАВКА НОВОСТЕЙ В TELEGRAM")
    print("=" * 60)
    print("📤 Отправка новостей в Telegram...")
    send_all(unique_news, IMAGES_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    print("✅ Все новости отправлены в Telegram!")

    # 11. Баннер подписки — отправляем ПОСЛЕДНИМ
    print("\n" + "=" * 60)
    print("  БАННЕР ПОДПИСКИ")
    print("=" * 60)
    create_subscribe_banner(BANNER_PATH)
    send_banner(BANNER_PATH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    # 12. Добавляем опубликованные новости в кэш
    print("\n" + "=" * 60)
    print("  ОБНОВЛЕНИЕ КЭША")
    print("=" * 60)
    for news in unique_news:
        add_to_cache(news)
    print(f"[Кэш] Добавлено {len(unique_news)} новостей в output/published_news.json")

    # 13. Итог
    elapsed = time.time() - start
    print(f"\n  Время выполнения: {elapsed:.1f} сек.")
    print_summary(len(all_news), len(unique_news), len(image_paths), pdf_path)


if __name__ == "__main__":
    main()
