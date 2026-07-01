import json
import re
from datetime import datetime, timedelta, time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ===================== ТВОИ ДАННЫЕ =====================
TOKEN = "8874085340:AAHSnjJ0J7unQq6DxQ-oiXnLKLYC1iZDCn0"
MY_USER_ID = 2127291890
# =======================================================

DATA_FILE = Path("data.json")

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "start_date": None,
        "reminder_time": None,
        "cycle_day": 0,
        "last_update_date": datetime.now().date().isoformat(),
        "today_taken": False,
        "reminders_sent_today": 0,
        "paused": False,          # флаг паузы
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def parse_datetime(text):
    patterns = [
        r'(\d{2})[./](\d{2})[./](\d{4})\s+(\d{2}):(\d{2})',
        r'(\d{4})[./](\d{2})[./](\d{2})\s+(\d{2}):(\d{2})',
    ]
    for pat in patterns:
        m = re.match(pat, text)
        if m:
            groups = m.groups()
            if len(groups) == 5:
                if len(groups[0]) == 4:
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                else:
                    day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                hour, minute = int(groups[3]), int(groups[4])
                try:
                    dt = datetime(year, month, day, hour, minute)
                    return dt.date(), dt.time()
                except ValueError:
                    return None
    return None

def update_cycle_day(data):
    """Обновляет день цикла и управляет паузой"""
    if data["start_date"] is None:
        return data, None

    today = datetime.now().date()
    start = datetime.fromisoformat(data["start_date"]).date()
    if today < start:
        data["cycle_day"] = 0
        data["paused"] = False
        return data, None

    days_passed = (today - start).days
    cycle_day = (days_passed % 28) + 1  # 1..28
    old_day = data.get("cycle_day", 0)

    notification = None

    # Проверяем переходы
    if old_day <= 21 and cycle_day == 22:
        notification = "⏸ Начинается 7-дневный перерыв. Напоминания отключены."
        data["paused"] = True
    elif old_day <= 28 and cycle_day == 1 and old_day != 0:
        notification = "🔄 Начинаем новый цикл приёма! 21 день таблеток."
        data["paused"] = False

    # Если только что задали start_date и сейчас уже перерыв
    if old_day == 0 and cycle_day > 21:
        data["paused"] = True
    elif old_day == 0 and cycle_day <= 21:
        data["paused"] = False

    data["cycle_day"] = cycle_day
    data["last_update_date"] = today.isoformat()
    data["today_taken"] = False
    data["reminders_sent_today"] = 0
    return data, notification

async def send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_data()
    # Если пауза — ничего не делаем
    if data.get("paused", False):
        return
    if data["cycle_day"] == 0 or data["cycle_day"] > 21:
        return
    if data["today_taken"]:
        return
    if data["reminders_sent_today"] >= 3:
        return

    data["reminders_sent_today"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("💊 Приняла", callback_data="take")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    day = data["cycle_day"]
    text = f"🔔 День цикла {day}/28. Не забудь принять таблетку!"
    await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)

    if data["reminders_sent_today"] < 3:
        run_time = datetime.now() + timedelta(hours=1)
        context.job_queue.run_once(
            send_reminder_job,
            run_time,
            user_id=user_id,
            name=f"reminder_{user_id}_{run_time.timestamp()}"
        )

async def send_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data
    await send_reminder(context, user_id)

async def daily_trigger(context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает каждый день в установленное время"""
    data = load_data()
    if data["start_date"] is None or data["reminder_time"] is None:
        return

    data, notification = update_cycle_day(data)
    save_data(data)

    if notification:
        await context.bot.send_message(chat_id=MY_USER_ID, text=notification)

    # Если пауза — не отправляем напоминание
    if data.get("paused", False):
        return

    if data["cycle_day"] != 0 and data["cycle_day"] <= 21 and not data["today_taken"]:
        await send_reminder(context, MY_USER_ID)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id != MY_USER_ID:
        await query.edit_message_text("Этот бот только для одного пользователя.")
        return

    data = load_data()
    if data.get("paused", False):
        await query.edit_message_text("Сейчас перерыв, таблетки не принимаются.")
        return

    if query.data == "take":
        if data["today_taken"]:
            await query.edit_message_text("Ты уже приняла сегодня 😊")
            return

        data["today_taken"] = True
        save_data(data)

        for job in context.job_queue.jobs():
            if job.name and job.name.startswith(f"reminder_{user_id}"):
                job.schedule_removal()

        await query.edit_message_text("✅ Вы приняли таблетку сегодня")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if data["start_date"] is None or data["reminder_time"] is None:
        await update.message.reply_text(
            "👋 Привет! Я буду напоминать о таблетках.\n"
            "Сначала настрой меня: отправь команду\n"
            "`/setstart 08.07.2026 09:00`\n"
            "где дата – первый день приёма, время – когда напоминать (МСК).",
            parse_mode="Markdown"
        )
    else:
        status_text = "активен" if not data.get("paused", False) else "в режиме перерыва (напоминания отключены)"
        await update.message.reply_text(
            f"Бот настроен. Старт: {data['start_date']}, время: {data['reminder_time']}.\n"
            f"Статус: {status_text}\n"
            "Команда /status – показать текущий день.\n"
            "Чтобы изменить настройки – /setstart"
        )

async def setstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != MY_USER_ID:
        await update.message.reply_text("Недоступно.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Укажи дату и время в формате:\n"
            "`/setstart 08.07.2026 09:00`\n"
            "или `/setstart 2026-07-08 09:00`",
            parse_mode="Markdown"
        )
        return

    full_text = " ".join(args)
    parsed = parse_datetime(full_text)
    if parsed is None:
        await update.message.reply_text(
            "❌ Не понял формат. Используй `08.07.2026 09:00` или `2026-07-08 09:00`.",
            parse_mode="Markdown"
        )
        return

    start_date, reminder_time = parsed
    data = load_data()
    data["start_date"] = start_date.isoformat()
    data["reminder_time"] = reminder_time.strftime("%H:%M")
    # Пересчитываем день цикла и паузу
    data, _ = update_cycle_day(data)
    save_data(data)

    # Перепланируем ежедневную задачу
    jobs = context.job_queue.jobs()
    for job in jobs:
        if job.name == "daily_reminder":
            job.schedule_removal()

    hour, minute = map(int, reminder_time.strftime("%H:%M").split(":"))
    new_time = time(hour=hour, minute=minute)
    context.job_queue.run_daily(daily_trigger, new_time, days=tuple(range(7)), name="daily_reminder")

    paused_status = "включены" if not data.get("paused", False) else "отключены (перерыв)"
    await update.message.reply_text(
        f"✅ Настройки сохранены!\n"
        f"Начало цикла: {start_date.strftime('%d.%m.%Y')}\n"
        f"Время напоминания: {reminder_time.strftime('%H:%M')} МСК\n"
        f"Текущий день цикла: {data['cycle_day']}\n"
        f"Напоминания: {paused_status}"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != MY_USER_ID:
        await update.message.reply_text("Недоступно.")
        return
    data = load_data()
    day = data["cycle_day"]
    if day == 0:
        phase = "ожидание старта или настройки"
    else:
        phase = "приём" if day <= 21 else "перерыв"
    paused = data.get("paused", False)
    taken = "✅ Да" if data["today_taken"] else "❌ Нет"
    sent = data["reminders_sent_today"]
    text = (
        f"📊 Текущий день цикла: {day}/28\n"
        f"Фаза: {phase}\n"
        f"Пауза: {'🟢 выкл' if not paused else '🔴 вкл (напоминания отключены)'}\n"
        f"Сегодня принято: {taken}\n"
        f"Отправлено напоминаний сегодня: {sent}\n"
        f"Начало цикла: {data.get('start_date', 'не задано')}\n"
        f"Время напоминания: {data.get('reminder_time', 'не задано')}"
    )
    await update.message.reply_text(text)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setstart", setstart))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # При запуске загружаем настройки и создаём ежедневное задание
    data = load_data()
    if data.get("reminder_time") and data.get("start_date"):
        hour, minute = map(int, data["reminder_time"].split(":"))
        reminder_time = time(hour=hour, minute=minute)
        app.job_queue.run_daily(daily_trigger, reminder_time, days=tuple(range(7)), name="daily_reminder")

    print("Бот запущен. Ожидание команд...")
    app.run_polling()

if __name__ == "__main__":
    main()