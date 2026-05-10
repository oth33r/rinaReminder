import json
import logging
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)

STATE_FILE = Path(__file__).with_name("reminder_state.json")
REMIND_EVERY = timedelta(minutes=10)
DEFAULT_DESCRIPTION = "Таблетка"

BTN_ADD = "Добавить запись"
BTN_LIST = "Мои записи"
BTN_COMPLIMENT = "Комплиментик ❤️"
BTN_CLEAR_ALL = "Удалить все"

FLOW_KEY = "flow"
EDIT_ID_KEY = "edit_id"
TEMP_TIME_KEY = "temp_time"
RECENT_COMPLIMENTS_LIMIT = 5

CUTE_MESSAGES = [
    "Умничка, котенок❤️",
    "Маленькая победа зафиксирована. Горжусь тобой🥰",
    "Супер, надеюсь улыбка на твоем лице присутствует и ничто не испортит твой день😌",
    "Видишь как быстро и удобно выпила, теперь можно смело идти заниматься своими делами🥺",
    "Еще один заботливый пункт выполнен, я тобой очень горжусь🥰",
    "Ты большая молодец, даже такие маленькие действия очень важны😌",
    "Водичка внутри, галочка в голове, котенок умница❤️",
    "Вот так и строится забота о себе, маленькими нежными шагами🥺",
    "Питьевой режим одобряет, а я вообще в восторге от тебя))",
    "Ты справилась, теперь можно идти покорять день дальше😌",
    "Маленькая забота о себе засчитана, котенок молодец(можно и любимого порадовать чем-нибудь, внезапно)",
    "Горжусь тобой за то, что не забываешь про себя даже в мелочах🥺",
]

COMPLIMENT_MESSAGES = [
    "Сияй так будто сегодня сбылась твоя самая заветная мечта(возможно это шашлычок)))",
    "Любимка, ты делаешь этот мир мягче одним своим существованием🥰",
    "Котенок, ты сегодня особенно чудесная🥰",
    "Если ты сильно устала, у тебя всегда есть к кому можно обратиться за помощью и получить поддержку😌, ты очень важна для меня)",
    "Цветы конечно хорошо дополняют твою красоту, но я был бы лучше, хвхахпхва)))",
    "Цветы, конечно, красиво дополняют твою красоту, но конкуренцию тебе они все равно не выдерживают))",
    "Ты как теплый пледик для души, рядом с тобой становится спокойнее🥰",
    "Котенок, ты заслуживаешь самый нежный и хороший день😌",
    "Даже если день сложный, ты все равно остаешься невероятной🥺",
    "Ты очень красивая, но самое unfair — какая ты еще и добрая, ю ноу?))",
    "Ты моя самая приятная мысль за день, особенно в начале дня)",
    "Если бы нежность с нотками безумия была человеком, она бы точно выглядела как ты",
    "Котенок, ты не просто чудесная, ты прям отдельный вид чуда🥰",
    "Пусть сегодня все будет к тебе таким же мягким, как ты к обычно ко мне))",
]


@dataclass
class Reminder:
    id: int
    time: str
    description: str = DEFAULT_DESCRIPTION
    last_taken_date: str | None = None
    last_taken_at: str | None = None
    last_reminder_at: str | None = None


@dataclass
class UserState:
    reminders: list[Reminder] = field(default_factory=list)
    next_id: int = 1
    recent_compliments: list[str] = field(default_factory=list)


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, UserState] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for chat_id, payload in raw.items():
            if "reminders" in payload:
                reminders = [Reminder(**item) for item in payload.get("reminders", [])]
                next_id = payload.get("next_id", max((r.id for r in reminders), default=0) + 1)
                self.data[chat_id] = UserState(
                    reminders=reminders,
                    next_id=next_id,
                    recent_compliments=payload.get("recent_compliments", []),
                )
                continue

            reminder_time = payload.get("reminder_time")
            reminders: list[Reminder] = []
            if reminder_time:
                reminders.append(
                    Reminder(
                        id=1,
                        time=reminder_time,
                        description=DEFAULT_DESCRIPTION,
                        last_taken_date=payload.get("last_taken_date"),
                        last_taken_at=payload.get("last_taken_at"),
                        last_reminder_at=payload.get("last_reminder_at"),
                    )
                )
            self.data[chat_id] = UserState(
                reminders=reminders,
                next_id=2,
                recent_compliments=payload.get("recent_compliments", []),
            )

    def save(self) -> None:
        serialized = {chat_id: asdict(state) for chat_id, state in self.data.items()}
        self.path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, chat_id: int) -> UserState:
        key = str(chat_id)
        if key not in self.data:
            self.data[key] = UserState()
            self.save()
        return self.data[key]

    def update(self, chat_id: int, state: UserState) -> None:
        self.data[str(chat_id)] = state
        self.save()

    def items(self) -> list[tuple[int, UserState]]:
        return [(int(chat_id), state) for chat_id, state in self.data.items()]


def get_storage(application: Application) -> Storage:
    return application.bot_data["storage"]


def parse_hhmm(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def due_today(time_text: str) -> datetime:
    return datetime.combine(date.today(), datetime.strptime(time_text, "%H:%M").time())


def was_taken_today(reminder: Reminder) -> bool:
    return reminder.last_taken_date == date.today().isoformat()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BTN_ADD, BTN_LIST], [BTN_COMPLIMENT, BTN_CLEAR_ALL]],
        resize_keyboard=True,
    )


def reminder_actions(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Готово", callback_data=f"took:{reminder_id}"),
            InlineKeyboardButton("Комплиментик ❤️", callback_data=f"compliment:{reminder_id}"),
        ]]
    )


def manage_actions(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Изменить", callback_data=f"edit:{reminder_id}"),
                InlineKeyboardButton("Удалить", callback_data=f"delete:{reminder_id}"),
            ],
            [InlineKeyboardButton("Комплиментик ❤️", callback_data=f"compliment:{reminder_id}")],
        ]
    )


def clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(FLOW_KEY, None)
    context.user_data.pop(EDIT_ID_KEY, None)
    context.user_data.pop(TEMP_TIME_KEY, None)


def find_reminder(state: UserState, reminder_id: int) -> Reminder | None:
    return next((item for item in state.reminders if item.id == reminder_id), None)


def reminder_text(reminder: Reminder) -> str:
    return (
        f"ID: {reminder.id}\n"
        f"Время: {reminder.time}\n"
        f"Описание: {reminder.description}\n"
        f"Сегодня выпила: {'да' if was_taken_today(reminder) else 'нет'}\n"
        f"Последняя отметка: {reminder.last_taken_at or 'еще не отмечено'}"
    )


def choose_compliment(state: UserState) -> str:
    recent = set(state.recent_compliments[-RECENT_COMPLIMENTS_LIMIT:])
    candidates = [message for message in COMPLIMENT_MESSAGES if message not in recent]
    compliment = random.choice(candidates or COMPLIMENT_MESSAGES)
    state.recent_compliments = (
        state.recent_compliments + [compliment]
    )[-RECENT_COMPLIMENTS_LIMIT:]
    return compliment


async def reply_menu(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, reply_markup=main_menu())


async def show_reminders(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_storage(context.application).get(chat_id)
    if not state.reminders:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Записей пока нет. Нажми кнопку Добавить запись.",
            reply_markup=main_menu(),
        )
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="Текущие записи:",
        reply_markup=main_menu(),
    )
    for reminder in sorted(state.reminders, key=lambda item: item.time):
        await context.bot.send_message(
            chat_id=chat_id,
            text=reminder_text(reminder),
            reply_markup=manage_actions(reminder.id),
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text(
        "Приветик, котенок. Я рядом, чтобы помочь тебе не забывать важные события и дела\n\n"
        "Тут можно легко добавлять, смотреть, редактировать и удалять записи, а еще ловить комплиментики❤️",
        reply_markup=main_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    storage = get_storage(context.application)
    state = storage.get(chat_id)
    flow = context.user_data.get(FLOW_KEY)

    if text == BTN_ADD:
        clear_flow(context)
        context.user_data[FLOW_KEY] = "add_time"
        await reply_menu(update, "Напиши время в формате HH:MM.\nНапример: 09:30")
        return

    if text == BTN_LIST:
        clear_flow(context)
        await show_reminders(chat_id, context)
        return

    if text == BTN_COMPLIMENT:
        clear_flow(context)
        compliment = choose_compliment(state)
        storage.update(chat_id, state)
        await reply_menu(update, compliment)
        return

    if text == BTN_CLEAR_ALL:
        state.reminders = []
        state.next_id = 1
        storage.update(chat_id, state)
        clear_flow(context)
        await reply_menu(update, "Все записи удалены.")
        return

    if flow == "add_time":
        parsed = parse_hhmm(text)
        if parsed is None:
            await reply_menu(update, "Не получилось разобрать время. Напиши его в формате HH:MM.")
            return
        context.user_data[TEMP_TIME_KEY] = parsed
        context.user_data[FLOW_KEY] = "add_description"
        await reply_menu(update, "Теперь напиши описание.\nЕсли описание не нужно, отправь `-`.")
        return

    if flow == "add_description":
        parsed_time = context.user_data.get(TEMP_TIME_KEY)
        if not parsed_time:
            clear_flow(context)
            await reply_menu(update, "Что-то сбилось. Нажми Добавить запись еще раз.")
            return
        description = DEFAULT_DESCRIPTION if text == "-" else text or DEFAULT_DESCRIPTION
        reminder = Reminder(id=state.next_id, time=parsed_time, description=description)
        state.reminders.append(reminder)
        state.next_id += 1
        storage.update(chat_id, state)
        clear_flow(context)
        await reply_menu(update, "Запись добавлена:\n\n" + reminder_text(reminder))
        return

    if flow == "edit_time":
        parsed = parse_hhmm(text)
        if parsed is None:
            await reply_menu(update, "Не получилось разобрать время. Напиши его в формате HH:MM.")
            return
        context.user_data[TEMP_TIME_KEY] = parsed
        context.user_data[FLOW_KEY] = "edit_description"
        await reply_menu(update, "Теперь напиши новое описание.\nЕсли хочешь оставить текущее, отправь `-`.")
        return

    if flow == "edit_description":
        reminder_id = context.user_data.get(EDIT_ID_KEY)
        parsed_time = context.user_data.get(TEMP_TIME_KEY)
        reminder = find_reminder(state, reminder_id) if reminder_id is not None else None
        if reminder is None or not parsed_time:
            clear_flow(context)
            await reply_menu(update, "Запись уже изменилась. Открой список заново.")
            return
        reminder.time = parsed_time
        if text != "-":
            reminder.description = text or DEFAULT_DESCRIPTION
        reminder.last_reminder_at = None
        storage.update(chat_id, state)
        clear_flow(context)
        await reply_menu(update, "Запись обновлена:\n\n" + reminder_text(reminder))
        return

    await reply_menu(update, "Используй кнопки снизу, чтобы управлять ботом.")


async def took_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    storage = get_storage(context.application)
    state = storage.get(chat_id)
    reminder = find_reminder(state, int(query.data.split(":")[1]))

    if reminder is None:
        await query.answer("Эта запись уже удалена или изменена.", show_alert=True)
        return
    if datetime.now() < due_today(reminder.time):
        await query.answer("Еще не время для сегодняшней таблетки.", show_alert=True)
        return
    if was_taken_today(reminder):
        await query.answer("На сегодня уже отмечено.", show_alert=True)
        return

    reminder.last_taken_date = date.today().isoformat()
    reminder.last_taken_at = datetime.now().isoformat(timespec="seconds")
    reminder.last_reminder_at = None
    storage.update(chat_id, state)

    await query.answer("Отметила, умничка.")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"{random.choice(CUTE_MESSAGES)}\n\nОтмечено: {reminder.description}")


async def compliment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    storage = get_storage(context.application)
    state = storage.get(chat_id)
    reminder = find_reminder(state, int(query.data.split(":")[1]))

    if reminder is None:
        await query.answer("Эта запись уже удалена или изменена.", show_alert=True)
        return

    compliment = choose_compliment(state)
    storage.update(chat_id, state)
    await query.answer("Лови комплиментик.")
    await query.message.reply_text(f"{compliment}\n\nДля записи: {reminder.description}")


async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    state = get_storage(context.application).get(chat_id)
    reminder = find_reminder(state, int(query.data.split(":")[1]))

    if reminder is None:
        await query.answer("Эта запись уже удалена или изменена.", show_alert=True)
        return

    clear_flow(context)
    context.user_data[FLOW_KEY] = "edit_time"
    context.user_data[EDIT_ID_KEY] = reminder.id
    await query.answer("Переходим к редактированию.")
    await query.message.reply_text(
        f"Напиши новое время в формате HH:MM.\nСейчас стоит: {reminder.time}",
        reply_markup=main_menu(),
    )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    storage = get_storage(context.application)
    state = storage.get(chat_id)
    reminder_id = int(query.data.split(":")[1])
    reminder = find_reminder(state, reminder_id)

    if reminder is None:
        await query.answer("Эта запись уже удалена или изменена.", show_alert=True)
        return

    state.reminders = [item for item in state.reminders if item.id != reminder_id]
    storage.update(chat_id, state)
    clear_flow(context)

    await query.answer("Запись удалена.")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Запись {reminder_id} удалена.", reply_markup=main_menu())


async def reminder_loop(context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = get_storage(context.application)
    now = datetime.now()

    for chat_id, state in storage.items():
        changed = False
        for reminder in state.reminders:
            if was_taken_today(reminder):
                continue
            if now < due_today(reminder.time):
                continue
            if reminder.last_reminder_at:
                last = datetime.fromisoformat(reminder.last_reminder_at)
                if now - last < REMIND_EVERY:
                    continue

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Пора выпить таблетку: {reminder.description}\nНажми кнопку Готово, когда все будет готово.",
                reply_markup=reminder_actions(reminder.id),
            )
            reminder.last_reminder_at = now.isoformat(timespec="seconds")
            changed = True

        if changed:
            storage.update(chat_id, state)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled exception", exc_info=context.error)


def build_application() -> Application:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment or .env file.")

    application = ApplicationBuilder().token(token).build()
    application.bot_data["storage"] = Storage(STATE_FILE)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(took_callback, pattern=r"^took:\d+$"))
    application.add_handler(CallbackQueryHandler(compliment_callback, pattern=r"^compliment:\d+$"))
    application.add_handler(CallbackQueryHandler(edit_callback, pattern=r"^edit:\d+$"))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^delete:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)

    if application.job_queue is None:
        raise RuntimeError("Job queue is not available. Install python-telegram-bot[job-queue].")

    application.job_queue.run_repeating(
        reminder_loop,
        interval=60,
        first=5,
        name="pill-reminder-loop",
    )
    return application


def main() -> None:
    application = build_application()
    LOGGER.info("Bot is running")
    application.run_polling()


if __name__ == "__main__":
    main()
