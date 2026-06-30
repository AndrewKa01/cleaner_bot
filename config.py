import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHAT_ID = int(os.getenv("CHAT_ID"))

# прокси (http или https) — значения берутся из .env, не хранятся в коде
HOST = os.getenv("PROXY_HOST", "")
PORT = int(os.getenv("PROXY_PORT", "0"))
NEED_AUTH = os.getenv("PROXY_NEED_AUTH", "true").lower() == "true"
LOGIN = os.getenv("PROXY_LOGIN", "")
PASSWORD = os.getenv("PROXY_PASSWORD", "")

TIMEZONE = "Europe/Moscow"

# Время отправки сообщений (московское)
REMINDER_HOUR = 19      # Пятница — напоминание
REMINDER_MINUTE = 0

CONTROL_HOUR = 19       # Суббота и далее — контроль
CONTROL_MINUTE = 0
