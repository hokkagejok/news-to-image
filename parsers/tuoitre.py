"""
Парсер Tuoi Tre (tuoitre.vn) — второе по популярности вьетнамское издание.

Алгоритм:
  1. Загружаем главную страницу
  2. Берём заголовок, ссылку, описание, картинку из карточки
  3. Если картинка не найдена — заходим на страницу статьи за og:image / twitter:image
"""

import requests
from bs4 import BeautifulSoup

_HEADERS  = {"User-Agent": "Mozilla/5.0"}
_BASE_URL = "https://tuoitre.vn"


def get_article_image(url: str, headers: dict) -> str:
    """Загружает страницу статьи и возвращает og:image или twitter:image."""
    try:
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "lxml")

        og = s.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og.get("content", "")

        tw = s.find("meta", {"name": "twitter:image"})
        if tw and tw.get("content"):
            return tw.get("content", "")
    except Exception:
        pass
    return ""


def get_news() -> list[dict]:
    url  = _BASE_URL
    news = []

    try:
        res = requests.get(url, headers=_HEADERS, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")

        articles = soup.select("li.news-item, div.box-category-item")[:10]

        for item in articles:
            title_tag = item.select_one("a[title], h3 a, h2 a")
            img_tag   = item.select_one("img")

            if not title_tag:
                continue

            title = (
                title_tag.get("title")
                or title_tag.get_text(strip=True)
            )
            if not title:
                continue

            # URL статьи
            article_url = title_tag.get("href", "")
            if article_url and not article_url.startswith("http"):
                article_url = _BASE_URL + article_url

            # Картинка из карточки
            image_url = ""
            if img_tag:
                image_url = (
                    img_tag.get("src")
                    or img_tag.get("data-src")
                    or img_tag.get("data-original")
                    or ""
                )

            # Fallback: og:image / twitter:image со страницы статьи
            if not image_url and article_url:
                image_url = get_article_image(article_url, _HEADERS)

            # Описание
            description = ""
            desc_tag = item.select_one("p, .sapo")
            if desc_tag:
                description = desc_tag.get_text(strip=True)[:200]

            news.append({
                "title":       title,
                "image_url":   image_url,
                "description": description,
                "url":         article_url,
                "source":      "Tuoi Tre",
                "type":        "vietnam",
            })

    except Exception as e:
        print(f"❌ TuoiTre error: {e}")

    print(f"✅ TuoiTre: {len(news)} новостей")
    return news
