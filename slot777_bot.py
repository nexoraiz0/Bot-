"""
Telegram-бот: слот-машина (🎰) с джекпотом и выбором подарка из ячеек.

Установка:
    pip install aiogram

Запуск:
    python slot777_bot.py

Токен бота берётся из переменной окружения BOT_TOKEN
(задаётся в настройках хостинга — например, Railway → Variables).
Никогда не вписывай сам токен прямо в этот файл.
"""

import asyncio
import logging
import os
import random
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
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

# Призы под подарками — количество звёзд Telegram под каждым из подарков.
# При джекпоте этот список перемешивается случайно между кнопками.
STAR_PRIZES = [
    25, 15, 15, 15, 25, 15, 15, 15, 25,
    15, 15, 25, 50, 25, 15, 15, 15, 50,
    25, 50, 25, 15, 15, 25, 25,
]
STAR_EMOJI = "⭐️"

# Сколько подарков в игре и как разложить их в сетку кнопок
TOTAL_GIFTS = len(STAR_PRIZES)
GIFTS_PER_ROW = 5

# Активные игры-джекпоты: game_id -> данные об игре
active_games = {}


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

    # Пересланные сообщения (в том числе с чужим настоящим броском 🎰)
    # игнорируем — реагируем только на бросок, сделанный прямо в этом чате.
    if message.forward_origin is not None:
        return

    user = message.from_user
    symbols = decode_slot_symbols(dice.value)
    combo = " | ".join(symbols)

    if dice.value == JACKPOT_VALUE:
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
    # Во всех остальных случаях (включая "почти джекпот") бот молчит —
    # реагирует только на настоящий джекпот 777.


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


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
