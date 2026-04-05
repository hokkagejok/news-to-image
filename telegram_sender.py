"""
Модуль отправки новостей и баннеров в Telegram.

Использует python-telegram-bot v20+ (async API).
Установка: pip install python-telegram-bot

Каждая новость отправляется как фото с подписью.
В начале отправляется интро-баннер, в конце — баннер подписки.
Если токен или chat_id не заданы — отправка пропускается.
"""

import asyncio
from pathlib import Path


def _credentials_ok(token: str, chat_id: str) -> bool:
    """Возвращает False если токен/chat_id пустые или содержат заглушку."""
    if not token or not chat_id:
        print("[Telegram] Токен или chat_id не заданы — отправка пропущена.")
        return False
    if "ЗДЕСЬ" in token or "ЗДЕСЬ" in chat_id:
        print("[Telegram] Токен/chat_id не настроены (содержат заглушку) — отправка пропущена.")
        print("           Укажите реальные значения в config.py:")
        print("             TELEGRAM_BOT_TOKEN = '<токен от @BotFather>'")
        print("             TELEGRAM_CHAT_ID   = '<ID канала или чата>'")
        return False
    return True


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
    except ImportError:
        print("[Telegram] ОШИБКА: python-telegram-bot не установлен.")
        print("           Запустите: pip install python-telegram-bot")
        return 0

    bot     = Bot(token=token)
    sent    = 0
    total   = len(news_list)
    img_dir = Path(images_folder)

    for idx, news in enumerate(news_list, 1):
        try:
            image_path: Path | None = None

            saved_path = news.get("image_path", "")
            if saved_path and Path(saved_path).exists():
                image_path = Path(saved_path)
            else:
                candidates = sorted(img_dir.glob(f"{idx:03d}_*.png"))
                if candidates:
                    image_path = candidates[0]

            if image_path is None:
                print(f"  [{idx}/{total}] ⚠ Картинка не найдена, пропускаю: {news.get('title', '')[:50]}")
                continue

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

            # Пауза — не более 20 сообщений в минуту (лимит Telegram)
            await asyncio.sleep(3)

        except Exception as e:
            print(f"  [{idx}/{total}] ❌ Ошибка отправки: {e}")

    return sent


async def _send_single_photo(
    photo_path: str,
    caption: str,
    token: str,
    chat_id: str,
) -> bool:
    """Асинхронно отправляет одно фото с заданной подписью."""
    try:
        from telegram import Bot
    except ImportError:
        return False

    bot = Bot(token=token)
    try:
        with open(photo_path, "rb") as f:
            await bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=caption[:1024],
            )
        return True
    except Exception as e:
        print(f"  ❌ Ошибка отправки фото: {e}")
        return False


def send_all(
    news_list: list[dict],
    images_folder: str,
    token: str,
    chat_id: str,
) -> None:
    """Синхронная обёртка: отправляет все новости в Telegram."""
    if not _credentials_ok(token, chat_id):
        return

    sent = asyncio.run(
        _send_news_to_telegram(news_list, images_folder, token, chat_id)
    )
    print(f"[Telegram] Отправлено {sent} из {len(news_list)} новостей.")


def send_banner(photo_path: str, token: str, chat_id: str, caption: str = "") -> None:
    """
    Синхронная обёртка: отправляет одно фото (баннер) в Telegram.

    Args:
        photo_path: путь к PNG/JPG файлу
        token:      Telegram Bot Token
        chat_id:    ID канала или чата
        caption:    подпись к фото (опционально)
    """
    if not _credentials_ok(token, chat_id):
        return

    if not Path(photo_path).exists():
        print(f"[Telegram] Фото не найдено: {photo_path}")
        return

    if not caption:
        caption = "📲 Подпишись на @todayrealnews, чтобы не пропустить главные новости дня! 🌍"

    ok = asyncio.run(_send_single_photo(photo_path, caption, token, chat_id))
    if ok:
        print(f"[Telegram] ✅ Баннер отправлен: {Path(photo_path).name}")
