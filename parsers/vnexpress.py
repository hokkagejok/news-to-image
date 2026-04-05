"""
Парсер новостей с VnExpress (vnexpress.net) — крупнейшее вьетнамское издание.

Алгоритм:
  1. Загружаем RSS-ленту последних новостей
  2. Из каждого item берём заголовок, ссылку, описание, дату
  3. Для картинки: ищем enclosure / media:content в RSS,
     если нет — заходим на страницу статьи и берём og:image
"""

import os
import sys
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NEWS_LIMIT, REQUEST_TIMEOUT

RSS_URL     = "https://vnexpress.net/rss/tin-moi-nhat.rss"
BASE_URL    = "https://vnexpress.net"
SOURCE_NAME = "VnExpress"

ARTICLE_DELAY = 0.4   # пауза между запросами к статьям

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def parse_vnexpress() -> list[dict]:
    """
    Парсит последние новости с VnExpress через RSS.
    Возвращает список:
      {"title", "image_url", "description", "source", "type", "url"}
    type всегда "vietnam".
    """
    news_items: list[dict] = []
    print(f"[{SOURCE_NAME}] Начинаю парсинг RSS...")

    try:
        resp = requests.get(RSS_URL, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[{SOURCE_NAME}] Ошибка загрузки RSS: {e}")
        return news_items

    # Парсим XML; пробуем lxml-xml, потом xml, потом html.parser как fallback
    try:
        soup = BeautifulSoup(resp.content, "lxml-xml")
    except Exception:
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            soup = BeautifulSoup(resp.content, "html.parser")

    items = soup.find_all("item")
    if not items:
        print(f"[{SOURCE_NAME}] RSS-элементы не найдены.")
        return news_items

    print(f"[{SOURCE_NAME}] Найдено {len(items)} записей, обрабатываю первые {NEWS_LIMIT}...")

    seen_titles: set[str] = set()

    for rss_item in items[:NEWS_LIMIT * 2]:   # берём с запасом, фильтруем дубли
        if len(news_items) >= NEWS_LIMIT:
            break
        try:
            item = _parse_rss_item(rss_item)
            if not item:
                continue

            norm = item["title"].lower().strip()
            if norm in seen_titles or len(norm) < 5:
                continue
            seen_titles.add(norm)

            news_items.append(item)
            print(f"[{SOURCE_NAME}] [{len(news_items)}/{NEWS_LIMIT}] {item['title'][:80]}...")

        except Exception as e:
            print(f"[{SOURCE_NAME}] Ошибка обработки item: {e}")
            continue

    print(f"[{SOURCE_NAME}] Готово: {len(news_items)} новостей.")
    return news_items


def _parse_rss_item(item) -> dict | None:
    """
    Извлекает данные из одного <item> RSS.
    Если картинка не найдена в RSS — дополнительно загружает страницу статьи.
    """
    # ── Заголовок ─────────────────────────────────────────────────────────────
    title_tag = item.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    # Убираем CDATA-обёртку если есть
    title = title.replace("<![CDATA[", "").replace("]]>", "").strip()
    if not title or len(title) < 5:
        return None

    # ── URL статьи ────────────────────────────────────────────────────────────
    url = ""
    link_tag = item.find("link")
    if link_tag:
        # В некоторых RSS <link> стоит как текст между тегами
        url = (link_tag.get_text(strip=True) or link_tag.get("href") or "").strip()
    if not url:
        guid = item.find("guid")
        if guid:
            url = guid.get_text(strip=True)
    if not url:
        return None

    # ── Описание ──────────────────────────────────────────────────────────────
    description = ""
    desc_tag = item.find("description")
    if desc_tag:
        raw = desc_tag.get_text()
        # VnExpress часто кладёт HTML внутрь <description>
        desc_soup = BeautifulSoup(raw, "html.parser")
        description = " ".join(desc_soup.get_text().split())[:200]

    # ── Картинка ─────────────────────────────────────────────────────────────
    image_url = _extract_image_from_rss_item(item)

    # Если в RSS картинки нет — идём на страницу статьи
    if not image_url and url:
        image_url = _fetch_og_image(url)
        time.sleep(ARTICLE_DELAY)

    return {
        "title":       title,
        "image_url":   image_url,
        "description": description,
        "source":      SOURCE_NAME,
        "type":        "vietnam",
        "url":         url,
    }


def _extract_image_from_rss_item(item) -> str:
    """Пробует найти URL картинки внутри RSS-элемента."""
    # enclosure (стандартный способ)
    enclosure = item.find("enclosure")
    if enclosure:
        url = enclosure.get("url", "")
        if url and _is_image_url(url):
            return url

    # media:content (Media RSS)
    for tag_name in ("media:content", "media:thumbnail", "content"):
        media = item.find(tag_name)
        if media:
            url = media.get("url", "")
            if url and _is_image_url(url):
                return url

    # Ищем <img> внутри <description>
    desc_tag = item.find("description")
    if desc_tag:
        raw = desc_tag.get_text()
        desc_soup = BeautifulSoup(raw, "html.parser")
        img = desc_soup.find("img")
        if img:
            src = img.get("src", "")
            if src and _is_image_url(src):
                return src

    return ""


def _fetch_og_image(url: str) -> str:
    """Загружает страницу статьи и возвращает og:image."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        tag = soup.find("meta", property="og:image")
        if tag:
            return (tag.get("content") or "").strip()
    except Exception:
        pass
    return ""


def _is_image_url(url: str) -> bool:
    url_lower = url.lower().split("?")[0]
    return any(url_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
