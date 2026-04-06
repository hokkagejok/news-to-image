import os

# === Пути ===
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")
FONTS_DIR  = os.path.join(BASE_DIR, "fonts")
FONT_PATH  = os.path.join(FONTS_DIR, "font.ttf")

# === Размеры изображения (формат TikTok/Reels 9:16) ===
IMAGE_WIDTH  = 1080
IMAGE_HEIGHT = 1920

# === Настройки текста ===
MAX_TITLE_LENGTH = 200
TITLE_FONT_SIZE  = 68
SOURCE_FONT_SIZE = 36
TITLE_PADDING    = 80
OVERLAY_OPACITY  = 180  # 0-255, 180 ≈ 70%

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# === Google Custom Search (для поиска релевантных фото) ===
# API Key: https://console.developers.google.com → включить "Custom Search API"
# CX (Search Engine ID): https://cse.google.com → создать поисковик → "Search entire web"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX      = os.environ.get("GOOGLE_CX", "")

print(f"[Config] GOOGLE_API_KEY: {'SET' if GOOGLE_API_KEY else 'EMPTY'}")
print(f"[Config] GOOGLE_CX: {'SET' if GOOGLE_CX else 'EMPTY'}")

# === HTTP заголовки ===
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language":         "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":         "gzip, deflate, br",
    "Connection":              "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# === Количество новостей с каждого источника ===
NEWS_LIMIT = 20

# === Таймаут запросов (секунды) ===
REQUEST_TIMEOUT = 15

# === Цвета (RGB) ===
COLOR_WHITE  = (255, 255, 255)
COLOR_BLACK  = (0,   0,   0)
COLOR_OVERLAY = (0,   0,   0)
COLOR_ACCENT_LENTA = (255, 80,  60)   # Красный для Lenta.ru
COLOR_ACCENT_RIA   = (0,  120, 210)   # Синий для RIA Novosti
COLOR_ACCENT_BBC   = (187,  0,   0)   # Тёмно-красный для BBC

SOURCE_COLORS = {
    "Lenta.ru":    COLOR_ACCENT_LENTA,
    "RIA Novosti": COLOR_ACCENT_RIA,
    "BBC News":    COLOR_ACCENT_BBC,
}

# === Фоновые цвета для случаев без картинки (RGB) ===
FALLBACK_BACKGROUNDS = [
    (30, 40, 60),
    (20, 50, 40),
    (50, 30, 50),
    (60, 35, 20),
    (25, 45, 65),
    (45, 20, 35),
    (35, 55, 35),
    (55, 40, 25),
]
