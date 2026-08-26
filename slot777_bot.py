"""
Telegram-бот: слот-машина (🎰), джекпот с подарками, челленджи "Ludo*",
игра "Перебив", случайные числа и базовая модерация (мут/бан).

Установка:
    pip install aiogram

Запуск:
    python slot777_bot.py

Токен бота берётся из переменной окружения BOT_TOKEN
(задаётся в настройках хостинга — например, Railway → Variables).
Никогда не вписывай сам токен прямо в этот файл.

Для команд мута/бана бот должен быть администратором группы
с правом "Блокировка участников" (Restrict members).
"""

import asyncio
import logging
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

# Username админов, которых тегаем при джекпоте (без символа @)
ADMIN_USERNAMES = ["dol1ro"]

# Username, куда отправлять смотреть выдачи прошлых призов (без символа @)
PAST_PRIZES_USERNAME = "SeeSheperep"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Значение dice.value == 64 соответствует комбинации 777
# на слот-машине (эмодзи 🎰) в Telegram Bot API.
JACKPOT_VALUE = 64

# Символы на барабанах слот-машины Telegram, в порядке 1-2-3-4
REEL_SYMBOLS = ["BAR", "🍇", "🍋", "7️⃣"]
SEVEN = "7️⃣"

# ---------------------------------------------------------------------------
# ЧЕЛЛЕНДЖИ "Ludo*" — честный подсчёт совпадений на слот-машине.
# Пока такой челлендж активен на 777 ("seven") — обычный режим подарков
# на джекпот отключается, дело только в счётчике челленджа.
# ---------------------------------------------------------------------------

COMBO_BY_SYMBOL = {
    "BAR": "bar",
    "🍇": "grapes",
    "🍋": "lemons",
    SEVEN: "seven",
}

COMBO_DISPLAY_NAMES = {
    "bar": "BAR BAR BAR 🍫🍫🍫",
    "grapes": "виноград 🍇🍇🍇",
    "lemons": "лимоны 🍋🍋🍋",
    "seven": "777 7️⃣7️⃣7️⃣",
}

# команда -> тип комбинации
LUDO_COMMAND_TO_COMBO = {
    "LudoBar": "bar",
    "LudoSeven": "seven",
    "LudoLimons": "lemons",
    "LudoGraps": "grapes",
}

JACKPOT_PROGRESS_TEXT = (
    "ДЖЕКПОТ! \n"
    "7️⃣ | 7️⃣ | 7️⃣\n\n"
    "{mention}, поздравляю! Ты выбил 777!\n"
    "Прогресс: {count}/{target}"
)

JACKPOT_COMPLETE_TEXT = (
    "ДЖЕКПОТ! \n"
    "7️⃣ | 7️⃣ | 7️⃣\n\n"
    "{mention}, поздравляю! Ты завершил условия челленджа!\n"
    "За выдачей пишите сюда: {admins}"
)

# Активные челленджи: chat_id -> {"combo": "seven", "target": 3}
ludo_challenges: dict[int, dict] = {}
# Прогресс участников: chat_id -> {user_id: {"bar": 0, "seven": 0, ...}}
ludo_progress: dict[int, dict] = {}


def get_combo_type(symbols: tuple[str, str, str]) -> str | None:
    """Тип тройной комбинации ('bar'/'grapes'/'lemons'/'seven') или None."""
    if symbols[0] == symbols[1] == symbols[2]:
        return COMBO_BY_SYMBOL.get(symbols[0])
    return None


# Призы под подарками — количество звёзд Telegram под каждым из 30 подарков.
# При джекпоте этот список перемешивается случайно между 30 кнопками.
STAR_PRIZES = [
    25, 15, 15, 15, 25, 15, 15, 15, 25, 
    15, 15, 25, 50, 25, 15, 15, 15, 50,
    25, 50, 25, 15, 15, 25, 25,  
]
STAR_EMOJI = "⭐️"

# Сколько подарков в игре и как разложить их в сетку кнопок
TOTAL_GIFTS = len(STAR_PRIZES)
GIFTS_PER_ROW = 5

# "Красивые" числа для команды "SeeSheper рандом" — им намеренно занижен шанс выпадения.
BEAUTIFUL_NUMBERS = {
    1, 22, 33, 77, 99, 101, 111, 177, 222, 228, 333, 444, 555, 666, 777, 888,
    999, 1001, 1111, 2222, 1777, 1444, 1222, 1999, 2777, 2999,
}
# Во сколько раз шанс "красивого" числа ниже, чем у обычного (10 = в 10 раз реже).
BEAUTIFUL_NUMBER_PENALTY = 2

# Активные игры-джекпоты: game_id -> данные об игре
active_games = {}

# Активные игры "Перебив": chat_id -> данные об игре
perebiv_games = {}


def is_admin_user(user) -> bool:
    if user is None or user.username is None:
        return False
    return user.username.lower() in {u.lower() for u in ADMIN_USERNAMES}


def weighted_random(low: int, high: int) -> int:
    """
    Случайное число в [low, high], но "красивые" числа из BEAUTIFUL_NUMBERS
    выпадают в BEAUTIFUL_NUMBER_PENALTY раз реже, чем любое обычное число.
    Для очень больших диапазонов откатывается на обычный random.randint.
    """
    span = high - low + 1
    if span > 200_000:
        return random.randint(low, high)

    numbers = list(range(low, high + 1))
    weights = [
        (1 / BEAUTIFUL_NUMBER_PENALTY) if n in BEAUTIFUL_NUMBERS else 1.0
        for n in numbers
    ]
    return random.choices(numbers, weights=weights, k=1)[0]


def decode_slot_symbols(value: int) -> tuple[str, str, str]:
    """
    Превращает число 1-64 из dice.value в кортеж трёх символов барабанов,
    которые реально показывает анимация слот-машины в Telegram.
    """
    v = value - 1
    r1 = REEL_SYMBOLS[v % 4]
    r2 = REEL_SYMBOLS[(v // 4) % 4]
    r3 = REEL_SYMBOLS[(v // 16) % 4]
    return r1, r2, r3


def is_near_miss(symbols: tuple[str, str, str]) -> bool:
    """'Почти джекпот' — ровно два барабана из трёх показывают семёрку."""
    return symbols.count(SEVEN) == 2


def mention(user_id: int, name: str) -> str:
    """HTML-ссылка, которая тегает пользователя по id (работает без username)."""
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def admins_mention_line() -> str:
    return " ".join(f"@{u}" for u in ADMIN_USERNAMES)


def build_gift_keyboard(game_id: str, game: dict) -> InlineKeyboardMarkup:
    """
    Пока подарок не открыт — показывает 🎁.
    После завершения игры — показывает приз (звёзды) под КАЖДОЙ кнопкой,
    выбранный победителем подарок отмечен ✅.
    """
    buttons = []
    row = []
    for i in range(TOTAL_GIFTS):
        if game["finished"]:
            prefix = "✅" if i == game["chosen_index"] else ""
            text = f"{prefix}{game['values'][i]}{STAR_EMOJI}"
            callback_data = "gift:closed"
        else:
            text = "🎁"
            callback_data = f"gift:{game_id}:{i}"
        row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        if len(row) == GIFTS_PER_ROW:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(F.dice)
async def handle_dice(message: Message):
    dice = message.dice
    if dice.emoji != "🎰":
        return

    user = message.from_user
    symbols = decode_slot_symbols(dice.value)
    combo = " | ".join(symbols)

    # Если в этом чате идёт активный /LudoSeven-челлендж — режим подарков
    # на 777 отключается, джекпот засчитывается только в челлендж.
    active_challenge = ludo_challenges.get(message.chat.id)
    seven_challenge_active = bool(active_challenge and active_challenge["combo"] == "seven")

    if dice.value == JACKPOT_VALUE and not seven_challenge_active:
        game_id = uuid.uuid4().hex[:12]
        values = list(STAR_PRIZES)
        random.shuffle(values)

        active_games[game_id] = {
            "values": values,
            "winner_id": user.id,
            "finished": False,
            "chosen_index": None,
        }

        text = (
            f"🎉🎰 <b>ДЖЕКПОТ!</b> 🎰🎉\n"
            f"{combo}\n\n"
            f"🔥{mention(user.id, user.full_name)} 🔥, поздравляю! Ты выбил 777!\n"
            f"За выдачей пишите: {admins_mention_line()}\n\n"
            f"✅ Выдачи прошлых призов: @{PAST_PRIZES_USERNAME}\n\n"
            f"Выбери свой приз из списка ниже 👇"
        )
        await message.reply(
            text, reply_markup=build_gift_keyboard(game_id, active_games[game_id])
        )
    elif is_near_miss(symbols):
        await message.reply(
            f"{combo}\n"
            f"777 уже близко, попробуй еще раз ({user.full_name})"
        )
    # Во всех остальных случаях бот сам ничего не пишет —
    # реагирует только на джекпот 777 и на "почти джекпот".

    # Отдельно от джекпот-игры: если в чате запущен /Ludo*-челлендж
    # на эту же комбинацию, считаем прогресс и поздравляем.
    await process_ludo_challenge(message, symbols)


async def process_ludo_challenge(message: Message, symbols: tuple[str, str, str]) -> None:
    """Считает прогресс активного /Ludo*-челленджа для выпавшей комбинации."""
    combo = get_combo_type(symbols)
    if combo is None:
        return

    challenge = ludo_challenges.get(message.chat.id)
    if not challenge or challenge["combo"] != combo:
        return

    user = message.from_user
    chat_progress = ludo_progress.setdefault(message.chat.id, {})
    user_progress = chat_progress.setdefault(user.id, {})
    user_progress[combo] = user_progress.get(combo, 0) + 1
    count = user_progress[combo]
    target = challenge["target"]
    who = mention(user.id, user.full_name)

    if count < target:
        if combo == "seven":
            await message.reply(
                JACKPOT_PROGRESS_TEXT.format(mention=who, count=count, target=target)
            )
        else:
            await message.reply(
                f"{who}, выбил {COMBO_DISPLAY_NAMES[combo]}! "
                f"Прогресс: {count}/{target}"
            )
    else:
        if combo == "seven":
            await message.reply(
                JACKPOT_COMPLETE_TEXT.format(mention=who, admins=admins_mention_line())
            )
        else:
            await message.reply(
                f"{who}, готово! Выбил {COMBO_DISPLAY_NAMES[combo]} {count}/{target} раз(а). "
                f"Условие челленджа выполнено 🎉"
            )
        ludo_challenges.pop(message.chat.id, None)
        ludo_progress.pop(message.chat.id, None)


@dp.message(F.text.regexp(r"(?i)^/(LudoBar|LudoSeven|LudoLimons|LudoGraps)(?:@\S+)?(?:\s+(\d+))?"))
async def cmd_ludo_challenge(message: Message):
    """Админ-команды: /LudoBar N, /LudoSeven N, /LudoLimons N, /LudoGraps N."""
    if not is_admin_user(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(
        r"(?i)^/(LudoBar|LudoSeven|LudoLimons|LudoGraps)(?:@\S+)?(?:\s+(\d+))?",
        message.text,
    )
    command_name = match.group(1)
    canonical = next(
        (c for c in LUDO_COMMAND_TO_COMBO if c.lower() == command_name.lower()),
        None,
    )
    combo = LUDO_COMMAND_TO_COMBO.get(canonical)
    args_str = match.group(2)

    if combo is None or not args_str or int(args_str) <= 0:
        await message.reply(
            f"Неверный формат команды.\n"
            f"Пример: /{command_name} 1 — число это сколько раз надо выбить "
            f"определённую комбинацию."
        )
        return

    target = int(args_str)
    previous_challenge = ludo_challenges.get(message.chat.id)
    ludo_challenges[message.chat.id] = {"combo": combo, "target": target}
    ludo_progress[message.chat.id] = {}

    verb = "изменён" if previous_challenge is not None else "запущен"
    await message.reply(
        f"Челлендж {verb}!\n"
        f"Нужно выбить {COMBO_DISPLAY_NAMES[combo]} — {target} раз(а) на слот-машине 🎰\n"
        f"Считаются только реальные результаты автомата."
    )


@dp.message(F.text.regexp(r"(?i)^/LudoStop(?:@\S+)?"))
async def cmd_ludo_stop(message: Message):
    if not is_admin_user(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    ludo_challenges.pop(message.chat.id, None)
    ludo_progress.pop(message.chat.id, None)
    await message.reply("Челлендж остановлен.")


@dp.callback_query(F.data.startswith("gift:"))
async def handle_gift_click(callback: CallbackQuery):
    if callback.data == "gift:closed":
        await callback.answer("Эта игра уже завершена.", show_alert=True)
        return

    _, game_id, idx_str = callback.data.split(":")
    idx = int(idx_str)

    game = active_games.get(game_id)
    if game is None or game["finished"]:
        await callback.answer("Эта игра уже завершена.", show_alert=True)
        return

    if callback.from_user.id != game["winner_id"]:
        await callback.answer("Это приз не для тебя 🙂", show_alert=True)
        return

    value = game["values"][idx]
    game["finished"] = True
    game["chosen_index"] = idx

    winner = callback.from_user
    text = (
        f"🎁 Подарок открыт!\n\n"
        f"🔥Пользователь ({mention(winner.id, winner.full_name)}) 🔥, "
        f"выиграл приз: <b>{value}{STAR_EMOJI}</b> 🎉\n\n"
        f"✅ За выдачей пишите: {admins_mention_line()}\n\n"
        f"Все призы ниже 👇"
    )

    await callback.message.edit_text(
        text, reply_markup=build_gift_keyboard(game_id, game)
    )
    await callback.answer()


async def run_perebiv_timer(
    chat_id: int, status_message_id: int, holder_id: int, holder_name: str, duration: int
):
    """
    Ждёт до конца таймера, редактируя ОДНО и то же сообщение.
    Считает вслух каждую секунду на протяжении ВСЕЙ игры.
    Если таймер отменят (кто-то перебил) — тихо завершается.

    Примечание: Telegram лимитирует частоту редактирования одного
    сообщения — раз в секунду укладывается в общие рамки, но для
    очень длинных игр (сотни секунд) может слать много запросов подряд.
    """
    try:
        for remaining in range(duration, -1, -1):
            game = perebiv_games.get(chat_id)
            if game is None or game.get("status_message_id") != status_message_id:
                return

            if remaining == 0:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=f"🏆 {mention(holder_id, holder_name)}, ты победил в перебив! 🎉",
                )
                perebiv_games.pop(chat_id, None)
                return

            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=f"{mention(holder_id, holder_name)}, перебив!\n⏳ Осталось: {remaining} сек.",
            )
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return


@dp.message(F.text.regexp(r"(?i)^/perebiv\s+(\d+)"))
async def cmd_perebiv(message: Message):
    if not is_admin_user(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/perebiv\s+(\d+)", message.text)
    duration = int(match.group(1))
    if duration <= 0:
        await message.reply("Число секунд должно быть больше нуля.")
        return

    old_game = perebiv_games.get(message.chat.id)
    if old_game and old_game.get("task"):
        old_game["task"].cancel()

    perebiv_games[message.chat.id] = {
        "duration": duration,
        "holder_id": None,
        "status_message_id": None,
        "task": None,
    }

    await message.reply(
        f"🎮 Игра «Перебив» запущена! Длительность: {duration} сек.\n"
        f"Напишите что-нибудь в чат, чтобы стать претендентом на победу."
    )


@dp.message(F.text.regexp(r"(?i)^seesheper\s+рандом\s+(-?\d+)\s+(-?\d+)"))
async def cmd_random(message: Message):
    match = re.match(
        r"(?i)^seesheper\s+рандом\s+(-?\d+)\s+(-?\d+)", message.text
    )
    low = int(match.group(1))
    high = int(match.group(2))
    if low > high:
        low, high = high, low

    value = weighted_random(low, high)
    user = message.from_user
    await message.reply(
        f"🎲 {mention(user.id, user.full_name)}, случайное число: <b>{value}</b>"
    )


# ---------------------------------------------------------------------------
# ИГРА "УГАДАЙКА": /Ugadaika [low] [high] → админ отправляет боту в личку
# секретное число из этого диапазона → игра стартует в группе → первый,
# кто угадает число прямо в чате, побеждает.
# ---------------------------------------------------------------------------

# admin_user_id -> {"chat_id": ..., "low": ..., "high": ...} (ждём секретное число в ЛС)
pending_guess_setup: dict[int, dict] = {}
# chat_id -> {"low": ..., "high": ..., "secret": ..., "setter_id": ...}
active_guess_games: dict[int, dict] = {}


@dp.message(F.text.regexp(r"(?i)^/ugadaika(?:@\S+)?\s+(-?\d+)\s+(-?\d+)"))
async def cmd_ugadaika(message: Message):
    if not is_admin_user(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/ugadaika(?:@\S+)?\s+(-?\d+)\s+(-?\d+)", message.text)
    low = int(match.group(1))
    high = int(match.group(2))
    if low > high:
        low, high = high, low

    pending_guess_setup[message.from_user.id] = {
        "chat_id": message.chat.id,
        "low": low,
        "high": high,
    }

    await message.reply(
        f"🎯 Игра «Угадайка» готовится! Диапазон: {low}-{high}.\n"
        f"{mention(message.from_user.id, message.from_user.full_name)}, напиши мне в "
        f"личные сообщения секретное число из этого диапазона, чтобы запустить игру."
    )


async def is_pending_secret_dm(message: Message) -> bool:
    return bool(
        message.chat.type == "private"
        and message.from_user
        and message.from_user.id in pending_guess_setup
        and message.text
        and message.text.strip().lstrip("-").isdigit()
    )


@dp.message(is_pending_secret_dm)
async def handle_secret_number_dm(message: Message):
    pending = pending_guess_setup.pop(message.from_user.id)
    secret = int(message.text.strip())

    if not (pending["low"] <= secret <= pending["high"]):
        pending_guess_setup[message.from_user.id] = pending  # ждём число ещё раз
        await message.reply(
            f"Число должно быть в диапазоне {pending['low']}-{pending['high']}. "
            f"Попробуй ещё раз."
        )
        return

    active_guess_games[pending["chat_id"]] = {
        "low": pending["low"],
        "high": pending["high"],
        "secret": secret,
        "setter_id": message.from_user.id,
    }

    await message.reply("Принято! Игра запущена в группе 🎯")
    await bot.send_message(
        pending["chat_id"],
        f"🎯 Игра «Угадайка» началась! Отгадайте число от {pending['low']} "
        f"до {pending['high']}. Пишите числа прямо в чат!",
    )


async def is_guess_attempt(message: Message) -> bool:
    return bool(
        message.text
        and message.text.strip().lstrip("-").isdigit()
        and message.chat.id in active_guess_games
    )


@dp.message(is_guess_attempt)
async def handle_guess_attempt(message: Message):
    game = active_guess_games.get(message.chat.id)
    if game is None:
        return

    guess = int(message.text.strip())
    if guess != game["secret"]:
        return  # неверные попытки — тихо, без спама в чат

    winner = message.from_user
    await message.reply(
        f"🎉 {mention(winner.id, winner.full_name)} угадал число "
        f"<b>{game['secret']}</b>! Победа!"
    )
    active_guess_games.pop(message.chat.id, None)


# ---------------------------------------------------------------------------
# МОДЕРАЦИЯ: мут / размут / бан / разбан по реплаю на сообщение.
# Требует прав администратора у бота (право "Блокировка участников").
# ---------------------------------------------------------------------------

FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)
MUTED_PERMISSIONS = ChatPermissions(can_send_messages=False)


def get_target_user(replied: Message):
    """
    Определяет, кого мутить/банить/размучивать/разбанивать.
    Если реплай идёт на объявление самого бота (там есть text_mention
    с реальным пользователем внутри) — берём того, о ком там шла речь.
    Иначе — автора реплайнутого сообщения.
    """
    if replied.entities:
        for e in replied.entities:
            if e.type == "text_mention" and e.user:
                return e.user
    return replied.from_user


async def reply_permission_error(message: Message, e: Exception):
    await message.reply(
        "⚠️ Не получилось выполнить действие.\n"
        "Проверь, что бот — админ группы с правом «Блокировка участников».\n"
        f"Ошибка: {e}"
    )


@dp.message(F.text.regexp(r"(?i)^мут(?:\s+(\d+))?$"))
async def cmd_mute(message: Message):
    if not is_admin_user(message.from_user):
        return
    if not message.reply_to_message:
        await message.reply("Эту команду нужно писать реплаем на сообщение пользователя.")
        return

    match = re.match(r"(?i)^мут(?:\s+(\d+))?$", message.text)
    hours = int(match.group(1)) if match.group(1) else 1

    target = get_target_user(message.reply_to_message)
    until = datetime.now(timezone.utc) + timedelta(hours=hours)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=MUTED_PERMISSIONS,
            until_date=until,
        )
    except TelegramBadRequest as e:
        await reply_permission_error(message, e)
        return

    await message.reply(
        f"🦶 {mention(target.id, target.full_name)} лишается права слова на {hours} час(ов)\n"
        f"👮 Модератор: {mention(message.from_user.id, message.from_user.full_name)}"
    )


@dp.message(F.text.regexp(r"(?i)^размут$"))
async def cmd_unmute(message: Message):
    if not is_admin_user(message.from_user):
        return
    if not message.reply_to_message:
        await message.reply("Эту команду нужно писать реплаем на сообщение пользователя.")
        return

    target = get_target_user(message.reply_to_message)

    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=FULL_PERMISSIONS,
        )
    except TelegramBadRequest as e:
        await reply_permission_error(message, e)
        return

    await message.reply(
        f"✅ Пользователю {mention(target.id, target.full_name)} вернули право слова. "
        f"Можете свободно общаться! Но лучше следите за языком..."
    )


@dp.message(F.text.regexp(r"(?i)^бан$"))
async def cmd_ban(message: Message):
    if not is_admin_user(message.from_user):
        return
    if not message.reply_to_message:
        await message.reply("Эту команду нужно писать реплаем на сообщение пользователя.")
        return

    target = get_target_user(message.reply_to_message)

    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
    except TelegramBadRequest as e:
        await reply_permission_error(message, e)
        return

    await message.reply(
        f"🔴 {mention(target.id, target.full_name)} получает бан навсегда\n"
        f"🏹 Охотник: {mention(message.from_user.id, message.from_user.full_name)}"
    )


@dp.message(F.text.regexp(r"(?i)^разбан$"))
async def cmd_unban(message: Message):
    if not is_admin_user(message.from_user):
        return
    if not message.reply_to_message:
        await message.reply("Эту команду нужно писать реплаем на сообщение (например, на объявление о бане).")
        return

    target = get_target_user(message.reply_to_message)

    try:
        await bot.unban_chat_member(
            chat_id=message.chat.id, user_id=target.id, only_if_banned=True
        )
    except TelegramBadRequest as e:
        await reply_permission_error(message, e)
        return

    await message.reply(
        f"✅ Пользователь {mention(target.id, target.full_name)} разбанен. "
        f"Теперь его можно добавить в чат"
    )


def has_enough_letters(text: str, minimum: int = 3) -> bool:
    """
    Считает только БУКВЫ (не цифры, не пунктуацию, не пробелы, не эмодзи).
    Сообщение вроде "." или "!!" не пройдёт, а "ок" (2 буквы) — тоже нет,
    нужно минимум `minimum` буквенных символов.
    """
    letters_count = sum(1 for ch in text if ch.isalpha())
    return letters_count >= minimum


@dp.message(F.text | F.sticker | F.animation)
async def handle_perebiv_interrupt(message: Message):
    # Команды (текст, начинающийся с "/") игру не перебивают
    if message.text and message.text.startswith("/"):
        return

    # Обычный текст засчитывается только если в нём хотя бы 3 буквы.
    # Стикеры и гифки (у них нет message.text) засчитываются всегда.
    if message.text is not None and not has_enough_letters(message.text):
        return

    game = perebiv_games.get(message.chat.id)
    if game is None:
        return

    if is_admin_user(message.from_user):
        return

    user = message.from_user

    if game.get("holder_id") == user.id:
        return

    if game.get("task"):
        game["task"].cancel()

    game["holder_id"] = user.id

    status_msg = await message.reply(
        f"{mention(user.id, user.full_name)}, перебив! До победы осталось: "
        f"{game['duration']} сек."
    )
    game["status_message_id"] = status_msg.message_id

    task = asyncio.create_task(
        run_perebiv_timer(
            message.chat.id,
            status_msg.message_id,
            user.id,
            user.full_name,
            game["duration"],
        )
    )
    game["task"] = task


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
