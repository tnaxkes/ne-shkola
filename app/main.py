import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Base

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger(__name__)

# Absolute paths — work locally, on Railway, and on Vercel
_BASE_DIR = Path(__file__).parent
_STATIC_DIR = _BASE_DIR / "static"


# ─── Shared bot/dp (webhook mode only) ──────────────────────────────────────

_bot = None
_dp  = None


def _get_bot():
    global _bot
    if _bot is None and settings.telegram_bot_token:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def _get_dp():
    global _dp
    if _dp is None:
        from aiogram import Dispatcher
        from aiogram.fsm.storage.memory import MemoryStorage
        from app.bot.telegram_bot import router as bot_router
        _dp = Dispatcher(storage=MemoryStorage())
        _dp.include_router(bot_router)
    return _dp


# ─── Lifespan ───────────────────────────────────────────────────────────────

_polling_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task

    Base.metadata.create_all(bind=engine)
    if settings.seed_on_startup:
        from app.seed import seed
        db = SessionLocal()
        try:
            seed(db)
        finally:
            db.close()

    if settings.telegram_bot_token:
        if settings.webhook_url:
            # ── Webhook mode (Vercel) ──────────────────────────────────────
            try:
                bot = _get_bot()
                _get_dp()
                webhook_url = f"{settings.webhook_url.rstrip('/')}/webhook/telegram"
                await bot.set_webhook(webhook_url, drop_pending_updates=True)
                log.info("Telegram webhook set: %s", webhook_url)
            except Exception as exc:
                log.warning("Failed to set webhook: %s", exc)
        else:
            # ── Polling mode (Railway / local) ─────────────────────────────
            # Bot runs as a background asyncio task alongside uvicorn
            from app.bot.telegram_bot import run_polling
            _polling_task = asyncio.create_task(run_polling())
            log.info("Telegram bot started in polling mode")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    if _polling_task is not None:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
    if _bot is not None:
        await _bot.session.close()


# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.session_https_only,
)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

from app.routers import api, admin  # noqa: E402

app.include_router(api.router)
app.include_router(admin.router)


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/admin")


# ─── Telegram webhook endpoint ───────────────────────────────────────────────

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receives Telegram updates in webhook mode."""
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="Bot not configured")

    from aiogram.types import Update

    bot = _get_bot()
    dp = _get_dp()

    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


# ─── Internal cron endpoint (reminders) ─────────────────────────────────────

@app.post("/internal/reminders")
async def run_reminders(request: Request):
    """
    Called by Vercel Cron every 5 minutes.
    Protected by CRON_SECRET env var (Vercel sets Authorization header automatically).
    """
    if settings.cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {settings.cron_secret}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    if not settings.telegram_bot_token:
        return {"ok": True, "skipped": "no bot token"}

    try:
        from app.bot.telegram_bot import run_reminders_once
        await run_reminders_once()
    except Exception as exc:
        logging.getLogger(__name__).warning("Reminders error: %s", exc)
        return {"ok": False, "error": str(exc)}

    return {"ok": True}
