"""
Парсер новостей с RIA Novosti (ria.ru)
Алгоритм: собираем ссылки с главной → для каждой статьи берём og:image
"""

import re
import sys
import os
import time

import requests
from bs4 import BeautifulSoup
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HEADERS, NEWS_LIMIT, REQUEST_TIMEOUT

BASE_URL = "https://ria.ru"
SOURCE_NAME = "RIA Novosti"

# Задержка между запросами к статьям
ARTICLE_DELAY = 0.4  # секунд

# RIA Novosti: статьи имеют формат /YYYYMMDD/NNNNNNN.html
_RIA_ARTICLE_RE = re.compile(r"/(\d{8})/\d+\.html")


def parse_ria() -> list[dict]:
    """
    Парсит последние новости с главной страницы ria.ru.
    Для каждой найденной статьи заходит на её страницу и берёт og:image.
    Возвращает список: {"title", "image_url", "description", "source", "url"}
    """
    news_items = []
    print(f"[{SOURCE_NAME}] Начинаю парсинг...")

    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = "utf-8"
    except requests.RequestException as e:
        print(f"[{SOURCE_NAME}] Ошибка загрузки главной: {e}")
        return news_items

    try:
        soup = BeautifulSoup(response.text, "lxml")
    except Exception:
        soup = BeautifulSoup(response.text, "html.parser")

    # ── Собираем ссылки на статьи ──────────────────────────────────────────────
    article_links = _collect_article_links(soup)

    if not article_links:
        print(f"[{SOURCE_NAME}] Ссылки на статьи не найдены.")
        return news_items

    print(f"[{SOURCE_NAME}] Найдено {len(article_links)} ссылок, обрабатываю первые {NEWS_LIMIT}...")

    # ── Для каждой статьи получаем данные ──────────────────────────────────────
    seen_titles: set[str] = set()
    processed = 0

    for url, hint_title in article_links:
        if processed >= NEWS_LIMIT:
            break

        try:
            item = _fetch_article(url, hint_title)
            if not item:
                continue

            norm = item["title"].lower().strip()
            if norm in seen_titles or len(norm) < 10:
                continue
            seen_titles.add(norm)

            news_items.append(item)
            processed += 1
            print(f"[{SOURCE_NAME}] [{processed}/{NEWS_LIMIT}] {item['title'][:80]}...")

            time.sleep(ARTICLE_DELAY)

        except Exception as e:
            print(f"[{SOURCE_NAME}] Ошибка при обработке {url}: {e}")
            continue

    print(f"[{SOURCE_NAME}] Готово: {len(news_items)} новостей.")
    return news_items


# ── Сбор ссылок ────────────────────────────────────────────────────────────────

def _collect_article_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """
    Возвращает список (article_url, hint_title).
    Приоритет — сегодняшние статьи по дате в URL.
    """
    today_str = date.today().strftime("%Y%m%d")
    today_links: list[tuple[str, str]] = []
    other_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "").strip()
        if not href:
            continue

        m = _RIA_ARTICLE_RE.search(href)
        if not m:
            continue

        # Строим полный URL
        if href.startswith("/"):
            full_url = BASE_URL + href
        elif href.startswith("http"):
            full_url = href
        else:
            continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        hint = _hint_title(tag)
        link_date = m.group(1)

        if link_date == today_str:
            today_links.append((full_url, hint))
        else:
            other_links.append((full_url, hint))

    if not today_links:
        print(f"[{SOURCE_NAME}] Нет статей за сегодня, берём последние доступные.")

    return today_links + other_links


def _hint_title(tag) -> str:
    """Пытается извлечь заголовок прямо из тега-ссылки."""
    for heading in ["h1", "h2", "h3", "h4"]:
        child = tag.find(heading)
        if child:
            t = child.get_text(strip=True)
            if t and len(t) > 5:
                return t

    # Ищем span/div с классом, содержащим "title"
    for el in tag.find_all(["span", "div", "p"]):
        classes = " ".join(el.get("class", []))
        if "title" in classes.lower() or "heading" in classes.lower():
            t = el.get_text(strip=True)
            if t and len(t) > 5:
                return t

    text = " ".join(tag.get_text(strip=True).split())
    return text if len(text) > 5 else ""


# ── Загрузка отдельной статьи ──────────────────────────────────────────────────

def _fetch_article(url: str, hint_title: str) -> dict | None:
    """
    Загружает страницу статьи и извлекает:
      - заголовок (og:title или h1)
      - картинку (og:image — всегда уникальная для статьи)
      - описание (og:description)
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        # Не смогли загрузить — возвращаем подсказку без картинки
        if hint_title and len(hint_title) > 10:
            return {
                "title": hint_title,
                "image_url": "",
                "description": "",
                "source": SOURCE_NAME,
                "url": url,
            }
        return None

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")

    # ── Заголовок ──────────────────────────────────────────────────────────────
    title = _meta_content(soup, "og:title") or _meta_content(soup, "twitter:title")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        title = hint_title
    title = " ".join(title.split())
    if not title or len(title) < 5:
        return None

    # ── Картинка (og:image всегда уникальна для каждой статьи RIA) ────────────
    image_url = _meta_content(soup, "og:image") or ""

    # Запасной вариант: первая крупная картинка в теле статьи
    if not image_url:
        for selector in [
            "div.article__header__image img",
            "div.photoview__slide img",
            "figure img",
            "div.media__image img",
            "img[itemprop='image']",
        ]:
            img = soup.select_one(selector)
            if img:
                src = img.get("src") or img.get("data-src") or ""
                if src and _is_img_url(src):
                    if src.startswith("//"):
                        src = "https:" + src
                    image_url = src
                    break

    # ── Описание ───────────────────────────────────────────────────────────────
    description = (
        _meta_content(soup, "og:description")
        or _meta_content(soup, "description")
        or ""
    )
    description = " ".join(description.split())[:200]

    return {
        "title": title,
        "image_url": image_url,
        "description": description,
        "source": SOURCE_NAME,
        "url": url,
    }


# ── Утилиты ────────────────────────────────────────────────────────────────────

def _meta_content(soup: BeautifulSoup, prop: str) -> str:
    """Читает content мета-тега по property или name."""
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def _is_img_url(url: str) -> bool:
    url_lower = url.lower().split("?")[0]
    return any(url_lower.endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
