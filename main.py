"""
news_to_image — главный модуль (вьетнамская версия).

Конвейер:
  1. Сбор новостей  — Lenta, RIA, BBC + VnExpress, Tuoi Tre, Dan Tri
  2. Фильтрация кэша — пропускаем уже опубликованные
  3. Дедупликация   — убираем похожие заголовки
  4. Перевод        — мировые → вьетнамский; вьетнамские — пропускаем
  5. Генерация PNG  — TikTok 1080×1920, два стиля (world / vietnam)
  6. Telegram       — удалить старые → интро → новости → подписка
  7. Кэш            — сохранить опубликованные в published_news.json

Запуск: python main.py
"""

import os
import re
import sys
import time
from datetime import date
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    IMAGES_DIR, OUTPUT_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from parsers import (
    parse_lenta,
    parse_ria,
    parse_bbc,
)
from parsers.vnexpress import get_news as get_vnexpress
from parsers.tuoitre   import get_news as get_tuoitre
from parsers.dantri    import get_news as get_dantri
from generator import create_image
from generator.image_gen import create_intro_banner, create_subscribe_banner
from telegram_sender import send_all
from cache_manager import filter_new_news, add_to_cache

# ── Настройки ─────────────────────────────────────────────────────────────────

DEDUP_THRESHOLD   = 0.60   # Порог схожести заголовков (0–1)
TRANSLATE_DELAY   = 0.3    # Задержка между запросами к переводчику (сек)

# Источники уже на вьетнамском — перевод пропускается
VIETNAMESE_SOURCES = {"VnExpress", "Tuoi Tre", "Dan Tri"}

# Пути к баннерам
INTRO_PATH  = os.path.join(IMAGES_DIR, "000_intro_banner.png")
BANNER_PATH = os.path.join(IMAGES_DIR, "000_subscribe_banner.png")

# Названия месяцев на вьетнамском
_VI_MONTHS = {
    1: "thang 1",  2: "thang 2",  3: "thang 3",
    4: "thang 4",  5: "thang 5",  6: "thang 6",
    7: "thang 7",  8: "thang 8",  9: "thang 9",
    10: "thang 10", 11: "thang 11", 12: "thang 12",
}


# ── Вспомогательные ───────────────────────────────────────────────────────────

def ensure_directories() -> None:
    os.makedirs(IMAGES_DIR, exist_ok=True)
    print("[Setup] Директория готова: output/images")


def sanitize_filename(text: str, max_length: int = 60) -> str:
    for ch in r'<>:"/\|?*':
        text = text.replace(ch, "_")
    return text.strip(". ").replace("  ", " ")[:max_length]


def _vi_date(d: date) -> str:
    return f"ngay {d.day} {_VI_MONTHS[d.month]} nam {d.year}"


# ── Сбор новостей ─────────────────────────────────────────────────────────────

def collect_all_news() -> list[dict]:
    """Запускает все парсеры и возвращает объединённый список."""
    print("\n" + "=" * 60)
    print("  СБОР НОВОСТЕЙ")
    print("=" * 60)

    all_news: list[dict] = []

    # Мировые источники
    for source_name, parser_func in [
        ("Lenta.ru",    parse_lenta),
        ("RIA Novosti", parse_ria),
        ("BBC News",    parse_bbc),
    ]:
        print(f"\n→ Парсинг {source_name}...")
        try:
            news = parser_func()
            all_news.extend(news)
            print(f"  Получено: {len(news)} новостей")
        except Exception as e:
            print(f"  [ОШИБКА] {source_name}: {e}")
        time.sleep(1)

    # Вьетнамские источники
    for label, parser_func in [
        ("VnExpress", get_vnexpress),
        ("Tuoi Tre",  get_tuoitre),
        ("Dan Tri",   get_dantri),
    ]:
        print(f"\n🇻🇳 Парсим {label}...")
        try:
            news = parser_func()
            all_news.extend(news)
            print(f"  Получено: {len(news)} новостей")
        except Exception as e:
            print(f"  [ОШИБКА] {label}: {e}")
        time.sleep(1)

    print(f"\n[Итого до фильтрации] {len(all_news)} новостей.")
    return all_news


# ── Фильтр новостей без фото ─────────────────────────────────────────────────

def filter_news_with_images(news_list: list[dict]) -> list[dict]:
    """Оставляет только новости с непустым image_url (длина > 10 символов)."""
    filtered = []
    skipped  = 0
    for news in news_list:
        if news.get("image_url") and len(news["image_url"]) > 10:
            filtered.append(news)
        else:
            skipped += 1
            print(f"  [SKIP] Нет фото: {news.get('title', '')[:50]}")
    print(f"[Фильтр фото] С фото: {len(filtered)} | Без фото (пропущено): {skipped}")
    return filtered


# ── Дедупликация ──────────────────────────────────────────────────────────────

def _title_key(title: str) -> str:
    title = title.lower().strip()
    return " ".join(re.sub(r"[^\w\s]", " ", title).split())


def deduplicate(news_list: list[dict]) -> list[dict]:
    """Убирает дублирующиеся новости. При совпадении оставляет ту, у которой есть картинка."""
    if not news_list:
        return []

    keys     = [_title_key(n.get("title", "")) for n in news_list]
    n        = len(news_list)
    excluded = [False] * n

    for i in range(n):
        if excluded[i]:
            continue
        for j in range(i + 1, n):
            if excluded[j]:
                continue
            if SequenceMatcher(None, keys[i], keys[j]).ratio() >= DEDUP_THRESHOLD:
                i_has = bool(news_list[i].get("image_url"))
                j_has = bool(news_list[j].get("image_url"))
                if j_has and not i_has:
                    excluded[i] = True
                    break
                else:
                    excluded[j] = True

    unique  = [item for idx, item in enumerate(news_list) if not excluded[idx]]
    removed = n - len(unique)
    if removed:
        print(f"[Дедупликация] Удалено {removed} дублей (порог: {DEDUP_THRESHOLD:.0%}).")
    else:
        print("[Дедупликация] Дубли не найдены.")
    print(f"[Дедупликация] Уникальных новостей: {len(unique)}")
    return unique


# ── Перевод на вьетнамский ────────────────────────────────────────────────────

def _make_translator():
    """Возвращает функцию перевода или заглушку если пакет не установлен."""
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
        return lambda text: text


def translate_all_news(news_list: list[dict]) -> list[dict]:
    """Переводит title и description на вьетнамский. Вьетнамские источники пропускаются."""
    print("\n" + "=" * 60)
    print("  ПЕРЕВОД НА ВЬЕТНАМСКИЙ")
    print("=" * 60)

    translate        = _make_translator()
    total            = len(news_list)
    translated_count = 0
    skipped_count    = 0

    for idx, item in enumerate(news_list, 1):
        source = item.get("source", "")

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


# ── Итог ──────────────────────────────────────────────────────────────────────

def print_summary(raw_count: int, unique_count: int, images_count: int) -> None:
    print("\n" + "=" * 60)
    print("  HOAN THANH / ГОТОВО")
    print("=" * 60)
    print(f"  Собрано новостей:     {raw_count}")
    print(f"  После фильтрации:     {unique_count}")
    print(f"  Создано изображений:  {images_count}")
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

    # 3. Фильтр — оставляем только новости с фото
    print("\n" + "=" * 60)
    print("  LOC ANH / ФИЛЬТР НОВОСТЕЙ БЕЗ ФОТО")
    print("=" * 60)
    all_news = filter_news_with_images(all_news)
    if not all_news:
        print("\n[ВНИМАНИЕ] Нет новостей с фото.")
        sys.exit(1)

    # 5. Фильтрация по кэшу
    print("\n" + "=" * 60)
    print("  LOC CACHE / ФИЛЬТРАЦИЯ КЭША")
    print("=" * 60)
    all_news = filter_new_news(all_news)

    if len(all_news) == 0:
        print("\n[INFO] Khong co tin moi! / Нет новых новостей!")
        sys.exit(0)

    # 6. Дедупликация
    print("\n" + "=" * 60)
    print("  LOC TRUNG LAP / ДЕДУПЛИКАЦИЯ")
    print("=" * 60)
    unique_news = deduplicate(all_news)

    # 7. Перевод на вьетнамский (VnExpress / Tuoi Tre / Dan Tri — пропускаются)
    unique_news = translate_all_news(unique_news)

    # 8. Генерация изображений (два стиля: world / vietnam)
    image_paths = generate_images(unique_news)

    # 9. Генерация баннеров
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
        f"Chao buoi sang! Hom nay {date_vi}\n\n"
        f"{len(unique_news)} tin tuc chinh trong ngay\n\n"
        f"@todayrealnews"
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
    print_summary(len(all_news), len(unique_news), len(image_paths))


if __name__ == "__main__":
    main()
