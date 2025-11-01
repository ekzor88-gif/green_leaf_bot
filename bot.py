import asyncio
import logging
import ast
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.enums.chat_action import ChatAction # <-- ДОБАВИТЬ ЭТОТ ИМПОРТ В НАЧАЛО ФАЙЛА


# ❌ ИСПРАВЛЕНИЕ: Заменяем удаленный get_query_type на is_product_query
from llm import generate_answer, is_product_query 

import config
import db # Импортируем модуль db


logging.basicConfig(level=logging.INFO)
CATALOG_SHEET_URL = config.GOOGLE_SHEET_URL

bot = Bot(
    token=config.TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
router = Router()
dp.include_router(router)

MANAGER_PHONE = "77012706305"  # без +, Казахстан пример

# ----------------- КЛАВИАТУРЫ -----------------

def get_main_reply_keyboard():
    """Главное меню (постоянно отображаемое снизу)."""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⭐ Просмотреть каталог"),
                KeyboardButton(text="📞 Связь с менеджером")
            ]
        ],
        resize_keyboard=True, # Делает кнопки меньше
        one_time_keyboard=False # Кнопки не исчезают
    )
    return kb

def get_catalog_inline_keyboard():
    """Inline-кнопка для открытия URL каталога."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Открыть каталог в браузере", url=CATALOG_SHEET_URL)
            ]
        ]
    )
    return kb

def get_manager_keyboard():
    """Inline-кнопка для связи с менеджером через WhatsApp."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📞 Связаться в WhatsApp",
                url=f"https://wa.me/{MANAGER_PHONE}"
            )
        ],
    ])
    return kb

# ----------------- ОБРАБОТЧИКИ -----------------

@router.message(Command("start"))
async def on_start(message: Message):
    u = message.from_user
    # ⚠️ ОБЕРТКА DB: upsert_user
    await asyncio.to_thread(db.upsert_user, u.id, u.first_name or "", u.last_name or "", u.username or "")
    
    # 1. Отправляем приветствие и постоянно видимое Reply-меню
    await message.answer(
        "Привет! Я ИИ консультант по продуктам компании GreenLeaf 🌿. \n\n"
        "Был разработан семейной парой Артемьевых \n\n"
        "Если Вы не нашли какой-то товар в нашем каталоге, просто напишите нам: +77012706302; +77029554206 \n\n"
        "Можете задавать мне вопросы, или выберите действие в меню ниже 👇:",
        reply_markup=get_main_reply_keyboard() # <-- Используем Reply Keyboard
    )

# Обработчик нажатия на кнопку "Просмотреть каталог"
@router.message(F.text == "⭐ Просмотреть каталог")
async def handle_view_catalog_reply(message: Message):
    await message.answer(
        "Вот ссылка на наш полный и актуальный каталог. Вы можете просмотреть его прямо сейчас:",
        reply_markup=get_catalog_inline_keyboard()
    )

# Обработчик нажатия на кнопку "Связь с менеджером"
@router.message(F.text == "📞 Связь с менеджером")
async def handle_manager_reply(message: Message):
    await message.answer(
        "Вы можете связаться с нашим менеджером 👇",
        reply_markup=get_manager_keyboard()
    )

# Обработчик для нетекстовых сообщений
@router.message(~F.text)
async def on_media(message: Message):
    """Отвечает на нетекстовые сообщения.""" 
    await message.answer(
        "Извините, я умею работать только с **текстовыми сообщениями**."
        "\n\nПожалуйста, отправьте мне свой вопрос текстом. 👇"
    )


# ====================================================================
# --- ОБНОВЛЕННАЯ ФУНКЦИЯ on_text (БЕЗ ЛОГИКИ 'health') ---
# ====================================================================

@router.message(F.text)
async def on_text(message: Message):
    
    # ... (Оставим реакцию и typing_task без изменений)
    typing_task = asyncio.create_task(
        # ... (код для отправки "печатает")
        bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    )
    await asyncio.sleep(0.3) 
    
    try:
        # ... (код для установки реакции)
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[types.ReactionTypeEmoji(emoji="🤩")] 
        )
    except Exception as e:
        logging.info(f"Не удалось установить реакцию: {e}")

    try:
        u = message.from_user
        text = (message.text or "").strip()
        if not text:
            return

        # ------------------------------------------------------------------
        # --- ПРЕДВАРИТЕЛЬНЫЕ ПРОВЕРКИ И СОХРАНЕНИЕ ---
        # ------------------------------------------------------------------

        # Проверка на прямой запрос менеджера
        if any(word in text.lower() for word in ["менеджер", "заказ", "связь", "оператор"]):
            await message.answer(
                "Вы можете связаться с нашим менеджером 👇",
                reply_markup=get_manager_keyboard()
            )
            # 💡 ВАЖНО: При запросе менеджера очищаем контекст товаров, так как диалог окончен
            await asyncio.to_thread(db.clear_last_products, u.id)
            return

        # Сохранение пользователя и сообщения
        await asyncio.to_thread(db.upsert_user, u.id, u.first_name or "", u.last_name or "", u.username or "")
        await asyncio.to_thread(db.save_message, u.id, "user", text)

        # Получение истории диалога
        history = await asyncio.to_thread(db.get_recent_messages, u.id, limit=8)

        # --------------------------------------------------------
        # --- ШАГ 1: КЛАССИФИКАЦИЯ И RAG (ПРЯМОЙ ПОИСК) ---
        # --------------------------------------------------------
        
        do_rag_search = await asyncio.to_thread(is_product_query, text)
        
        matched_products = []
        products_for_text_gen = [] # Будет хранить ПОЛНЫЕ объекты товаров

        if do_rag_search:
            # --- СЦЕНАРИЙ 1: ПОИСК ТОВАРА (RAG) ---
            
            # 1. Ищем товары
            matched_products = await asyncio.to_thread(db.search_products, text) 

            # 2. Передаем LLM найденные товары
            products_for_text_gen = matched_products
            
            # 3. Сохраняем ПОЛНЫЙ список товаров в Supabase для навигации
            await asyncio.to_thread(db.save_last_products, u.id, matched_products)

        else:
            # --- СЦЕНАРИЙ 2: ПРОСТОЙ ДИАЛОГ (Проверка на продолжение контекста) ---
            
            # Пытаемся загрузить предыдущий контекст, ТОЛЬКО если запрос — это уточнение.
            # 💡 Здесь нужна дополнительная логика проверки типа запроса: 
            # Например, 'А сколько стоит второй?' или 'Какой лучше?'
            # Для простого 'Привет' LLM должен получить ПУСТОЙ список товаров.
            
            # 1. Загружаем сохраненный список товаров
            last_products = await asyncio.to_thread(db.get_last_products, u.id)
            
            # 2. 💡 ИЗМЕНЕНИЕ: Используем LLM для определения, нужно ли использовать СТАРЫЙ контекст.
            # Если запрос не похож на "сколько стоит" или "какой лучше", то контекст игнорируется.
            # В противном случае, если это "болталка" (Привет, Как дела) - контекст не передаем.
            
            # Мы используем LLM (generate_answer) для принятия этого решения:
            # Если запрос - "Привет" (do_rag_search=False), мы все равно передаем старые товары.
            # 🚨 НО! Мы должны убедиться, что в generate_answer есть ЖЕСТКАЯ ЛОГИКА:
            # ЕСЛИ text = "Привет" И products_for_text_gen не пуст, ТО ИГНОРИРУЙ товары.
            
            # Оставляем передачу старых товаров, но полагаемся на СИСТЕМНЫЙ ПРОМПТ!
            products_for_text_gen = last_products
            
            # 💡 КРИТИЧЕСКОЕ УЛУЧШЕНИЕ ЛОГИКИ: 
            # Если это 'Привет' или 'Как дела', то есть do_rag_search=False, 
            # и при этом запрос не похож на 'второй', то лучше очистить контекст. 
            # Самый простой способ: если LLM-классификатор вернул False, а длина запроса < 15 символов, очищаем контекст.
            if len(text) < 15: # Условно считаем короткие запросы болталкой
                await asyncio.to_thread(db.clear_last_products, u.id)
                products_for_text_gen = [] # Передаем пустой контекст

        
        # 4. Вызываем LLM для генерации ответа в обоих сценариях
        # LLM увидит товары ТОЛЬКО если:
        # 1) do_rag_search=True (новый поиск) ИЛИ
        # 2) do_rag_search=False, но запрос не был слишком коротким (продолжение диалога).
        answer = await asyncio.to_thread(generate_answer, history, text, products_for_text_gen)
        
        
        # --------------------------------------------------------
        # --- ШАГ 2: ОТВЕТ И ПОСТ-ОБРАБОТКА (ОБЩЕЕ) ---
        # --------------------------------------------------------
        
        if answer:
            await asyncio.to_thread(db.save_message, u.id, "assistant", answer)
            await message.answer(answer)

        # Вывод кнопок для товаров (только если был RAG-поиск и товары найдены)
        # Кнопки должны выводиться только после НОВОГО поиска.
        if do_rag_search and matched_products:
            
            # ... (Остальной код вывода кнопок остается без изменений)
            total = len(matched_products)
            
            if total <= 5:
                # 1. 1–5 товаров → сразу выводим все кнопки
                buttons = [
                    [InlineKeyboardButton(
                        text=f"Подробнее: {p['name']}",
                        callback_data=f"product_{p['id']}"
                    )]
                    for p in matched_products
                ]
                
                header = "Я нашёл этот товар 👇" if total == 1 else f"Я нашёл {total} товаров 👇"
                
                await message.answer(
                    header,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
                )

            else:
                # 2. Больше 5 → предлагаем показать первые 5 или уточнить
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Показать первые 5", callback_data="show_page_0"),
                        InlineKeyboardButton(text="Нет, уточнить", callback_data="filter")
                    ]
                ])
                await message.answer(
                    f"Я нашёл еще {total} товаров, которые могу Вас заинтересовать. Показать первые 5 или уточним?",
                    reply_markup=kb
                )

    except Exception as e:
        logging.error(f"Ошибка в on_text: {e}")
        await message.answer("Упс, что-то пошло не так 🙏")
    
    # Отменяем задачу "Печатает..."
    if not typing_task.done():
        typing_task.cancel()

        
# ================== КОЛЛБЕКИ НАВИГАЦИИ ПО ТОВАРАМ ===================

@router.callback_query(F.data.startswith("show_page_"))
async def show_page(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    PAGE_SIZE = 5
    
    # Извлекаем текущий индекс/оффсет из callback_data (например, 'show_page_0' -> 0)
    try:
        current_offset = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации: некорректный индекс.")
        return

    # ********** ИЗВЛЕКАЕМ ИЗ SUPABASE **********
    all_products = await asyncio.to_thread(db.get_last_products, user_id)
    total = len(all_products)

    if not all_products:
        await callback.message.edit_text("Извините, результаты поиска устарели или не найдены. Попробуйте начать новый поиск.")
        await callback.answer()
        return

    # Выбираем товары для текущей страницы
    products_on_page = all_products[current_offset : current_offset + PAGE_SIZE]
    
    # ----------------- КНОПКИ ТОВАРОВ -----------------
    buttons = [
        [InlineKeyboardButton(
            text=f"Подробнее: {p['name']}",
            # Здесь предполагается, что в элементах all_products есть 'id'
            callback_data=f"product_{p.get('id')}" 
        )]
        for p in products_on_page
    ]
    
    # ----------------- КНОПКИ НАВИГАЦИИ (ПОДВАЛ) -----------------
    
    footer_buttons = []
    
    next_offset = current_offset + PAGE_SIZE
    has_next = next_offset < total
    
    # Если есть еще товары, добавляем кнопку "Показать ещё"
    if has_next:
        footer_buttons.append(
            # 💡 ПЕРЕДАЕМ НОВЫЙ СТАРТОВЫЙ ИНДЕКС
            InlineKeyboardButton(text=f"Показать ещё ({total - next_offset})", callback_data=f"show_page_{next_offset}")
        )
    
    # Добавляем кнопку "Хватит" (или "Назад", если нужна)
    footer_buttons.append(
        InlineKeyboardButton(text="Хватит, вернуться", callback_data="stop")
    )
    
    # Формируем итоговую клавиатуру
    kb = InlineKeyboardMarkup(
        inline_keyboard=buttons + [footer_buttons]
    )

    # ----------------- ОТПРАВКА -----------------
    
    # Определяем текст для заголовка страницы
    if current_offset == 0:
        header_text = f"Первые {len(products_on_page)} из {total} товаров 👇"
    else:
        header_text = f"Показаны товары {current_offset + 1} - {current_offset + len(products_on_page)} из {total} 👇"
        
    await callback.message.edit_text(header_text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "stop")
async def stop_navigation(callback: types.CallbackQuery):
    await callback.message.edit_text("Хорошо, рад был помочь с выбором!")
    await callback.answer()


# ================== ПРОДУКТ ДЕТАЛИ (ИСПРАВЛЕНО) ===================

@router.callback_query(F.data.startswith("product_"))
async def on_product_detail(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Максимальные лимиты Telegram
    MAX_CAPTION_LENGTH = 1024
    MAX_MESSAGE_LENGTH = 4096
    
    try:
        product_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.message.answer("Ошибка: некорректный ID товара.")
        await callback.answer()
        return

    # ********** ИЗВЛЕКАЕМ ИЗ SUPABASE **********
    products = await asyncio.to_thread(db.get_last_products, user_id)
    # Используем str() для корректного сравнения ID
    product = next((p for p in products if str(p.get("id")) == str(product_id)), None)
    
    if not product:
        # Уведомляем пользователя, но не удаляем сообщение, чтобы не вызвать ошибку
        await callback.answer("❌ Товар не найден. Возможно, результаты устарели.")
        return

    # ----------------- СБОР ДАННЫХ -----------------
    name = product.get("name") or product.get("Название") or "Без названия"
    price = product.get("price") 
    price_text = f"{price} тг" if price else "не указана"
    description = product.get("description") or product.get("Описание") or ""
    description = description.strip()
    
    # --- Поиск URL изображения (логика из вашего кода) ---
    image_url = None
    images_field = product.get("url") or product.get("image") or product.get("Фото")
    if images_field:
        try:
            # Пытаемся распарсить как строковый JSON-массив
            if isinstance(images_field, str) and images_field.startswith("["):
                parsed = ast.literal_eval(images_field)
                if isinstance(parsed, list) and parsed:
                    image_url = parsed[0].strip()
            # Иначе берем как прямую строку
            elif isinstance(images_field, str):
                image_url = images_field.strip().strip("[]'\"")
        except Exception:
            # Если парсинг не удался, берем как есть
            if isinstance(images_field, str):
                image_url = images_field.strip()

    # ----------------- ФОРМИРОВАНИЕ ТЕКСТА -----------------
    
    header_text = f"✨ <b>{name}</b>\n\n💰 Цена: {price_text}"
    full_text = f"{header_text}\n\n{description}"
    
    # ----------------- ОТПРАВКА СООБЩЕНИЙ -----------------

    if image_url:
        # 1. Сценарий с фото: отправляем фото + caption (макс 1024 символа)
        caption_to_send = full_text[:MAX_CAPTION_LENGTH]
        
        try:
            await callback.message.answer_photo(
                photo=image_url, 
                caption=caption_to_send, 
                parse_mode=ParseMode.HTML
            )
            
            # Если описание длиннее 1024 символов, отправляем остаток отдельным сообщением.
            if len(full_text) > MAX_CAPTION_LENGTH:
                # Берем остаток текста
                remaining_description = full_text[MAX_CAPTION_LENGTH:]
                
                # Убеждаемся, что остаток не превышает лимит 4096 символов
                text_to_send = remaining_description[:MAX_MESSAGE_LENGTH]
                
                if text_to_send:
                    await callback.message.answer(
                        text=text_to_send, 
                        parse_mode=ParseMode.HTML
                    )

        except Exception as e:
            # Если фото не загрузилось (ошибка Telegram/URL), отправляем текст полностью
            logging.error(f"Ошибка загрузки фото: {e}")
            text_to_send = full_text[:MAX_MESSAGE_LENGTH]
            await callback.message.answer(
                text=text_to_send + "\n⚠️ **Ошибка загрузки фото.**", 
                parse_mode=ParseMode.HTML
            )
    else:
        # 2. Сценарий без фото: отправляем только текст (макс 4096 символов)
        text_to_send = full_text[:MAX_MESSAGE_LENGTH]
        await callback.message.answer(
            text=text_to_send, 
            parse_mode=ParseMode.HTML
        )

    # ----------------- КНОПКИ -----------------
    buttons = [
        [InlineKeyboardButton(text="📞 Связаться с менеджером", callback_data="manager")]
    ]
    await callback.message.answer(
        "Что хочешь сделать дальше?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    # Обязательно подтверждаем callback, чтобы исчезли часы на кнопке
    await callback.answer()


@router.callback_query(F.data == "manager")
async def on_manager(callback: types.CallbackQuery):
    await callback.message.answer(
        "📞 Я передал твой запрос менеджеру. Он скоро выйдет на связь!"
    )
    await callback.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())