"""
Менеджер кэша опубликованных новостей.

Хранит список уже отправленных в Telegram новостей в файле
output/published_news.json, чтобы избежать повторных публикаций.

Максимальный размер кэша: 200 последних записей (старые удаляются).
"""

import json
import os
from datetime import datetime
from pathlib import Path

CACHE_FILE = "output/published_news.json"


def load_cache() -> list[dict]:
    """Загружает кэш из файла. Если файл не существует — возвращает пустой список."""
    if not Path(CACHE_FILE).exists():
        return []
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_cache(cache: list[dict]) -> None:
    """Сохраняет кэш в файл."""
    Path("output").mkdir(exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_already_published(title: str, cache: list[dict]) -> bool:
    """
    Проверяет, был ли заголовок уже опубликован.
    Сравниваются первые 50 символов в нижнем регистре без пробелов по краям.
    """
    title_short = title[:50].lower().strip()
    return any(
        item.get("title", "")[:50].lower().strip() == title_short
        for item in cache
    )


def add_to_cache(news_item: dict) -> None:
    """
    Добавляет одну новость в кэш и сохраняет файл.
    Кэш ограничен 200 последними записями: старые удаляются.
    """
    cache = load_cache()
    cache.append({
        "title":        news_item.get("title",  ""),
        "source":       news_item.get("source", ""),
        "published_at": datetime.now().isoformat(),
    })
    if len(cache) > 200:
        cache = cache[-200:]
    save_cache(cache)


def filter_new_news(news_list: list[dict]) -> list[dict]:
    """
    Возвращает только те новости, которых ещё нет в кэше.
    Выводит в консоль статистику: сколько новых, сколько пропущено.
    """
    cache = load_cache()
    new_news: list[dict] = []
    skipped = 0

    for news in news_list:
        title = news.get("title", "")
        if is_already_published(title, cache):
            skipped += 1
            print(f"  ⏭️  Пропускаем (уже публиковалось): {title[:60]}")
        else:
            new_news.append(news)

    print(f"\n  ✅ Новых новостей: {len(new_news)} | Пропущено дублей кэша: {skipped}")
    return new_news
