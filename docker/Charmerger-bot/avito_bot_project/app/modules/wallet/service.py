import logging
from typing import Optional
import redis.asyncio as redis
from shared.database import get_session
from ..database import crud
from db_models import Transaction, User

logger = logging.getLogger(__name__)

# Время жизни кэша баланса в Redis (в секундах).
BALANCE_CACHE_TTL = 300 

class WalletService:
    """
    Сервис для управления кошельком пользователя.
    Использует кэширование в Redis для баланса.
    """
    async def get_balance(self, user_id: int, redis_client: redis.Redis) -> float:
        """
        Получает баланс пользователя.
        Сначала проверяет кэш в Redis, при отсутствии - запрашивает из БД.
        """
        cache_key = f"user:{user_id}:balance"
        
        # 1. Пытаемся получить баланс из кэша
        try:
            cached_balance = await redis_client.get(cache_key)
            if cached_balance is not None:
                logger.info(f"Баланс для пользователя {user_id} найден в кэше.")
                return float(cached_balance)
        except Exception as e:
            # Не даем ошибке Redis остановить процесс, просто логируем
            logger.error(f"Ошибка получения баланса из Redis для пользователя {user_id}: {e}", exc_info=True)

        # 2. Если в кэше нет - идем в базу данных
        logger.info(f"Баланс пользователя {user_id} отсутствует в кэше. Извлечение из базы данных.")
        async with get_session() as session:
            # Используем CRUD функцию для получения пользователя
            user: Optional[User] = await crud.get_user_by_id(session, user_id)
            if not user:
                logger.warning(f"Пользователь с идентификатором {user_id} не найден при получении баланса.")
                return 0.0
            
            balance = user.balance
            
            # 3. Сохраняем полученное значение в кэш
            try:
                await redis_client.set(cache_key, str(balance), ex=BALANCE_CACHE_TTL)
            except Exception as e:
                logger.error(f"Ошибка установки баланса Redis для пользователя {user_id}: {e}", exc_info=True)

            return balance

    async def deposit(
        self, 
        user_id: int, 
        amount: float, 
        description: str, 
        redis_client: redis.Redis
    ) -> Optional[Transaction]:
        """
        Пополняет баланс пользователя. Сумма должна быть положительной.
        """
        if amount <= 0:
            logger.warning(f"Сумма депозита должна быть положительной. Получено: {amount} для пользователя {user_id}.")
            return None
        
        transaction = None
        async with get_session() as session:
            transaction = await crud.create_transaction_and_update_balance(
                session=session,
                user_id=user_id,
                amount=amount, 
                description=description
            )
        
        # Инвалидируем (удаляем) кэш баланса, чтобы при следующем запросе он обновился
        if transaction:
            await self._invalidate_balance_cache(user_id, redis_client)
        
        return transaction

    async def withdraw(
        self, 
        user_id: int, 
        amount: float, 
        description: str, 
        redis_client: redis.Redis
    ) -> Optional[Transaction]:
        """
        Списывает средства с баланса пользователя. Сумма должна быть положительной.
        """
        if amount <= 0:
            logger.warning(f"Сумма вывода должна быть положительной. Получено: {amount} для пользователя {user_id}.")
            return None
        
        transaction = None
        async with get_session() as session:
            # Передаем отрицательное значение amount в CRUD-функцию
            transaction = await crud.create_transaction_and_update_balance(
                session=session,
                user_id=user_id,
                amount=-amount, # Отрицательная сумма
                description=description
            )
            
        # Инвалидируем кэш баланса
        if transaction:
            await self._invalidate_balance_cache(user_id, redis_client)
        
        return transaction
    
    async def _invalidate_balance_cache(self, user_id: int, redis_client: redis.Redis):
        """Вспомогательный приватный метод для удаления кэша баланса из Redis."""
        cache_key = f"user:{user_id}:balance"
        try:
            logger.info(f"Аннулирование кэша баланса для пользователя {user_id}.")
            await redis_client.delete(cache_key)
        except Exception as e:
            logger.error(f"Не удалось сделать кэш баланса недействительным для пользователя. {user_id}: {e}", exc_info=True)

# Создаем единственный экземпляр сервиса для удобного импорта по всему проекту
wallet_service = WalletService()