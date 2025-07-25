import logging
import json
from datetime import timedelta
from typing import List, Optional, Dict, Any

from aiogram import Router, types, Bot, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

# --- Адаптированные импорты под нашу структуру ---
from shared.config import settings
from modules.billing.config import DEPOSIT_OPTIONS_KOP
from modules.database.crud import get_or_create_user
from modules.wallet.service import wallet_service
from modules.billing.service import billing_service
from modules.billing.enums import TariffPlan
from modules.billing.config import TARIFF_CONFIG

# Импорт типа для аннотации
import redis.asyncio as redis

logger = logging.getLogger(__name__)

payment_router = Router(name="payment_handlers_router")


# --- 1. Функция отправки инвойса (без изменений) ---
async def send_deposit_invoice(
    bot: Bot,
    chat_id: int,
    user_id: int,
    amount_kop: int
):
    """Отправляет инвойс на пополнение кошелька."""
    logger.info(f"Подготовка инвойса на пополнение {amount_kop / 100} руб. для TG ID {user_id}")

    if not settings.tg_payments_provider_token:
        logger.error("TG_PAYMENTS_PROVIDER_TOKEN не настроен! Инвойс не будет отправлен.")
        await bot.send_message(chat_id, "Сервис оплаты временно недоступен. Администратор уведомлен.")
        return

    invoice_payload = f"deposit_{amount_kop}_{user_id}"
    title = "Пополнение кошелька"
    description = f"Пополнение внутреннего баланса в боте на сумму {amount_kop / 100:.2f} руб."
    prices = [types.LabeledPrice(label="Пополнение кошелька", amount=amount_kop)]

    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=invoice_payload,
            provider_token=settings.tg_payments_provider_token,
            currency="RUB",
            prices=prices,
            is_flexible=False
        )
        logger.info(f"Инвойс {invoice_payload} успешно отправлен TG ID {user_id}.")
    except TelegramAPIError as e:
        logger.error(f"Ошибка API при отправке инвойса TG ID {user_id}: {e.message}", exc_info=True)
        await bot.send_message(chat_id, "⚠️ Не удалось создать счет на оплату. Пожалуйста, попробуйте позже.")


# --- 2. Обработчик Pre-Checkout Query (без изменений) ---
@payment_router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery, bot: Bot):
    """
    Подтверждает готовность принять платеж.
    """
    user_id = pre_checkout_query.from_user.id
    logger.info(f"Получен PreCheckoutQuery от UserID={user_id}, Payload='{pre_checkout_query.invoice_payload}'")

    user = await get_or_create_user(telegram_id=user_id, username=pre_checkout_query.from_user.username)
    if not user:
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Пользователь не найден. Пожалуйста, перезапустите бота командой /start.")
        return

    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    logger.info(f"PreCheckoutQuery для UserID={user_id} одобрен.")


# ==========================================================
# === ПОЛНАЯ ВЕРСИЯ МЕТОДА process_successful_payment
# ==========================================================
@payment_router.message(F.successful_payment)
async def process_successful_payment(message: types.Message, redis_client: redis.Redis):
    """
    Обрабатывает сообщение об успешной оплате от Telegram.
    """
    payment_info = message.successful_payment
    invoice_payload = payment_info.invoice_payload
    user_tg_id = message.from_user.id
    
    logger.info(f"Получен SuccessfulPayment от UserID={user_tg_id}, Payload='{invoice_payload}'")

    # 1. Идемпотентность: проверяем, не обработан ли уже этот платеж
    processed_charge_key = f"tgpay_charge_processed:{payment_info.telegram_payment_charge_id}"
    if not await redis_client.set(processed_charge_key, "1", ex=timedelta(days=60), nx=True):
        logger.warning(f"Платеж {payment_info.telegram_payment_charge_id} уже был обработан. Игнорируем.")
        return

    # 2. Получаем нашего пользователя и его внутренний ID
    user = await get_or_create_user(telegram_id=user_tg_id, username=message.from_user.username)
    if not user:
        logger.error(f"Не удалось найти или создать пользователя {user_tg_id} при обработке платежа.")
        await message.reply("Произошла ошибка (пользователь не найден). Свяжитесь с поддержкой.")
        return
        
    internal_user_id = user.id

    # 3. Разбираем payload и выполняем действие
    payload_parts = invoice_payload.split('_')
    action_type = payload_parts[0] if payload_parts else "unknown"

    try:
        if action_type == "deposit":
            # Убеждаемся, что payload корректный
            if len(payload_parts) < 2:
                raise ValueError("Некорректный payload для пополнения")

            amount_kop_deposited = int(payload_parts[1])
            
            # Вызываем наш wallet_service для пополнения, используя внутренний ID
            # и передавая redis_client
            await wallet_service.deposit(
                user_id=internal_user_id,
                amount=float(amount_kop_deposited) / 100,
                description=f"Пополнение через Telegram Payments. Charge ID: {payment_info.telegram_payment_charge_id}",
                redis_client=redis_client
            )
            await message.answer(f"✅ Кошелек успешно пополнен на {amount_kop_deposited / 100:.2f} руб.!")
        
        elif action_type == "buy":
            # Эта логика для прямой покупки тарифа, минуя кошелек.
            # Если вы хотите, чтобы все шло через пополнение, этот блок можно убрать.
            await message.answer("Покупка тарифа напрямую в разработке.")
            logger.warning(f"Получен 'buy' payload, который не обрабатывается: {invoice_payload}")

        else:
            raise ValueError(f"Неизвестный тип действия в payload: {action_type}")

    except (ValueError, IndexError) as e:
        logger.error(f"Некорректный формат payload в успешном платеже: {invoice_payload}. Ошибка: {e}")
        await message.reply("Произошла ошибка (некорректные данные платежа). Свяжитесь с поддержкой.")
    except Exception as e:
        logger.error(f"Критическая ошибка при обработке SuccessfulPayment (payload: {invoice_payload}): {e}", exc_info=True)
        await message.reply("Произошла критическая ошибка при обработке вашего платежа. Администратор уже уведомлен. Пожалуйста, свяжитесь с поддержкой.")