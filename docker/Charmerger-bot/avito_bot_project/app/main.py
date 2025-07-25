# /app/main.py
# --- 1. БЛОК ИМПОРТОВ ДЛЯ РЕГИСТРАЦИИ МОДЕЛЕЙ И РОУТЕРОВ ---
from db_models import Base
from db_models import (
    User, AvitoAccount, Template, Transaction,
    AutoReplyRule, ChatNote, MessageLog
)
# --- 2. БЛОК ИМПОРТОВ ОСНОВНОЙ ЛОГИКИ ПРИЛОЖЕНИЯ ---
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Request

# --- 3. БЛОК ИМПОРТОВ КОМПОНЕНТОВ ПРИЛОЖЕНИЯ ---
# Этот engine используется для создания таблиц в lifespan
from shared.database import engine
from shared.redis_client import init_redis, close_redis
from shared.scheduler import start_scheduler, stop_scheduler

# Модули с фоновыми задачами (воркерами)
from modules.telegram.worker import (
    start_telegram_sender_worker, 
    start_chat_action_worker,
    start_event_processor_worker
)
from modules.avito.worker import start_avito_outgoing_worker
from modules.autoreplies.worker import start_autoreply_worker
from modules.avito.forwarder import avito_to_telegram_forwarder
from routers import api_router as main_api_router
from modules.webapp.routers import router as webapp_router
# Компоненты для инициализации
from modules.database.initial_data import load_initial_data
# Компоненты aiogram, которые нужны в main
from modules.telegram.bot import bot, dp, set_telegram_webhook, remove_telegram_webhook
from modules.telegram.middlewares import DbSessionMiddleware

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- Жизненный цикл приложения: запуск и остановка ресурсов ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управляет запуском и остановкой фоновых задач и соединений
    вместе с жизненным циклом FastAPI.
    """
    logger.critical("="*50)
    logger.critical("ЗАРЕГИСТРИРОВАННЫЕ МАРШРУТЫ:")
    for route in app.routes:
        # Для обычных роутов
        if hasattr(route, "path"):
            logger.critical(f"Путь: {route.path} | Имя: {route.name} | Методы: {getattr(route, 'methods', 'N/A')}")
        # Для монтированной статики
        elif hasattr(route, "path_regex"):
             logger.critical(f"Примонтирован путь: {route.path} | Имя: {route.name}")
    logger.critical("="*50)

    logger.info("Запуск приложения: Инициализация ресурсов...")
    
    # --- 1. Создание таблиц в БД ---
    logger.info("Проверка и создание таблиц базы данных...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Таблицы базы данных готовы.")
    
    # --- 2. Загрузка начальных данных ---
    await load_initial_data()
    
    # --- 3. Инициализация Redis ---
    logger.info("Инициализация соединения Redis...")
    redis_client = await init_redis()
    app.state.redis = redis_client
    logger.info("Redis-соединение готово.")

    # --- 4. Регистрация Middleware для Aiogram ---
    # Мы передаем в middleware наш уже созданный клиент Redis.
    # dp.update.outer_middleware - значит, что он будет срабатывать на все типы апдейтов.
    dp.update.outer_middleware(DbSessionMiddleware(redis_client))
    logger.info("Aiogram DbSessionMiddleware зарегистрирован.")

    # --- 5. Запуск планировщика и воркеров ---
    start_scheduler()
    logger.info("Запуск фоновых работников...")
    tasks = [
        # Воркеры Telegram
        asyncio.create_task(start_telegram_sender_worker(redis_client, bot), name="TelegramSenderWorker"),
        asyncio.create_task(start_event_processor_worker(redis_client, bot), name="EventProcessorWorker"),
        asyncio.create_task(start_chat_action_worker(redis_client, bot), name="ChatActionWorker"),
        
        # Воркеры Avito
        asyncio.create_task(start_avito_outgoing_worker(redis_client), name="AvitoOutgoingWorker"),
        asyncio.create_task(avito_to_telegram_forwarder(redis_client), name="AvitoToTelegramForwarder"),

        # Воркер Автоответов
        asyncio.create_task(start_autoreply_worker(redis_client), name="AutoreplyWorker"),
    ]
    logger.info(f"Запуск {len(tasks)} фоновых задач.")
    
    # --- 6. Установка вебхука Telegram ---
    await set_telegram_webhook()
    
    logger.info("Запуск приложения завершен.")
    yield
    
    # ==========================================================
    # Код ниже выполнится при остановке приложения (shutdown)
    # ==========================================================
    logger.info("Завершение работы приложения: Очистка ресурсов...")
    await remove_telegram_webhook()

    for task in tasks:
        task.cancel()
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("Фоновые работники успешно остановлены.")
    
    stop_scheduler()
    await close_redis()
    await engine.dispose()
    logger.info("Приложение корректно завершает работу.")


# --- Создание основного экземпляра приложения FastAPI ---
app = FastAPI(
    title="Avito Bot Project",
    description="Bridge for messaging between Avito and Telegram.",
    version="1.0.0",
    lifespan=lifespan
)

app.mount("/panel/static", StaticFiles(directory="modules/webapp/static"), name="static")
app.include_router(main_api_router)
app.include_router(webapp_router)

@app.api_route("/debug/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def debug_catch_all(request: Request, full_path: str):
    """
    Этот эндпоинт ловит все запросы на /debug/* и выводит
    полную информацию о них в консоль.
    """
    body = await request.body()
    for name, value in request.headers.items():
        print(f"  {name}: {value}")

    return {"status": "ok", "path_received": request.url.path, "method": request.method}

# Блок для локальной разработки без Docker/Nginx
if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск приложения в режиме локальной разработки...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )