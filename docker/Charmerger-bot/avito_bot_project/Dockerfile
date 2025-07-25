# /avito_bot_project/Dockerfile
FROM python:3.10-slim

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY ./app /app

RUN chown -R appuser:appuser /app

USER appuser

# Открываем порт 8000 для внутренней сети Docker
EXPOSE 8000

# Команда для запуска Uvicorn на обычном HTTP порту
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]