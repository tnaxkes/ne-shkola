from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Drum School Booking"
    app_url: str = "http://localhost:8000"
    app_env: str = "development"
    app_debug: bool = False
    log_level: str = "INFO"
    database_url: str = "sqlite:///./drum_school.db"
    secret_key: str = "change-me-in-production"
    session_https_only: bool = False
    seed_on_startup: bool = True
    admin_username: str = "admin"
    admin_password: str = "admin"
    admin_api_token: str = ""

    telegram_bot_token: str = ""
    admin_telegram_chat_id: str = ""
    # Set to your Vercel deployment URL (e.g. https://drum-school.vercel.app)
    # to run the bot in webhook mode instead of polling.
    webhook_url: str = ""
    # Secret token Vercel uses to call /internal/reminders (optional protection)
    cron_secret: str = ""

    salon_name: str = "Не Школа Барабанов"
    salon_phone: str = "+7 (999) 000-00-00"
    salon_address: str = "Москва, ул. Пример, д. 1"
    salon_contacts: str = "@drum_admin"

    calendar_id: str = "primary"
    google_service_account_file: str = ""

    enable_masters: bool = True
    workday_start: str = "10:00"
    workday_end: str = "22:00"
    slot_step_minutes: int = 60
    app_timezone: str = "Europe/Moscow"
    run_host: str = "127.0.0.1"
    run_port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
