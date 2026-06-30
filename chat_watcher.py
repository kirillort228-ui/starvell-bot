from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import Database, StarvellAccount
from keyboards import chat_link_keyboard
from starvell_client import StarvellClient, StarvellApiError


def extract_chats(data: dict) -> list[dict]:
    return data.get("pageProps", {}).get("chats", [])


def extract_user(data: dict) -> dict:
    return data.get("pageProps", {}).get("user", {})


def get_chat_link(chat_id: str) -> str:
    return f"https://starvell.com/chat/{chat_id}"


def get_interlocutor(chat: dict, my_user_id: int | None = None, my_username: str | None = None) -> str:
    for participant in chat.get("participants", []):
        username = participant.get("username") or "Неизвестно"
        user_id = participant.get("id")
        if my_user_id is not None and user_id == my_user_id:
            continue
        if my_username and username.lower() == my_username.lower():
            continue
        return username
    return "Неизвестно"


def format_notification_message(last_message: dict) -> str:
    content = (last_message.get("content") or "").strip()
    if content:
        return content

    if last_message.get("type") == "NOTIFICATION":
        metadata = last_message.get("metadata") or {}
        notification_type = metadata.get("notificationType", "уведомление")
        translations = {
            "ORDER_COMPLETED": "Заказ завершён",
            "ORDER_REFUND": "Возврат по заказу",
            "REVIEW_CREATED": "Новый отзыв",
            "ORDER_CREATED": "Новый заказ",
            "ORDER_PAID": "Заказ оплачен",
            "ORDER_CANCELLED": "Заказ отменён",
        }
        return translations.get(notification_type, f"Системное уведомление: {notification_type}")

    return "Новое сообщение"


def is_unread_chat(chat: dict, unread_chat_ids: list[str]) -> bool:
    chat_id = chat.get("id")
    unread_count = int(chat.get("unreadMessageCount") or 0)
    return unread_count > 0 or chat_id in unread_chat_ids


def get_order_short_info(last_message: dict) -> str | None:
    order = last_message.get("order") or {}
    offer = order.get("offerDetails") or {}
    game = (offer.get("game") or {}).get("name")
    category = (offer.get("category") or {}).get("name")
    desc = ((offer.get("descriptions") or {}).get("rus") or {}).get("briefDescription")

    parts = [p for p in [game, category, desc] if p]
    if not parts:
        return None
    text = " / ".join(parts)
    if len(text) > 300:
        text = text[:297] + "..."
    return text


async def baseline_account_messages(db: Database, account_id: int, data: dict) -> None:
    """Save current lastMessage IDs so the bot won't notify about old messages after adding account."""
    for chat in extract_chats(data):
        chat_id = chat.get("id")
        last_message = chat.get("lastMessage") or {}
        message_id = last_message.get("id")
        if chat_id and message_id:
            await db.save_last_message_id(account_id, chat_id, message_id)


async def check_one_account(bot: Bot, db: Database, account: StarvellAccount) -> None:
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_chats()
        user = extract_user(data)
        my_user_id = user.get("id")
        my_username = user.get("username") or account.username or "Starvell"
        unread_chat_ids = user.get("unreadChatIds") or []

        if my_username != account.username:
            await db.update_account_username(account.id, my_username)

        for chat in extract_chats(data):
            chat_id = chat.get("id")
            last_message = chat.get("lastMessage") or {}
            last_message_id = last_message.get("id")

            if not chat_id or not last_message_id:
                continue

            if not is_unread_chat(chat, unread_chat_ids):
                # Even if no unread, update baseline to avoid later old notifications after toggling states.
                if await db.get_last_message_id(account.id, chat_id) is None:
                    await db.save_last_message_id(account.id, chat_id, last_message_id)
                continue

            saved_id = await db.get_last_message_id(account.id, chat_id)
            if saved_id == last_message_id:
                continue

            await db.save_last_message_id(account.id, chat_id, last_message_id)

            interlocutor = get_interlocutor(chat, my_user_id=my_user_id, my_username=my_username)
            message_text = format_notification_message(last_message)
            chat_link = get_chat_link(chat_id)
            order_info = get_order_short_info(last_message)
            unread_count = int(chat.get("unreadMessageCount") or 0)

            text = (
                "💬 Новое сообщение на Starvell\n\n"
                f"👤 Аккаунт: {my_username}\n"
                f"👥 Собеседник: {interlocutor}\n"
                f"📩 Непрочитанных: {unread_count}\n"
                f"💭 Сообщение: {message_text}"
            )
            if order_info:
                text += f"\n\n🛒 Заказ: {order_info}"

            try:
                await bot.send_message(account.user_id, text, reply_markup=chat_link_keyboard(chat_link))
            except (TelegramBadRequest, TelegramForbiddenError):
                # User blocked bot or chat is unavailable. Keep watcher alive.
                pass

        await db.set_account_error(account.id, None)

    except StarvellApiError as error:
        await db.set_account_error(account.id, str(error)[:500])
    finally:
        await client.close()
