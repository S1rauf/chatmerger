import logging
import asyncio
import redis.asyncio as redis
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from shared.database import get_session
from db_models import User, AvitoAccount, ForwardingRule


logger = logging.getLogger(__name__)

async def avito_to_telegram_forwarder(redis_client: redis.Redis):
    """
    Слушает 'avito:processed:messages', проверяет, принял ли владелец
    пользовательское соглашение, находит всех получателей (владельца и помощников)
    и передает сообщение в очередь для обработки событий для каждого из них.
    """
    logger.info("Avito-to-Telegram Forwarder (v7, with terms agreement check) started.")
    
    stream_name = "avito:processed:messages"
    group_name = "forwarder_group"
    consumer_name = "forwarder_1"

    try:
        # mkstream=True создаст стрим, если его еще нет
        await redis_client.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group '{group_name}' for stream '{stream_name}' already exists.")
        else:
            # Пробрасываем другие, неожиданные ошибки Redis
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
                    logger.info(f"FORWARDER: Processing message {message_id} from '{stream_name}'")
                    
                    # ID пользователя в системе Avito
                    avito_user_id = int(data['account_id'])
                    
                    async with get_session() as session:
                        stmt = (
                            select(User)
                            .join(User.avito_accounts)
                            .where(AvitoAccount.avito_user_id == avito_user_id)
                            # "Жадно" загружаем все связанные данные, которые нам понадобятся
                            .options(
                                selectinload(User.avito_accounts),
                                selectinload(User.owned_forwarding_rules)
                            )
                        )
                        result = await session.execute(stmt)
                        owner = result.scalar_one_or_none()

                    if not owner:
                        logger.warning(f"FORWARDER: No user (owner) found for Avito user ID {avito_user_id}.")
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue
                    
                    # --- УПРОЩЕННАЯ ЛОГИКА ПРОВЕРКИ СОГЛАШЕНИЯ ---
                    # Просто проверяем флаг. Если он False, молча игнорируем сообщение.
                    # Пользователь получит предложение принять соглашение при подключении аккаунта.
                    if not owner.has_agreed_to_terms:
                        logger.warning(f"FORWARDER: Dropping message for user {owner.telegram_id} because they have not agreed to terms.")
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue # Переходим к следующему сообщению в очереди
                    # --- КОНЕЦ ЛОГИКИ ПРОВЕРКИ ---

                    # Находим конкретный Avito-аккаунт, с которого пришло сообщение
                    source_account = next((acc for acc in owner.avito_accounts if acc.avito_user_id == avito_user_id), None)
                    if not source_account:
                        logger.error(f"FORWARDER: Inconsistency! Owner found but source account {avito_user_id} not in their list.")
                        await redis_client.xack(stream_name, group_name, message_id)
                        continue

                    # --- Формируем список всех, кто должен получить это сообщение ---
                    recipients = []
                    
                    # 1. Добавляем владельца
                    recipients.append({
                        "telegram_id": owner.telegram_id,
                        "can_reply": True
                    })
                    
                    # 2. Добавляем помощников
                    for rule in owner.owned_forwarding_rules:
                        # Правило должно быть принято (есть target_telegram_id)
                        if rule.target_telegram_id:
                            permissions = rule.permissions or {}
                            allowed_accounts = permissions.get("allowed_accounts")
                            
                            # Проверяем, есть ли у помощника доступ к этому аккаунту
                            # (None означает доступ ко всем)
                            if allowed_accounts is None or source_account.id in allowed_accounts:
                                recipients.append({
                                    "telegram_id": rule.target_telegram_id,
                                    "can_reply": permissions.get("can_reply", False)
                                })
                    
                    # Убираем дубликаты, если вдруг владелец добавил сам себя в помощники
                    unique_recipients = {r['telegram_id']: r for r in recipients}.values()
                    
                    # --- Отправляем обогащенное сообщение в очередь для каждого получателя ---
                    original_avito_user_id = data.pop('account_id', None)
                    
                    for recipient in unique_recipients:
                        enriched_data = {
                            "user_telegram_id": str(recipient['telegram_id']),
                            "db_account_id": str(source_account.id),
                            "can_reply": str(recipient['can_reply']).lower(),
                            "avito_user_id": str(original_avito_user_id),
                            **data
                        }
                        await redis_client.xadd("events:new_avito_message", enriched_data)
                        logger.info(f"FORWARDER: Forwarded message to TG ID {recipient['telegram_id']} with can_reply={recipient['can_reply']}")

                    # Подтверждаем, что исходное сообщение из стрима обработано
                    await redis_client.xack(stream_name, group_name, message_id)

        except Exception as e:
            logger.error(f"Critical error in 'avito_to_telegram_forwarder': {e}", exc_info=True)
            # В случае критической ошибки ждем перед следующей попыткой
            await asyncio.sleep(5)