import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def clean_env_value(value: str) -> str:
    value = (value or "").strip()
    if "=" in value and value.split("=", 1)[0].strip().upper() in {"BOT_TOKEN", "TELEGRAM_BOT_TOKEN"}:
        value = value.split("=", 1)[1].strip()
    return value.strip().strip('"').strip("'")


def validate_bot_token(token: str) -> None:
    """
    Telegram bot token format is like 123456789:AA....
    If token is wrong, stop with a clear message before aiogram traceback.
    """
    if not token:
        raise SystemExit(
            "❌ BOT_TOKEN не указан. Открой .env и вставь токен от @BotFather. "
            "Пример: BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )

    if not re.fullmatch(r"\d{6,20}:[A-Za-z0-9_-]{30,}", token):
        raise SystemExit(
            "❌ BOT_TOKEN имеет неправильный формат.\n"
            "Нужно вставить токен от @BotFather полностью, без кавычек, без пробелов и без слова bot.\n"
            "Правильно: BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "Неправильно: BOT_TOKEN=твой_токен_бота"
        )


@dataclass(frozen=True)
class Config:
    bot_token: str
    check_interval_seconds: int = 60
    database_path: str = "starvell_bot.db"
    admin_id: int | None = None
    top_up_payment_details: str = ""
    free_usernames: set[str] | None = None
    support_username: str = ""
    support_url: str = ""


def load_config() -> Config:
    token = clean_env_value(os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "")
    validate_bot_token(token)

    interval_raw = os.getenv("CHECK_INTERVAL_SECONDS", "60").strip()
    try:
        interval = max(30, int(interval_raw))
    except ValueError:
        interval = 60

    admin_id_raw = os.getenv("ADMIN_ID", "").strip()
    try:
        admin_id = int(admin_id_raw) if admin_id_raw else None
    except ValueError:
        admin_id = None

    free_usernames_raw = os.getenv("TELEGRAM_FREE_USERNAMES", "").strip()
    free_usernames = {
        item.strip().lower().lstrip("@")
        for item in free_usernames_raw.split(",")
        if item.strip()
    }

    return Config(
        bot_token=token,
        check_interval_seconds=interval,
        database_path=clean_env_value(os.getenv("DATABASE_PATH", "starvell_bot.db")) or "starvell_bot.db",
        admin_id=admin_id,
        top_up_payment_details=clean_env_value(os.getenv("TOP_UP_PAYMENT_DETAILS", "")),
        free_usernames=free_usernames,
        support_username=clean_env_value(os.getenv("SUPPORT_USERNAME", "")).lstrip("@"),
        support_url=clean_env_value(os.getenv("SUPPORT_URL", "")),
    )
