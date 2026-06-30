import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    check_interval_seconds: int = 60
    database_path: str = "starvell_bot.db"
    admin_id: int | None = None
    top_up_payment_details: str = ""
    free_usernames: set[str] | None = None


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is missing. Create .env from .env.example and paste your BotFather token.")

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
        database_path=os.getenv("DATABASE_PATH", "starvell_bot.db").strip() or "starvell_bot.db",
        admin_id=admin_id,
        top_up_payment_details=os.getenv("TOP_UP_PAYMENT_DETAILS", "").strip(),
        free_usernames=free_usernames,
    )
