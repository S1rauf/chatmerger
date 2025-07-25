# /app/modules/autoreplies/engine.py
import logging
import time
from typing import List, Dict, Optional, Any
from sqlalchemy import select
import redis.asyncio as redis
from shared.database import get_session
from db_models import AutoReplyRule

logger = logging.getLogger(__name__)

class AutoReplyEngine:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _match_rule(self, rule: AutoReplyRule, message_text: str) -> bool:
        trigger_type = rule.trigger_type
        keywords = [kw.lower() for kw in (rule.trigger_keywords or [])]
        text_lower = message_text.lower()
        
        if trigger_type == "always":
            print("Результат: Сработало (всегда)")
            return True
        
        if not keywords:
            print("Результат: НЕ сработало (нет ключевых слов)")
            return False

        if trigger_type == "exact" and text_lower == keywords[0]:
            print("Результат: Сработало (точное совпадение)")
            return True
            
        if trigger_type == "contains_any" and any(kw in text_lower for kw in keywords):
            print("Результат: Сработало (содержит любое из)")
            return True
            
        if trigger_type == "contains_all" and all(kw in text_lower for kw in keywords):
            print("Результат: Сработало (содержит все)")
            return True
            
        print("Результат: НЕ сработало (ни одно условие не выполнено)")
        return False

    async def find_and_apply_rule(
        self, account_id: int, chat_id: str, message_text: str
    ) -> Optional[Dict[str, Any]]:
        
        async with get_session() as session:
            stmt = (
                select(AutoReplyRule)
                .where(AutoReplyRule.account_id == account_id, AutoReplyRule.is_active == True)
            )
            result = await session.execute(stmt)
            rules = result.scalars().all()

        if not rules:
            logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Не найдено активных правил для аккаунта: {account_id}")
            return None
        
        logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Найдено {len(rules)} активных правил для аккаунта {account_id}. Начинаю проверку...")

        for rule in rules:
            logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Проверяю правило '{rule.name}' (ID: {rule.id}, Тип: {rule.trigger_type}, Ключевые слова: {rule.trigger_keywords})")
            
            if self._match_rule(rule, message_text):
                logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Правило '{rule.name}' СОВПАЛО с текстом.")
                cooldown_key = f"autoreply:cooldown:{chat_id}:{rule.id}"
                
                is_on_cooldown = await self.redis.exists(cooldown_key)
                if is_on_cooldown:
                    logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Правило '{rule.name}' НА ПЕРЕЗАРЯДКЕ (cooldown). Пропускаю.")
                    continue

                logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Правило '{rule.name}' прошло проверку перезарядки. ПРИМЕНЯЮ ПРАВИЛО.")
                if rule.cooldown_seconds > 0:
                    await self.redis.set(cooldown_key, "1", ex=rule.cooldown_seconds)
                
                return { "text": rule.reply_text, "delay_seconds": rule.delay_seconds, "rule_name": rule.name }
            else:
                logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Правило '{rule.name}' НЕ совпало с текстом.")
        
        logger.info(f"ДВИЖОК_АВТООТВЕТОВ: Завершена проверка всех правил для аккаунта {account_id}. Подходящих правил не найдено.")
        return None