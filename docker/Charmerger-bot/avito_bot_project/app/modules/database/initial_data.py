# /app/modules/database/initial_data.py (ИСПРАВЛЕННАЯ ВЕРСИЯ)

import logging
from .crud import create_template  # Убедитесь, что create_tariff удален из импорта
from shared.database import get_session

logger = logging.getLogger(__name__)

async def load_initial_data():
    """
    Загружает начальные данные (шаблоны) в базу данных.
    Выполняется в одной транзакции.
    """
    logger.info("Loading initial data...")
    
    async with get_session() as session:
        try:

            await session.commit()
            logger.info("Initial data has been successfully loaded and committed.")
            
        except Exception as e:
            logger.error(f"Failed to load initial data. Rolling back... Error: {e}", exc_info=True)
            # Если произошла ошибка, откатываем все изменения
            await session.rollback()
            raise