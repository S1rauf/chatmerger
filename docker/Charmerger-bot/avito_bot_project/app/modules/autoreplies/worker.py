import logging
import asyncio
import redis.asyncio as redis
from sqlalchemy import select

from .engine import AutoReplyEngine
from db_models import AvitoAccount
from shared.database import get_session

logger = logging.getLogger(__name__)

async def send_delayed_reply(
    redis_client: redis.Redis, 
    delay: int, 
    queue: str, 
    message: dict
):
    """Ждет, и отправляет сообщение."""
    
    logger.info(f"AUTOREPLY_DELAY: Waiting for {delay} seconds to send message.")
    await asyncio.sleep(delay)
    await redis_client.xadd(queue, message)
    
# --- ИЗМЕНЕННАЯ ВЕРСИЯ ВАШЕЙ ФУНКЦИИ ---
async def start_autoreply_worker(redis_client: redis.Redis):
    """
    Слушает 'avito:incoming:messages', и если правило сработало,
    отправляет ОБОГАЩЕННЫЙ автоответ в 'avito:outgoing:messages' (с задержкой или без).
    Также обогащает исходное сообщение перед отправкой в 'avito:processed:messages'.
    """
    logger.info("Autoreply Worker (v5, based on user code with delay) started.")
    
    incoming_stream = "avito:incoming:messages"
    outgoing_stream = "avito:processed:messages"
    autoreply_queue = "avito:outgoing:messages"
    
    group_name = "autoreply_workers"
    consumer_name = "autoreplier_1"
    
    engine = AutoReplyEngine(redis_client=redis_client)

    try:
        await redis_client.xgroup_create(incoming_stream, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e): raise

    while True:
        try:
            events = await redis_client.xreadgroup(
                group_name, consumer_name, {incoming_stream: ">"}, count=1, block=5000
            )
            
            if not events: continue

            for _, messages in events:
                for message_id, data in messages:
                    logger.info(f"ВОРКЕР_АВТООТВЕТОВ: --- НАЧАЛО ОБРАБОТКИ СООБЩЕНИЯ {message_id} ---")
                    
                    avito_user_id = int(data['account_id'])
                    chat_id = data['chat_id']
                    message_text = data.get('text', '')
                    logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Текст сообщения: '{message_text}' для Avito User ID: {avito_user_id}")
                    
                    async with get_session() as session:
                        account = await session.scalar(
                            select(AvitoAccount).where(AvitoAccount.avito_user_id == avito_user_id)
                        )
                    
                    if not account:
                        logger.warning(f"ВОРКЕР_АВТООТВЕТОВ: Аккаунт с Avito ID {avito_user_id} НЕ НАЙДЕН в БД. Пропускаю.")
                    else:
                        logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Найден внутренний ID аккаунта: {account.id}. Ищу правила...")
                        reply_info = await engine.find_and_apply_rule(
                            account_id=account.id,
                            chat_id=chat_id,
                            message_text=message_text
                        )
                        
                        if reply_info:
                            logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Правило найдено! Информация для ответа: {reply_info}")                            
                            delay = reply_info.get('delay_seconds', 5) 

                            # 1. Формируем ОБОГАЩЕННОЕ сообщение для отправки в Avito
                            outgoing_message = {
                                "account_id": str(account.id),
                                "chat_id": chat_id,
                                "text": reply_info['text'],
                                "action_type": "auto_reply",
                                "rule_name": reply_info['rule_name'],
                                "author_name": "Автоответчик"
                            }
                            
                            # 2. Обогащаем ИСХОДНОЕ сообщение `data` для передачи дальше в Telegram
                            data['autoreply_sent'] = 'true'
                            data['autoreply_rule_name'] = reply_info['rule_name']
                            data['autoreply_text'] = reply_info['text'] # Это поле нужно для telegram/worker.py

                            # 3. Реализуем задержку
                            delay = reply_info.get('delay_seconds', 0)
                            if delay > 0:
                                asyncio.create_task(
                                    send_delayed_reply(redis_client, delay, autoreply_queue, outgoing_message)
                                )
                                logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Автоответ для чата {chat_id} поставлен в очередь с задержкой ({delay} сек).")
                            else:
                                await redis_client.xadd(autoreply_queue, outgoing_message)
                                logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Мгновенный автоответ для чата {chat_id} поставлен в очередь по правилу '{reply_info['rule_name']}'")
                        else:
                            logger.info("ВОРКЕР_АВТООТВЕТОВ: Подходящих правил не найдено или все на перезарядке.")

                    # 4. Отправляем (возможно, обогащенное) сообщение `data` дальше
                    await redis_client.xadd(outgoing_stream, data)
                    logger.info(f"ВОРКЕР_АВТООТВЕТОВ: Переслал сообщение {message_id} в поток '{outgoing_stream}'")
                    
                    await redis_client.xack(incoming_stream, group_name, message_id)
                    logger.info(f"ВОРКЕР_АВТООТВЕТОВ: --- ЗАВЕРШЕНИЕ ОБРАБОТКИ СООБЩЕНИЯ {message_id} ---")

        except Exception as e:
            logger.error(f"Ошибка в 'start_autoreply_worker': {e}", exc_info=True)
            await asyncio.sleep(5)