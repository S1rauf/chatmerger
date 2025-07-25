# src/modules/telegram/bot.py
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import WebAppInfo, ReplyKeyboardRemove, Message, User, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties

from .handlers import register_all_handlers
from .payment_handlers import payment_router
import shared.config as sh

logger = logging.getLogger(__name__)

# --- Инициализация ---
# Сначала создаем бота, потом диспетчер
bot = Bot(
    token=sh.settings.telegram_bot_token, 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# Регистрируем все наши обработчики (команды, ответы, кнопки)
register_all_handlers(dp)
dp.include_router(payment_router)

# --- Управление вебхуком ---
WEBHOOK_PATH = f"/webhook/telegram/{sh.settings.telegram_bot_token}" 
WEBHOOK_URL = sh.settings.webapp_base_url + WEBHOOK_PATH

async def set_telegram_webhook():
    """Устанавливает вебхук при старте приложения."""
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        masked_url = sh.settings.webapp_base_url + "/webhook/telegram/<hidden_token>"
        logger.info(f"Setting new Telegram webhook to: {masked_url}")

        await bot.set_webhook(url=WEBHOOK_URL)
    else:
        logger.info("Telegram webhook is already set.")

async def remove_telegram_webhook():
    """Удаляет вебхук при остановке приложения."""
    logger.info("Removing Telegram webhook...")
    await bot.delete_webhook()

async def process_telegram_update(update: dict):
    """
    Принимает обновление от FastAPI и передает его в aiogram для обработки.
    """
    await dp.feed_webhook_update(bot, update)

def register_all_handlers(dp: Dispatcher):
    """Регистрирует все хендлеры этого модуля в главном диспетчере."""
    dp.include_router(router)