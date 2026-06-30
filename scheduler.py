import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import TIMEZONE, REMINDER_HOUR, REMINDER_MINUTE, CONTROL_HOUR, CONTROL_MINUTE, CHAT_ID
from database import (
    get_tasks_for_reminder,
    get_pending_tasks,
    mark_reminder_sent,
    increment_control_count,
    get_custom_tasks_to_notify,
    mark_task_done,
    set_last_message_id,
)
from messages import reminder_text, control_text_1, control_text_repeat, horse_reminder_text
from utils import get_next_saturday

logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)


async def send_tracked(bot, task: dict, text: str, reply_markup=None):
    """Отправить сообщение по задаче, удалив предыдущее напоминание по ней же — чтобы чат не засорялся."""
    last_id = task.get("last_message_id")
    if last_id:
        try:
            await bot.delete_message(CHAT_ID, last_id)
        except Exception as e:
            logger.warning(f"Could not delete previous message {last_id} for task {task['id']}: {e}")

    msg = await bot.send_message(CHAT_ID, text, reply_markup=reply_markup)
    set_last_message_id(task["id"], msg.message_id)
    return msg


async def send_friday_reminders(bot):
    """Пятница 19:00 — напоминание о предстоящей уборке."""
    saturday = get_next_saturday()
    saturday_str = saturday.strftime("%Y-%m-%d")

    tasks = get_tasks_for_reminder(saturday_str)

    for task in tasks:
        try:
            text = reminder_text(task["username"], task["task_type"], task["place"])
            await send_tracked(bot, task, text)
            mark_reminder_sent(task["id"])
            logger.info(f"Reminder sent for {task['username']}, date {saturday_str}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


async def send_daily_controls(bot):
    """Каждый день в 20:00 начиная с субботы — контроль выполнения."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    today = date.today()
    pending = get_pending_tasks()

    for task in pending:
        task_date = date.fromisoformat(task["task_date"])

        # Контроль начинается с субботы
        if today < task_date:
            continue

        # Конь — без контроля
        if task["task_type"] == "horse":
            continue

        # Ожидает подтверждения — не беспокоить
        if task["status"] == "waiting_confirm":
            continue

        try:
            count = task["control_count"]
            username = task["username"]
            task_type = task["task_type"]
            place = task["place"]

            if count == 0:
                text = control_text_1(username, task_type, place)
            else:
                text = control_text_repeat(username)

            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Я сделалъ",
                    callback_data=f"done_{task['id']}"
                )
            ]])

            await send_tracked(bot, task, text, reply_markup=kb)
            increment_control_count(task["id"])
            logger.info(f"Control #{count+1} sent for {username}, task {task['id']}")

        except Exception as e:
            logger.error(f"Failed to send control: {e}")


async def send_horse_reminders(bot):
    """1-го и 15-го числа — напоминание о мытье коня."""
    today = date.today()
    if today.day not in (1, 15):
        return

    pending = get_pending_tasks()

    for task in pending:
        if task["task_type"] != "horse":
            continue

        task_date = date.fromisoformat(task["task_date"])
        if task_date.year != today.year or task_date.month != today.month:
            continue

        try:
            text = horse_reminder_text(task["username"])
            await send_tracked(bot, task, text)
            logger.info(f"Horse reminder sent for {task['username']}")
        except Exception as e:
            logger.error(f"Failed to send horse reminder: {e}")


async def send_custom_event_notifications(bot):
    """Каждую минуту — проверяем кастомные события."""
    from datetime import datetime
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    tasks = get_custom_tasks_to_notify(now)

    from config import CHAT_ID
    for task in tasks:
        try:
            name = task["name"]
            username = task["username"]
            desc = task.get("description") or ""
            text = f"📢 @{username} напоминание: {desc}" if desc else f"📢 @{username} запланированное событие!"
            await bot.send_message(CHAT_ID, text)
            mark_task_done(task["id"])
            logger.info(f"Custom event sent for {username}, task {task['id']}")
        except Exception as e:
            logger.error(f"Failed to send custom event: {e}")


def start_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=tz)

    # Пятница 19:00 — напоминание об уборке
    scheduler.add_job(
        send_friday_reminders,
        CronTrigger(day_of_week="fri", hour=REMINDER_HOUR, minute=REMINDER_MINUTE, timezone=tz),
        args=[bot],
        id="friday_reminders",
        replace_existing=True,
    )

    # Каждый день 20:00 — контроль
    scheduler.add_job(
        send_daily_controls,
        CronTrigger(hour=CONTROL_HOUR, minute=CONTROL_MINUTE, timezone=tz),
        args=[bot],
        id="daily_controls",
        replace_existing=True,
    )

    # 1-го и 15-го числа в 19:00 — конь
    scheduler.add_job(
        send_horse_reminders,
        CronTrigger(day="1,15", hour=REMINDER_HOUR, minute=REMINDER_MINUTE, timezone=tz),
        args=[bot],
        id="horse_reminders",
        replace_existing=True,
    )

    # Каждую минуту — кастомные события
    scheduler.add_job(
        send_custom_event_notifications,
        CronTrigger(minute="*", timezone=tz),
        args=[bot],
        id="custom_events",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started")
    return scheduler
