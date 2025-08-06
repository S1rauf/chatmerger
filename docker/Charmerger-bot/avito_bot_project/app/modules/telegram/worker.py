# /app/modules/telegram/worker.py

import logging
import asyncio
import json
import httpx
import redis.asyncio as redis
from aiogram import Bot
from db_models import MessageLog # <-- Добавляем импорт MessageLog
from datetime import datetime, timezone # <-- Добавляем импорты времени
from shared.config import REPLY_MAPPING_TTL 
from ..avito.client import AvitoAPIClient
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.enums import ChatAction
from shared.database import get_session
# Убедитесь, что все эти импорты присутствуют в начале файла
from .view_renderer import ViewRenderer
from .view_models import ChatViewModel
from .view_provider import rehydrate_view_model, subscribe_user_to_view, VIEW_KEY_TPL 
from modules.database.crud import get_avito_account_by_id, get_or_create_user

from aiogram.types import InlineKeyboardMarkup, FSInputFile, InputFile, BufferedInputFile 
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# ===================================================================
# === ВОРКЕР 1: Отправка простых сообщений ==========================
# ===================================================================

async def start_telegram_sender_worker(redis_client: redis.Redis, bot: Bot):
    """
    Слушает очередь 'telegram:outgoing:messages' для отправки сообщений
    и документов пользователям.
    """
    logger.info("Telegram Sender Worker (v3, correct xreadgroup call) started.")
    stream_name = "telegram:outgoing:messages"
    group_name = "telegram_senders"
    consumer_name = "sender_1"
    max_retries = 3
    
    try:
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e): 
            raise
    
    while True:
        try:
            # ---!!! ВОТ ИСПРАВЛЕНИЕ: ВОССТАНАВЛИВАЕМ ПОЛНЫЙ ВЫЗОВ !!!---
            events = await redis_client.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_name: ">"},
                count=1,
                block=5000
            )
            if not events: continue

            for _, messages in events:
                for message_id, data in messages:
                    # Логируем, что мы получили
                    logger.info(f"SENDER_WORKER: Processing message {message_id} with data: {data}")
                    
                    retries = int(data.get("retries", 0))
                    try:
                        user_id = int(data['user_id'])
                        message_type = data.get("type", "text")

                        # --- Общая логика для клавиатуры ---
                        keyboard = None
                        reply_markup_json = data.get('reply_markup')
                        if reply_markup_json:
                            try:
                                keyboard = InlineKeyboardMarkup.model_validate_json(reply_markup_json)
                            except Exception as e:
                                logger.error(f"SENDER_WORKER: Failed to parse reply_markup JSON: {e}")
                        
                        # --- Общая логика для parse_mode ---
                        # По умолчанию используем HTML, если он указан в данных
                        parse_mode = data.get("parse_mode")
                        if parse_mode and parse_mode.lower() == 'html':
                            parse_mode = ParseMode.HTML
                        elif parse_mode and parse_mode.lower() == 'markdown':
                            parse_mode = ParseMode.MARKDOWN_V2
                        else:
                            parse_mode = None # Без форматирования

                        if message_type == "document":
                            file_path = data.get("file_path")
                            caption = data.get("caption")
                            if file_path:
                                document = FSInputFile(file_path)
                                await bot.send_document(
                                    chat_id=user_id,
                                    document=document,
                                    caption=caption,
                                    reply_markup=keyboard,
                                    parse_mode=parse_mode
                                )
                            else:
                                logger.warning(...)
                        
                        else: # message_type == "text"
                            text = data.get('text', '(пустое сообщение)')
                            await bot.send_message(
                                chat_id=user_id, 
                                text=text, 
                                reply_markup=keyboard,
                                parse_mode=parse_mode
                            )
                        
                        logger.info(f"SENDER_WORKER: Successfully sent message {message_id} to user {user_id}.")
                        await redis_client.xack(stream_name, group_name, message_id)

                    except (TelegramBadRequest, TelegramRetryAfter, Exception) as e:
                        # Если Telegram просит подождать
                        logger.warning(f"SENDER_WORKER: Telegram RetryAfter: sleep for {e.retry_after}s. Re-queueing message {message_id}.")
                        await asyncio.sleep(e.retry_after)
                        # Возвращаем сообщение в очередь для повторной попытки
                        await redis_client.xadd(stream_name, {"retries": retries + 1, **data})
                        await redis_client.xack(stream_name, group_name, message_id) # Подтверждаем старое

                    except Exception as e:
                        # Ловим все остальные ошибки (например, пользователь заблокировал бота)
                        logger.error(f"SENDER_WORKER: Failed to process message {message_id}. Retries: {retries}. Error: {e}")
                        
                        if retries >= max_retries:
                            # Если превышен лимит попыток, отправляем в "мертвую" очередь
                            logger.error(f"SENDER_WORKER: Max retries exceeded for message {message_id}. Moving to DLQ.")
                            await redis_client.xadd("telegram:outgoing:dlq", {"error": str(e), **data})
                        else:
                            # Иначе, возвращаем в очередь для повторной попытки
                            await redis_client.xadd(stream_name, {"retries": retries + 1, **data})
                        
                        await redis_client.xack(stream_name, group_name, message_id) # Подтверждаем старое

        except Exception as e:
            logger.error(f"SENDER_WORKER: Critical error in main loop: {e}", exc_info=True)
            await asyncio.sleep(5)


# ===================================================================
# === ВОРКЕР 2: Обработчик внутренних событий =======================
# ===================================================================
async def start_event_processor_worker(redis_client: redis.Redis, bot: Bot):
    """
    Слушает очередь 'events:new_avito_message', обрабатывает вложения,
    отправляет их отдельным сообщением, а затем отправляет карточку чата.
    """
    logger.info("Event Processor Worker (v17, stable logic) started.")
    stream_name = "events:new_avito_message"
    group_name = "event_processors"
    consumer_name = "processor_1"
    
    renderer = ViewRenderer(bot, redis_client)

    try:
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e): raise

    while True:
        try:
            events = await redis_client.xreadgroup(group_name, consumer_name, {stream_name: ">"}, count=1, block=5000)
            if not events:
                continue

            for _, messages in events:
                for message_id, data in messages:
                    try:
                        chat_id = data['chat_id']
                        account_id = int(data['db_account_id'])
                        user_telegram_id = int(data['user_telegram_id'])
                        can_reply_flag = data.get('can_reply', 'false')
                    except (KeyError, ValueError) as e:
                        logger.error(f"EVENT_PROCESSOR: Invalid data in message {message_id}: {data}. Error: {e}")
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue

                    user = await get_or_create_user(telegram_id=user_telegram_id, username=None)
                    account = await get_avito_account_by_id(account_id)
                    if not (user and account):
                        logger.warning(f"Could not find user or account for event data: {data}")
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue

                    async with get_session() as session:
                        log_entry = MessageLog(
                            account_id=account.id,
                            chat_id=chat_id,
                            direction='in',
                            is_autoreply=data.get('autoreply_sent') == 'true',
                            trigger_name=data.get('autoreply_rule_name'),
                            timestamp=datetime.fromtimestamp(int(data.get('created_ts', 0)), tz=timezone.utc)
                        )
                        session.add(log_entry)
                    logger.info(f"EVENT_PROCESSOR: Logged incoming message for chat {chat_id} to DB.")

                    # 1. Загружаем "фоновую" информацию о чате (имена, заметки и т.д.)
                    model = await rehydrate_view_model(redis_client, account, chat_id)
                    if not model:
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue

                    # 2. Отправляем вложение, если оно есть
                    attachment_message_id = None
                    attachment_type = None
                    interlocutor_name = model.get('interlocutor_name', 'клиент')
                    try:
                        image_url = data.get('image_url')
                        voice_id = data.get('voice_id')
                        video_preview_url = data.get('video_preview_url')
                        location_lat = data.get('location_lat')
                        location_lon = data.get('location_lon')

                        if image_url:
                            attachment_type = "фото"
                            sent_attachment = await bot.send_photo(
                                chat_id=user_telegram_id, photo=image_url,
                                caption=f"Вложение (фото) от: {interlocutor_name}"
                            )
                            attachment_message_id = sent_attachment.message_id
                        elif voice_id:
                            attachment_type = "голосовое сообщение"
                            api_client = AvitoAPIClient(account)
                            voice_data = await api_client.get_voice_files([voice_id])
                            voice_url = voice_data.get('voices_urls', {}).get(voice_id)
                            if voice_url:
                                async with httpx.AsyncClient() as client:
                                    r = await client.get(voice_url)
                                    r.raise_for_status()
                                    sent_attachment = await bot.send_voice(
                                        chat_id=user_telegram_id,
                                        voice=BufferedInputFile(r.content, filename="voice.mp4"),
                                        caption=f"Вложение (голос) от: {interlocutor_name}"
                                    )
                                    attachment_message_id = sent_attachment.message_id
                        elif video_preview_url:
                            attachment_type = "видео"
                            sent_attachment = await bot.send_photo(
                                chat_id=user_telegram_id, 
                                photo=video_preview_url,
                                caption=f"Вложение (видео-превью) от: {interlocutor_name}\n(Просмотр доступен в Avito)"
                            )
                            attachment_message_id = sent_attachment.message_id
                        elif location_lat and location_lon:
                            attachment_type = "геопозиция"
                            sent_attachment = await bot.send_location(
                                chat_id=user_telegram_id,
                                latitude=float(location_lat),
                                longitude=float(location_lon)
                            )
                            attachment_message_id = sent_attachment.message_id
                    except Exception as e:
                        logger.error(f"EVENT_PROCESSOR: Failed to send attachment to {user_telegram_id}: {e}", exc_info=True)

                    model['action_log'] = []
                    
                    # 3. УСТАНАВЛИВАЕМ в модель информацию о КОНКРЕТНОМ последнем сообщении
                    if attachment_message_id and attachment_type:
                        model['last_client_message_attachment'] = {"message_id": attachment_message_id, "type": attachment_type}
                        model['last_client_message_text'] = data.get('text') or f"[{attachment_type.capitalize()}]"
                    else:
                        model['last_client_message_text'] = data.get('text', '[Нет текста]')
                        model.pop('last_client_message_attachment', None)
                    
                    model['last_client_message_timestamp'] = int(data.get('created_ts', 0))
                    
                    was_autoreplied = data.get('autoreply_sent') == 'true'
                    if was_autoreplied:
                        model['is_last_message_read'] = True
                        log_entry = {
                            "type": "auto_reply", "author_name": "Автоответчик",
                            "text": data.get('autoreply_text', '...'),
                            "rule_name": data.get('autoreply_rule_name', '...'),
                            "timestamp": int(datetime.now(timezone.utc).timestamp())
                        }
                        if 'action_log' not in model: model['action_log'] = []
                        model['action_log'].insert(0, log_entry)
                    else:
                        model['is_last_message_read'] = False

                    # 4. СОХРАНЯЕМ финальную модель и отправляем карточку
                    view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
                    await redis_client.set(view_key, json.dumps(model), keepttl=True)
                    sent_card_message = await renderer.render_new_card(model, user)

                    # 5. Подписываем на обновления
                    if sent_card_message:
                        await subscribe_user_to_view(
                            redis_client, view_key, user.telegram_id, sent_card_message.message_id
                        )
                        context_key = f"tg_context:{sent_card_message.message_id}"
                        context_value = json.dumps({
                            "avito_chat_id": model['chat_id'],
                            "avito_account_id": model['account_id'],
                            "can_reply": can_reply_flag
                        })
                        await redis_client.set(context_key, context_value, ex=REPLY_MAPPING_TTL)
                        logger.info(f"EVENT_PROCESSOR: Saved reply context for card msg {sent_card_message.message_id}")

                    await redis_client.xack(stream_name, group_name, message_id)

        except Exception as e:
            logger.error(f"Critical error in 'start_event_processor_worker': {e}", exc_info=True)
            await asyncio.sleep(5)

# ===================================================================
# === ВОРКЕР 3: Рендеринг карточек чатов =============================
# ===================================================================

async def start_chat_action_worker(redis_client: redis.Redis, bot: Bot):
    """
    Слушает очередь 'telegram:chat_actions' и отправляет статусы
    (например, 'печатает...') в чат Telegram.
    """
    logger.info("Chat Action Worker started.")
    stream_name = "telegram:chat_actions"
    group_name = "chat_action_workers"
    consumer_name = "action_sender_1"

    try:
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e): raise
    
    while True:
        try:
            events = await redis_client.xreadgroup(group_name, consumer_name, {stream_name: ">"}, count=1, block=5000)
            if not events: continue

            for _, messages in events:
                for message_id, data in messages:
                    try:
                        # Получаем ID чата и действие
                        chat_id = int(data['chat_id'])
                        action = data.get('action', 'typing') # По умолчанию - 'typing'
                        
                        await bot.send_chat_action(chat_id=chat_id, action=action)
                        
                        logger.info(f"Sent chat action '{action}' to chat {chat_id}")
                    except Exception as e:
                        logger.error(f"Failed to send chat action: {e}", exc_info=False)
                    finally:
                        await redis_client.xack(stream_name, group_name, message_id)
        except Exception as e:
            logger.error(f"Critical error in 'start_chat_action_worker': {e}", exc_info=True)
            await asyncio.sleep(5)