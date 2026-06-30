import asyncio
import logging
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import BasicAuth
from config import BOT_TOKEN, ADMIN_ID, HOST, PORT, LOGIN, PASSWORD, NEED_AUTH
from database import (
    init_db,
    add_member, update_member, deactivate_member,
    get_all_members, get_member,
    add_task, get_task, mark_task_done, delete_task,
    get_tasks_by_member, get_pending_tasks,
    add_event_type, get_all_event_types, delete_event_type,
    clear_month_cleaning_tasks,
    get_custom_tasks_to_notify,
    set_task_waiting_confirm, set_task_pending,
)
from messages import TASK_NAMES, PLACE_NAMES, get_task_name
from scheduler import start_scheduler

# Прокси нужен только если задан PROXY_HOST в .env. Если пусто — подключаемся
# к Telegram напрямую, без прокси и без отключения проверки SSL.
if HOST:
    PROXY_URL = f"http://{HOST}:{PORT}"
    auth = BasicAuth(login=LOGIN, password=PASSWORD)

    if NEED_AUTH:
        session = AiohttpSession(proxy=(PROXY_URL, auth))
    else:
        session = AiohttpSession(proxy=(PROXY_URL))

    # Отключаем проверку SSL-сертификата на коннекторе именно этой сессии
    session._connector_init["ssl"] = False
else:
    session = AiohttpSession()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# =========================
# FSM STATES
# =========================

class AddMember(StatesGroup):
    waiting_tg_id = State()
    waiting_username = State()
    waiting_name = State()


class EditMember(StatesGroup):
    waiting_username = State()
    waiting_name = State()


class AddTask(StatesGroup):
    waiting_member = State()
    waiting_task_type = State()
    waiting_place = State()
    waiting_year = State()
    waiting_month = State()


class AddEventType(StatesGroup):
    waiting_name = State()


class AddCustomEvent(StatesGroup):
    waiting_member = State()
    waiting_description = State()
    waiting_date = State()
    waiting_time = State()


# =========================
# HELPERS
# =========================

def admin_only(func):
    """Декоратор: только для админа."""
    async def wrapper(event, *args, **kwargs):
        user_id = event.from_user.id if hasattr(event, "from_user") else None
        if user_id != ADMIN_ID:
            if hasattr(event, "answer"):
                await event.answer("⛔ Нет доступа")
            return
        return await func(event, *args, **kwargs)
    return wrapper


def get_saturdays_of_month(year: int, month: int) -> list:
    """Все субботы месяца."""
    saturdays = []
    d = date(year, month, 1)
    while d.month == month:
        if d.weekday() == 5:  # суббота
            saturdays.append(d)
        d += timedelta(days=1)
    return saturdays


def is_first_weekend(saturday: date) -> bool:
    return saturday.day <= 7


def auto_task_type(saturday: date) -> str:
    return "wet" if is_first_weekend(saturday) else "dry"


def back_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


def main_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Участники", callback_data="admin_members")],
        [InlineKeyboardButton(text="🧹 Назначить уборку", callback_data="admin_add_task")],
        [InlineKeyboardButton(text="🐴 Назначить коня", callback_data="admin_add_horse")],
        [InlineKeyboardButton(text="📢 Кастомное событие", callback_data="admin_add_custom")],
        [InlineKeyboardButton(text="📋 Активные задачи", callback_data="admin_active_tasks")],
    ])


# =========================
# START
# =========================

@router.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    username = user.username or f"id{user.id}"
    name = user.full_name or username

    # Сохраняем в базу при любом /start
    add_member(user.id, username, name)

    # Отвечаем только в личке, чтобы не мусорить в беседе
    if message.chat.type == "private":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Активные задачи", callback_data="show_active_tasks")]
        ])
        await message.answer(
            "👋 Привет! Ты зарегистрирован в системе.\n"
            "Напоминания об уборке будут приходить в беседу.\n\n"
            "Команда /tasks — посмотреть активные задачи (доступна и в беседе).\n"
            "Если ты администратор — используй /admin в личке.",
            reply_markup=kb
        )


# =========================
# ADMIN PANEL
# =========================

@router.message(Command("chatid"))
async def get_chat_id(message: Message):
    await message.answer(
        f"chat_id = {message.chat.id}\n"
        f"user_id = {message.from_user.id}"
    )
    print("chat_id:", message.chat.id)

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа")
    await message.answer("⚙️ Админ-панель:", reply_markup=main_admin_kb())


@router.callback_query(F.data == "admin_main")
async def admin_main(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)
    await state.clear()
    await call.message.edit_text("⚙️ Админ-панель:", reply_markup=main_admin_kb())
    await call.answer()


# =========================
# PARTICIPANTS
# =========================

@router.callback_query(F.data == "admin_members")
async def admin_members(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    members = get_all_members()
    kb = []

    for m in members:
        kb.append([InlineKeyboardButton(
            text=f"{m['name']} (@{m['username']})",
            callback_data=f"member_{m['tg_id']}"
        )])

    kb.append([InlineKeyboardButton(text="➕ Добавить участника", callback_data="member_add")])
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    text = "👥 Участники:" if members else "👥 Участников пока нет."
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()


@router.callback_query(F.data.regexp(r"^member_\d+$"))
async def member_detail(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[1])
    m = get_member(tg_id)

    if not m:
        return await call.answer("Участник не найден", show_alert=True)

    tasks = get_tasks_by_member(tg_id)
    pending = [t for t in tasks if t["status"] == "pending"]

    text = (
        f"👤 {m['name']}\n"
        f"📱 @{m['username']}\n"
        f"🆔 {m['tg_id']}\n\n"
        f"Активных задач: {len(pending)}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"member_edit_{tg_id}")],
        [InlineKeyboardButton(text="🗑 Удалить из системы", callback_data=f"member_del_{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_members")],
    ])

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# --- ADD MEMBER ---

@router.callback_query(F.data == "member_add")
async def member_add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    await state.set_state(AddMember.waiting_tg_id)
    await call.message.edit_text(
        "Введи Telegram ID нового участника:\n\n"
        "_(Участник должен сначала написать /start боту, "
        "чтобы получать сообщения)_\n\n"
        "Или введи /cancel для отмены.",
        parse_mode="Markdown"
    )
    await call.answer()


@router.message(AddMember.waiting_tg_id, ~F.text.startswith("/"))
async def member_add_tg_id(message: Message, state: FSMContext):
    if not message.text.strip().lstrip("-").isdigit():
        return await message.answer("❌ Введи числовой Telegram ID.")

    tg_id = int(message.text.strip())
    await state.update_data(tg_id=tg_id)
    await state.set_state(AddMember.waiting_username)
    await message.answer("Введи username (без @):")


@router.message(AddMember.waiting_username, ~F.text.startswith("/"))
async def member_add_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    await state.update_data(username=username)
    await state.set_state(AddMember.waiting_name)
    await message.answer("Введи имя участника:")


@router.message(AddMember.waiting_name, ~F.text.startswith("/"))
async def member_add_name(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()

    add_member(data["tg_id"], data["username"], name)
    await state.clear()

    await message.answer(
        f"✅ Участник добавлен:\n{name} (@{data['username']})",
        reply_markup=back_kb("admin_members")
    )


# --- EDIT MEMBER ---

@router.callback_query(F.data.startswith("member_edit_"))
async def member_edit_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    await state.update_data(edit_tg_id=tg_id)
    await state.set_state(EditMember.waiting_username)
    await call.message.edit_text("Введи новый username (без @):")
    await call.answer()


@router.message(EditMember.waiting_username, ~F.text.startswith("/"))
async def member_edit_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    await state.update_data(username=username)
    await state.set_state(EditMember.waiting_name)
    await message.answer("Введи новое имя:")


@router.message(EditMember.waiting_name, ~F.text.startswith("/"))
async def member_edit_name(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()

    update_member(data["edit_tg_id"], data["username"], name)
    await state.clear()

    await message.answer(
        f"✅ Участник обновлён:\n{name} (@{data['username']})",
        reply_markup=back_kb("admin_members")
    )


# --- DELETE MEMBER ---

@router.callback_query(F.data.startswith("member_del_"))
async def member_delete(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    m = get_member(tg_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"member_delconfirm_{tg_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"member_{tg_id}"),
        ]
    ])

    await call.message.edit_text(
        f"Удалить {m['name']} (@{m['username']}) из системы?",
        reply_markup=kb
    )
    await call.answer()


@router.callback_query(F.data.startswith("member_delconfirm_"))
async def member_delete_confirm(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    deactivate_member(tg_id)

    await call.message.edit_text(
        "✅ Участник деактивирован.",
        reply_markup=back_kb("admin_members")
    )
    await call.answer()


# =========================
# ADD TASK (CLEANING)
# =========================

@router.callback_query(F.data == "admin_add_task")
async def add_task_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    members = get_all_members()
    if not members:
        await call.message.edit_text("❌ Нет участников.", reply_markup=back_kb("admin_main"))
        return await call.answer()

    kb = [[InlineKeyboardButton(
        text=f"{m['name']} (@{m['username']})",
        callback_data=f"task_member_{m['tg_id']}"
    )] for m in members]
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    await state.set_state(AddTask.waiting_member)
    await call.message.edit_text(
        "Кому назначить уборку на месяц?\nВыбери участника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await call.answer()


@router.callback_query(F.data.startswith("task_member_"))
async def task_pick_member(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    await state.update_data(tg_id=tg_id)
    await state.set_state(AddTask.waiting_task_type)

    # Встроенные типы уборки
    kb = [
        [
            InlineKeyboardButton(text="💧 Влажная", callback_data="tasktype_wet"),
            InlineKeyboardButton(text="🧹 Сухая", callback_data="tasktype_dry"),
        ],
    ]

    # Кастомные типы
    custom_events = get_all_event_types()
    for ev in custom_events:
        kb.append([InlineKeyboardButton(
            text=f"⭐ {ev['name']}",
            callback_data=f"tasktype_{ev['key']}"
        )])

    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_task")])

    await call.message.edit_text(
        "Выбери тип события:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await call.answer()


@router.callback_query(F.data.startswith("tasktype_"))
async def task_pick_type(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_type = call.data.split("_", 1)[1]
    await state.update_data(task_type=task_type)
    await state.set_state(AddTask.waiting_place)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛏 Трёхместка", callback_data="taskplace_threesome"),
            InlineKeyboardButton(text="🏠 Общая территория", callback_data="taskplace_common"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_task")],
    ])

    await call.message.edit_text("Выбери место:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("taskplace_"))
async def task_pick_place(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    place = call.data.split("_")[1]
    await state.update_data(place=place)
    await state.set_state(AddTask.waiting_year)

    today = date.today()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=str(today.year), callback_data=f"taskyear_{today.year}"),
            InlineKeyboardButton(text=str(today.year + 1), callback_data=f"taskyear_{today.year + 1}"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_task")],
    ])

    await call.message.edit_text("Выбери год:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("taskyear_"))
async def task_pick_year(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    year = int(call.data.split("_")[1])
    await state.update_data(year=year)
    await state.set_state(AddTask.waiting_month)

    months = [
        "Январь", "Февраль", "Март", "Апрель",
        "Май", "Июнь", "Июль", "Август",
        "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    kb_rows = []
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(text=months[i+j], callback_data=f"taskmonth_{i+j+1}")
            for j in range(3)
        ]
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_task")])

    await call.message.edit_text(
        f"Год: {year}\nВыбери месяц:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
    await call.answer()


@router.callback_query(F.data.startswith("taskmonth_"))
async def task_pick_month(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    month = int(call.data.split("_")[1])
    data = await state.get_data()
    year = data["year"]
    tg_id = data["tg_id"]
    place = data["place"]
    task_type_override = data.get("task_type")  # кастомное или wet/dry

    from utils import get_saturdays_of_month, auto_task_type
    saturdays = get_saturdays_of_month(year, month)

    m = get_member(tg_id)
    place_name = PLACE_NAMES.get(place, place)

    # Чистим старые задачи влажной/сухой уборки за этот месяц и локацию
    clear_month_cleaning_tasks(year, month, place)

    # Для стандартных уборок (wet/dry) — авто по дате
    # Для кастомных — один тип на все субботы
    is_custom = task_type_override and task_type_override not in ("wet", "dry")

    created = []
    for sat in saturdays:
        if is_custom:
            task_type = task_type_override
            label = get_task_name(task_type)
        else:
            task_type = auto_task_type(sat)
            label = "💧 влажная" if task_type == "wet" else "🧹 сухая"
        add_task(tg_id, task_type, place, sat.isoformat())
        created.append(f"  {sat.strftime('%d.%m')} ({label})")

    await state.clear()

    tasks_text = "\n".join(created)
    await call.message.edit_text(
        f"✅ События на месяц назначены!\n\n"
        f"👤 {m['name']} (@{m['username']})\n"
        f"📍 {place_name}\n"
        f"📅 {year}/{month:02d}\n\n"
        f"Создано задач:\n{tasks_text}\n\n"
        f"🔔 Напоминания придут в пятницу в 19:00\n"
        f"✅ Контроль начнётся в субботу в 20:00",
        reply_markup=back_kb("admin_main")
    )
    await call.answer()


# =========================
# ADD HORSE TASK
# =========================

@router.callback_query(F.data == "admin_add_horse")
async def add_horse_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    members = get_all_members()
    if not members:
        await call.message.edit_text("❌ Нет участников.", reply_markup=back_kb("admin_main"))
        return await call.answer()

    kb = [[InlineKeyboardButton(
        text=f"{m['name']} (@{m['username']})",
        callback_data=f"horse_member_{m['tg_id']}"
    )] for m in members]
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    await state.set_state(AddTask.waiting_member)
    await call.message.edit_text(
        "Кому назначить мытьё коня?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await call.answer()


@router.callback_query(F.data.startswith("horse_member_"))
async def horse_pick_member(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    await state.update_data(tg_id=tg_id, task_type="horse")
    await state.set_state(AddTask.waiting_year)

    today = date.today()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=str(today.year), callback_data=f"horseyear_{today.year}"),
            InlineKeyboardButton(text=str(today.year + 1), callback_data=f"horseyear_{today.year + 1}"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_horse")],
    ])

    await call.message.edit_text("Выбери год:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("horseyear_"))
async def horse_pick_year(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    year = int(call.data.split("_")[1])
    await state.update_data(year=year)

    months = [
        "Январь", "Февраль", "Март", "Апрель",
        "Май", "Июнь", "Июль", "Август",
        "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    kb_rows = []
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(text=months[i+j], callback_data=f"horsemonth_{i+j+1}")
            for j in range(3)
        ]
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_add_horse")])

    await call.message.edit_text(
        f"Год: {year}\nВыбери месяц для мытья коня:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
    await call.answer()


@router.callback_query(F.data.startswith("horsemonth_"))
async def horse_pick_month(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    month = int(call.data.split("_")[1])
    data = await state.get_data()
    year = data["year"]
    tg_id = data["tg_id"]

    # Для коня используем 1-е число месяца как "дату задачи"
    task_date = date(year, month, 1).isoformat()
    add_task(tg_id, "horse", "common", task_date)

    m = get_member(tg_id)
    await state.clear()

    await call.message.edit_text(
        f"✅ Мытьё коня назначено!\n\n"
        f"👤 {m['name']} (@{m['username']})\n"
        f"📅 Месяц: {year}/{month:02d}\n"
        f"🔔 Напоминания: 1-го и 15-го числа в 19:00",
        reply_markup=back_kb("admin_main")
    )
    await call.answer()


MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель",
    "Май", "Июнь", "Июль", "Август",
    "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]


def build_tasks_view(current_key: str, admin: bool):
    """Текст + клавиатура со списком задач за месяц.

    admin=True  — задачи кликабельны (просмотр/редактирование), кнопка "Назад" в админку.
    admin=False — список только для чтения, без админских кнопок и действий.
    """
    tasks = get_pending_tasks()

    all_keys_set = set()
    for t in tasks:
        y, m, _ = t["task_date"].split("-")
        all_keys_set.add(f"{y}-{m}")

    today_key = date.today().strftime("%Y-%m")
    all_keys_set.add(today_key)
    all_keys_set.add(current_key)

    all_keys = sorted(all_keys_set)
    idx = all_keys.index(current_key)
    year, month = current_key.split("-")
    month_name = MONTHS_RU[int(month)]

    month_tasks = [t for t in tasks if t["task_date"].startswith(current_key)]

    nav_prefix = "tasksmonth_" if admin else "utasksmonth_"
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_prefix}{all_keys[idx-1]}"))
    else:
        nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    nav.append(InlineKeyboardButton(text=f"{month_name} {year}", callback_data="noop"))

    if idx < len(all_keys) - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_prefix}{all_keys[idx+1]}"))
    else:
        nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    kb = []
    if admin:
        for t in month_tasks:
            if t["task_type"] == "custom_event":
                task_label = t.get("description") or "событие"
            else:
                task_label = get_task_name(t["task_type"])
            place_short = "🛏" if t["place"] == "threesome" else "🏠"
            day = t["task_date"].split("-")[2]
            kb.append([InlineKeyboardButton(
                text=f"{place_short} {t['name']} — {task_label} {day}.{month}",
                callback_data=f"viewtask_{t['id']}"
            )])

    kb.append(nav)
    if admin:
        kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    def fmt_task(t):
        if t["task_type"] == "custom_event":
            label = t.get("description") or "событие"
        else:
            label = get_task_name(t["task_type"])
        place = PLACE_NAMES.get(t["place"], t["place"])
        day = t["task_date"].split("-")[2]
        status_mark = " ⏳" if t["status"] == "waiting_confirm" else ""
        return f"• {t['name']} — {label} ({place}) {day}.{month}{status_mark}"

    if month_tasks:
        lines = "\n".join(fmt_task(t) for t in month_tasks)
        text = f"📋 {month_name} {year}:\n\n{lines}"
    else:
        text = f"📋 {month_name} {year}:\n\nУборок нет."

    return text, InlineKeyboardMarkup(inline_keyboard=kb)


# =========================
# ACTIVE TASKS
# =========================

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "admin_active_tasks")
async def active_tasks(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    today = date.today()
    current_key = today.strftime("%Y-%m")
    await show_tasks_for_month(call, current_key)


@router.callback_query(F.data.startswith("tasksmonth_"))
async def active_tasks_by_month(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    key = call.data.split("_", 1)[1]
    await show_tasks_for_month(call, key)


async def show_tasks_for_month(call: CallbackQuery, current_key: str):
    """Админский вид списка задач за месяц (кликабельные задачи, кнопка в админку)."""
    text, kb = build_tasks_view(current_key, admin=True)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# =========================
# ACTIVE TASKS — ДЛЯ ВСЕХ УЧАСТНИКОВ (без админки)
# =========================

@router.message(Command("tasks"))
async def user_tasks_cmd(message: Message):
    current_key = date.today().strftime("%Y-%m")
    text, kb = build_tasks_view(current_key, admin=False)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "show_active_tasks")
async def user_tasks_btn(call: CallbackQuery):
    current_key = date.today().strftime("%Y-%m")
    text, kb = build_tasks_view(current_key, admin=False)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("utasksmonth_"))
async def user_tasks_nav(call: CallbackQuery):
    key = call.data.split("_", 1)[1]
    text, kb = build_tasks_view(key, admin=False)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("viewtask_"))
async def view_task(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_id = int(call.data.split("_")[1])
    t = get_task(task_id)

    if not t:
        return await call.answer("Задача не найдена", show_alert=True)

    m = get_member(t["tg_id"])
    task_name = get_task_name(t["task_type"])
    place_name = PLACE_NAMES.get(t["place"], t["place"])

    text = (
        f"📋 Задача #{task_id}\n\n"
        f"👤 {m['name']} (@{m['username']})\n"
        f"🧹 {task_name}\n"
        f"📍 {place_name}\n"
        f"📅 {t['task_date']}\n"
        f"📊 Статус: {t['status']}\n"
        f"🔔 Напоминаний: {t['control_count']}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить выполненной", callback_data=f"admindone_{task_id}")],
        [InlineKeyboardButton(text="🗑 Удалить задачу", callback_data=f"admindel_{task_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_active_tasks")],
    ])

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("admindone_"))
async def admin_mark_done(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_id = int(call.data.split("_")[1])
    mark_task_done(task_id)

    await call.message.edit_text(
        "✅ Задача отмечена как выполненная.",
        reply_markup=back_kb("admin_active_tasks")
    )
    await call.answer()


@router.callback_query(F.data.startswith("admindel_"))
async def admin_delete_task(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_id = int(call.data.split("_")[1])
    delete_task(task_id)

    await call.message.edit_text(
        "🗑 Задача удалена.",
        reply_markup=back_kb("admin_active_tasks")
    )
    await call.answer()


# =========================
# DONE BUTTON (USER)
# =========================

@router.callback_query(F.data.startswith("done_"))
async def user_done(call: CallbackQuery):
    task_id = int(call.data.split("_")[1])
    t = get_task(task_id)

    if not t:
        return await call.answer("Задача не найдена", show_alert=True)

    if call.from_user.id != t["tg_id"]:
        return await call.answer("⛔ Это не твоя задача", show_alert=True)

    if t["status"] == "done":
        return await call.answer("Уже отмечено ✅", show_alert=True)

    if t["status"] == "waiting_confirm":
        return await call.answer("Уже ожидает подтверждения ⏳", show_alert=True)

    # Ставим статус ожидания и убираем кнопку
    set_task_waiting_confirm(task_id)
    await call.message.edit_text(
        call.message.text + "\n\n⏳ Ожидает подтверждения администратора..."
    )
    await call.answer("⏳ Отправлено на подтверждение!")

    # Шлём админу в личку запрос на подтверждение
    m = get_member(t["tg_id"])
    task_name = get_task_name(t["task_type"])
    place_name = PLACE_NAMES.get(t["place"], t["place"])

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{task_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{task_id}"),
    ]])

    await bot.send_message(
        ADMIN_ID,
        f"🔔 Запрос на подтверждение уборки\n\n"
        f"👤 {m['name']} (@{m['username']})\n"
        f"🧹 {task_name}\n"
        f"📍 {place_name}\n"
        f"📅 {t['task_date']}",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("confirm_"))
async def admin_confirm_done(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_id = int(call.data.split("_")[1])
    t = get_task(task_id)

    if not t:
        return await call.answer("Задача не найдена", show_alert=True)

    mark_task_done(task_id)

    m = get_member(t["tg_id"])
    task_name = get_task_name(t["task_type"])

    # Подтверждение в личку админу
    await call.message.edit_text(
        f"✅ Уборка подтверждена\n"
        f"👤 {m['name']} — {task_name} ({t['task_date']})"
    )
    await call.answer("✅ Подтверждено!")

    # Уведомление в беседу
    from config import CHAT_ID
    await bot.send_message(
        CHAT_ID,
        f"✅ @{m['username']} молодец! Уборка подтверждена. Партия довольна."
    )


@router.callback_query(F.data.startswith("reject_"))
async def admin_reject_done(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    task_id = int(call.data.split("_")[1])
    t = get_task(task_id)

    if not t:
        return await call.answer("Задача не найдена", show_alert=True)

    set_task_pending(task_id)

    m = get_member(t["tg_id"])
    task_name = get_task_name(t["task_type"])

    # Отклонение в личку админу
    await call.message.edit_text(
        f"❌ Уборка отклонена\n"
        f"👤 {m['name']} — {task_name} ({t['task_date']})"
    )
    await call.answer("❌ Отклонено")

    # Уведомление в беседу
    from config import CHAT_ID
    await bot.send_message(
        CHAT_ID,
        f"❌ @{m['username']} уборка не принята. Партия недовольна. Контроль продолжается."
    )




# =========================
# ADD CUSTOM EVENT
# =========================

@router.callback_query(F.data == "admin_add_custom")
async def add_custom_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    members = get_all_members()
    if not members:
        await call.message.edit_text("❌ Нет участников.", reply_markup=back_kb("admin_main"))
        return await call.answer()

    kb = [[InlineKeyboardButton(
        text=f"{m['name']} (@{m['username']})",
        callback_data=f"custev_member_{m['tg_id']}"
    )] for m in members]
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    await state.set_state(AddCustomEvent.waiting_member)
    await call.message.edit_text(
        "Кому отправить событие?\nВыбери участника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await call.answer()


@router.callback_query(F.data.startswith("custev_member_"))
async def custev_pick_member(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    tg_id = int(call.data.split("_")[2])
    await state.update_data(tg_id=tg_id)
    await state.set_state(AddCustomEvent.waiting_description)
    await call.message.edit_text(
        "Введи описание события:\n"
        "_(например: «Генеральная уборка кухни» или «Проверить огнетушитель»)_\n\n"
        "Или /cancel для отмены.",
        parse_mode="Markdown"
    )
    await call.answer()


@router.message(AddCustomEvent.waiting_description, ~F.text.startswith("/"))
async def custev_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddCustomEvent.waiting_date)
    await message.answer(
        "Введи дату уведомления в формате ДД.ММ.ГГГГ\n"
        "_(например: 15.07.2026)_"
    )


@router.message(AddCustomEvent.waiting_date, ~F.text.startswith("/"))
async def custev_date(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        d = date(int(text[6:]), int(text[3:5]), int(text[:2]))
    except Exception:
        return await message.answer("❌ Неверный формат. Введи дату как ДД.ММ.ГГГГ")

    await state.update_data(event_date=d.isoformat())
    await state.set_state(AddCustomEvent.waiting_time)
    await message.answer(
        "Введи время уведомления в формате ЧЧ:ММ\n"
        "_(например: 19:00)_"
    )


@router.message(AddCustomEvent.waiting_time, ~F.text.startswith("/"))
async def custev_time(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        parts = text.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        time_str = f"{h:02d}:{m:02d}"
    except Exception:
        return await message.answer("❌ Неверный формат. Введи время как ЧЧ:ММ")

    data = await state.get_data()
    tg_id = data["tg_id"]
    description = data["description"]
    event_date = data["event_date"]
    notify_at = f"{event_date} {time_str}"

    member = get_member(tg_id)
    add_task(tg_id, "custom_event", "common", event_date, description=description, notify_at=notify_at)
    await state.clear()

    await message.answer(
        f"✅ Событие создано!\n\n"
        f"👤 {member['name']} (@{member['username']})\n"
        f"📝 {description}\n"
        f"🔔 Уведомление: {event_date.split('-')[2]}.{event_date.split('-')[1]}.{event_date.split('-')[0]} в {time_str} (МСК)",
        reply_markup=back_kb("admin_main")
    )


# =========================
# EVENT TYPES MANAGEMENT
# =========================

@router.callback_query(F.data == "admin_event_types")
async def admin_event_types(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    events = get_all_event_types()
    kb = []

    for ev in events:
        kb.append([InlineKeyboardButton(
            text=f"🗑 {ev['name']}",
            callback_data=f"eventdel_{ev['id']}"
        )])

    kb.append([InlineKeyboardButton(text="➕ Добавить событие", callback_data="event_add")])
    kb.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main")])

    text = "⚙️ Типы событий:\n\nНажми на событие чтобы удалить." if events else "⚙️ Кастомных событий пока нет."
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()


@router.callback_query(F.data == "event_add")
async def event_add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    await state.set_state(AddEventType.waiting_name)
    await call.message.edit_text(
        "Введи название нового типа события:\n"
        "_(например: «Мытьё окон» или «Чистка вентиляции»)_\n\n"
        "Или /cancel для отмены.",
        parse_mode="Markdown"
    )
    await call.answer()


@router.message(AddEventType.waiting_name, ~F.text.startswith("/"))
async def event_add_name(message: Message, state: FSMContext):
    name = message.text.strip()

    if len(name) > 50:
        return await message.answer("❌ Слишком длинное название, максимум 50 символов.")

    success = add_event_type(name)
    await state.clear()

    if success:
        await message.answer(
            f"✅ Событие «{name}» добавлено!\n"
            f"Теперь оно доступно при назначении задачи.",
            reply_markup=back_kb("admin_event_types")
        )
    else:
        await message.answer(
            f"❌ Событие с таким названием уже существует.",
            reply_markup=back_kb("admin_event_types")
        )


@router.callback_query(F.data.startswith("eventdel_"))
async def event_delete(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    event_id = int(call.data.split("_")[1])
    events = get_all_event_types()
    event = next((e for e in events if e["id"] == event_id), None)

    if not event:
        return await call.answer("Событие не найдено", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"eventdelconfirm_{event_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_event_types"),
        ]
    ])

    await call.message.edit_text(
        f"Удалить тип события «{event['name']}»?",
        reply_markup=kb
    )
    await call.answer()


@router.callback_query(F.data.startswith("eventdelconfirm_"))
async def event_delete_confirm(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("⛔", show_alert=True)

    event_id = int(call.data.split("_")[1])
    delete_event_type(event_id)

    await call.message.edit_text(
        "✅ Тип события удалён.",
        reply_markup=back_kb("admin_event_types")
    )
    await call.answer()

# =========================
# CANCEL
# =========================

@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=back_kb("admin_main"))


# =========================
# MAIN
# =========================

async def set_commands():
    # Команды для всех пользователей
    user_commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="tasks", description="Активные задачи"),
    ]
    # Дополнительные команды для админа
    admin_commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="tasks", description="Активные задачи"),
        BotCommand(command="admin", description="Админ-панель"),
        BotCommand(command="cancel", description="Отменить действие"),
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))


async def main():
    init_db()
    start_scheduler(bot)
    me = await bot.get_me()



    await set_commands()
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

