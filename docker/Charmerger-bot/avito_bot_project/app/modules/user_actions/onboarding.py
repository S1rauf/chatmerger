# /app/modules/user_actions/onboarding.py
import logging
import redis.asyncio as redis
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db_models import User
from shared.config import USER_AGREEMENT_INTRO_TEXT, USER_AGREEMENT_FULL_TEXT

logger = logging.getLogger(__name__)

async def check_and_send_terms_agreement(
    user: User, 
    session: AsyncSession, 
    redis_client: redis.Redis
):
    """
    Проверяет, нужно ли отправить пользователю соглашение, и отправляет его
    в виде интерактивного сообщения с раскрывающимся блоком.
    """
    logger.info(f"Пользователь {user.telegram_id} должен принять условия. Подготовка к отправке сообщения.")

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Я прочитал(а) и принимаю условия", callback_data="terms:accept")
    
    # --- СОБИРАЕМ НОВЫЙ ТЕКСТ СООБЩЕНИЯ ---
    full_text = (
        "🎉 <b>Аккаунт Avito успешно подключен!</b>\n\n"
        "Остался последний шаг. Чтобы начать получать сообщения, "
        "пожалуйста, ознакомьтесь с Пользовательским Соглашением и примите его условия."
        f"\n{USER_AGREEMENT_INTRO_TEXT}"
        f'<blockquote expandable><b>Читать полный текст соглашения...</b>\n{USER_AGREEMENT_FULL_TEXT}</blockquote>'
    )

    # Убедимся, что общая длина не превышает лимит Telegram
    if len(full_text) > 4096:
        logger.warning("Текст сгенерированных терминов слишком длинный для одного сообщения Telegram. Усечение.")
        # Обрезаем скрытую часть, если все вместе не влезает
        full_text = full_text[:4090] + "..."

    message_data = {
        "user_id": str(user.telegram_id),
        "text": full_text,
        "reply_markup": builder.as_markup().json(),
        "parse_mode": "HTML"
    }
    
    # Отправляем в очередь. Воркер уже умеет обрабатывать этот формат.
    await redis_client.xadd("telegram:outgoing:messages", message_data)