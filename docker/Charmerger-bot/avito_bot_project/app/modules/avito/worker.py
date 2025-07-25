# /app/modules/avito/worker.py

import asyncio
import logging
from typing import Optional

import json
from db_models import MessageLog
from datetime import datetime, timezone
from ..telegram.view_provider import VIEW_KEY_TPL # <-- Импортируем ключ
from ..telegram.view_renderer import ViewRenderer # <-- Импортируем рендерер
from ..telegram.bot import bot # <-- Импортируем объект бота

import redis.asyncio as redis
from sqlalchemy import select

# Импорты из нашего проекта
from shared.database import get_session
from db_models import AvitoAccount
from .client import AvitoAPIClient
from .messaging import AvitoMessaging
from .actions import AvitoChatActions # <-- Импортируем Actions

logger = logging.getLogger(__name__)

async def process_outgoing_messages(redis_client: redis.Redis):
    """
    Слушает стрим avito:outgoing:messages, отправляет сообщения в Avito,
    ЛОГИРУЕТ ИСХОДЯЩЕЕ СООБЩЕНИЕ, обновляет ChatViewModel и запускает перерисовку.
    """
    stream_name = "avito:outgoing:messages"
    group_name = "avito_workers"
    consumer_name = "outgoing_consumer_1"

    try:
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
        logger.info(f"Consumer group '{group_name}' created for stream '{stream_name}'.")
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group '{group_name}' for stream '{stream_name}' already exists.")
        else:
            raise

    while True:
        try:
            events = await redis_client.xreadgroup(
                group_name, consumer_name, {stream_name: ">"}, count=1, block=5000
            )
            
            if not events:
                continue

            for _, messages in events:
                for message_id, data in messages:
                    logger.info(f"AVITO_WORKER: Processing outgoing Avito message {message_id}")
                    
                    account_id = int(data['account_id'])
                    chat_id = data['chat_id']
                    text_to_send = data['text']
                    
                    async with get_session() as session:
                        account = await session.get(AvitoAccount, account_id)
                        if not (account and account.is_active):
                            logger.warning(f"Account {account_id} not found or inactive. Skipping message.")
                            await redis_client.xack(stream_name, group_name, message_id)
                            continue

                    try:
                        # 1. Отправляем сообщение в Avito
                        api_client = AvitoAPIClient(account)
                        messaging = AvitoMessaging(api_client)
                        await messaging.send_text_message(chat_id, text_to_send)
                        logger.info(f"AVITO_WORKER: Successfully sent message to Avito chat {chat_id}")

                        # ---!!! НОВЫЙ БЛОК: ЛОГИРУЕМ ИСХОДЯЩЕЕ СООБЩЕНИЕ В БД !!!---
                        action_type = data.get("action_type", "manual_reply")
                        is_autoreply = action_type == "auto_reply"
                        trigger_name = None
                        if action_type == "template_reply":
                            trigger_name = data.get("template_name")
                        elif is_autoreply:
                            trigger_name = data.get("rule_name")
                        
                        async with get_session() as log_session:
                            log_entry_db = MessageLog(
                                account_id=account.id,
                                chat_id=chat_id,
                                direction='out',
                                is_autoreply=is_autoreply,
                                trigger_name=trigger_name
                            )
                            log_session.add(log_entry_db)
                        logger.info(f"AVITO_WORKER: Logged outgoing message for chat {chat_id} to DB.")
                        # ---!!! КОНЕЦ НОВОГО БЛОКА !!!---

                        # 2. Обновляем нашу ChatViewModel
                        view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
                        model_json = await redis_client.get(view_key)
                        if model_json:
                            model = json.loads(model_json)
                            
                            log_entry = {
                                "type": action_type,
                                "author_name": data.get("author_name", "Неизвестно"),
                                "text": text_to_send,
                                "timestamp": int(datetime.now(timezone.utc).timestamp())
                            }
                            if log_entry["type"] == "template_reply":
                                log_entry["template_name"] = data.get("template_name", "...")
                            if log_entry["type"] == "auto_reply":
                                log_entry["rule_name"] = data.get("rule_name", "...")

                            action_log = model.setdefault("action_log", [])
                            action_log.insert(0, log_entry)
                            model["action_log"] = action_log[:5]

                            # ---!!! ВОЗВРАЩАЕМ ПРАВИЛЬНУЮ ЛОГИКУ ОБНОВЛЕНИЯ !!!---
                            # Наш ответ становится последним событием в чате
                            # model["last_message_text"] = text_to_send
                            # model["last_message_direction"] = "out"
                            # model["last_message_timestamp"] = log_entry["timestamp"]
                            # Так как мы ответили, последнее сообщение клиента считается прочитанным
                            model["is_last_message_read"] = True
                            # ---!!! КОНЕЦ ИЗМЕНЕНИЯ !!!---
                            
                            await redis_client.set(view_key, json.dumps(model), keepttl=True)
                            
                            # 3. Триггерим перерисовку у всех
                            renderer = ViewRenderer(bot, redis_client)
                            await renderer.update_all_subscribers(view_key, model)

                    except Exception as e:
                        logger.error(f"AVITO_WORKER: Failed to send message for account {account_id}: {e}", exc_info=True)
                    
                    await redis_client.xack(stream_name, group_name, message_id)

        except Exception as e:
            logger.error(f"Critical error in 'process_outgoing_messages' worker: {e}", exc_info=True)
            await asyncio.sleep(5)


async def process_chat_actions(redis_client: redis.Redis):
    """
    Слушает очередь 'avito:chat:actions' и выполняет действия 
    (прочитано, печатаю, стоп печатаю).
    """
    stream_name = "avito:chat:actions"
    group_name = "avito_action_workers"
    consumer_name = "action_consumer_1"

    renderer = ViewRenderer(bot, redis_client)

    try:
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e): raise

    while True:
        try:
            events = await redis_client.xreadgroup(
                group_name, consumer_name, {stream_name: ">"}, count=1, block=5000
            )
            if not events: continue

            for _, messages in events:
                for message_id, data in messages:
                    logger.info(f"AVITO_ACTIONS_WORKER: Processing action {message_id} with data: {data}")
                    
                    account_id = int(data['account_id'])
                    chat_id = data['chat_id']
                    action_type = data['action']
                    
                    async with get_session() as session:
                        account = await session.get(AvitoAccount, account_id)
                        if not (account and account.is_active):
                            logger.warning(f"Account {account_id} not found/inactive for chat action.")
                            await redis_client.xack(stream_name, group_name, message_id)
                            continue
                    
                    try:
                        api_client = AvitoAPIClient(account)
                        actions = AvitoChatActions(api_client) # AvitoChatActions еще не существует, создадим его
                        
                        if action_type == "mark_read":
                            # 1. Выполняем действие с API Avito
                            await actions.mark_as_read(chat_id)
                            
                            # 2. Обновляем ChatViewModel
                            view_key = VIEW_KEY_TPL.format(account_id=account.id, chat_id=chat_id)
                            model_json = await redis_client.get(view_key)

                            if not model_json:
                                # Если модели еще нет, создаем ее.
                                # Это защищает от состояния гонки.
                                # При навигации (не новое сообщение) is_new_message=False.
                                logger.warning(f"ACTIONS_WORKER: No view model for {view_key}. Rehydrating.")
                                model = await rehydrate_view_model(redis_client, account, chat_id)
                                if not model:
                                    logger.error(f"ACTIONS_WORKER: Failed to rehydrate model for {view_key}.")
                                    # Выходим, если не удалось создать модель
                                    await redis_client.xack(stream_name, group_name, message_id)
                                    continue
                            else:
                                model = json.loads(model_json)
                            
                            # 3. Взводим флаг
                            model["is_last_message_read"] = True
                            await redis_client.set(view_key, json.dumps(model), keepttl=True)
                            
                            # 4. Запускаем перерисовку у всех подписчиков
                            logger.info(f"ACTIONS_WORKER: Triggering rerender for {view_key} after mark_read.")
                            await renderer.update_all_subscribers(view_key, model)
                        # ---!!! КОНЕЦ БЛОКА !!!---
                            
                        else:
                            logger.warning(f"AVITO_ACTIONS_WORKER: Received unknown action type '{action_type}'")

                    except Exception as e:
                        logger.error(f"AVITO_ACTIONS_WORKER: Failed to perform action {action_type}: {e}", exc_info=True)

                    await redis_client.xack(stream_name, group_name, message_id)

        except Exception as e:
            logger.error(f"Critical error in 'process_chat_actions' worker: {e}", exc_info=True)
            await asyncio.sleep(5)

# --- Главная функция-запускатор для всех воркеров Avito ---
async def start_avito_outgoing_worker(redis_client: redis.Redis):
    """
    Запускает все асинхронные задачи, связанные с исходящими действиями Avito.
    """
    logger.info("Starting Avito workers (messages and actions)...")
    
    # Запускаем оба воркера параллельно
    await asyncio.gather(
        process_outgoing_messages(redis_client),
        process_chat_actions(redis_client)
    )