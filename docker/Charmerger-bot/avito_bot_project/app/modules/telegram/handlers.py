# /app/modules/telegram/handlers.py

import logging
import html
import asyncio
from html import escape as html_escape
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import json
from .view_renderer import ViewRenderer
from db_models import User, Template
from aiogram import Bot, Router, types, F, Dispatcher
from aiogram.filters import CommandStart, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.utils.deep_linking import decode_payload
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
import redis.asyncio as redis
from .keyboards import get_single_account_menu, get_avito_accounts_menu
from .filters import HasAvitoContextFilter
from shared.config import REPLY_MAPPING_TTL
from shared.config import settings
from modules.database.crud import (
    get_or_create_user,
    get_user_templates,
    get_avito_account_by_id,
    delete_avito_account,
    toggle_avito_account_active_status,
    get_user_avito_accounts,
    upsert_note_for_chat,
    get_account_stats,
    set_avito_account_alias
)
from modules.avito.client import AvitoAPIClient
from .keyboards import get_main_menu_keyboard, get_avito_accounts_menu, get_single_account_menu, build_chats_list_keyboard, get_wallet_menu_keyboard, get_tariffs_list_keyboard, get_deposit_options_keyboard,get_templates_for_chat_keyboard
from .states import RenameAvitoAccount, EditChatNote, AcceptInvite 
from .view_provider import (
    rehydrate_view_model,
    subscribe_user_to_view,
    VIEW_KEY_TPL
)
from aiogram.enums import ParseMode 
from .view_provider import subscribe_user_to_view 
from .view_renderer import ViewRenderer
from modules.billing.service import billing_service
from modules.billing.exceptions import TariffLimitReachedError, InsufficientFundsError, BillingError
from modules.billing.enums import TariffPlan
from modules.billing.config import TARIFF_CONFIG
from .keyboards import get_deposit_options_keyboard 
from .payment_handlers import send_deposit_invoice 
from .navigation import handle_navigation_by_action
from shared.database import get_session
from modules.database import crud
from aiogram.fsm.context import FSMContext
from shared.config import SUPPORT_GREETING_MESSAGE, SUPPORT_FAQ
from .keyboards import get_support_menu_keyboard
from .states import ContactAdmin
from modules.avito.messaging import AvitoMessaging

logger = logging.getLogger(__name__)
router = Router()



# ===================================================================
# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ========================================
# ===================================================================

async def _publish_view_update(redis_client: redis.Redis, account_id: int, chat_id: str):
    view_key = f"chat_view:{account_id}:{chat_id}"
    await redis_client.publish("views:update_required", view_key)
    logger.info(f"Published update event for view: {view_key}")

async def _show_accounts_menu(target: types.Message | types.CallbackQuery, user_telegram_id: int):
    accounts = await get_user_avito_accounts(user_telegram_id)
    text = "👤 **Ваши аккаунты Avito**\n\nВыберите аккаунт для управления или добавьте новый." if accounts else \
           "У вас пока нет подключенных аккаунтов Avito. Давайте добавим первый!"
    keyboard = get_avito_accounts_menu(accounts)
    message_to_act_on = target.message if isinstance(target, types.CallbackQuery) else target
    try:
        await message_to_act_on.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            if isinstance(target, types.CallbackQuery): await target.answer()
            return
        logger.warning(f"Could not edit message, sending new one. Reason: {e}")
        try:
            await message_to_act_on.delete()
        except TelegramBadRequest: pass
        await target.bot.send_message(target.from_user.id, text, reply_markup=keyboard, parse_mode="Markdown")

async def show_chat_card_by_context(avito_context: dict, user_tg: types.User, redis_client: redis.Redis, bot: Bot):
    """Перерисовывает карточку чата, используя данные из контекста."""
    try:
        account_id = int(avito_context['avito_account_id'])
        chat_id = avito_context['avito_chat_id']
        account = await get_avito_account_by_id(account_id)
        if not account: return

        view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
        model = await rehydrate_view_model(redis_client, account, chat_id)
        if not model: return

        renderer = ViewRenderer(bot, redis_client)
        user_db = await get_or_create_user(user_tg.id, user_tg.username)
        sent_message = await renderer.render_new_card(model, user_db)
        if sent_message:
            await subscribe_user_to_view(redis_client, view_key, user_db.telegram_id, sent_message.message_id)
            context_key = f"tg_context:{sent_message.message_id}"
            await redis_client.set(context_key, json.dumps(avito_context), ex=REPLY_MAPPING_TTL)
    except Exception as e:
        logger.error(f"Failed to redraw chat card after error: {e}")
# ===================================================================
# === УПРАВЛЕНИЕ АККАУНТАМИ ========================================
# ===================================================================

@router.callback_query(lambda c: c.data == "avito_acc:add_new")
async def add_new_avito_account(callback: types.CallbackQuery):
    logger.info(f"Handler 'add_new_avito_account' triggered for user {callback.from_user.id}")
    
    try:
        async with get_session() as session:
            db_user = await crud.get_or_create_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username
            )
            
            # --- ИЗМЕНЕНИЕ: Проверка лимита на количество аккаунтов ---
            await billing_service.check_avito_account_limit(db_user, session)
            
        await callback.answer("Генерирую ссылку для подключения...", show_alert=False)
        
        connect_url = f"{settings.webapp_base_url}/connect/avito?user_id={db_user.id}"
        
        text = (
            "✅ **Ссылка готова!**\n\n"
            "Нажмите на кнопку ниже, чтобы перейти на сайт Avito и разрешить доступ.\n\n"
            "*Убедитесь, что вы авторизованы в нужном аккаунте Avito в браузере.*"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🔗 Подключить Avito", url=connect_url)
        builder.button(text="❌ Отмена", callback_data="navigate:accounts_list")
        builder.adjust(1)
              
        await callback.message.edit_text(
            text, reply_markup=builder.as_markup(), parse_mode="Markdown"
        )

    except TariffLimitReachedError as e:
        await callback.answer(str(e), show_alert=True)
    except Exception as e:
        logger.error(f"Error in add_new_avito_account: {e}", exc_info=True)
        await callback.message.answer("Произошла ошибка при генерации ссылки. Попробуйте снова.")

async def some_handler_to_add_account(user: User):
    # Получаем текущее количество аккаунтов
    user_accounts = await get_user_avito_accounts(user.telegram_id)
    
    # Проверяем лимит через сервис
    if not billing_service.can_add_avito_account(user, len(user_accounts)):
        await bot.send_message(user.telegram_id, "Вы достигли лимита на количество аккаунтов Avito для вашего тарифа.")
        return
        
# ===================================================================
# === ОСНОВНЫЕ И НАВИГАЦИОННЫЕ ХЕНДЛЕРЫ (без изменений) ==============
# ===================================================================
@router.callback_query(F.data.startswith("navigate:"))
async def handle_navigation_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    await handle_navigation_by_action(action, callback, state)


@router.message(F.text, F.reply_to_message, HasAvitoContextFilter())
async def handle_reply_message(message: types.Message, redis_client: redis.Redis, avito_context: dict):
    logger.info(f"Handling text reply to Avito message. Context: {avito_context}")
    
    try:
        # --- ИЗМЕНЕНИЕ: Проверка дневного лимита сообщений ---
        user = await get_or_create_user(message.from_user.id, message.from_user.username)
        await billing_service.check_and_increment_daily_messages(user, redis_client)

        # --- Существующая логика ---
        await redis_client.xadd(
            "avito:chat:actions",
            {"account_id": str(avito_context['avito_account_id']), "chat_id": avito_context['avito_chat_id'], "action": "mark_read"}
        )
        outgoing_message = {
            "account_id": str(avito_context['avito_account_id']),
            "chat_id": avito_context['avito_chat_id'],
            "text": message.text,
            "action_type": "manual_reply",
            "author_name": message.from_user.first_name or message.from_user.username or f"ID {message.from_user.id}"
        }
        await redis_client.xadd("avito:outgoing:messages", outgoing_message)
        await message.delete()

    except TariffLimitReachedError as e:
        # --- ИЗМЕНЕНИЕ: Обработка ошибки лимита ---
        await message.reply(f"❗️ {e}\n\nЧтобы снять ограничение, перейдите на более высокий тариф.")
    except Exception as e:
        logger.error(f"Error in handle_reply_message: {e}", exc_info=True)
        await message.reply("❌ Произошла ошибка при отправке сообщения.")

# --- Хендлер для ответа ФОТОГРАФИЕЙ ---
@router.message(F.photo, F.reply_to_message, HasAvitoContextFilter())
async def handle_photo_reply(message: types.Message, redis_client: redis.Redis, bot: Bot, avito_context: dict):
    logger.info(f"Handling PHOTO reply (re-send logic). Context: {avito_context}")
    
    old_card_message = message.reply_to_message
    if not old_card_message: return

    try:
        # --- ИЗМЕНЕНИЕ: Проверка дневного лимита сообщений ДО всех действий ---
        user = await get_or_create_user(message.from_user.id, message.from_user.username)
        await billing_service.check_and_increment_daily_messages(user, redis_client)

        # --- Существующая логика ---
        photo = message.photo[-1]
        photo_file_id = photo.file_id
        caption = message.caption or ""

        try:
            await old_card_message.delete()
            await message.delete()
        except TelegramBadRequest as e:
            logger.warning(f"Could not delete old messages: {e}")

        new_photo_message = None
        new_card_message = None
        try:
            new_photo_message = await bot.send_photo(
                chat_id=message.chat.id, photo=photo_file_id,
                caption=f"<i>(Ваше вложение для Avito)</i>\n{caption}", parse_mode=ParseMode.HTML
            )
            new_card_message = await bot.send_message(
                chat_id=message.chat.id,
                text=old_card_message.text + "\n\n<b>⏳ Отправляю ваше изображение...</b>",
                parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Failed to resend messages: {e}")
            await bot.send_message(message.chat.id, "❌ Произошла ошибка при обработке вашего фото.")
            return

        # Основная логика отправки в Avito
        try:
            account_id = int(avito_context['avito_account_id'])
            chat_id = avito_context['avito_chat_id']
            account = await get_avito_account_by_id(account_id)
            if not account: raise ValueError("Аккаунт Avito не найден")

            file_info = await bot.get_file(photo_file_id)
            image_bytes = await bot.download_file(file_info.file_path)
            
            api_client = AvitoAPIClient(account)
            messaging = AvitoMessaging(api_client)
            upload_response = await messaging.upload_image(image_bytes.read())
            image_id = list(upload_response.keys())[0]
            
            outgoing_message = {
                "account_id": str(account_id), "chat_id": chat_id, "action_type": "image_reply",
                "image_id": image_id, "text": caption,
                "author_name": message.from_user.first_name or message.from_user.username or f"ID {message.from_user.id}",
            }
            await redis_client.xadd("avito:outgoing:messages", outgoing_message)

            view_key = f"chat_view:{account_id}:{chat_id}"
            await subscribe_user_to_view(redis_client, view_key, message.from_user.id, new_card_message.message_id)
            context_key = f"tg_context:{new_card_message.message_id}"
            avito_context['can_reply'] = 'true'
            await redis_client.set(context_key, json.dumps(avito_context), ex=REPLY_MAPPING_TTL)
        except Exception as e_inner:
            logger.error(f"Error during Avito processing for chat {chat_id}: {e_inner}", exc_info=True)
            if new_card_message:
                await bot.edit_message_text(
                    text=new_card_message.text.replace("⏳ Отправляю ваше изображение...", "❌ Ошибка отправки."),
                    chat_id=new_card_message.chat.id, message_id=new_card_message.message_id
                )
    
    except TariffLimitReachedError as e:
        # --- ИЗМЕНЕНИЕ: Обработка ошибки лимита ---
        await message.reply(f"❗️ {e}\n\nЧтобы снять ограничение, перейдите на более высокий тариф.")
        # Восстанавливаем карточку для пользователя
        await show_chat_card_by_context(avito_context, message.from_user, redis_client, bot)
        # Удаляем его сообщение, которое не удалось отправить
        await message.delete()

    except Exception as e:
        logger.error(f"Outer error in handle_photo_reply for chat {avito_context.get('avito_chat_id')}: {e}", exc_info=True)
        await message.reply("❌ Не удалось отправить изображение. Попробуйте снова.")

@router.message(CommandStart(deep_link=True))
async def handle_deep_link(message: types.Message, command: CommandObject, state: FSMContext, redis_client):
    payload = command.args
    user_id = message.from_user.id # Сохраняем для лога
    logger.info(f"User {user_id} started bot with deep-link payload: {payload}")

    if payload.startswith("fw_accept_"):
        invite_code = payload.replace("fw_accept_", "")
        async with get_session() as session:
            # --- ВАЖНО: загружаем владельца сразу, он понадобится в любом случае ---
            rule = await session.scalar(
                select(crud.ForwardingRule)
                .where(crud.ForwardingRule.invite_code == invite_code)
                .options(selectinload(crud.ForwardingRule.owner))
            )
            if not rule or rule.target_telegram_id is not None:
                await message.answer("❌ Ссылка-приглашение недействительна или уже была использована.")
                return
            if rule.invite_password:
                await state.update_data(invite_code=invite_code)
                
                # ---!!! ОТЛАДОЧНЫЙ ЛОГ №1: УСТАНОВКА СОСТОЯНИЯ !!!---
                target_state = AcceptInvite.waiting_for_password
                logger.critical(
                    f"[DEBUG-FSM] DEEP_LINK for user {user_id}: "
                    f"Attempting to set state to '{target_state.state}'"
                )
                await state.set_state(target_state)
                # --------------------------------------------------

                await message.answer("Для принятия приглашения, пожалуйста, введите пароль:")
            else:
                # Логика для инвайта без пароля
                await crud.accept_forwarding_invite(session, rule.id, message.from_user.id)
                await message.answer("✅ <b>Приглашение принято!</b>", parse_mode="HTML")
                # Уведомляем владельца
                if rule.owner:
                     notification_text = f"✅ Ваш помощник «{html.escape(rule.custom_rule_name)}» принял приглашение!"
                     await redis_client.xadd("telegram:outgoing:messages", {"user_id": rule.owner.telegram_id, "text": notification_text})
    else:
        await handle_start_command(message, state)


# --- НОВЫЙ ХЕНДЛЕР ДЛЯ ВВОДА ПАРОЛЯ (с добавленным декоратором и логами) ---
@router.message(StateFilter(AcceptInvite.waiting_for_password)) # <--- !!! ДОБАВЛЕН ЭТОТ ДЕКОРАТОР !!!
async def process_invite_password(message: types.Message, state: FSMContext, redis_client):
    user_id = message.from_user.id # Сохраняем для лога
    
    # ---!!! ОТЛАДОЧНЫЙ ЛОГ №2: ПРОВЕРКА СОСТОЯНИЯ !!!---
    current_state = await state.get_state()
    logger.critical(
        f"[DEBUG-FSM] PROCESS_PASSWORD for user {user_id}: "
        f"Handler triggered. Current FSM state is: '{current_state}'"
    )
    # ----------------------------------------------------

    user_password = message.text.strip()
    state_data = await state.get_data()
    invite_code = state_data.get('invite_code')

    if not invite_code:
        await state.clear()
        return
    
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    async with get_session() as session:
        rule = await session.scalar(
            select(crud.ForwardingRule)
            .where(crud.ForwardingRule.invite_code == invite_code)
            .options(selectinload(crud.ForwardingRule.owner))
        )

        if not rule or rule.invite_password != user_password:
            error_msg = await message.answer("❌ Неверный пароль. Попробуйте еще раз или запросите новый инвайт.")
            await delete_message_after_delay(error_msg, 5)
            return

        await crud.accept_forwarding_invite(session, rule.id, message.from_user.id)
        await state.clear()
        
        success_msg = await message.answer("✅ <b>Приглашение принято!</b> Пароль верный.")
        await delete_message_after_delay(success_msg, 3)
        if rule.owner:
            notification_text = (f"✅ Ваш помощник «{html.escape(rule.custom_rule_name)}» принял приглашение!\n"
                                 f"Пользователь: {html.escape(message.from_user.full_name)} (@{message.from_user.username or '...'})")
            await redis_client.xadd("telegram:outgoing:messages", {"user_id": rule.owner.telegram_id, "text": notification_text})

@router.message(CommandStart())
async def handle_start_command(message: types.Message, state: FSMContext):
    await state.clear()
    await crud.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    safe_name = html_escape(message.from_user.full_name)
    welcome_text = (f"Привет, <b>{safe_name}</b>!\n\nЯ ваш помощник для управления чатами Avito. "
                    f"Используйте кнопки ниже для доступа к панели управления.")
    await message.answer(
        text=welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == "👤 Мои Avito аккаунты")
async def show_avito_accounts_menu_handler(message: types.Message):
    await _show_accounts_menu(message, message.from_user.id)

# ===================================================================
# === УПРАВЛЕНИЕ ЧАТАМИ (НОВАЯ ЛОГИКА) ===============================
# ===================================================================

@router.callback_query(F.data.startswith("account_actions:chats:") | F.data.startswith("chats:list:"))
async def show_latest_chats(callback: types.CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    account_id = int(parts[2])
    offset = int(parts[3]) if len(parts) > 3 else 0
    limit = 5
    account = await crud.get_avito_account_by_id(account_id)
    if not account: return
    api_client = AvitoAPIClient(account)
    chats_data = await api_client.get_chats(limit=5, offset=offset)
    keyboard = build_chats_list_keyboard(chats_data.get("chats", []), account.id, offset, limit)
    await callback.message.edit_text(
        f"Последние чаты для аккаунта **{html_escape(account.alias or f'ID {account.avito_user_id}')}**:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("chat:show_card:"))
async def show_chat_card(callback: types.CallbackQuery, redis_client: redis.Redis, bot: Bot):
    """
    Отрисовывает или обновляет карточку чата.
    Срабатывает при первом показе или при нажатии кнопки "Назад к чату".
    """
    await callback.answer()
    
    try:
        _, _, account_id_str, chat_id = callback.data.split(":")[:4]
        account_id = int(account_id_str)
    except (ValueError, IndexError):
        return

    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    account = await get_avito_account_by_id(account_id)
    if not (user and account): return

    # --- ЛОГИКА ПЕРЕРИСОВКИ ---
    # Получаем общую модель чата
    view_key = VIEW_KEY_TPL.format(account_id=account_id, chat_id=chat_id)
    model = await rehydrate_view_model(redis_client, account, chat_id)
    
    if not model:
        await callback.message.edit_text("Не удалось загрузить данные чата.")
        return

    # Устанавливаем ID текущего сообщения для редактирования
    model['telegram_message_id'] = callback.message.message_id
    
    # Вызываем рендерер, который обновит сообщение до вида карточки чата
    renderer = ViewRenderer(bot, redis_client)
    await renderer.update_all_subscribers(view_key, model)

# ==========================================================
# === ОБРАБОТЧИК КНОПКИ "ПРОЧИТАНО" (НОВАЯ ВЕРСИЯ)
# ==========================================================
@router.callback_query(F.data.startswith("chat:mark_read:"))
async def mark_read_handler(callback: types.CallbackQuery, redis_client: redis.Redis, bot: Bot):
    try:
        await callback.answer("Помечаю как прочитанное...")
    except TelegramBadRequest:
        return

    try:
        _, _, account_id_str, chat_id = callback.data.split(":")[:4]
        account_id = int(account_id_str)
    except (ValueError, IndexError):
        logger.error(f"Invalid callback_data format for mark_read: {callback.data}")
        return

    account = await crud.get_avito_account_by_id(account_id)
    if not account:
        logger.warning(f"Account {account_id} not found for mark_read action.")
        return

    try:
        api_client = AvitoAPIClient(account)
        await api_client.mark_chat_as_read(chat_id)
        logger.info(f"Chat {chat_id} marked as read in Avito API by user {callback.from_user.id}")
    except AvitoAPIError as e:
        logger.error(f"Failed to mark chat as read in Avito API for chat {chat_id}: {e}")
        try:
            await callback.answer("❌ Ошибка при обращении к API Avito.", show_alert=True)
        except TelegramBadRequest: pass
        return

    view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
    model_json = await redis_client.get(view_key)
    if not model_json:
        logger.warning(f"View model not found for key: {view_key}. Cannot update Telegram messages.")
        return
        
    model: dict = json.loads(model_json)
    
    # Меняем флаг
    model['is_last_message_read'] = True
    
    # ---!!! ОТЛАДОЧНЫЙ ЛОГ №4 !!!---
    logger.critical(
        f"[DEBUG-READ-STATUS] MARK_READ for chat {chat_id}: "
        f"SETTING is_last_message_read = True. Saving to Redis and updating subscribers."
    )
    
    await redis_client.set(view_key, json.dumps(model), keepttl=True)
    
    renderer = ViewRenderer(bot, redis_client)
    await renderer.update_all_subscribers(view_key, model)


@router.callback_query(F.data.startswith("chat:block:") | F.data.startswith("chat:unblock:"))
async def block_unblock_chat_handler(callback: types.CallbackQuery, redis_client: redis.Redis, bot: Bot):
    await callback.answer("Выполняю...")
    
    action, _, account_id_str, chat_id, user_id_str = callback.data.split(":")
    account_id = int(account_id_str)
    
    account = await get_avito_account_by_id(account_id)
    if not account: return

    api_client = AvitoAPIClient(account=account)
    
    try:
        new_status = (action == "block")
        if new_status:
            chat_info = await api_client.get_chat_info(chat_id)
            item_id = chat_info.get("context", {}).get("value", {}).get("id")
            await api_client.block_user_in_chat(chat_id, int(user_id_str), item_id)
        else:
            await api_client.unblock_user(int(user_id_str))

        view_key = f"chat_view:{account.id}:{chat_id}"
        model_json = await redis_client.get(view_key)
        if model_json:
            model = json.loads(model_json)
            model['is_blocked'] = new_status
            model['telegram_message_id'] = callback.message.message_id
            await redis_client.set(view_key, json.dumps(model), keepttl=True)

            renderer = ViewRenderer(bot, redis_client)
            await renderer.render(view_key=view_key, model=model)
        
        await callback.answer("Готово!", show_alert=True)
    
    except Exception as e:
        logger.error(f"Error in block/unblock handler: {e}", exc_info=True)
        await callback.answer("Действие не удалось выполнить.", show_alert=True)

# ===================================================================
# === МАШИНЫ СОСТОЯНИЙ (FSM) (ИСПРАВЛЕННАЯ ВЕРСИЯ) ===================
# ===================================================================

# --- Переименование аккаунта (ИСПРАВЛЕНО) ---
@router.callback_query(F.data.startswith("avito_acc:rename:"))
async def rename_avito_account_start(callback: types.CallbackQuery, state: FSMContext):
    """Шаг 1: Запускает FSM для переименования аккаунта."""
    account_id = int(callback.data.split(":")[2])
    
    # Сохраняем ID сообщения, которое нужно будет обновить в конце
    await state.update_data(
        account_id=account_id, 
        original_message_id=callback.message.message_id
    )
    await state.set_state(RenameAvitoAccount.waiting_for_new_alias)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="navigate:cancel_fsm")
    
    prompt = await callback.message.answer(
        "✍️ Отправьте новый псевдоним для аккаунта (3-50 симв).",
        reply_markup=builder.as_markup() # <--- Добавляем клавиатуру
    )
    # Сохраняем ID сообщения-приглашения, чтобы его потом удалить
    await state.update_data(prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(StateFilter(RenameAvitoAccount.waiting_for_new_alias))
async def rename_avito_account_process(message: types.Message, state: FSMContext, bot: Bot):
    """Шаг 2: Обрабатывает новый псевдоним и обновляет меню."""
    new_alias = message.text.strip()
    if not (3 <= len(new_alias) <= 50):
        return await message.reply("❗️Ошибка: псевдоним должен быть от 3 до 50 символов.")
    
    data = await state.get_data()
    account_id = data['account_id']
    original_message_id = data['original_message_id']
    prompt_message_id = data['prompt_message_id']

    # 1. Сохраняем новый псевдоним в БД
    await set_avito_account_alias(account_id, new_alias)
    
    # 2. Формируем текст и клавиатуру для обновленного меню аккаунтов
    accounts = await get_user_avito_accounts(message.from_user.id)
    text = "👤 **Ваши аккаунты Avito**\n\nПсевдоним успешно изменен. Выберите аккаунт для управления или добавьте новый."
    keyboard = get_avito_accounts_menu(accounts)

    # 3. Редактируем ИСХОДНОЕ сообщение с меню, используя сохраненные ID
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=original_message_id,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        logger.error(f"Could not edit original accounts menu: {e}")
        # Если не удалось отредактировать, просто отправляем новое сообщение
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    # 4. Удаляем служебные сообщения (приглашение и ответ пользователя)
    try:
        await bot.delete_message(message.chat.id, prompt_message_id)
        await message.delete()
    except TelegramBadRequest:
        pass # Игнорируем ошибки, если сообщения уже удалены

    # 5. Завершаем состояние FSM
    await state.clear()

#text="✍️ **Отправьте ответным сообщением новый текст заметки**\n\nИли используйте кнопки ниже для удаления или отмены.",
# --- Редактирование заметки к чату (без изменений, но оставляю для полноты) ---
@router.callback_query(F.data.startswith("chat:edit_note:"))
async def edit_chat_note_start(callback: types.CallbackQuery, state: FSMContext, bot: Bot, redis_client: redis.Redis):
    await callback.answer()
    _, _, account_id_str, chat_id = callback.data.split(":")
    account_id = int(account_id_str)

    user = await crud.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name
    )
    
    view_key = VIEW_KEY_TPL.format(account_id=account_id, chat_id=chat_id)
    model_json = await redis_client.get(view_key)
    model = json.loads(model_json) if model_json else {}

    prompt_text = "✍️ Отправьте ответным сообщением новый текст вашей заметки.\n\n"
    
    # Ищем заметку от ТЕКУЩЕГО пользователя в модели
    note_data = model.get("notes", {}).get(str(user.telegram_id))
    if note_data and note_data.get('text'):
        prompt_text += "Текущая заметка (нажмите, чтобы скопировать):\n"
        prompt_text += f"<code>{html.escape(note_data.get('text'))}</code>\n\n"
    
    prompt_text += "Для удаления вашей заметки отправьте одно слово: <code>удалить</code>."

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="note:cancel_edit")
    
    await state.update_data(account_id=account_id, chat_id=chat_id)
    await state.set_state(EditChatNote.waiting_for_note_text)
    
    prompt_message = await bot.send_message(
        chat_id=callback.from_user.id, text=prompt_text,
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await state.update_data(prompt_message_id=prompt_message.message_id)


# ==========================================================
# === ОБРАБОТЧИК FSM ДЛЯ ЗАМЕТКИ (НОВАЯ ВЕРСИЯ)
# ==========================================================
@router.message(StateFilter(EditChatNote.waiting_for_note_text))
@router.message(StateFilter(EditChatNote.waiting_for_note_text))
async def process_chat_note_text(message: types.Message, state: FSMContext, redis_client: redis.Redis, bot: Bot):
    data = await state.get_data()
    account_id = data.get('account_id')
    chat_id = data.get('chat_id')
    prompt_message_id = data.get('prompt_message_id')
    
    if not all([account_id, chat_id]):
        await state.clear()
        await message.reply("Произошла ошибка, сессия редактирования устарела. Пожалуйста, начните заново.")
        return

    note_text = message.text.strip()
    user = await crud.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    
    feedback_text = ""
    is_deletion = note_text.lower() == 'удалить'
    
    if is_deletion:
        # Удаляем заметку из БД (upsert с пустым текстом сделает это)
        await crud.upsert_note_for_chat(account_id, chat_id, text="", author_id=user.id)
        feedback_text = "✅ Заметка удалена!"
    else:
        # Валидируем и сохраняем/обновляем заметку в БД
        if len(note_text) > 500:
            await message.reply("❗️Ошибка: текст заметки слишком длинный (макс. 500 символов).")
            return
        await crud.upsert_note_for_chat(account_id, chat_id, note_text, user.id)
        feedback_text = "✅ Заметка сохранена!"

    # Обновляем ChatViewModel и перерисовываем
    view_key = VIEW_KEY_TPL.format(account_id=account_id, chat_id=chat_id)
    model_json = await redis_client.get(view_key)
    
    if model_json:
        model = json.loads(model_json)
        user_tg_id_str = str(user.telegram_id)
        
        # Получаем словарь заметок, если его нет - создаем
        notes = model.setdefault("notes", {})
        
        if is_deletion:
            # Если заметка была удалена, убираем ее из модели
            if user_tg_id_str in notes:
                del notes[user_tg_id_str]
        else:
            # Иначе, добавляем/обновляем ее в модели
            notes[user_tg_id_str] = {
                "author_name": user.first_name or user.username or f"ID {user.id}",
                "text": note_text,
                "timestamp": int(datetime.now(timezone.utc).timestamp())
            }
        
        # Сохраняем обновленную модель
        await redis_client.set(view_key, json.dumps(model), keepttl=True)
        
        # Запускаем перерисовку у всех подписчиков
        renderer = ViewRenderer(bot, redis_client)
        await renderer.update_all_subscribers(view_key, model)

    # "Прибираемся"
    await state.clear()
    feedback_msg = await message.answer(feedback_text)
    await delete_message_after_delay(feedback_msg, 2)
    if prompt_message_id:
        try:
            await bot.delete_message(message.chat.id, prompt_message_id)
        except TelegramBadRequest: pass
    await message.delete()



@router.callback_query(F.data == "note:cancel_edit", StateFilter(EditChatNote.waiting_for_note_text))
async def cancel_edit_note_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    data = await state.get_data()
    prompt_message_id = data.get('prompt_message_id')
    
    await state.clear()

    if prompt_message_id:
        try:
            # Удаляем сообщение с приглашением ввести текст
            await callback.bot.delete_message(callback.from_user.id, prompt_message_id)
        except TelegramBadRequest: pass

@router.callback_query(F.data.startswith("note:delete:"))
async def delete_chat_note_handler(callback: types.CallbackQuery, redis_client: redis.Redis):
    """Шаг 2 (Путь Б): Пользователь нажал кнопку "Удалить заметку"."""
    await callback.answer("✅ Заметка удалена!", show_alert=False)
    
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Парсим все 4 части ---
    _, _, account_id_str, chat_id, target_message_id_str = callback.data.split(":")
    account_id = int(account_id_str)
    target_message_id = int(target_message_id_str)

    # Удаляем заметку из БД
    await upsert_note_for_chat(account_id, chat_id, text="", author_id=callback.from_user.id)
    
    # Обновляем view_model
    view_key = f"chat_view:{account_id}:{chat_id}"
    model_json = await redis_client.get(view_key)
    if model_json:
        model = json.loads(model_json)
        model.update({
            "note_text": None,
            "note_author_name": None,
            "note_timestamp": None,
            # ВАЖНО: Указываем, какую карточку редактировать
            "telegram_message_id": target_message_id 
        })
        await redis_client.set(view_key, json.dumps(model), keepttl=True)
        
        # Публикуем событие на перерисовку
        await _publish_view_update(redis_client, account_id, chat_id)
        
    # Удаляем сообщение с кнопками ("Введите текст заметки...")
    await callback.message.delete()

@router.callback_query(F.data == "note:cancel_edit")
async def cancel_edit_note_handler(callback: types.CallbackQuery, state: FSMContext):
    """Шаг 2 (Путь В): Пользователь нажал "Отмена"."""
    await callback.answer()
    await state.clear()
    await callback.message.delete()

@router.callback_query(F.data.startswith("billing:purchase:"))
async def purchase_tariff_handler(callback: types.CallbackQuery, state: FSMContext, redis_client: redis.Redis):
    """
    Обрабатывает нажатие на кнопку покупки/смены тарифа.
    Различает апгрейд и даунгрейд.
    """
    # 1. Извлекаем данные из callback'а
    plan_value = callback.data.split(":")[2]
    
    try:
        tariff_to_purchase = TariffPlan(plan_value)
    except ValueError:
        await callback.answer("Ошибка: некорректный тариф!", show_alert=True)
        return

    # 2. Получаем пользователя из БД
    user = await get_or_create_user(
        telegram_id=callback.from_user.id, 
        username=callback.from_user.username,
        with_accounts=True # Загружаем аккаунты, т.к. они могут понадобиться в сервисе
    )
    if not user:
        await callback.answer("Ошибка: не удалось найти вашего пользователя. Попробуйте /start.", show_alert=True)
        return

    # 3. Отвечаем на callback, чтобы убрать "часики" на кнопке
    await callback.answer(f"Обрабатываем переход на тариф «{TARIFF_CONFIG[tariff_to_purchase]['name_readable']}»...")

    try:
        # 4. Вызываем основной метод из нашего billing_service, передавая все зависимости
        result = await billing_service.purchase_tariff(user, tariff_to_purchase, redis_client)
        
        # 5. Обрабатываем результат, который вернул сервис
        if result.get("status") == "downgrade_scheduled":
             # Если был запланирован даунгрейд
             await callback.message.edit_text(
                "✅ <b>Переход на тариф запланирован</b>\n\n"
                f"Тариф «{TARIFF_CONFIG[tariff_to_purchase]['name_readable']}» будет автоматически "
                "активирован после окончания срока действия вашей текущей подписки.",
                parse_mode="HTML",
                # Можно добавить кнопку для возврата в главное меню
                reply_markup=InlineKeyboardBuilder().button(text="⬅️ В главное меню", callback_data="navigate:main_menu").as_markup()
             )
        elif result.get("status") == "upgraded":
            # Если был успешный апгрейд
            await callback.message.answer(
                f"✅ Поздравляем! Вы успешно перешли на тариф <b>{TARIFF_CONFIG[tariff_to_purchase]['name_readable']}</b>.",
                parse_mode="HTML"
            )
            # Обновляем меню тарифов, чтобы показать новый текущий тариф
            await handle_navigation_by_action("tariffs_list", callback, state)

    except InsufficientFundsError as e:
        # Обработка ошибки нехватки средств
        await callback.message.answer(
            f"❌ <b>Не удалось сменить тариф</b>\n\n"
            f"<b>Причина:</b> {e}\n\n"
            "Пожалуйста, пополните баланс и попробуйте снова.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardBuilder().button(
                text="💳 Пополнить баланс", callback_data="wallet:deposit"
            ).as_markup()
        )
    except BillingError as e:
        # Обработка других ошибок биллинга (например, повторная покупка того же тарифа)
        await callback.answer(f"❕ {e}", show_alert=True)
    except Exception as e:
        # Обработка всех остальных непредвиденных ошибок
        logger.error(f"Error purchasing tariff for user {user.id}: {e}", exc_info=True)
        await callback.answer("Произошла непредвиденная ошибка. Пожалуйста, обратитесь в поддержку.", show_alert=True)

def _format_features_for_telegram(features: list[str]) -> str:
    """
    Форматирует список фич: находит самую длинную строку
    и дополняет остальные невидимыми пробелами для выравнивания.
    """
    if not features:
        return ""
    
    # Убираем HTML-теги для корректного подсчета длины видимых символов
    clean_features = [
        f.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        for f in features
    ]
    
    max_len = 0
    if clean_features:
        max_len = max(len(s) for s in clean_features)

    # Невидимый пробел с фиксированной шириной (Figure Space)
    # Можно экспериментировать с другими, например, U+2002 (En Space) ` `
    INVISIBLE_SEPARATOR = " " 

    padded_features = []
    for original_feature, clean_feature in zip(features, clean_features):
        # Добавляем нужное количество невидимых разделителей
        padding_count = max_len - len(clean_feature)
        padding = INVISIBLE_SEPARATOR * padding_count
        padded_features.append(f"{original_feature}{padding}")

    # Соединяем строки. Теги <pre><code> больше не нужны!
    return "\n".join(padded_features)
    
# Хендлер для кнопки "Пополнить баланс"
@router.callback_query(F.data == "wallet:deposit")
async def show_deposit_options_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Выберите сумму для пополнения баланса:",
        reply_markup=get_deposit_options_keyboard()
    )
    await callback.answer()

# Хендлер для выбора конкретной суммы
@router.callback_query(F.data.startswith("deposit:amount:"))
async def create_deposit_invoice_handler(callback: types.CallbackQuery, bot: Bot):
    amount_kop = int(callback.data.split(":")[2])
    
    await send_deposit_invoice(
        bot=bot,
        chat_id=callback.from_user.id,
        user_id=callback.from_user.id,
        amount_kop=amount_kop
    )
    await callback.answer("Создаю счет на оплату...")

# ===================================================================
# === ПРОЧИЕ ДЕЙСТВИЯ С ЧАТАМИ (Шаблоны, заметки и т.д.) ==============
# ===================================================================

@router.callback_query(F.data.startswith("chat:templates:"))
async def show_templates_for_chat(callback: types.CallbackQuery):
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
    
    _, _, account_id_str, chat_id = callback.data.split(":")
    account_id = int(account_id_str)

    user = await crud.get_or_create_user(telegram_id=callback.from_user.id, username=callback.from_user.username)
    if not user:
        await callback.message.edit_text("Ошибка: не удалось найти вашего пользователя.")
        return

    async with get_session() as session:
        user_templates = await crud.get_user_templates(session, user.id)
    
    text = "Выберите шаблон для отправки:"
    if not user_templates:
        text = "У вас пока нет созданных шаблонов. Вы можете добавить их в панели управления (WebApp)."
        
    keyboard = get_templates_for_chat_keyboard(user_templates, account_id, chat_id)
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("template:send:"))
async def send_template_to_chat(callback: types.CallbackQuery, redis_client: redis.Redis, bot: Bot):
    """
    Отправляет шаблон в Avito. НЕ удаляет карточку, а ждет ее обновления от воркера.
    """
    # 1. Отвечаем на callback, чтобы пользователь видел, что действие принято
    await callback.answer("Отправляю шаблон...")

    _, _, template_id_str, account_id_str, chat_id = callback.data.split(":")
    template_id = int(template_id_str)
    account_id = int(account_id_str)

    async with get_session() as session:
        template = await session.get(Template, template_id)

    if not template:
        await callback.answer("Ошибка: шаблон не найден!", show_alert=True)
        return

    # 2. Опционально: можно временно "заморозить" клавиатуру, чтобы избежать двойных нажатий
    # Мы просто редактируем сообщение, убирая клавиатуру.
    # Воркер потом вернет ее обратно.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass # Игнорируем, если не получилось

    # 3. Отправляем команду "прочитано"
    await redis_client.xadd(
        "avito:chat:actions",
        {"account_id": str(account_id), "chat_id": chat_id, "action": "mark_read"}
    )

    # 4. Отправляем сообщение в очередь, как и раньше
    outgoing_message = {
        "account_id": str(account_id),
        "chat_id": chat_id,
        "text": template.text,
        "action_type": "template_reply",
        "template_name": template.name,
        "author_name": callback.from_user.first_name or callback.from_user.username or f"ID {callback.from_user.id}"
    }
    await redis_client.xadd("avito:outgoing:messages", outgoing_message)

@router.callback_query(F.data.startswith("chat:show:"))
async def show_chat_card_handler(callback: types.CallbackQuery, redis_client: redis.Redis, bot: Bot):
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    try:
        _, _, account_id_str, chat_id = callback.data.split(":")[:4]
        account_id = int(account_id_str)
    except (ValueError, IndexError):
        logger.error(f"Invalid callback_data format for show_chat_card: {callback.data}")
        return

    user = await crud.get_or_create_user(callback.from_user.id, callback.from_user.username)
    account = await crud.get_avito_account_by_id(account_id)
    if not (user and account):
        try:
            await callback.message.edit_text("❌ Ошибка: не удалось найти данные пользователя или аккаунта.")
        except TelegramBadRequest: pass
        return

    view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
    
    model_json = await redis_client.get(view_key)
    
    if model_json:
        model = json.loads(model_json)
        # ---!!! ОТЛАДОЧНЫЙ ЛОГ №3 !!!---
        logger.critical(
            f"[DEBUG-READ-STATUS] SHOW_CARD (from cache) for chat {chat_id}: "
            f"Read from Redis is_last_message_read = {model.get('is_last_message_read')}"
        )
    else:
        logger.warning(f"No model for {view_key} in cache. Rehydrating from API...")
        # Этот вызов уже залогирует данные из API через лог №1
        model = await rehydrate_view_model(redis_client, account, chat_id)
        # ---!!! ОТЛАДОЧНЫЙ ЛОГ №3.1 !!!---
        if model:
            logger.critical(
                f"[DEBUG-READ-STATUS] SHOW_CARD (rehydrated) for chat {chat_id}: "
                f"Value is is_last_message_read = {model.get('is_last_message_read')}"
            )

    if not model:
        try:
            await callback.message.edit_text("❌ Не удалось загрузить данные чата. Попробуйте позже.")
        except TelegramBadRequest: pass
        return

    await subscribe_user_to_view(
        redis_client,
        view_key,
        callback.from_user.id,
        callback.message.message_id
    )

    model_json = await redis_client.get(view_key)
    if not model_json:
        logger.error(f"Model for {view_key} disappeared after subscription. Aborting render.")
        return
    model = json.loads(model_json)
    
    renderer = ViewRenderer(bot, redis_client)
    try:
        await renderer.update_all_subscribers(view_key, model)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.info(f"View for {view_key} is already up to date. Skipping render.")
        else:
            logger.error(f"Error rendering card for {view_key}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error in renderer for {view_key}: {e}", exc_info=True)

async def delete_message_after_delay(message: types.Message, delay: int):
    """Вспомогательная функция для удаления сообщения с задержкой."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except TelegramBadRequest:
        # Игнорируем ошибку, если сообщение уже удалено или его нельзя удалить
        pass

@router.callback_query(F.data == "terms:accept")
async def handle_terms_accept(callback: types.CallbackQuery):
    """
    Обрабатывает нажатие кнопки "Принять условия".
    """
    user_id = callback.from_user.id
    logger.info(f"HANDLER: Caught 'terms:accept' callback from user {user_id}")
    
    async with get_session() as session:
        # Получаем пользователя из БД
        user = await session.scalar(select(User).where(User.telegram_id == user_id))
        
        if user and not user.has_agreed_to_terms:
            # Если пользователь найден и еще не соглашался
            user.has_agreed_to_terms = True
            # session.commit() будет вызван автоматически при выходе из `async with`
            logger.info(f"User {user_id} has agreed to the terms. Updating DB.")
            
            await callback.answer("Спасибо! Теперь вы можете полноценно пользоваться сервисом.", show_alert=True)
            
            # Удаляем сообщение с соглашением, чтобы не мешало
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
        elif user and user.has_agreed_to_terms:
            # Если пользователь уже согласился ранее
            await callback.answer("Вы уже приняли условия.", show_alert=False)
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
        else:
            # Если по какой-то причине пользователь не найден в БД
            await callback.answer("Произошла ошибка (пользователь не найден). Попробуйте перезапустить бота командой /start.", show_alert=True)

@router.callback_query(F.data.startswith("avito_acc:select:"))
async def select_avito_account(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает меню управления для конкретного выбранного аккаунта.
    """
    await callback.answer()
    account_id = int(callback.data.split(":")[2])
    
    account = await crud.get_avito_account_by_id(account_id)
    if not account:
        await callback.message.edit_text("❌ Ошибка: аккаунт не найден.")
        return

    # Получаем user_id из account, чтобы кнопка "Назад" работала
    text = (
        f"Управление аккаунтом: <b>{html.escape(account.alias or f'ID {account.avito_user_id}')}</b>\n\n"
        "Выберите действие:"
    )
    keyboard = get_single_account_menu(account)
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


# 2. Хендлер для кнопки "Отключить"
@router.callback_query(F.data.startswith("avito_acc:disable:"))
async def disable_avito_account(callback: types.CallbackQuery):
    """
    Деактивирует аккаунт Avito (устанавливает is_active = False).
    """
    account_id = int(callback.data.split(":")[2])
    
    # Меняем статус в БД
    await crud.toggle_avito_account_active_status(account_id, is_active=False)
    
    await callback.answer("Аккаунт отключен. Вы больше не будете получать по нему сообщения.", show_alert=True)
    
    # Обновляем меню аккаунтов, чтобы показать новый статус
    accounts = await crud.get_user_avito_accounts(callback.from_user.id)
    text = "👤 <b>Ваши аккаунты Avito</b>\n\nАккаунт отключен. Выберите другой аккаунт для управления."
    keyboard = get_avito_accounts_menu(accounts)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


# 3. Хендлер для кнопки "Включить"
@router.callback_query(F.data.startswith("avito_acc:enable:"))
async def enable_avito_account(callback: types.CallbackQuery):
    """
    Активирует аккаунт Avito (устанавливает is_active = True).
    """
    account_id = int(callback.data.split(":")[2])

    # Меняем статус в БД
    await crud.toggle_avito_account_active_status(account_id, is_active=True)

    await callback.answer("Аккаунт включен.", show_alert=True)

    # Обновляем меню аккаунтов, чтобы показать новый статус
    accounts = await crud.get_user_avito_accounts(callback.from_user.id)
    text = "👤 <b>Ваши аккаунты Avito</b>\n\nАккаунт снова активен. Выберите аккаунт для управления."
    keyboard = get_avito_accounts_menu(accounts)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("account_actions:chats:") | F.data.startswith("chats:list:"))
async def show_latest_chats(callback: types.CallbackQuery):
    try:
        # Сразу отвечаем на callback, чтобы убрать "часики"
        await callback.answer()
    except TelegramBadRequest:
        # Игнорируем ошибку, если callback уже "протух"
        pass

    parts = callback.data.split(":")
    account_id = int(parts[2])
    offset = int(parts[3]) if len(parts) > 3 else 0
    limit = 5 # Задаем лимит, который мы используем для пагинации

    account = await crud.get_avito_account_by_id(account_id)
    if not account:
        try:
            await callback.message.edit_text("❌ Ошибка: аккаунт не найден.")
        except TelegramBadRequest: pass
        return
    
    # Делаем запрос к API Avito
    api_client = AvitoAPIClient(account)
    chats_data = await api_client.get_chats(limit=limit, offset=offset)
    
    # ---!!! ИСПРАВЛЕНИЕ ЗДЕСЬ: ПЕРЕДАЕМ `limit` КАК ПОСЛЕДНИЙ АРГУМЕНТ !!!---
    keyboard = build_chats_list_keyboard(chats_data.get("chats", []), account.id, offset, limit)
    
    try:
        await callback.message.edit_text(
            f"Последние чаты для аккаунта **{html.escape(account.alias or f'ID {account.avito_user_id}')}**:", 
            reply_markup=keyboard, 
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
             logger.warning(f"Could not edit message to show chats: {e}")


# --- Хендлер для "Статистика" ---
@router.callback_query(F.data.startswith("account_actions:stats:"))
async def show_account_stats(callback: types.CallbackQuery):
    try:
        await callback.answer("Собираю статистику...")
    except TelegramBadRequest:
        pass
        
    account_id = int(callback.data.split(":")[2])
    
    stats = await crud.get_account_stats(account_id)
    account = await crud.get_avito_account_by_id(account_id)
    
    if not account:
        try:
            await callback.message.edit_text("❌ Ошибка: аккаунт не найден.")
        except TelegramBadRequest: pass
        return

    text = (
        f"📊 Статистика для аккаунта <b>{html.escape(account.alias or f'ID {account.avito_user_id}')}</b>\n\n"
        "<b>За сегодня:</b>\n"
        f"  - Входящих: {stats.get('today', {}).get('in', 0)}\n"
        f"  - Исходящих: {stats.get('today', {}).get('out', 0)}\n\n"
        "<b>За эту неделю:</b>\n"
        f"  - Входящих: {stats.get('week', {}).get('in', 0)}\n"
        f"  - Исходящих: {stats.get('week', {}).get('out', 0)}"
    )
    
    keyboard = get_single_account_menu(account)
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
             logger.warning(f"Could not edit message to show stats: {e}")


# --- Хендлер для "Отвязать аккаунт" (шаг 1: подтверждение) ---
@router.callback_query(F.data.startswith("account_actions:unbind_confirm:"))
async def unbind_account_confirm(callback: types.CallbackQuery):
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
        
    account_id = int(callback.data.split(":")[2])
    
    text = (
        "<b>⚠️ Вы уверены?</b>\n\n"
        "Это действие полностью удалит аккаунт и все связанные с ним данные (правила, токены) из системы. "
        "Вы больше не сможете получать или отправлять сообщения через этого бота для данного аккаунта."
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Да, отвязать аккаунт", callback_data=f"account_actions:unbind_execute:{account_id}")
    builder.button(text="⬅️ Нет, вернуться", callback_data=f"avito_acc:select:{account_id}")
    builder.adjust(1)
    
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
             logger.warning(f"Could not edit message for unbind confirmation: {e}")


# --- Хендлер для "Отвязать аккаунт" (шаг 2: выполнение) ---
@router.callback_query(F.data.startswith("account_actions:unbind_execute:"))
async def unbind_account_execute(callback: types.CallbackQuery, state: FSMContext):
    account_id = int(callback.data.split(":")[2])
    
    deleted = await crud.delete_avito_account(account_id)
    
    if deleted:
        try:
            await callback.answer("Аккаунт успешно отвязан.", show_alert=True)
        except TelegramBadRequest:
            # Если не удалось показать alert, покажем обычное сообщение
            await callback.message.answer("Аккаунт успешно отвязан.")
    else:
        try:
            await callback.answer("Ошибка: не удалось найти аккаунт для удаления.", show_alert=True)
        except TelegramBadRequest:
             await callback.message.answer("Ошибка: не удалось найти аккаунт для удаления.")
    
    # Возвращаем пользователя к общему списку аккаунтов, используя нашу навигационную функцию
    await handle_navigation_by_action("accounts_list", callback, state)

@router.callback_query(F.data.startswith("support:faq:"))
async def handle_faq_answer(callback: types.CallbackQuery):
    question_key = callback.data.split(":")[2]
    
    # Находим ответ в нашем конфиге
    answer_text = SUPPORT_FAQ.get(question_key, "Извините, ответ на этот вопрос не найден.")
    
    # Клавиатура для возврата
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к вопросам", callback_data="navigate:support")
    
    await callback.message.edit_text(
        text=answer_text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()

# --- Хендлер для начала диалога с админом (запускает FSM) ---
@router.callback_query(F.data == "support:contact_admin")
async def contact_admin_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ContactAdmin.waiting_for_message)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="navigate:support") # Кнопка отмены ведет обратно в меню поддержки
    
    await callback.message.edit_text(
        "Пожалуйста, опишите ваш вопрос или проблему одним сообщением. Вы можете прикрепить скриншот. Ваше сообщение будет переслано администратору.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# --- Хендлер, который ловит сообщение для админа ---
@router.message(StateFilter(ContactAdmin.waiting_for_message))
async def forward_to_admin(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    admin_id = settings.telegram_admin_id
    if not admin_id:
        await message.answer("К сожалению, связь с администратором сейчас не настроена. Попробуйте позже.")
        # Возвращаемся в главное меню после ошибки
        await handle_start_command(message, state)
        return

    # 1. Извлекаем текст или подпись из сообщения пользователя
    user_text = message.text or message.caption
    if not user_text:
        # Если в сообщении только фото без подписи, или стикер и т.д.
        user_text = "(Сообщение без текста, см. вложение выше)"

    # 2. Формируем единый текст для карточки админа
    admin_card_text = (
        f"👤 <b>От:</b> {html.escape(message.from_user.full_name)}\n"
        f"<b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"<b>Username:</b> @{message.from_user.username or 'не указан'}\n\n"
        f"<b>Текст обращения:</b>\n"
        f"<blockquote>{html.escape(user_text)}</blockquote>"
    )

    # 3. Создаем клавиатуру с кнопкой "Ответить"
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✍️ Ответить пользователю", 
        callback_data=f"admin:reply_to:{message.from_user.id}:{message.message_id}"
    )
    
    try:
        # 4. Отправляем вложения (если они есть) ОТДЕЛЬНО, без текста
        # Это нужно, чтобы пользователь мог их видеть. Мы просто скопируем сообщение.
        # Если в сообщении был только текст, этот блок ничего не сломает.
        if message.content_type != types.ContentType.TEXT:
             await bot.copy_message(
                chat_id=admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

        # 5. Отправляем нашу красивую карточку с текстом и кнопкой
        await bot.send_message(
            chat_id=admin_id,
            text=admin_card_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        
        await message.answer("✅ Ваше сообщение отправлено администратору. Ожидайте ответа.")
    except Exception as e:
        logger.error(f"Could not forward message to admin {admin_id}: {e}")
        await message.answer("❌ Произошла ошибка при отправке сообщения. Пожалуйста, попробуйте еще раз позже.")
        
    # Возвращаем пользователя в главное меню
    await handle_start_command(message, state)

@router.callback_query(F.data.startswith("admin:reply_to:"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    # Проверяем, что кнопку нажал именно админ
    if callback.from_user.id != settings.telegram_admin_id:
        await callback.answer("Это действие доступно только администратору.", show_alert=True)
        return

    # Парсим ID пользователя и ID его сообщения
    _, _, user_id_to_reply, message_id_to_reply = callback.data.split(":")
    
    # Сохраняем в FSM, кому и на что мы отвечаем
    await state.update_data(
        user_id_to_reply=int(user_id_to_reply),
        message_id_to_reply=int(message_id_to_reply)
    )
    await state.set_state(ContactAdmin.waiting_for_admin_reply)
    
    await callback.answer("Введите ваш ответ...", show_alert=True)
    # Можно отправить сообщение "Введите ваш ответ", а не alert
    await callback.message.answer("Пожалуйста, отправьте ваш ответ. Он будет переслан пользователю.")


# ---!!! НОВЫЙ ХЕНДЛЕР: Админ отправляет текст ответа (ловим по состоянию) !!!---
@router.message(StateFilter(ContactAdmin.waiting_for_admin_reply))
async def admin_send_reply(message: types.Message, state: FSMContext, bot: Bot):
    # Получаем из FSM данные, кому и на что мы отвечаем
    data = await state.get_data()
    user_id = data.get('user_id_to_reply')
    original_message_id = data.get('message_id_to_reply')
    
    if not user_id:
        await state.clear()
        await message.reply("Ошибка: не удалось найти, кому отвечать. Сессия истекла.")
        return

    await state.clear()
    
    try:
        # 1. Получаем оригинальное сообщение пользователя, чтобы его процитировать
        original_message = await bot.forward_message(
            chat_id=message.chat.id, # Временная "корзина"
            from_chat_id=user_id,
            message_id=original_message_id,
            disable_notification=True
        )
        original_text = original_message.text or original_message.caption or "(без текста)"
        await original_message.delete() # Удаляем временную копию
        
        # 2. Формируем красивую карточку для пользователя
        reply_card_text = (
            f"<b>Сообщение поддержки</b>\n"
            f"<b>Ответ на ваше обращение:</b>\n"
            f"<blockquote>{html.escape(original_text)}</blockquote>\n\n"
            f"<b>Ответ Администратора:</b>\n"
            f"<blockquote>{html.escape(message.text)}</blockquote>"
        )
        
        # 3. Отправляем карточку пользователю
        await bot.send_message(
            chat_id=user_id,
            text=reply_card_text,
            parse_mode="HTML"
        )
        
        # 4. Сообщаем админу, что все успешно
        await message.reply("✅ Ваш ответ успешно отправлен пользователю.")

    except Exception as e:
        logger.error(f"Failed to send admin reply to user {user_id}: {e}", exc_info=True)
        await message.reply(f"❌ Не удалось отправить ответ. Ошибка: {e}")

# --- ВРЕМЕННЫЙ ЛОГИРУЮЩИЙ ХЕНДЛЕР ---
# @router.callback_query()
# async def log_all_callbacks(callback: types.CallbackQuery):
#     """
#     Этот хендлер ловит АБСОЛЮТНО ВСЕ callback'и.
#     Он должен быть первым, чтобы гарантированно сработать.
#     ВНИМАНИЕ: Он перехватит управление, поэтому другие хендлеры
#     для callback'ов после него не сработают. Используем только для отладки.
#     """
#     logger.critical(f"!!!!!!!!!! CATCH-ALL CALLBACK HANDLER !!!!!!!!!!!")
#     logger.critical(f"Received callback_data: '{callback.data}'")
#     logger.critical(f"From message ID: {callback.message.message_id}")
#     # Можно временно ответить пользователю, чтобы видеть, что он сработал
#     await callback.answer(f"DEBUG: Got '{callback.data}'")

def register_all_handlers(dp: Dispatcher):
    dp.include_router(router)