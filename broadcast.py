import asyncio
import logging
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from config import TELEGRAM_TOKEN
from db import supabase

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Текст сообщения для рассылки
MESSAGE_TEXT = """🚀 <b>Каталог обновлен!</b> 

Мы добавили новые товары, в том числе крутые новинки. Теперь вы можете искать их через бота! 

✨ <b>Вот лишь некоторые из них:</b>

• <b>Kardli стакан</b> для получения оздоровительной воды
• <b>SEALUXE 120 мл</b> Двухкомпонентный дрожжевой тоник — <i>8 800 KZT</i>
• <b>SEALUXE 100 г</b> Смягчающий лосьон на основе двухвалентных дрожжей — <i>11 600 KZT</i>
• <b>SEALUXE 30 мл</b> Эссенция для размягчения двухраздельных дрожжей — <i>11 600 KZT</i>
• <b>SEALUXE 20 г</b> Укрепляющий крем для глаз на основе двух дрожжей — <i>8 800 KZT</i>
• <b>CARICH Освежитель дыхания</b> (с зеленым чаем и мятой) 17 мл — <i>1 800 KZT (0.1 PV)</i>
• <b>Чай улун из листьев лотоса Nilrich</b> — <i>5 400 KZT (0.2 PV)</i>
• <b>KARDLI Волшебная меламиновая губка (16шт)</b> — <i>3 400 KZT (0.2 PV)</i>
• <b>Nilrich Грушевая паста с локвой от кашля</b> — <i>4 000 KZT (1.0 PV)</i>
• <b>iLiFE ароматизированная жидкость</b> для мытья полов 500 мл — <i>2 200 KZT (0.1 PV)</i>

Приятных покупок и использования! 🛍"""

async def main():
    # Инициализируем бота
    bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    
    logger.info("Получаем список всех пользователей из базы данных...")
    
    # Получаем всех пользователей (кто когда-либо писал боту)
    res = supabase.table('users').select('user_id').execute()
    users = res.data
    
    if not users:
        logger.info("Пользователи не найдены в базе.")
        return
        
    logger.info(f"Найдено {len(users)} пользователей. Начинаем рассылку...")
    
    success_count = 0
    fail_count = 0
    blocked_count = 0
    
    for idx, user in enumerate(users):
        user_id = user['user_id']
        try:
            await bot.send_message(chat_id=user_id, text=MESSAGE_TEXT)
            success_count += 1
            
            # Логируем прогресс каждые 10 сообщений или если база маленькая
            if (idx + 1) % 10 == 0 or len(users) < 20:
                logger.info(f"Прогресс: {idx+1}/{len(users)} обработано...")
            
            # БЕЗОПАСНАЯ ЗАДЕРЖКА (Anti-Ban)
            # Telegram разрешает до 30 сообщений в секунду суммарно, 
            # 0.1 сек = 10 сообщений в секунду (максимально безопасно)
            await asyncio.sleep(0.1)
            
        except TelegramRetryAfter as e:
            # Если Telegram всё же сказал притормозить (Flood Control)
            logger.warning(f"Уперлись в лимиты Telegram. Ждем {e.retry_after} секунд...")
            await asyncio.sleep(e.retry_after)
            
            # Повторяем попытку после ожидания
            try:
                await bot.send_message(chat_id=user_id, text=MESSAGE_TEXT)
                success_count += 1
            except Exception:
                fail_count += 1
                
        except TelegramForbiddenError:
            # Пользователь удалил чат с ботом или заблокировал его
            blocked_count += 1
        except Exception as e:
            # Любые другие ошибки (например, неверный ID)
            logger.error(f"Ошибка при отправке пользователю {user_id}: {e}")
            fail_count += 1
            
    # Закрываем сессию бота
    await bot.session.close()
            
    logger.info("=============================")
    logger.info("🎉 Рассылка успешно завершена!")
    logger.info(f"✅ Успешно доставлено: {success_count}")
    logger.info(f"🚫 Заблокировали бота: {blocked_count}")
    logger.info(f"❌ Ошибок отправки: {fail_count}")
    logger.info("=============================")

if __name__ == "__main__":
    # Запуск асинхронной функции
    asyncio.run(main())
