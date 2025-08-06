import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import redis.asyncio as redis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db_models import User, AvitoAccount, Template, AutoReplyRule, ForwardingRule
from shared.database import get_session
from .config import TARIFF_CONFIG
from .enums import TariffPlan
from ..database import crud
from ..wallet.service import wallet_service

from .exceptions import InsufficientFundsError, BillingError, TariffLimitReachedError

logger = logging.getLogger(__name__)


class BillingService:
    
    @staticmethod
    def get_user_tariff_plan(user: User) -> TariffPlan:
        """
        Определяет текущий тарифный план пользователя.
        Если подписка истекла, возвращает базовый тариф.
        """
        if user.tariff_expires_at and user.tariff_plan != TariffPlan.START:
            if user.tariff_expires_at > datetime.now(timezone.utc):
                return user.tariff_plan
        
        return user.tariff_plan or TariffPlan.START

    @staticmethod
    def get_tariff_limits(tariff_plan: TariffPlan) -> Dict[str, Any]:
        """Возвращает словарь с лимитами для указанного тарифного плана."""
        return TARIFF_CONFIG.get(tariff_plan, {}).get("limits", {})

    @classmethod
    def can_add_avito_account(cls, user: User) -> bool:
        """Проверяет, может ли пользователь добавить еще один аккаунт Avito."""
        current_count = len(user.avito_accounts)
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("avito_accounts", 0)
        return current_count < limit

    async def schedule_downgrade(self, user: User, new_plan: TariffPlan):
        """Обрабатывает логику даунгрейда."""
        async with get_session() as session:
            await crud.schedule_user_downgrade(session, user.id, new_plan)
        logger.info(f"User {user.id} scheduled a downgrade to {new_plan.value}")
        # TODO: Здесь можно запустить FSM-опрос для выбора урезаемого функционала
    # ==========================================================
    # === purchase_tariff
    # ==========================================================
    @classmethod
    async def purchase_tariff(
        cls, user: User, new_plan: TariffPlan, redis_client: redis.Redis
    ):
        """Обрабатывает покупку/смену тарифа (апгрейд)."""
        current_plan_enum = cls.get_user_tariff_plan(user)
        current_config = TARIFF_CONFIG[current_plan_enum]
        new_config = TARIFF_CONFIG[new_plan]

        # --- Логика Апгрейд vs Даунгрейд ---
        if new_config['price_rub'] < current_config['price_rub']:
            # Если новый тариф дешевле - это даунгрейд
            await cls.schedule_downgrade(cls, user, new_plan)
            # Возвращаем флаг, что это даунгрейд, для хендлера
            return {"status": "downgrade_scheduled"}

        # --- Логика Апгрейда ---
        price = new_config['price_rub']
        if price > 0:
            current_balance = await wallet_service.get_balance(user.id, redis_client)
            if current_balance < price:
                raise InsufficientFundsError(f"Недостаточно средств. Требуется: {price} ₽, на балансе: {current_balance:.2f} ₽.")
            
            await wallet_service.withdraw(
                user_id=user.id, amount=price,
                description=f"Оплата тарифа «{new_config['name_readable']}»",
                redis_client=redis_client
            )
        
        duration_days = new_config.get('duration_days')
        new_expiration_date: Optional[datetime] = None
        if duration_days:
            start_date = datetime.now(timezone.utc)
            if user.tariff_expires_at and user.tariff_expires_at > start_date:
                start_date = user.tariff_expires_at
            new_expiration_date = start_date + timedelta(days=duration_days)

        async with get_session() as session:
            await crud.update_user_tariff(
                session, user.id, new_plan, new_expiration_date
            )
        
        logger.info(f"User {user.id} successfully upgraded to tariff {new_plan.value}.")
        return {"status": "upgraded"}

    @classmethod
    async def check_template_limit(cls, user: User, session: AsyncSession):
        """Проверяет, может ли пользователь создать еще один шаблон."""
        current_count = await session.scalar(
            select(func.count(Template.id)).where(Template.user_id == user.id)
        )
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("templates", 0)
        
        if current_count >= limit:
            raise TariffLimitReachedError(f"Достигнут лимит шаблонов ({limit} шт.) для вашего тарифа. Перейдите на более высокий тариф для увеличения лимита.")

    # --- МЕТОД: ПРОВЕРКА ЛИМИТА ПРАВИЛ АВТООТВЕТА ---
    @classmethod
    async def check_autoreply_rules_limit(cls, user: User, session: AsyncSession):
        """Проверяет, может ли пользователь создать еще одно правило автоответа."""
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("auto_reply_rules", 0)
        
        result = await session.execute(
            select(func.count(AutoReplyRule.id))
            .join(AutoReplyRule.account)
            .where(AvitoAccount.user_id == user.id)
        )
        current_count = result.scalar_one()
        
        if current_count >= limit:
            raise TariffLimitReachedError(f"Достигнут лимит правил автоответов ({limit} шт.) для вашего тарифа.")
            
    # --- МЕТОД: ПРОВЕРКА ЛИМИТА ПРАВИЛ ПЕРЕСЫЛКИ ---
    @classmethod
    async def check_forwarding_rules_limit(cls, user: User, session: AsyncSession):
        """Проверяет, может ли пользователь создать еще одно правило пересылки (помощника)."""
        current_count = await session.scalar(
            select(func.count(ForwardingRule.id)).where(ForwardingRule.owner_id == user.id)
        )
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("forwarding_rules", 0)
        
        if current_count >= limit:
            raise TariffLimitReachedError(f"Достигнут лимит помощников ({limit} шт.) для вашего тарифа.")
    
    @classmethod
    async def check_avito_account_limit(cls, user: User, session: AsyncSession):
        """Проверяет, может ли пользователь добавить еще один аккаунт Avito."""
        current_count = await session.scalar(
            select(func.count(AvitoAccount.id)).where(AvitoAccount.user_id == user.id)
        )
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("avito_accounts", 0)
        
        if current_count >= limit:
            raise TariffLimitReachedError(f"Достигнут лимит аккаунтов Avito ({limit} шт.) для вашего тарифа.")

    @classmethod
    async def check_and_increment_daily_messages(
        cls, user: User, redis_client: redis.Redis
    ):
        """
        Проверяет и увеличивает счетчик отправленных вручную сообщений за день.
        Использует Redis для хранения счетчика.
        """
        tariff_plan = cls.get_user_tariff_plan(user)
        limit = cls.get_tariff_limits(tariff_plan).get("daily_outgoing_messages_tg_to_avito", 0)
        
        # Бесконечный лимит
        if limit == float('inf'):
            return

        # Ключ включает ID пользователя и текущую дату в UTC
        today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        redis_key = f"daily_msg_count:{user.id}:{today_utc}"

        # Атомарно получаем текущее значение счетчика
        current_count = await redis_client.get(redis_key)
        current_count = int(current_count) if current_count else 0
        
        if current_count >= limit:
            raise TariffLimitReachedError(f"Достигнут дневной лимит на отправку сообщений ({limit} шт.) для вашего тарифа.")

        # Увеличиваем счетчик и устанавливаем время жизни ключа (25 часов на случай смены часовых поясов)
        pipe = redis_client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, timedelta(hours=25))
        await pipe.execute()
                    
billing_service = BillingService()