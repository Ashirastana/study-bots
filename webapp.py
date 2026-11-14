"""Мини-приложение (игры) для учебных ботов: ссылка с прогрессом ученицы и кнопки «🎮 Играть».

Приложение лежит на GitHub Pages (репозиторий Ashirastana/lingua-app). В ссылку передаём,
какие слова уже выучены (k — битовая маска) и слова сегодняшнего дня (t), — без личных данных.
"""
import base64
import logging
import os

from aiogram.types import InlineKeyboardButton, MenuButtonWebApp, WebAppInfo

log = logging.getLogger("webapp")
URL = os.getenv("WEBAPP_URL", "https://ashirastana.github.io/lingua-app/").strip()
PLAY = "🎮 Играть"


def bits(idx) -> str:
    idx = [i for i in idx if i >= 0]
    if not idx:
        return ""
    b = bytearray(max(idx) // 8 + 1)
    for i in idx:
        b[i >> 3] |= 1 << (i & 7)
    return base64.urlsafe_b64encode(bytes(b)).decode().rstrip("=")


def url(lang: str, known, today_idx, day) -> str:
    return (f"{URL}?lang={lang}&k={bits(known)}&t={','.join(str(i) for i in today_idx)}"
            f"&d={day.isoformat()}")


def button(link: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=PLAY, web_app=WebAppInfo(url=link))


async def set_menu(bot, uid: int, link: str):
    """Кнопка слева от поля ввода — сразу открывает приложение со свежими словами."""
    try:
        await bot.set_chat_menu_button(chat_id=uid, menu_button=MenuButtonWebApp(text="🎮 Играть",
                                                                                   web_app=WebAppInfo(url=link)))
    except Exception as e:
        log.warning("menu button %s: %s", uid, e)
