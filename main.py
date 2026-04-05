"""
news_to_image — главный модуль (вьетнамская версия).
Парсит новости (мировые + Вьетнам) → фильтрует кэш → дедуплицирует
→ переводит на вьетнамский → генерирует PNG (два стиля)
→ создаёт PDF → сохраняет отчёт
→ отправляет интро → новости → баннер подписки в Telegram
→ обновляет кэш.

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
from parsers import (
    parse_lenta,
    parse_ria,
    parse_bbc,
    parse_vnexpress,
    parse_tuoitre,
    parse_dantri,
)
from parsers.vnexpress import get_news as get_vnexpress
from parsers.tuoitre import get_news as get_tuoitre
from parsers.dantri import get_news as get_dantri
from generator import create_image
from generator.image_gen import create_intro_banner, create_subscribe_banner
from telegram_sender import send_all
from cache_manager import filter_new_news, add_to_cache

# ── Настройки ─────────────────────────────────────────────────────────────────

# Порог схожести заголовков (0.0–1.0): >= порога → дубль
DEDUP_THRESHOLD = 0.60

# Задержка между запросами к переводчику (секунды)
TRANSLATE_DELAY = 0.3

# Источники уже на вьетнамском — не переводить
VIETNAMESE_SOURCES = {"VnExpress", "Tuoi Tre", "Dan Tri"}

# Пути к баннерам
INTRO_PATH  = os.path.join(IMAGES_DIR, "000_intro_banner.png")
BANNER_PATH = os.path.join(IMAGES_DIR, "000_subscribe_banner.png")

# Названия месяцев на вьетнамском
_VI_MONTHS = {
    1:  "thang 1",  2:  "thang 2",  3:  "thang 3",
    4:  "thang 4",  5:  "thang 5",  6:  "thang 6",
    7:  "thang 7",  8:  "thang 8",  9:  "thang 9",
    10: "thang 10", 11: "thang 11", 12: "thang 12",
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


def _vi_date(d: date) -> str:
    """Возвращает дату в формате «ngay 5 thang 4 nam 2026»."""
    return f"ngay {d.day} {_VI_MONTHS[d.month]} nam {d.year}"


# ── Сбор новостей ─────────────────────────────────────────────────────────────

def collect_all_news() -> list[dict]:
    """Запускает все парсеры и возвращает объединённый список."""
    print("\n" + "=" * 60)
    print("  СБОР НОВОСТЕЙ")
    print("=" * 60)

    all_news: list[dict] = []

    # ── Мировые источники ─────────────────────────────────────────────────────
    world_parsers = [
        ("Lenta.ru",    parse_lenta),
        ("RIA Novosti", parse_ria),
        ("BBC News",    parse_bbc),
    ]

    for source_name, parser_func in world_parsers:
        print(f"\n→ Парсинг {source_name}...")
        try:
            news = parser_func()
            all_news.extend(news)
            print(f"  Получено: {len(news)} новостей")
        except Exception as e:
            print(f"  [ОШИБКА] {source_name}: {e}")
        time.sleep(1)

    # ── Вьетнамские источники ─────────────────────────────────────────────────
    print(f"\n🇻🇳 Парсим VnExpress...")
    try:
        vn_news = get_vnexpress()
        all_news.extend(vn_news)
        print(f"  Получено: {len(vn_news)} новостей")
    except Exception as e:
        print(f"  [ОШИБКА] VnExpress: {e}")
    time.sleep(1)

    print(f"\n🇻🇳 Парсим Tuoi Tre...")
    try:
        tt_news = get_tuoitre()
        all_news.extend(tt_news)
        print(f"  Получено: {len(tt_news)} новостей")
    except Exception as e:
        print(f"  [ОШИБКА] Tuoi Tre: {e}")
    time.sleep(1)

    print(f"\n🇻🇳 Парсим Dan Tri...")
    try:
        dt_news = get_dantri()
        all_news.extend(dt_news)
        print(f"  Получено: {len(dt_news)} новостей")
    except Exception as e:
        print(f"  [ОШИБКА] Dan Tri: {e}")
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


# ── Перевод на вьетнамский ────────────────────────────────────────────────────

def _make_translator():
    """
    Возвращает функцию перевода на вьетнамский через GoogleTranslator.
    Если deep-translator не установлен — возвращает заглушку.
    """
    try:
        from deep_translator import GoogleTranslator

        def translate(text: str) -> str:
            if not text or not text.strip():
                return text
            try:
                result = GoogleTranslator(source="auto", target="vi").translate(text)
                return result if result else text
            except Exception as e:
                print(f"    [Translate] Ошибка: {e}")
                return text

        return translate

    except ImportError:
        print("[Translate] ВНИМАНИЕ: deep-translator не установлен.")
        print("            Запустите: pip install deep-translator")
        print("            Перевод пропускается.")
        return lambda text: text


def translate_all_news(news_list: list[dict]) -> list[dict]:
    """
    Переводит title и description на вьетнамский.
    Новости из вьетнамских источников (VnExpress, Tuoi Tre, Dan Tri) — пропускаются.
    """
    print("\n" + "=" * 60)
    print("  ПЕРЕВОД НА ВЬЕТНАМСКИЙ")
    print("=" * 60)

    translate = _make_translator()
    total = len(news_list)
    translated_count = 0
    skipped_count = 0

    for idx, item in enumerate(news_list, 1):
        source = item.get("source", "")

        # Вьетнамские источники уже на вьетнамском — пропустить
        if source in VIETNAMESE_SOURCES:
            skipped_count += 1
            continue

        title_orig = item.get("title", "")
        desc_orig  = item.get("description", "")

        print(f"  [{idx}/{total}] {source}: {title_orig[:60]}...")

        title_vi = translate(title_orig)
        if title_vi != title_orig:
            item["title"] = title_vi
            translated_count += 1

        if desc_orig:
            desc_vi = translate(desc_orig)
            if desc_vi != desc_orig:
                item["description"] = desc_vi

        time.sleep(TRANSLATE_DELAY)

    print(f"\n[Перевод] Переведено: {translated_count}, пропущено (уже VI): {skipped_count}")
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
        title     = news_item.get("title",  "untitled")
        source    = news_item.get("source", "unknown")
        news_type = news_item.get("type",   "world")

        print(f"\n[{idx}/{total}] [{news_type.upper()}] {source}: {title[:60]}...")

        safe_title  = sanitize_filename(title)
        safe_source = sanitize_filename(source).replace(" ", "_")
        filename    = f"{idx:03d}_{safe_source}_{safe_title}.png"
        output_path = os.path.join(IMAGES_DIR, filename)

        success = create_image(news_item, output_path)

        if success:
            print(f"    ✓ Сохранено: {filename}")
            created_paths.append(output_path)
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
    lines.append(f"DIGEST TIN TUC — {date_str}")
    lines.append(sep_long)
    lines.append("")

    json_records: list[dict] = []

    for idx, item in enumerate(news_list, 1):
        title       = item.get("title",       "").strip() or "Khong co du lieu"
        source      = item.get("source",      "").strip() or "Khong co du lieu"
        news_type   = item.get("type",        "world")
        description = item.get("description", "").strip() or "Khong co du lieu"
        image_path  = item.get("image_path",  "").strip() or "Khong co du lieu"
        url         = item.get("url",         "").strip() or "Khong co du lieu"

        lines.append(f"TIN #{idx} [{news_type.upper()}]")
        lines.append(sep_short)
        lines.append(f"Tieu de:   {title}")
        lines.append(f"Nguon:     {source}")
        lines.append(f"Mo ta:     {description}")
        lines.append(f"Hinh anh:  {image_path}")
        lines.append(f"Lien ket:  {url}")
        lines.append(sep_short)
        lines.append("")

        json_records.append({
            "number":      idx,
            "title":       title,
            "source":      source,
            "type":        news_type,
            "description": description,
            "image_path":  image_path,
            "url":         url,
        })

    lines.append(sep_long)
    lines.append(f"TONG CONG: {total} tin tuc")
    lines.append(f"Tao luc: {date_str} luc {time_str}")
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
    print("  HOAN THANH / ГОТОВО")
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
    start   = time.time()
    today   = date.today()
    date_vi = _vi_date(today)

    print("\n" + "=" * 60)
    print("  NEWS TO IMAGE — Ban tin hang ngay")
    print(f"  Ngay: {today.strftime('%d.%m.%Y')}")
    print("=" * 60)

    # 1. Директории
    ensure_directories()

    # 2. Сбор новостей (мировые + 3 вьетнамских источника)
    all_news = collect_all_news()
    if not all_news:
        print("\n[ВНИМАНИЕ] Новости не найдены. Проверьте подключение к интернету.")
        sys.exit(1)

    # 3. Фильтрация по кэшу
    print("\n" + "=" * 60)
    print("  LOC CACHE / ФИЛЬТРАЦИЯ КЭША")
    print("=" * 60)
    all_news = filter_new_news(all_news)

    if len(all_news) == 0:
        print("\n📭 Khong co tin moi! / Нет новых новостей!")
        sys.exit(0)

    # 4. Дедупликация
    print("\n" + "=" * 60)
    print("  LOC TRUNG LAP / ДЕДУПЛИКАЦИЯ")
    print("=" * 60)
    unique_news = deduplicate(all_news)

    # 5. Перевод на вьетнамский (VnExpress / Tuoi Tre / Dan Tri — пропускаются)
    unique_news = translate_all_news(unique_news)

    # 6. Генерация изображений (два стиля: world / vietnam)
    image_paths = generate_images(unique_news)

    # 7. PDF
    pdf_path = create_pdf(image_paths)

    # 8. Отчёт
    print("\n" + "=" * 60)
    print("  LUU BAO CAO / СОХРАНЕНИЕ ОТЧЁТА")
    print("=" * 60)
    save_news_report(unique_news)

    # 9. Генерация баннеров (только создание PNG, без отправки)
    print("\n" + "=" * 60)
    print("  TAO BANNER / СОЗДАНИЕ БАННЕРОВ")
    print("=" * 60)
    create_intro_banner(date_vi, len(unique_news), INTRO_PATH)
    create_subscribe_banner(BANNER_PATH)

    # 10. Отправка в Telegram: удалить старые → интро → новости → подписка
    print("\n" + "=" * 60)
    print("  GUI TELEGRAM / ОТПРАВКА В TELEGRAM")
    print("=" * 60)
    intro_caption = (
        f"🗞 Chao buoi sang! Hom nay {date_vi}\n\n"
        f"⚡️ {len(unique_news)} tin tuc chinh trong ngay\n\n"
        f"📲 @todayrealnews"
    )
    send_all(
        unique_news,
        IMAGES_DIR,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        intro_caption=intro_caption,
    )

    # 11. Обновление кэша
    print("\n" + "=" * 60)
    print("  CAP NHAT CACHE / ОБНОВЛЕНИЕ КЭША")
    print("=" * 60)
    for news in unique_news:
        add_to_cache(news)
    print(f"[Кэш] Добавлено {len(unique_news)} новостей в output/published_news.json")

    # 12. Итог
    elapsed = time.time() - start
    print(f"\n  Thoi gian thuc hien: {elapsed:.1f} giay")
    print_summary(len(all_news), len(unique_news), len(image_paths), pdf_path)


if __name__ == "__main__":
    main()
