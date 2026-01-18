# Используем легкий образ Python 3.11
FROM python:3.11-slim

# Отключаем создание .pyc файлов и буферизацию вывода (чтобы логи шли сразу)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала копируем и ставим зависимости (для кэширования Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]