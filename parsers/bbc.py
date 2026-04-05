"""
Парсер новостей с BBC News (bbc.com/news)
Алгоритм: собираем ссылки с главной → для каждой статьи берём og:image + og:description
"""

import re
import sys
import os
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NEWS_LIMIT, REQUEST_TIMEOUT

BASE_URL  = "https://www.bbc.com"
NEWS_URL  = "https://www.bbc.com/news"
SOURCE_NAME = "BBC News"

# Задержка между запросами к статьям
ARTICLE_DELAY = 0.4

_BBC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


def parse_bbc() -> list[dict]:
    """
    Парсит последние новости с bbc.com/news.
    Для каждой статьи заходит на её страницу и берёт og:image + og:description.
    Возвращает список: {"title", "image_url", "description", "source", "url"}
    """
    news_items = []
    print(f"[{SOURCE_NAME}] Начинаю парсинг...")

    try:
        response = requests.get(NEWS_URL, headers=_BBC_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[{SOURCE_NAME}] Ошибка при загрузке страницы: {e}")
        return news_items

    try:
        soup = BeautifulSoup(response.text, "lxml")
    except Exception:
        soup = BeautifulSoup(response.text, "html.parser")

    # ── Собираем ссылки ────────────────────────────────────────────────────────
    article_links = _collect_article_links(soup)

    if not article_links:
        print(f"[{SOURCE_NAME}] Ссылки на статьи не найдены.")
        return news_items

    print(f"[{SOURCE_NAME}] Найдено {len(article_links)} ссылок, обрабатываю первые {NEWS_LIMIT}...")

    # ── Загружаем каждую статью ────────────────────────────────────────────────
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
    Извлекает ссылки на статьи с главной страницы BBC.
    Возвращает список (article_url, hint_title).
    """
    links:     list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    # Метод 1: data-testid карточки
    cards = soup.find_all(attrs={"data-testid": re.compile(r"card|article|story", re.I)})
    for card in cards:
        url, hint = _extract_link_and_hint(card)
        if url and url not in seen_urls and _is_bbc_article(url):
            seen_urls.add(url)
            links.append((url, hint))

    # Метод 2: article теги
    if not links:
        for article in soup.find_all("article"):
            url, hint = _extract_link_and_hint(article)
            if url and url not in seen_urls and _is_bbc_article(url):
                seen_urls.add(url)
                links.append((url, hint))

    # Метод 3: fallback — все ссылки на статьи
    if not links:
        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "")
            text = tag.get_text(strip=True)

            if href.startswith("/"):
                full_url = BASE_URL + href
            elif href.startswith("http"):
                full_url = href
            else:
                continue

            if _is_bbc_article(full_url) and len(text) > 15 and full_url not in seen_urls:
                seen_urls.add(full_url)
                hint = " ".join(text.split())
                links.append((full_url, hint))

    return links


def _extract_link_and_hint(element) -> tuple[str, str]:
    """Извлекает URL и подсказку заголовка из элемента карточки."""
    # Ищем заголовок
    hint = ""
    for tag_name in ["h1", "h2", "h3", "h4"]:
        heading = element.find(tag_name)
        if heading:
            t = heading.get_text(strip=True)
            if t and len(t) > 5:
                hint = " ".join(t.split())
                break

    # Ищем ссылку
    link = element.find("a", href=True) if element.name != "a" else element
    if not link:
        return "", hint

    href = link.get("href", "")
    if not hint:
        hint = " ".join(link.get_text(strip=True).split())

    if href.startswith("//"):
        url = "https:" + href
    elif href.startswith("/"):
        url = BASE_URL + href
    elif href.startswith("http"):
        url = href
    else:
        return "", hint

    return url, hint


def _is_bbc_article(url: str) -> bool:
    """Проверяет, что URL ведёт на статью BBC (не главную, не раздел)."""
    if "bbc.com" not in url and "bbc.co.uk" not in url:
        return False
    # Статьи: /news/..., /news/articles/..., или содержат длинный slug
    path = url.split("bbc.com")[-1] if "bbc.com" in url else url.split("bbc.co.uk")[-1]
    return bool(
        re.search(r"/news/[a-z]", path)
        or re.search(r"/news/articles/", path)
        or re.search(r"-[a-z0-9]{6,}", path)
    )


# ── Загрузка отдельной статьи ──────────────────────────────────────────────────

def _fetch_article(url: str, hint_title: str) -> dict | None:
    """
    Загружает страницу статьи BBC и извлекает:
      - заголовок (og:title или h1)
      - картинку (og:image, twitter:image или первый img)
      - описание (og:description, ограничено 200 символами)
    """
    try:
        resp = requests.get(url, headers=_BBC_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        # Не смогли загрузить — возвращаем подсказку без картинки/описания
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

    # ── Картинка ───────────────────────────────────────────────────────────────
    image_url = _meta_content(soup, "og:image") or _meta_content(soup, "twitter:image") or ""

    # Запасной вариант: ищем в теле статьи
    if not image_url:
        for selector in [
            "figure img",
            "div[data-component='image-block'] img",
            "picture img",
            "img",
        ]:
            img = soup.select_one(selector)
            if img:
                # BBC часто использует srcset
                srcset = img.get("srcset", "")
                if srcset:
                    parts = srcset.split(",")
                    last  = parts[-1].strip().split()[0]
                    if last and _is_valid_img(last):
                        image_url = ("https:" + last) if last.startswith("//") else last
                        break

                src = img.get("src") or img.get("data-src") or ""
                if src and _is_valid_img(src):
                    image_url = ("https:" + src) if src.startswith("//") else src
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


def _is_valid_img(url: str) -> bool:
    url_lower = url.lower()
    if any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
        return True
    if "ichef.bbci.co.uk" in url_lower or "news.bbcimg.co.uk" in url_lower:
        return True
    return False
