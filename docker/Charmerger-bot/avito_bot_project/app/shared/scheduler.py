# /app/shared/scheduler.py
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

# Создаем единственный экземпляр шедулера для всего приложения
scheduler = AsyncIOScheduler(timezone="UTC")

def start_scheduler():
    try:
        scheduler.start()
        logger.info("Планировщик успешно запущен.")
    except Exception as e:
        logger.error(f"Ошибка запуска планировщика: {e}", exc_info=True)

def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Планировщик успешно завершил работу.")