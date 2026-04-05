"""
Модуль отправки новостей в Telegram.

Использует python-telegram-bot v20+ (async API).
Установка: pip install python-telegram-bot

Каждая новость отправляется как фото с подписью.
Если токен или chat_id не заданы — отправка пропускается.
"""

import asyncio
from pathlib import Path


async def _send_news_to_telegram(
    news_list: list[dict],
    images_folder: str,
    token: str,
    chat_id: str,
) -> int:
    """
    Асинхронно отправляет каждую новость из news_list в Telegram-канал/чат.
    Картинку ищет по полю news['image_path'], либо по паттерну NNN_*.png.
    Возвращает количество успешно отправленных новостей.
    """
    try:
        from telegram import Bot
        from telegram.error import TelegramError
    except ImportError:
        print("[Telegram] ОШИБКА: python-telegram-bot не установлен.")
        print("           Запустите: pip install python-telegram-bot")
        return 0

    bot       = Bot(token=token)
    sent      = 0
    total     = len(news_list)
    img_dir   = Path(images_folder)

    for idx, news in enumerate(news_list, 1):
        try:
            # Ищем файл картинки: сначала по сохранённому пути, потом по номеру
            image_path: Path | None = None

            saved_path = news.get("image_path", "")
            if saved_path and Path(saved_path).exists():
                image_path = Path(saved_path)
            else:
                # Fallback: ищем файл вида 001_*.png, 002_*.png и т.д.
                candidates = sorted(img_dir.glob(f"{idx:03d}_*.png"))
                if candidates:
                    image_path = candidates[0]

            if image_path is None:
                print(f"  [{idx}/{total}] ⚠ Картинка не найдена, пропускаю: {news.get('title', '')[:50]}")
                continue

            # Формируем подпись (лимит Telegram — 1024 символа)
            title       = news.get("title",       "") or ""
            description = news.get("description", "") or ""
            url         = news.get("url",         "") or ""
            source      = news.get("source",      "") or ""

            parts = [f"📰 {title}"]
            if description:
                parts.append(f"\n{description}")
            if source:
                parts.append(f"\n📡 {source}")
            if url:
                parts.append(f"\n🔗 {url}")

            caption = "\n".join(parts)[:1024]

            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                )

            sent += 1
            print(f"  [{idx}/{total}] ✅ Отправлено: {title[:60]}")

            # Пауза между отправками — не более 20 сообщений в минуту (лимит Telegram)
            await asyncio.sleep(3)

        except Exception as e:
            print(f"  [{idx}/{total}] ❌ Ошибка отправки: {e}")

    return sent


def send_all(
    news_list: list[dict],
    images_folder: str,
    token: str,
    chat_id: str,
) -> None:
    """
    Синхронная обёртка над async-отправкой.
    Пропускает отправку если токен или chat_id не заданы (содержат слово «ЗДЕСЬ»
    или пустые) — чтобы не падать при первом запуске без настройки.
    """
    if not token or not chat_id:
        print("[Telegram] Токен или chat_id не заданы — отправка пропущена.")
        return

    if "ЗДЕСЬ" in token or "ЗДЕСЬ" in chat_id:
        print("[Telegram] Токен/chat_id не настроены (содержат заглушку) — отправка пропущена.")
        print("           Укажите реальные значения в config.py:")
        print("             TELEGRAM_BOT_TOKEN = '<токен от @BotFather>'")
        print("             TELEGRAM_CHAT_ID   = '<ID канала или чата>'")
        return

    sent = asyncio.run(
        _send_news_to_telegram(news_list, images_folder, token, chat_id)
    )
    print(f"[Telegram] Отправлено {sent} из {len(news_list)} новостей.")
