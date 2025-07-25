# /app/shared/redis_client.py

import logging
from typing import Optional
import redis.asyncio as redis
from .config import settings

logger = logging.getLogger(__name__)

# --- Глобальная переменная для хранения клиента Redis ---
redis_client: Optional[redis.Redis] = None

async def init_redis():
    """
    Создает и инициализирует глобальный клиент Redis, используя пул соединений.
    Эта функция должна быть вызвана один раз при старте приложения (например, в lifespan FastAPI).
    """
    # Используем `global`, чтобы изменить переменную, объявленную на уровне модуля
    global redis_client
    
    # Проверяем, не был ли клиент уже инициализирован
    if redis_client is not None:
        logger.warning("Клиент Redis уже инициализирован.")
        return redis_client

    logger.info("Инициализация пула соединений Redis...")
    try:
        # Создаем клиент, используя URL, который был сгенерирован в config.py
        # settings.redis_url - это вычисляемое поле из Pydantic V2
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,  # Важно: Redis будет возвращать строки, а не байты
            health_check_interval=30 # Периодическая проверка, что соединение живое
        )   
        # Делаем тестовый запрос, чтобы убедиться, что соединение установлено
        await redis_client.ping()
        logger.info("Пул соединений Redis успешно инициализирован.")
    
    except Exception as e:
        logger.error(f"Не удалось подключиться к Redis. Ошибка.: {e}")
        # В случае ошибки оставляем клиента как None, чтобы приложение могло это обработать
        redis_client = None
        # Пробрасываем ошибку дальше, чтобы приложение не запустилось без Redis
        raise

    return redis_client

async def close_redis():
    """
    Корректно закрывает пул соединений с Redis.
    Эта функция должна быть вызвана при остановке приложения.
    """
    global redis_client
    if redis_client:
        logger.info("Закрытие пула соединений Redis...")
        await redis_client.close()
        # Сбрасываем глобальную переменную
        redis_client = None
        logger.info("Пул соединений Redis закрыт.")