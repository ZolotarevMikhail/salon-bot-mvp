# -*- coding: utf-8 -*-
"""Салон красоты — универсальный телеграм-бот (MVP).

Настройка под конкретный салон — только в knowledge.py, код не трогаем.

Что умеет:
    1. Меню: услуги и цены, мастера, акции, контакты.
    2. Ответы на частые вопросы (база знаний — knowledge.py).
    3. Запись на услугу: услуга → мастер → день → время → заявка админу.
    4. «Позвать администратора»: вопрос клиента уходит живому человеку,
       админ отвечает реплаем на пересланное сообщение — ответ
       возвращается клиенту.

Запуск:  python3 bot.py  (или двойным кликом по run.command)
Настройки: файл .env рядом с ботом (BOT_TOKEN, ADMIN_CHAT_ID) — README.md.
"""

import hashlib
import logging
import os
import sys
from datetime import date, datetime, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import knowledge as kb
import stats


# ------------------------------------------------------------
# Настройки из файла .env
# ------------------------------------------------------------

def load_env():
    """Подтягиваем .env рядом с ботом в переменные окружения."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

if not BOT_TOKEN:
    print("=" * 60)
    print("Не найден токен бота. Рядом с bot.py должен лежать файл .env")
    print("со строкой BOT_TOKEN=... — как получить, написано в README.md")
    print("=" * 60)
    sys.exit(1)

ADMIN = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID.isdigit() else None


def trim_log(path, keep=200):
    """Ротация: оставляем в логе только последние keep строк."""
    try:
        with open(path, encoding="utf-8") as log_file:
            tail = log_file.readlines()[-keep:]
        with open(path, "w", encoding="utf-8") as log_file:
            log_file.writelines(tail)
    except OSError:
        pass


trim_log("bot.log")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("mne-bot")


# ------------------------------------------------------------
# Тексты и меню
# ------------------------------------------------------------

WELCOME = (
    f"Здравствуйте! 👋\nВы в студии «{kb.STUDIO_NAME}».\n\n"
    "Я помогу с ценами, мастерами и записью. Выберите раздел:"
)

MENU_ROWS = [
    ["💅 Услуги", "👑 Мастера"],
    ["🔥 Акции", "📍 Контакты"],
    ["📅 Записаться", "💬 Админ"],
]
MENU_TEXTS = {button_text for row in MENU_ROWS for button_text in row}
MENU_MARKUP = ReplyKeyboardMarkup(
    [[KeyboardButton(button_text) for button_text in row]
     for row in MENU_ROWS],
    resize_keyboard=True,
    is_persistent=True,  # клавиатура закреплена: Telegram её не прячет
    input_field_placeholder="Выберите действие…",
)

# Состояния сценария записи
MST, DAY, TIME, CONFIRM = range(4)

# Навигация внутри записи: доступна на каждом шаге
NAV_ROW = [
    InlineKeyboardButton(
        "💬 Позвать администратора", callback_data="book|admin"),
    InlineKeyboardButton("❌ Отмена", callback_data="book|cancel"),
]

# Просим вопрос клиента перед передачей админу (используется дважды)
ADMIN_PROMPT = (
    "Хорошо, соединяю с администратором 👤\n"
    "Напишите ваш вопрос одним сообщением — я передам его вместе "
    "с историей диалога, и администратор ответит вам прямо здесь."
)


def nav_markup(rows):
    """Кнопки шага + всегда доступные «администратор/отмена»."""
    return InlineKeyboardMarkup(rows + [NAV_ROW])


# ------------------------------------------------------------
# Вспомогательное
# ------------------------------------------------------------

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def user_label(update):
    """Как называть клиента в сообщениях админу."""
    user = update.effective_user
    if user is None:
        return "клиент"
    if user.username:
        handle = f"@{user.username}"
    else:
        handle = f"id {user.id}"
    return f"{user.full_name} ({handle})"


def find_faq(text):
    """Ищем лучший ответ по ключевым словам. Возврат: текст ответа или None."""
    low = text.lower()
    best, best_score = None, 0
    for item in kb.FAQ:
        matched = sum(1 for keyword in item["keywords"] if keyword in low)
        if matched > best_score:
            best, best_score = item["answer"], matched
    return best


def day_options():
    """Демо-даты: сегодня, завтра, послезавтра, +3 дня."""
    today = date.today()
    return [today + timedelta(days=offset) for offset in range(4)]


def day_title(day):
    """Подпись даты: «Сегодня/Завтра/Послезавтра» или полноценная."""
    day_diff = (day - date.today()).days
    shortcut = {0: "Сегодня", 1: "Завтра", 2: "Послезавтра"}.get(day_diff)
    return shortcut or f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"  # noqa: E501


def day_caption(day):
    """Полная подпись даты: «Пн, 3 октября»."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


def free_slots(day):
    """Демо-расписание: слоты 9:00–23:00, ~20% «занято» (стабильно для одной даты)."""  # noqa: E501
    slots = []
    current_hour = datetime.now().hour
    for hour in range(9, 24):
        if day == date.today() and hour <= current_hour:
            continue  # прошедшие часы не предлагаем
        key = f"{day.isoformat()}-{hour}"
        busy_seed = hashlib.md5(key.encode()).hexdigest()
        is_busy = int(busy_seed, 16) % 5 == 0
        if not is_busy:
            slots.append(f"{hour}:00")
    return slots


async def day_keyboard(query, exclude_day=None):
    """Экран выбора дня (в т.ч. повторный, без уже выбранного дня)."""
    day_rows = [
        [InlineKeyboardButton(
            day_title(candidate),
            callback_data=f"day|{candidate.isoformat()}")]
        for candidate in day_options() if candidate != exclude_day
    ]
    screen_text = (
        "Выберите другой день:"
        if exclude_day else "📅 Шаг 3 из 4 — удобный день:"
    )
    await query.edit_message_text(
        screen_text, reply_markup=nav_markup(day_rows))


def pretty_text(text):
    """Короткий текст для логов."""
    return (text or "").replace("\n", " / ")[:80]


# ------------------------------------------------------------
# Передача админу: отправка + запоминание «подхвата»
# ------------------------------------------------------------

async def handoff_to_admin(context, chat_id, header, extra, with_hint=True):
    """Отправить админу сообщение и запомнить связь с клиентом."""
    if not ADMIN:
        return None
    admin_text = f"{header}\n\n{extra}".strip()
    sent = await context.bot.send_message(ADMIN, admin_text)
    stats.save_handoff(chat_id, sent.message_id)
    if with_hint:
        await context.bot.send_message(
            ADMIN,
            "↩️ Ответьте клиенту реплаем (ответом) на это сообщение — бот передаст ответ.",  # noqa: E501
        )
    return sent


# ------------------------------------------------------------
# Команды
# ------------------------------------------------------------

async def cmd_start(update, context):
    """/start — очистка сценария и приветственное меню."""
    context.user_data.clear()
    await update.message.reply_text(WELCOME, reply_markup=MENU_MARKUP)


async def cmd_id(update, context):
    """Помогает получить свой chat_id для .env."""
    await update.message.reply_text(
        f"Ваш ID в Telegram: {update.effective_chat.id}\n"
        f"Впишите его в файл .env строкой: "
        f"ADMIN_CHAT_ID={update.effective_chat.id}"
    )


async def cmd_menu(update, context):
    """/menu — снова показать основное меню."""
    await update.message.reply_text("Меню:", reply_markup=MENU_MARKUP)


# ------------------------------------------------------------
# Разделы меню (тексты ответов)
# ------------------------------------------------------------

def services_text():
    """Текст раздела «Услуги» из базы знаний."""
    lines = ["💅 Услуги и цены:\n"]
    for service in kb.SERVICES:
        lines.append(
            f"• {service['name']} — {service['price']} "
            f"({service['duration']})")
        lines.append(f"   {service['about']}")
    lines.append("\nЧтобы записаться — кнопка «📅 Записаться».")
    return "\n".join(lines)


def masters_text():
    """Текст раздела «Мастера» из базы знаний."""
    lines = ["👑 Наши мастера:\n"]
    for master in kb.MASTERS:
        lines.append(f"• {master['name']} — {master['title']}")
        lines.append(f"   {master['about']}")
    return "\n".join(lines)


def promos_text():
    """Текст раздела «Акции» из базы знаний."""
    return "🔥 Действующие акции:\n\n" + "\n\n".join(kb.PROMOS)


# ------------------------------------------------------------
# Основные сообщения: кнопки меню + свободные вопросы
# ------------------------------------------------------------

async def show_menu_again(context, chat_id,
                          note="Меню всегда рядом 👇"):
    """После завершения сценария снова показываем основное меню."""
    await context.bot.send_message(chat_id, note, reply_markup=MENU_MARKUP)


async def start_admin_chat(update, context):
    """Бот ждёт вопрос клиента, чтобы передать его администратору."""
    context.user_data["await_admin_question"] = True
    await update.message.reply_text(ADMIN_PROMPT)


async def answer_from_faq(update, text):
    """Свободный вопрос клиента — отвечаем по базе знаний."""
    answer = find_faq(text)
    if answer:
        await update.message.reply_text(answer)
        return
    await update.message.reply_text(kb.FALLBACK)
    await update.message.reply_text(
        "Или выберите раздел в меню 👇", reply_markup=MENU_MARKUP)


async def menu_router(update, context):
    """Главный роутер: кнопки меню, запрос админа, свободные вопросы."""
    text = update.message.text.strip()
    chat = update.effective_chat
    stats.log_message(chat.id, user_label(update), "in", text)

    # Клиент просил передать админу — следующий текст это его вопрос
    waiting_for_question = context.user_data.get("await_admin_question")
    if waiting_for_question and text not in MENU_TEXTS:
        await admin_question(update, context)
        return

    if "Услуги" in text:
        await update.message.reply_text(services_text())
    elif "Мастера" in text:
        await update.message.reply_text(masters_text())
    elif "Акции" in text:
        await update.message.reply_text(promos_text())
    elif "Контакты" in text:
        await update.message.reply_text(kb.CONTACTS)
    elif "Записаться" in text:
        return await start_booking(update, context)
    elif "администратора" in text or text == "💬 Админ":
        await start_admin_chat(update, context)
    else:
        await answer_from_faq(update, text)


# ------------------------------------------------------------
# Сценарий записи (шаг за шагом)
# ------------------------------------------------------------

async def start_booking(update, context):
    """Шаг 1: выбор услуги."""
    context.user_data.clear()
    # заново закрепляем компактное меню: оно должно быть видно весь диалог
    await update.message.reply_text(
        "📅 Запись. Меню сверху остаётся доступным в любой момент.",
        reply_markup=MENU_MARKUP,
    )
    service_rows = [
        [InlineKeyboardButton(
            f"{service['name']} — {service['price']}",
            callback_data=f"svc|{index}")]
        for index, service in enumerate(kb.SERVICES)
    ]
    await update.message.reply_text(
        "Шаг 1 из 4 — выберите услугу:",
        reply_markup=nav_markup(service_rows),
    )
    return MST


async def pick_service(update, context):
    """Шаг 2: выбор мастера."""
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("|")[1])
    context.user_data["service"] = kb.SERVICES[index]

    master_rows = [
        [InlineKeyboardButton(
            master["name"], callback_data=f"mst|{row_index}")]
        for row_index, master in enumerate(kb.MASTERS)
    ]
    master_rows.append(
        [InlineKeyboardButton(
            "Любой свободный мастер", callback_data="mst|any")])

    chosen = kb.SERVICES[index]
    await query.edit_message_text(
        f"Отлично: {chosen['name']} ({chosen['price']}). "
        "Шаг 2 из 4 — мастер:",
        reply_markup=nav_markup(master_rows),
    )
    return MST


async def pick_master(update, context):
    """Шаг 3: выбор дня записи."""
    query = update.callback_query
    await query.answer()
    if query.data.endswith("any"):
        context.user_data["master"] = "любой свободный мастер"
    else:
        master_index = int(query.data.split("|")[1])
        context.user_data["master"] = kb.MASTERS[master_index]["name"]
    await day_keyboard(query)
    return DAY


async def pick_day(update, context):
    """Шаг 4: выбор времени из свободных окон."""
    query = update.callback_query
    await query.answer()
    chosen_day = date.fromisoformat(query.data.split("|")[1])
    context.user_data["day"] = chosen_day

    available_slots = free_slots(chosen_day)
    if not available_slots:
        await query.edit_message_text(
            f"На {day_caption(chosen_day)} свободных окон нет.")
        await day_keyboard(query, chosen_day)
        return DAY

    slot_rows = [
        [InlineKeyboardButton(slot, callback_data=f"time|{slot}")]
        for slot in available_slots
    ]
    await query.edit_message_text(
        f"{day_caption(chosen_day)} — свободные окна:",
        reply_markup=nav_markup(slot_rows),
    )
    return TIME


async def pick_time(update, context):
    """Финальный экран: сводка заявки на проверку."""
    query = update.callback_query
    await query.answer()
    context.user_data["time"] = query.data.split("|")[1]
    booked = context.user_data

    await query.edit_message_text(
        "Шаг 4 из 4 — проверьте заявку:\n\n"
        f"💅 Услуга: {booked['service']['name']} "
        f"({booked['service']['price']})\n"
        f"👑 Мастер: {booked['master']}\n"
        f"🗓 {day_caption(booked['day'])}, {booked['time']}\n\nВсё верно?",
        reply_markup=nav_markup([
            [InlineKeyboardButton(
                "✅ Подтверждаю", callback_data="confirm|yes")],
            [InlineKeyboardButton("❌ Отменить", callback_data="confirm|no")],
        ]),
    )
    return CONFIRM


async def confirm(update, context):
    """Заявку подтверждаем (отправляем админу) или отменяем."""
    query = update.callback_query
    await query.answer()
    booked = context.user_data
    chat_id = update.effective_chat.id

    if query.data.endswith("no"):
        booked.clear()
        await query.edit_message_text(
            "Заявку отменил. Когда будете готовы — «📅 Записаться».")
        await show_menu_again(context, chat_id)
        return ConversationHandler.END

    details = (
        f"👤 Клиент: {user_label(update)}\n"
        f"💅 Услуга: {booked['service']['name']} — "
        f"{booked['service']['price']}\n"
        f"👑 Мастер: {booked['master']}\n"
        f"🗓 Когда: {day_caption(booked['day'])}, {booked['time']}"
    )
    sent = await handoff_to_admin(
        context, chat_id, "🗓 НОВАЯ ЗАЯВКА НА ЗАПИСЬ", details, with_hint=False)

    if sent:
        await query.edit_message_text(
            "✅ Заявка отправлена администратору!\n"
            "В ближайшее время подтвердим запись прямо здесь. "
            "Можете закрыть чат — мы напишем сами."
        )
    else:
        await query.edit_message_text(
            "⚠️ Администратор пока не подключён к боту "
            "(не задан ADMIN_CHAT_ID в .env). "
            "Позвоните нам: +7 (999) 000-00-00"
        )
    booked.clear()
    await show_menu_again(context, chat_id)
    return ConversationHandler.END


async def booking_cancel(update, context):
    """Слово «отмена» в любом месте — выходим из сценария."""
    context.user_data.clear()
    await update.message.reply_text(
        "Хорошо, ничего не записал. Меню всегда рядом 👇",
        reply_markup=MENU_MARKUP,
    )
    return ConversationHandler.END


async def booking_nav(update, context):
    """Кнопки «Позвать администратора» / «Отмена» на любом шаге записи."""
    query = update.callback_query
    await query.answer()
    action = query.data.split("|")[1]
    chat_id = update.effective_chat.id
    context.user_data.clear()

    if action == "cancel":
        await query.edit_message_text(
            "Запись отменена. Когда будете готовы — «📅 Записаться».")
        await show_menu_again(context, chat_id)
        return ConversationHandler.END

    # action == "admin": просим вопрос, дальше его поймает обычный поток
    context.user_data["await_admin_question"] = True
    await query.edit_message_text(ADMIN_PROMPT)
    return ConversationHandler.END


# ------------------------------------------------------------
# «Позвать администратора» и ответы админа
# ------------------------------------------------------------

async def admin_question(update, context):
    """Клиент описал вопрос — отправляем админу с историей диалога."""
    context.user_data.pop("await_admin_question", None)
    chat_id = update.effective_chat.id
    history = stats.dialog_history(chat_id)
    history_text = "\n".join(
        f"{'клиент' if direction == 'in' else 'бот'}: "
        f"{pretty_text(message_text)}"
        for direction, message_text, _ in history
    )
    question_details = (
        f"👤 {user_label(update)}\n\n"
        f"❓ Вопрос:\n{update.message.text}\n\n"
        f"📖 Последний диалог:\n{history_text}"
    )
    await handoff_to_admin(
        context, chat_id, "💬 ВОПРОС КЛИЕНТА", question_details)
    await update.message.reply_text(
        "📨 Вопрос передан администратору. Ответ придёт в этот чат — "
        "студия работает ежедневно 9:00–00:00, обычно отвечаем быстрее."
    )


async def admin_reply(update, context):
    """Реплай админа пересылаем клиенту, к которому относится сообщение."""
    chat = update.effective_chat
    if not ADMIN or chat.id != ADMIN:
        return
    if not (update.message and update.message.reply_to_message):
        return  # обычное сообщение админа — игнорируем, чтобы не мешать

    client_chat_id = stats.find_client_by_admin_msg(
        update.message.reply_to_message.message_id)
    if not client_chat_id:
        await update.message.reply_text(
            "Не нашёл клиента для этого сообщения. "
            "Отвечайте реплаем на пересланное сообщение."
        )
        return
    await context.bot.send_message(
        client_chat_id, f"👤 Администратор: {update.message.text}")
    await update.message.reply_text("✅ Ответ отправлен клиенту.")


# ------------------------------------------------------------
# Запуск
# ------------------------------------------------------------

def build_booking_handler():
    """Сценарий записи: шаги, кнопки и выходы из диалога."""
    booking_step_nav = CallbackQueryHandler(booking_nav, pattern=r"^book\|")
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & filters.Regex("Записаться"), start_booking),
        ],
        states={
            MST: [CallbackQueryHandler(pick_service, pattern=r"^svc\|"),
                  CallbackQueryHandler(pick_master, pattern=r"^mst\|"),
                  booking_step_nav],
            DAY: [CallbackQueryHandler(pick_day, pattern=r"^day\|"),
                  booking_step_nav],
            TIME: [CallbackQueryHandler(pick_time, pattern=r"^time\|"),
                   booking_step_nav],
            CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^confirm\|"),
                      booking_step_nav],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(r"(?i)отмен"), booking_cancel),
        ],
        allow_reentry=True,
    )


def register_handlers(app):
    """Подключаем обработчики в нужном порядке (порядок важен)."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(build_booking_handler())
    # Ответы админа — отдельная группа, чтобы не мешать обычным сообщениям
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply), group=1)
    # Основной поток — последним
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))


def build_error_handler():
    """Сообщаем клиенту, что что-то пошло не так, а не молчим."""

    async def log_error(update, context):
        log.error("Ошибка обработки: %s", context.error)
        if update and update.effective_chat:
            try:
                await context.bot.send_message(
                    update.effective_chat.id,
                    "Произошла ошибка 🙈 Попробуйте ещё раз "
                    "или начните с /start",
                )
            except Exception:
                pass

    return log_error


def main():
    """Собираем бота и запускаем опрос Telegram."""
    stats.init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    app.add_error_handler(build_error_handler())

    log.info("Бот запущен. Для остановки: Ctrl+C (или закрыть окно)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
