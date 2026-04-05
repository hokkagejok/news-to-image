"""
Парсер новостей с VnExpress (vnexpress.net) — крупнейшее вьетнамское издание.

Алгоритм:
  1. Загружаем главную страницу vnexpress.net
  2. Из каждого article.item-news берём заголовок, ссылку, описание, картинку
  3. Если картинка не найдена в HTML — заходим на страницу статьи за og:image
"""

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_BASE_URL = "https://vnexpress.net"


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
            link_tag  = item.select_one("h3.title-news a, h2.title-news a")

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

            # Описание
            description = ""
            if desc_tag:
                description = desc_tag.get_text(strip=True)[:200]

            # URL статьи
            article_url = link_tag.get("href", "") if link_tag else ""

            # Fallback: og:image со страницы статьи
            if article_url and not image_url:
                try:
                    r = requests.get(article_url, headers=_HEADERS, timeout=5)
                    r.raise_for_status()
                    s  = BeautifulSoup(r.text, "lxml")
                    og = s.find("meta", {"property": "og:image"})
                    if og:
                        image_url = og.get("content", "")
                except Exception:
                    pass

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
