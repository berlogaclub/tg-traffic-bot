import os
import json
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    supabase_url: str
    supabase_service_key: str
    webhook_url: str
    webhook_port: int
    google_credentials: dict
    google_sheet_id: str
    log_level: str
    bot_password: str  # если задан — новые пользователи должны ввести его при /start


def _load_google_credentials() -> dict:
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_config() -> Config:
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
        webhook_url=os.environ.get("WEBHOOK_URL", ""),
        webhook_port=int(os.environ.get("WEBHOOK_PORT", "8080")),
        google_credentials=_load_google_credentials(),
        google_sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        bot_password=os.environ.get("BOT_PASSWORD", ""),
    )


config = load_config()
