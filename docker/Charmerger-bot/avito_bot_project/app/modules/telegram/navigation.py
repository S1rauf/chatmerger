import logging
from html import escape as html_escape

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

# --- Импорты из нашего проекта ---

# Клавиатуры
from .keyboards import (
    get_main_menu_keyboard,
    get_avito_accounts_menu,
    get_wallet_menu_keyboard,
    get_tariffs_list_keyboard
)

# База данных и модели
from db_models import User
from modules.database.crud import get_or_create_user, get_user_avito_accounts

# Сервисы и конфигурация
from modules.billing.service import billing_service
from modules.billing.config import TARIFF_CONFIG
from .keyboards import get_support_menu_keyboard
from shared.config import SUPPORT_GREETING_MESSAGE

logger = logging.getLogger(__name__)


async def handle_navigation_by_action(
    action: str, 
    callback: types.CallbackQuery, 
    state: FSMContext,
    **kwargs
):
    """
    Централизованная функция для обработки навигационных действий.
    Вызывается из основного хендлера и других мест, где нужно обновить экран.
    
    :param action: Строка действия, например, "main_menu", "accounts_list".
    :param callback: Объект CallbackQuery, из которого берется пользователь и сообщение для редактирования.
    :param state: FSM-контекст для его очистки.
    """
    """
    Централизованная функция для обработки навигационных и FSM-отменяющих действий.
    """
    # ---!!! ИСПРАВЛЕННАЯ ЛОГИКА С ВСПЛЫВАЮЩИМ УВЕДОМЛЕНИЕМ !!!---
    if action == "cancel_fsm":
        # 1. Показываем всплывающее уведомление
        await callback.answer("Действие отменено.")
        
        data = await state.get_data()
        prompt_message_id = data.get('prompt_message_id')
        
        # 2. Очищаем состояние
        await state.clear()
        
        # 3. Удаляем сообщение-приглашение (например, "Введите псевдоним")
        if prompt_message_id:
            try:
                await callback.bot.delete_message(callback.from_user.id, prompt_message_id)
            except TelegramBadRequest:
                pass
        
        # 4. Удаляем сообщение, на котором была кнопка "Отмена"
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        
        return

    # Для ВСЕХ ОСТАЛЬНЫХ навигационных действий, сначала очищаем состояние
    await state.clear()
    
    user_model = await get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username
    )

    # ==========================================================
    # === ГЛАВНОЕ МЕНЮ
    # ==========================================================
    if action == "main_menu":
        text = (
            f"Привет, <b>{html_escape(callback.from_user.full_name)}</b>!\n\n"
            "Я ваш помощник для управления чатами Avito.\n"
            "Используйте кнопки ниже или кнопку «Меню» для доступа к панели управления."
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_main_menu_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                logger.warning(f"Could not edit message for main_menu, reason: {e}")

    # ==========================================================
    # === СПИСОК AVITO-АККАУНТОВ
    # ==========================================================
    elif action == "accounts_list":
        accounts = await get_user_avito_accounts(callback.from_user.id)
        text = "👤 <b>Ваши аккаунты Avito</b>\n\nВыберите аккаунт для управления или добавьте новый."
        if not accounts:
            text = "У вас пока нет подключенных аккаунтов Avito. Давайте добавим первый!"
        
        keyboard = get_avito_accounts_menu(accounts)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    # ==========================================================
    # === КОШЕЛЕК И ТАРИФЫ (Промежуточное меню)
    # ==========================================================
    elif action == "wallet_and_tariffs":
        text = f"💰 <b>Кошелек и Тарифы</b>\n\nВаш текущий баланс: <b>{user_model.balance:.2f} ₽</b>"
        keyboard = get_wallet_menu_keyboard(user_model)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    # ==========================================================
    # === СПИСОК ТАРИФОВ (С ПОЛНЫМ ОПИСАНИЕМ)
    # ==========================================================
    elif action == "tariffs_list":
        current_plan = billing_service.get_user_tariff_plan(user_model)
        
        text_parts = ["📈 <b>Доступные тарифы</b>"]
        
        for plan_enum, config in TARIFF_CONFIG.items():
            header = f"<b>{config['name_readable']}</b>"
            if plan_enum == current_plan:
                header += " (Ваш текущий)"
            
            price_line = f"<i>{config['price_rub']} ₽ / {config.get('duration_days', '30')} дней</i>" if config['price_rub'] > 0 else "<i>Бесплатно, навсегда</i>"
            
            features_list = "\n".join(config['features_html'])
            
            tariff_block = (
                f"{header}\n{price_line}\n"
                f"<blockquote expandable>{features_list}</blockquote>"
            )
            text_parts.append(tariff_block)
            
        text = "\n\n".join(text_parts)
        text += "\n\nНажмите на блок, чтобы раскрыть/свернуть описание."
        
        keyboard = get_tariffs_list_keyboard(current_plan)

        await callback.message.edit_text(
            text, 
            reply_markup=keyboard, 
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    
    elif action == "support":
        text = SUPPORT_GREETING_MESSAGE
        keyboard = get_support_menu_keyboard()
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    # ... Сюда можно добавлять другие экраны, например, 'support', 'settings' и т.д.
    
    else:
        logger.warning(f"Unknown navigation action '{action}' received from user {callback.from_user.id}")
        # Можно ответить пользователю, что действие неизвестно, или ничего не делать
        try:
            await callback.answer("Неизвестное действие.", show_alert=True)
        except TelegramBadRequest:
            pass # Если query уже старый, ничего страшного

async def _cancel_fsm_and_cleanup(callback: types.CallbackQuery, state: FSMContext):
    """Очищает состояние FSM и удаляет служебные сообщения."""
    # Получаем ID сообщения-приглашения, чтобы его удалить
    data = await state.get_data()
    prompt_message_id = data.get('prompt_message_id')
    
    # Очищаем состояние
    await state.clear()
    
    # Удаляем сообщение-приглашение (например, "Введите псевдоним")
    if prompt_message_id:
        try:
            await callback.bot.delete_message(callback.from_user.id, prompt_message_id)
        except TelegramBadRequest:
            pass
    
    # Удаляем сообщение, на котором была кнопка "Отмена"
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass