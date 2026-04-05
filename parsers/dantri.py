"""
Парсер новостей с Dan Tri (dantri.com.vn) — популярный вьетнамский новостной портал.

Алгоритм:
  1. Загружаем главную страницу dantri.com.vn
  2. Из каждого article.article-item берём заголовок, ссылку, описание, картинку
"""

import requests
from bs4 import BeautifulSoup

_HEADERS  = {"User-Agent": "Mozilla/5.0"}
_BASE_URL = "https://dantri.com.vn"


def get_news() -> list[dict]:
    url  = _BASE_URL
    news = []

    try:
        res = requests.get(url, headers=_HEADERS, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")

        articles = soup.select("article.article-item")[:10]

        for item in articles:
            title_tag = item.select_one("h3 a, h2 a")
            img_tag   = item.select_one("img")
            desc_tag  = item.select_one(".article-excerpt, p")

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            if not title:
                continue

            # Картинка из карточки
            image_url = ""
            if img_tag:
                image_url = (
                    img_tag.get("src")
                    or img_tag.get("data-src")
                    or img_tag.get("data-original")
                    or ""
                )

            # URL статьи
            article_url = title_tag.get("href", "")
            if article_url and not article_url.startswith("http"):
                article_url = _BASE_URL + article_url

            # Описание
            description = ""
            if desc_tag:
                description = desc_tag.get_text(strip=True)[:200]

            news.append({
                "title":       title,
                "image_url":   image_url,
                "description": description,
                "url":         article_url,
                "source":      "Dan Tri",
                "type":        "vietnam",
            })

    except Exception as e:
        print(f"❌ DanTri error: {e}")

    print(f"✅ DanTri: {len(news)} новостей")
    return news
