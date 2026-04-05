"""
Парсер VnExpress (vnexpress.net) — крупнейшее вьетнамское издание.

Алгоритм:
  1. Загружаем главную страницу
  2. Берём заголовок, ссылку, описание, картинку из карточки
  3. Если картинка не найдена — заходим на страницу статьи за og:image / twitter:image
"""

import requests
from bs4 import BeautifulSoup

_HEADERS  = {"User-Agent": "Mozilla/5.0"}
_BASE_URL = "https://vnexpress.net"


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

        articles = soup.select("article.item-news")[:10]

        for item in articles:
            title_tag = item.select_one("h3.title-news a, h2.title-news a")
            img_tag   = item.select_one("img")
            desc_tag  = item.select_one("p.description a, p.description")

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            if not title:
                continue

            article_url = title_tag.get("href", "")

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
            if desc_tag:
                description = desc_tag.get_text(strip=True)[:200]

            news.append({
                "title":       title,
                "image_url":   image_url,
                "description": description,
                "url":         article_url,
                "source":      "VnExpress",
                "type":        "vietnam",
            })

    except Exception as e:
        print(f"❌ VnExpress error: {e}")

    print(f"✅ VnExpress: {len(news)} новостей")
    return news
