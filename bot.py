# -*- coding: utf-8 -*-
# ============================================================
# MNE_MANIK — телеграм-бот студии маникюра (MVP, демо-данные)
#
# Функции:
#   1. Меню: услуги и цены, мастера, акции, контакты
#   2. Ответы на частые вопросы (по базе знаний knowledge.py)
#   3. Запись на услугу: услуга → мастер → день → время → заявка админу
#   4. «Позвать администратора»: вопрос клиента уходит живому человеку,
#      админ отвечает реплаем на пересланное сообщение — ответ возвращается клиенту
#
# Запуск:  python3 bot.py   (или двойным кликом по run.command)
# Настройки: файл .env рядом с ботом (BOT_TOKEN, ADMIN_CHAT_ID) — см. README.md
# ============================================================

import hashlib
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
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
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO,
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
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
MENU_TEXTS = {t for row in MENU_ROWS for t in row}
MENU_MARKUP = ReplyKeyboardMarkup(
    [[KeyboardButton(t) for t in row] for row in MENU_ROWS],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие…",
)

# Состояния сценария записи
(MST, DAY, TIME, CONFIRM) = range(4)

# Навигация внутри записи: доступна на каждом шаге
NAV_ROW = [
    InlineKeyboardButton("💬 Позвать администратора", callback_data="book|admin"),
    InlineKeyboardButton("❌ Отмена", callback_data="book|cancel"),
]


def nav_markup(rows):
    """Кнопки шага + всегда доступные «администратор/отмена»."""
    return InlineKeyboardMarkup(rows + [NAV_ROW])

# ------------------------------------------------------------
# Вспомогательное
# ------------------------------------------------------------

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def user_label(update):
    """Как называть клиента в сообщениях админу."""
    u = update.effective_user
    if u is None:
        return "клиент"
    handle = f"@{u.username}" if u.username else f"id {u.id}"
    return f"{u.full_name} ({handle})"


def find_faq(text):
    """Ищем лучший ответ по ключевым словам. Возврат: текст ответа или None."""
    low = text.lower()
    best, best_score = None, 0
    for item in kb.FAQ:
        score = sum(1 for kw in item["keywords"] if kw in low)
        if score > best_score:
            best, best_score = item["answer"], score
    return best


def day_options():
    """Демо-даты: сегодня, завтра, послезавтра, +3 дня."""
    today = date.today()
    return [today + timedelta(days=i) for i in range(4)]


def day_title(d):
    diff = (d - date.today()).days
    return {0: "Сегодня", 1: "Завтра", 2: "Послезавтра"}.get(
        diff, f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"
    )


def day_caption(d):
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def free_slots(d):
    """Демо-расписание: слоты 9:00–23:00, ~20% «занято» (стабильно для одной даты)."""
    slots = []
    now_hour = datetime.now().hour
    for hour in range(9, 24):
        if d == date.today() and hour <= now_hour:
            continue  # прошедшие часы не предлагаем
        digest = hashlib.md5(f"{d.isoformat()}-{hour}".encode()).hexdigest()
        busy = int(digest, 16) % 5 == 0
        if not busy:
            slots.append(f"{hour}:00")
    return slots


async def day_keyboard(q, d=None):
    rows = [[InlineKeyboardButton(day_title(x), callback_data=f"day|{x.isoformat()}")]
            for x in day_options() if x != d]
    await q.edit_message_text(
        "Выберите другой день:" if d else "📅 Шаг 3 из 4 — удобный день:",
        reply_markup=nav_markup(rows),
    )


def pretty_text(text):
    """Короткий текст для логов."""
    return (text or "").replace("\n", " / ")[:80]

# ------------------------------------------------------------
# Передача админу: отправка + запоминание «подхвата»
# ------------------------------------------------------------

async def handoff_to_admin(context, chat_id, header, extra, with_hint=True):
    """Отправить админу сообщение и запомнить, к какому клиенту оно относится."""
    if not ADMIN:
        return None
    msg = await context.bot.send_message(ADMIN, f"{header}\n\n{extra}".strip())
    stats.save_handoff(chat_id, msg.message_id)
    if with_hint:
        await context.bot.send_message(
            ADMIN, "↩️ Ответьте клиенту реплаем (ответом) на это сообщение — бот передаст ответ."
        )
    return msg

# ------------------------------------------------------------
# Команды
# ------------------------------------------------------------

async def cmd_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(WELCOME, reply_markup=MENU_MARKUP)


async def cmd_id(update, context):
    """Помогает получить свой chat_id для .env."""
    await update.message.reply_text(
        f"Ваш ID в Telegram: {update.effective_chat.id}\n"
        f"Впишите его в файл .env строкой: ADMIN_CHAT_ID={update.effective_chat.id}"
    )


async def cmd_menu(update, context):
    await update.message.reply_text("Меню:", reply_markup=MENU_MARKUP)

# ------------------------------------------------------------
# Основные сообщения: кнопки меню + свободные вопросы
# ------------------------------------------------------------

async def menu_router(update, context):
    text = update.message.text.strip()
    chat = update.effective_chat
    stats.log_message(chat.id, user_label(update), "in", text)

    # Клиент ранее нажал «Позвать администратора» — следующий текст это его вопрос
    if context.user_data.get("await_admin_question") and text not in MENU_TEXTS:
        await admin_question(update, context)
        return

    if "Услуги" in text:
        lines = ["💅 Услуги и цены:\n"]
        for s in kb.SERVICES:
            lines.append(f"• {s['name']} — {s['price']} ({s['duration']})")
            lines.append(f"   {s['about']}")
        lines.append("\nЧтобы записаться — кнопка «📅 Записаться».")
        await update.message.reply_text("\n".join(lines))

    elif "Мастера" in text:
        lines = ["👑 Наши мастера:\n"]
        for m in kb.MASTERS:
            lines.append(f"• {m['name']} — {m['title']}")
            lines.append(f"   {m['about']}")
        await update.message.reply_text("\n".join(lines))

    elif "Акции" in text:
        await update.message.reply_text("🔥 Действующие акции:\n\n" + "\n\n".join(kb.PROMOS))

    elif "Контакты" in text:
        await update.message.reply_text(kb.CONTACTS)

    elif "Записаться" in text:
        return await start_booking(update, context)

    elif "администратора" in text or text == "💬 Админ":
        context.user_data["await_admin_question"] = True
        await update.message.reply_text(
            "Хорошо, соединяю с администратором 👤\n"
            "Напишите ваш вопрос одним сообщением — я передам его вместе с историей диалога, "
            "и администратор ответит вам прямо здесь."
        )

    else:
        # Свободный текст — отвечаем по базе знаний
        answer = find_faq(text)
        if answer:
            await update.message.reply_text(answer)
        else:
            await update.message.reply_text(kb.FALLBACK)
            await update.message.reply_text("Или выберите раздел в меню 👇", reply_markup=MENU_MARKUP)

# ------------------------------------------------------------
# Сценарий записи (шаг за шагом)
# ------------------------------------------------------------

async def start_booking(update, context):
    context.user_data.clear()
    # заново закрепляем компактное меню: оно должно быть видно весь диалог
    await update.message.reply_text(
        "📅 Запись. Меню сверху остаётся доступным в любой момент.",
        reply_markup=MENU_MARKUP,
    )
    rows = [
        [InlineKeyboardButton(f"{s['name']} — {s['price']}", callback_data=f"svc|{i}")]
        for i, s in enumerate(kb.SERVICES)
    ]
    await update.message.reply_text(
        "Шаг 1 из 4 — выберите услугу:",
        reply_markup=nav_markup(rows),
    )
    return MST


async def pick_service(update, context):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("|")[1])
    context.user_data["service"] = kb.SERVICES[idx]

    rows = [[InlineKeyboardButton(m["name"], callback_data=f"mst|{i}")]
            for i, m in enumerate(kb.MASTERS)]
    rows.append([InlineKeyboardButton("Любой свободный мастер", callback_data="mst|any")])
    await q.edit_message_text(
        f"Отлично: {kb.SERVICES[idx]['name']} ({kb.SERVICES[idx]['price']}). "
        "Шаг 2 из 4 — мастер:",
        reply_markup=nav_markup(rows),
    )
    return MST


async def pick_master(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["master"] = (
        "любой свободный мастер" if q.data.endswith("any")
        else kb.MASTERS[int(q.data.split("|")[1])]["name"]
    )
    await day_keyboard(q)
    return DAY


async def pick_day(update, context):
    q = update.callback_query
    await q.answer()
    d = date.fromisoformat(q.data.split("|")[1])
    context.user_data["day"] = d

    free = free_slots(d)
    if not free:
        await q.edit_message_text(f"На {day_caption(d)} свободных окон нет.")
        await day_keyboard(q, d)
        return DAY

    rows = [[InlineKeyboardButton(s, callback_data=f"time|{s}")] for s in free]
    await q.edit_message_text(
        f"{day_caption(d)} — свободные окна:",
        reply_markup=nav_markup(rows),
    )
    return TIME


async def pick_time(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["time"] = q.data.split("|")[1]
    ud = context.user_data

    await q.edit_message_text(
        "Шаг 4 из 4 — проверьте заявку:\n\n"
        f"💅 Услуга: {ud['service']['name']} ({ud['service']['price']})\n"
        f"👑 Мастер: {ud['master']}\n"
        f"🗓 {day_caption(ud['day'])}, {ud['time']}\n\nВсё верно?",
        reply_markup=nav_markup([
            [InlineKeyboardButton("✅ Подтверждаю", callback_data="confirm|yes")],
            [InlineKeyboardButton("❌ Отменить", callback_data="confirm|no")],
        ]),
    )
    return CONFIRM


async def confirm(update, context):
    q = update.callback_query
    await q.answer()
    ud = context.user_data

    if q.data.endswith("no"):
        ud.clear()
        await q.edit_message_text("Заявку отменил. Когда будете готовы — «📅 Записаться».")
        await context.bot.send_message(
            update.effective_chat.id, "Меню всегда рядом 👇", reply_markup=MENU_MARKUP
        )
        return ConversationHandler.END

    details = (
        f"👤 Клиент: {user_label(update)}\n"
        f"💅 Услуга: {ud['service']['name']} — {ud['service']['price']}\n"
        f"👑 Мастер: {ud['master']}\n"
        f"🗓 Когда: {day_caption(ud['day'])}, {ud['time']}"
    )
    sent = await handoff_to_admin(
        context, update.effective_chat.id, "🗓 НОВАЯ ЗАЯВКА НА ЗАПИСЬ", details, with_hint=False
    )

    if sent:
        await q.edit_message_text(
            "✅ Заявка отправлена администратору!\n"
            "В ближайшее время подтвердим запись прямо здесь. "
            "Можете закрыть чат — мы напишем сами."
        )
    else:
        await q.edit_message_text(
            "⚠️ Администратор пока не подключён к боту (не задан ADMIN_CHAT_ID в .env). "
            "Позвоните нам: +7 (999) 000-00-00"
        )
    ud.clear()
    await context.bot.send_message(
        update.effective_chat.id, "Меню всегда рядом 👇", reply_markup=MENU_MARKUP
    )
    return ConversationHandler.END


async def booking_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Хорошо, ничего не записал. Меню всегда рядом 👇",
                                    reply_markup=MENU_MARKUP)
    return ConversationHandler.END


async def booking_nav(update, context):
    """Кнопки «Позвать администратора» / «Отмена» на любом шаге записи."""
    q = update.callback_query
    await q.answer()
    action = q.data.split("|")[1]
    context.user_data.clear()

    if action == "cancel":
        await q.edit_message_text("Запись отменена. Когда будете готовы — «📅 Записаться».")
        await context.bot.send_message(
            update.effective_chat.id, "Меню всегда рядом 👇", reply_markup=MENU_MARKUP
        )
        return ConversationHandler.END

    # action == "admin": просим вопрос, дальше его поймает обычный поток
    context.user_data["await_admin_question"] = True
    await q.edit_message_text(
        "Хорошо, соединяю с администратором 👤\n"
        "Напишите ваш вопрос одним сообщением — я передам его вместе с историей диалога, "
        "и администратор ответит вам прямо здесь."
    )
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
        f"{'клиент' if d == 'in' else 'бот'}: {pretty_text(t)}" for d, t, _ in history
    )
    await handoff_to_admin(
        context, chat_id, "💬 ВОПРОС КЛИЕНТА",
        f"👤 {user_label(update)}\n\n❓ Вопрос:\n{update.message.text}\n\n"
        f"📖 Последний диалог:\n{history_text}",
    )
    await update.message.reply_text(
        "📨 Вопрос передан администратору. Ответ придёт в этот чат — студия работает "
        "ежедневно 9:00–00:00, обычно отвечаем быстрее."
    )


async def admin_reply(update, context):
    """Админ отвечает реплаем на пересланное сообщение — бот пересылает клиенту."""
    chat = update.effective_chat
    if not ADMIN or chat.id != ADMIN:
        return
    if not (update.message and update.message.reply_to_message):
        return  # обычное сообщение админа — игнорируем, чтобы не мешать

    client_id = stats.find_client_by_admin_msg(update.message.reply_to_message.message_id)
    if not client_id:
        await update.message.reply_text(
            "Не нашёл клиента для этого сообщения. Отвечайте реплаем на пересланное сообщение."
        )
        return
    await context.bot.send_message(client_id, f"👤 Администратор: {update.message.text}")
    await update.message.reply_text("✅ Ответ отправлен клиенту.")

# ------------------------------------------------------------
# Запуск
# ------------------------------------------------------------

def main():
    stats.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    booking = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("Записаться"), start_booking)],
        states={
            MST: [CallbackQueryHandler(pick_service, pattern=r"^svc\|"),
                  CallbackQueryHandler(pick_master, pattern=r"^mst\|"),
                  CallbackQueryHandler(booking_nav, pattern=r"^book\|")],
            DAY: [CallbackQueryHandler(pick_day, pattern=r"^day\|"),
                  CallbackQueryHandler(booking_nav, pattern=r"^book\|")],
            TIME: [CallbackQueryHandler(pick_time, pattern=r"^time\|"),
                   CallbackQueryHandler(booking_nav, pattern=r"^book\|")],
            CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^confirm\|"),
                      CallbackQueryHandler(booking_nav, pattern=r"^book\|")],
        },
        fallbacks=[CommandHandler("start", cmd_start),
                   MessageHandler(filters.Regex(r"(?i)отмен"), booking_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(booking)
    # Ответы админа — отдельная группа, чтобы не мешать обычным сообщениям
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply), group=1)
    # Основной поток — последним
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    async def log_error(update, context):
        log.error("Ошибка обработки: %s", context.error)
        # сообщаем клиенту, что что-то пошло не так, а не молчим
        if update and update.effective_chat:
            try:
                await context.bot.send_message(
                    update.effective_chat.id,
                    "Произошла ошибка 🙈 Попробуйте ещё раз или начните с /start",
                )
            except Exception:
                pass

    app.add_error_handler(log_error)

    log.info("Бот запущен. Для остановки: Ctrl+C (или закрыть окно)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()