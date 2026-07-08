from __future__ import annotations

from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import Database, StarvellAccount
from keyboards import chat_link_keyboard
from starvell_client import StarvellClient, StarvellApiError

AUTO_SEND_VERSION = "starvell-real-send-v1"
CHAT_FEATURES_VERSION = "chat-auto-events-v1"



def render_template_text(template: str, *, username: str, seller: str, order_id: str = "") -> str:
    return (
        (template or "")
        .replace("{username}", username or "клиент")
        .replace("{seller}", seller or "")
        .replace("{order_id}", order_id or "")
    ).strip()


def is_client_default_message(last_message: dict, my_user_id: int | None) -> bool:
    if not last_message:
        return False
    if last_message.get("type") not in (None, "", "DEFAULT"):
        return False
    author_id = last_message.get("authorId")
    if my_user_id is not None and author_id == my_user_id:
        return False
    return bool((last_message.get("content") or "").strip())


def get_order_id_from_message(last_message: dict) -> str:
    order = last_message.get("order") or {}
    if isinstance(order, dict):
        return str(order.get("id") or order.get("orderId") or "")
    metadata = last_message.get("metadata") or {}
    if isinstance(metadata, dict):
        return str(metadata.get("orderId") or metadata.get("order_id") or "")
    return ""


def parse_starvell_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def get_notification_type(last_message: dict) -> str:
    metadata = last_message.get("metadata") or {}
    if isinstance(metadata, dict):
        return str(
            metadata.get("notificationType")
            or metadata.get("type")
            or metadata.get("event")
            or ""
        ).upper()
    return ""


def get_notification_rating(last_message: dict) -> int | None:
    candidates = []
    metadata = last_message.get("metadata") or {}
    if isinstance(metadata, dict):
        candidates.extend([
            metadata.get("rating"),
            metadata.get("stars"),
            metadata.get("score"),
            metadata.get("reviewRating"),
        ])
        review = metadata.get("review")
        if isinstance(review, dict):
            candidates.extend([review.get("rating"), review.get("stars"), review.get("score")])

    for obj_key in ("review", "feedback"):
        obj = last_message.get(obj_key)
        if isinstance(obj, dict):
            candidates.extend([obj.get("rating"), obj.get("stars"), obj.get("score")])

    content = str(last_message.get("content") or "")
    if "5" in content and ("⭐" in content or "зв" in content.lower()):
        candidates.append(5)

    for value in candidates:
        try:
            if value is not None and str(value).strip():
                return int(float(str(value).replace(",", ".")))
        except Exception:
            continue
    return None


def chat_event_setting_key(last_message: dict) -> str | None:
    ntype = get_notification_type(last_message)

    if "REVIEW" in ntype:
        rating = get_notification_rating(last_message)
        # If Starvell does not expose rating in metadata, send the configured message on REVIEW_CREATED.
        if rating is None or rating >= 5:
            return "after_5_stars"
        return None

    if any(word in ntype for word in ("REFUND", "DISPUTE", "PROBLEM", "ISSUE", "CANCEL")):
        return "problem_text"

    # Client confirmed / order completed.
    if any(word in ntype for word in ("ORDER_COMPLETED", "CLIENT_CONFIRMED", "BUYER_CONFIRMED", "ORDER_FINISHED")):
        return "after_client_confirm"

    # Seller marked order as delivered / confirmed.
    if any(word in ntype for word in ("SELLER_CONFIRMED", "SELLER_CONFIRM", "ORDER_DELIVERED", "ORDER_SENT", "WAITING_BUYER_CONFIRM")):
        return "after_seller_confirm"

    return None


async def maybe_send_event_auto_message(client: StarvellClient, db: Database, account: StarvellAccount, chat: dict, last_message: dict, *, my_user_id: int | None, my_username: str) -> None:
    """
    Sends configured chat texts after Starvell notification events:
    - after_5_stars for REVIEW_CREATED / 5-star review events
    - problem_text for refunds/disputes/problems
    - after_client_confirm for order completed/client confirmation
    - after_seller_confirm for seller delivery/confirmation events
    """
    if last_message.get("type") != "NOTIFICATION":
        return

    setting_key = chat_event_setting_key(last_message)
    if not setting_key:
        return

    chat_id = str(chat.get("id") or "")
    message_id = str(last_message.get("id") or "")
    if not chat_id or not message_id:
        return

    dedupe_key = f"chat_event_sent:{account.id}:{message_id}:{setting_key}"
    if await db.get_account_setting(account.user_id, dedupe_key):
        return

    template = await db.get_account_setting(account.user_id, setting_key)
    if not template or not template.strip():
        return

    interlocutor = get_interlocutor(chat, my_user_id=my_user_id, my_username=my_username)
    order_id = get_order_id_from_message(last_message)
    await client.send_chat_message(
        chat_id,
        render_template_text(template, username=interlocutor, seller=my_username, order_id=order_id),
    )
    await db.set_account_setting(account.user_id, dedupe_key, "1")


async def maybe_send_ignore_followup(client: StarvellClient, db: Database, account: StarvellAccount, chat: dict, last_message: dict, *, my_user_id: int | None, my_username: str) -> None:
    """
    Sends "ignore_text" if the last unread client message remains unanswered for a delay.
    Default delay is 60 minutes. It sends only once per last message id.
    """
    if not is_client_default_message(last_message, my_user_id):
        return

    ignore_text = await db.get_account_setting(account.user_id, "ignore_text")
    if not ignore_text or not ignore_text.strip():
        return

    try:
        delay_minutes = int(await db.get_account_setting(account.user_id, "ignore_delay_minutes", "60") or "60")
    except Exception:
        delay_minutes = 60
    delay_minutes = max(5, delay_minutes)

    created_at = parse_starvell_dt(last_message.get("createdAt"))
    if not created_at:
        return
    if datetime.now(timezone.utc) - created_at < timedelta(minutes=delay_minutes):
        return

    chat_id = str(chat.get("id") or "")
    message_id = str(last_message.get("id") or "")
    if not chat_id or not message_id:
        return

    dedupe_key = f"ignore_sent:{account.id}:{chat_id}:{message_id}"
    if await db.get_account_setting(account.user_id, dedupe_key):
        return

    interlocutor = get_interlocutor(chat, my_user_id=my_user_id, my_username=my_username)
    order_id = get_order_id_from_message(last_message)
    await client.send_chat_message(
        chat_id,
        render_template_text(ignore_text, username=interlocutor, seller=my_username, order_id=order_id),
    )
    await db.set_account_setting(account.user_id, dedupe_key, "1")




def _find_nested_first(obj, keys: tuple[str, ...]):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        for value in obj.values():
            found = _find_nested_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_nested_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def extract_chat_product_info(chat: dict, last_message: dict) -> dict:
    source = {"chat": chat, "lastMessage": last_message}
    offer_public_id = _find_nested_first(source, ("offerPublicId", "publicId", "uuid"))
    category_id = _find_nested_first(source, ("categoryId", "category_id"))
    game_id = _find_nested_first(source, ("gameId", "game_id"))
    title = (
        _find_nested_first(source, ("briefDescription", "title", "name"))
        or get_order_short_info(last_message)
        or "Товар"
    )
    return {
        "offerPublicId": str(offer_public_id).strip() if offer_public_id else "",
        "categoryId": str(category_id).strip() if category_id else "",
        "gameId": str(game_id).strip() if game_id else "",
        "title": str(title).strip(),
    }


def _csv_set(value: str | None) -> set[str]:
    return {part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()}


async def auto_responder_product_allowed(db: Database, account: StarvellAccount, chat: dict, last_message: dict) -> bool:
    """
    If no product filter is configured, autoresponder works for all products.
    If offerPublicId or categoryIds are configured, autoresponder works only for matching chats/orders.
    """
    offer_filter = _csv_set(await db.get_account_setting(account.user_id, "auto_responder_offer_ids"))
    category_filter = _csv_set(await db.get_account_setting(account.user_id, "auto_responder_category_ids"))

    if not offer_filter and not category_filter:
        return True

    product = extract_chat_product_info(chat, last_message)
    if offer_filter and product.get("offerPublicId") in offer_filter:
        return True
    if category_filter and product.get("categoryId") in category_filter:
        return True
    return False


async def maybe_send_auto_response(client: StarvellClient, db: Database, account: StarvellAccount, chat: dict, last_message: dict, *, my_user_id: int | None, my_username: str) -> None:
    """
    Sends real Starvell chat messages for saved text settings:
    - greeting: once per chat, if enabled/text exists
    - auto_responder: every new unread client DEFAULT message, if enabled/text exists

    It uses the real endpoint /api/messages/send.
    """
    if not is_client_default_message(last_message, my_user_id):
        return

    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return

    interlocutor = get_interlocutor(chat, my_user_id=my_user_id, my_username=my_username)
    order_id = get_order_id_from_message(last_message)

    # Greeting: send once per chat when text exists.
    greeting_text = await db.get_account_setting(account.user_id, "greeting")
    greeting_sent_key = f"greeting_sent:{account.id}:{chat_id}"
    greeting_already_sent = await db.get_account_setting(account.user_id, greeting_sent_key)
    if greeting_text and greeting_text.strip() and not greeting_already_sent:
        await client.send_chat_message(
            chat_id,
            render_template_text(greeting_text, username=interlocutor, seller=my_username, order_id=order_id),
        )
        await db.set_account_setting(account.user_id, greeting_sent_key, "1")

    # Auto responder: can be limited to selected offerPublicId/categoryIds.
    auto_text = await db.get_account_setting(account.user_id, "auto_responder")
    auto_enabled = await db.get_bool_account_setting(account.user_id, "auto_responder_enabled", default=True)
    if auto_enabled and auto_text and auto_text.strip():
        if await auto_responder_product_allowed(db, account, chat, last_message):
            await client.send_chat_message(
                chat_id,
                render_template_text(auto_text, username=interlocutor, seller=my_username, order_id=order_id),
            )


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

            try:
                await maybe_send_auto_response(
                    client,
                    db,
                    account,
                    chat,
                    last_message,
                    my_user_id=my_user_id,
                    my_username=my_username,
                )
                await maybe_send_event_auto_message(
                    client,
                    db,
                    account,
                    chat,
                    last_message,
                    my_user_id=my_user_id,
                    my_username=my_username,
                )
                await maybe_send_ignore_followup(
                    client,
                    db,
                    account,
                    chat,
                    last_message,
                    my_user_id=my_user_id,
                    my_username=my_username,
                )
            except StarvellApiError:
                # Do not break notifications if auto-send fails.
                pass

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
