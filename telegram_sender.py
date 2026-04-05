"""
Модуль отправки новостей и баннеров в Telegram.

Полный цикл (send_all):
  1. Загрузить ID предыдущих сообщений из output/message_ids.json
  2. Удалить все старые сообщения из канала
  3. Отправить интро-баннер (000_intro_banner.png)
  4. Отправить все новости (NNN_*.png)
  5. Отправить баннер подписки (000_subscribe_banner.png)
  6. Сохранить ID всех новых сообщений в output/message_ids.json

При следующем запуске шаг 2 удалит сообщения текущего запуска.
"""

import asyncio
import json
from pathlib import Path


# ── Файл с ID сообщений ───────────────────────────────────────────────────────

MESSAGE_IDS_FILE = "output/message_ids.json"


def load_message_ids() -> list[int]:
    """Загружает список ID сообщений из файла. Возвращает [] если файл отсутствует."""
    path = Path(MESSAGE_IDS_FILE)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [int(x) for x in data if str(x).isdigit() or isinstance(x, int)]
    except Exception as e:
        print(f"[Telegram] Не удалось прочитать {MESSAGE_IDS_FILE}: {e}")
        return []


def save_message_ids(ids: list[int]) -> None:
    """Сохраняет список ID сообщений в файл."""
    Path("output").mkdir(parents=True, exist_ok=True)
    try:
        with open(MESSAGE_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(ids, f)
    except Exception as e:
        print(f"[Telegram] Не удалось сохранить {MESSAGE_IDS_FILE}: {e}")


# ── Проверка учётных данных ───────────────────────────────────────────────────

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


# ── Удаление старых сообщений ─────────────────────────────────────────────────

async def _delete_old_messages_async(bot, chat_id: str) -> int:
    """
    Удаляет все сообщения из message_ids.json.
    Сообщения старше 48 часов Telegram не позволяет удалять — они пропускаются.
    После удаления очищает файл.
    """
    from telegram.error import TelegramError

    ids = load_message_ids()
    if not ids:
        print("[Telegram] 📭 Старых сообщений нет")
        return 0

    print(f"[Telegram] 🗑️ Найдено {len(ids)} старых сообщений, удаляю...")
    deleted = 0

    for msg_id in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted += 1
            await asyncio.sleep(0.3)
        except TelegramError as e:
            # Сообщение уже удалено, слишком старое или нет прав
            print(f"[Telegram] ⚠️ Не удалось удалить {msg_id}: {e}")

    save_message_ids([])
    print(f"[Telegram] 🗑️ Удалено: {deleted} из {len(ids)}")
    return deleted


# ── Отправка одного фото ──────────────────────────────────────────────────────

async def _send_photo_async(
    bot,
    chat_id: str,
    photo_path: Path,
    caption: str,
) -> int | None:
    """
    Отправляет одно фото в Telegram.
    Возвращает message_id при успехе, None при ошибке.
    """
    try:
        with open(photo_path, "rb") as f:
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=caption[:1024],
            )
        return msg.message_id
    except Exception as e:
        print(f"[Telegram] ❌ Ошибка отправки {photo_path.name}: {e}")
        return None


# ── Полный цикл отправки ──────────────────────────────────────────────────────

async def _run_full_pipeline(
    news_list: list[dict],
    images_folder: str,
    token: str,
    chat_id: str,
    intro_caption: str,
) -> int:
    """
    Асинхронный полный цикл:
      1. Удалить старые сообщения
      2. Отправить интро-баннер
      3. Отправить все новости
      4. Отправить баннер подписки
      5. Сохранить новые ID

    Возвращает количество успешно отправленных новостей.
    """
    try:
        from telegram import Bot
    except ImportError:
        print("[Telegram] ОШИБКА: python-telegram-bot не установлен.")
        print("           Запустите: pip install python-telegram-bot")
        return 0

    bot     = Bot(token=token)
    img_dir = Path(images_folder)
    new_ids: list[int] = []

    # ── Шаг 1: Удалить старые сообщения ──────────────────────────────────────
    print("\n[Telegram] ── Шаг 1: Удаление старых сообщений ──")
    await _delete_old_messages_async(bot, chat_id)
    await asyncio.sleep(1)

    # ── Шаг 2: Интро-баннер ───────────────────────────────────────────────────
    print("\n[Telegram] ── Шаг 2: Интро-баннер ──")
    intro_path = img_dir / "000_intro_banner.png"
    if intro_path.exists():
        msg_id = await _send_photo_async(bot, chat_id, intro_path, intro_caption)
        if msg_id:
            new_ids.append(msg_id)
            print(f"[Telegram] ✅ Интро-баннер отправлен (ID: {msg_id})")
        await asyncio.sleep(1)
    else:
        print(f"[Telegram] ⚠️ Интро-баннер не найден: {intro_path}")

    # ── Шаг 3: Новости ────────────────────────────────────────────────────────
    print(f"\n[Telegram] ── Шаг 3: Новости ({len(news_list)} шт.) ──")
    sent_news = 0
    total     = len(news_list)

    for idx, news in enumerate(news_list, 1):
        try:
            # Поиск картинки: сначала по сохранённому пути, затем по паттерну
            image_path: Path | None = None

            saved = news.get("image_path", "")
            if saved and Path(saved).exists():
                image_path = Path(saved)
            else:
                candidates = sorted(img_dir.glob(f"{idx:03d}_*.png"))
                if candidates:
                    image_path = candidates[0]

            if image_path is None:
                print(f"  [{idx}/{total}] ⚠️ Картинка не найдена: {news.get('title', '')[:50]}")
                continue

            # Формируем подпись
            title       = (news.get("title",       "") or "").strip()
            description = (news.get("description", "") or "").strip()
            source      = (news.get("source",      "") or "").strip()
            url         = (news.get("url",         "") or "").strip()

            parts = [f"📰 {title}"]
            if description:
                parts.append(f"\n{description}")
            if source:
                parts.append(f"\n📡 {source}")
            if url:
                parts.append(f"\n🔗 {url}")
            parts.append("\n\n📲 @todayrealnews")
            parts.append("#tintuc #news #worldnews #todayrealnews")

            caption = "\n".join(parts)[:1024]

            msg_id = await _send_photo_async(bot, chat_id, image_path, caption)
            if msg_id:
                new_ids.append(msg_id)
                sent_news += 1
                print(f"  [{idx}/{total}] ✅ {title[:60]}")

            # Пауза — лимит Telegram: 20 сообщений в минуту
            await asyncio.sleep(3)

        except Exception as e:
            print(f"  [{idx}/{total}] ❌ Ошибка: {e}")

    # ── Шаг 4: Баннер подписки ────────────────────────────────────────────────
    print("\n[Telegram] ── Шаг 4: Баннер подписки ──")
    subscribe_path = img_dir / "000_subscribe_banner.png"
    if subscribe_path.exists():
        sub_caption = (
            "📲 Dang ky @todayrealnews\n"
            "de khong bo lo tin tuc!\n\n"
            "#tintuc #news #todayrealnews"
        )
        msg_id = await _send_photo_async(bot, chat_id, subscribe_path, sub_caption)
        if msg_id:
            new_ids.append(msg_id)
            print(f"[Telegram] ✅ Баннер подписки отправлен (ID: {msg_id})")
    else:
        print(f"[Telegram] ⚠️ Баннер подписки не найден: {subscribe_path}")

    # ── Шаг 5: Сохранить новые ID ─────────────────────────────────────────────
    save_message_ids(new_ids)
    print(f"\n[Telegram] ✅ Сохранено {len(new_ids)} ID сообщений → {MESSAGE_IDS_FILE}")

    return sent_news


# ── Публичный API ─────────────────────────────────────────────────────────────

def send_all(
    news_list: list[dict],
    images_folder: str,
    token: str,
    chat_id: str,
    intro_caption: str = "",
) -> None:
    """
    Синхронная обёртка полного цикла отправки.

    Порядок:
      1. Удалить сообщения из прошлого запуска
      2. Отправить интро-баннер (000_intro_banner.png из images_folder)
      3. Отправить все новости из news_list
      4. Отправить баннер подписки (000_subscribe_banner.png из images_folder)
      5. Сохранить ID всех новых сообщений

    Args:
        news_list:      список новостей (каждый dict: title, description, source, url, image_path)
        images_folder:  папка с PNG-файлами
        token:          Telegram Bot Token
        chat_id:        ID канала или чата (напр. "@mychannel" или "-100xxxxxxxx")
        intro_caption:  подпись к интро-баннеру; если пусто — используется стандартная
    """
    if not _credentials_ok(token, chat_id):
        return

    if not intro_caption:
        intro_caption = "🗞 Tin tuc hom nay!\n\n📲 @todayrealnews"

    sent = asyncio.run(
        _run_full_pipeline(news_list, images_folder, token, chat_id, intro_caption)
    )
    print(f"\n[Telegram] Итого отправлено новостей: {sent} из {len(news_list)}")


def send_banner(
    photo_path: str,
    token: str,
    chat_id: str,
    caption: str = "",
) -> None:
    """
    Отправляет одно фото в Telegram и добавляет его ID в message_ids.json.
    Используется для отправки отдельных баннеров вне основного цикла.

    Args:
        photo_path: путь к PNG/JPG
        token:      Telegram Bot Token
        chat_id:    ID канала или чата
        caption:    подпись к фото (опционально)
    """
    if not _credentials_ok(token, chat_id):
        return

    path = Path(photo_path)
    if not path.exists():
        print(f"[Telegram] Фото не найдено: {photo_path}")
        return

    if not caption:
        caption = "📲 Dang ky @todayrealnews, de khong bo lo tin tuc! 🌍"

    async def _do() -> None:
        try:
            from telegram import Bot
        except ImportError:
            print("[Telegram] ОШИБКА: python-telegram-bot не установлен.")
            return

        bot    = Bot(token=token)
        msg_id = await _send_photo_async(bot, chat_id, path, caption)
        if msg_id:
            ids = load_message_ids()
            ids.append(msg_id)
            save_message_ids(ids)
            print(f"[Telegram] ✅ Баннер отправлен: {path.name} (ID: {msg_id})")

    asyncio.run(_do())
