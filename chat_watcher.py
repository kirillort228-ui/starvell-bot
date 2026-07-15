from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
import json

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import Database, StarvellAccount
from keyboards import chat_link_keyboard
from starvell_client import StarvellClient, StarvellApiError

AUTO_SEND_VERSION = "starvell-real-send-v1"
CHAT_FEATURES_VERSION = "chat-auto-events-v1"
AUTO_MESSAGE_DIAGNOSTICS_VERSION = "auto-message-diagnostics-v24"

logger = logging.getLogger(__name__)



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
    ntype = " ".join(
        [
            get_notification_type(last_message),
            str(last_message.get("type") or "").upper(),
        ]
    )
    content = str(last_message.get("content") or "").lower()
    combined = f"{ntype} {content}"

    review_words = (
        "REVIEW_CREATED",
        "REVIEW_ADDED",
        "REVIEW_RECEIVED",
        "FEEDBACK_CREATED",
        "ОСТАВИЛ ОТЗЫВ",
        "ОСТАВЛЕН ОТЗЫВ",
        "ОТЗЫВ ПОЛУЧЕН",
    )
    if any(word.lower() in combined.lower() for word in review_words):
        # Сообщение после 5 звёзд отправляется только тогда, когда рейтинг
        # явно присутствует в событии и действительно равен 5.
        # Раньше rating=None ошибочно считался подходящим событием.
        rating = get_notification_rating(last_message)
        if rating == 5:
            return "after_5_stars"
        return None

    problem_words = (
        "REFUND", "DISPUTE", "PROBLEM", "ISSUE", "CANCEL",
        "ВОЗВРАТ", "СПОР", "ПРОБЛЕМ", "ОТМЕН",
    )
    if any(word.lower() in combined.lower() for word in problem_words):
        return "problem_text"

    client_confirm_words = (
        "ORDER_COMPLETED", "CLIENT_CONFIRMED", "BUYER_CONFIRMED", "ORDER_FINISHED",
        "ПОКУПАТЕЛЬ ПОДТВЕРДИЛ", "КЛИЕНТ ПОДТВЕРДИЛ",
        "ПОЛУЧЕНИЕ ПОДТВЕРЖДЕНО", "ЗАКАЗ ЗАВЕРШЕН", "ЗАКАЗ ЗАВЕРШЁН",
    )
    if any(word.lower() in combined.lower() for word in client_confirm_words):
        return "after_client_confirm"

    seller_confirm_words = (
        "SELLER_CONFIRMED", "SELLER_CONFIRM", "ORDER_DELIVERED",
        "ORDER_SENT", "WAITING_BUYER_CONFIRM",
        "ПРОДАВЕЦ ПОДТВЕРДИЛ", "ТОВАР ВЫДАН",
        "ОЖИДАЕТ ПОДТВЕРЖДЕНИЯ ПОКУПАТЕЛЯ",
    )
    if any(word.lower() in combined.lower() for word in seller_confirm_words):
        return "after_seller_confirm"

    return None




async def auto_delivery_product_allowed(
    db: Database,
    account: StarvellAccount,
    chat: dict,
    last_message: dict,
) -> bool:
    offer_filter = _csv_set(await db.get_account_setting(account.user_id, "auto_delivery_offer_ids"))
    category_filter = _csv_set(await db.get_account_setting(account.user_id, "auto_delivery_category_ids"))

    # Auto-delivery requires at least one selected product/category.
    if not offer_filter and not category_filter:
        return False

    product = extract_chat_product_info(chat, last_message)
    offer_id = str(product.get("offerPublicId") or "").strip()
    category_id = str(product.get("categoryId") or "").strip()

    if offer_filter and offer_id and offer_id in offer_filter:
        return True
    if category_filter and category_id and category_id in category_filter:
        return True

    # Fallback: inspect all nested order/offer structures in the full chat.
    nested_offer = _find_nested_first(
        {"chat": chat, "message": last_message},
        ("offerPublicId", "offer_public_id", "offerUuid"),
    )
    nested_category = _find_nested_first(
        {"chat": chat, "message": last_message},
        ("categoryId", "category_id"),
    )

    if offer_filter and nested_offer and str(nested_offer).strip() in offer_filter:
        return True
    if category_filter and nested_category and str(nested_category).strip() in category_filter:
        return True

    return False


def is_purchase_paid_event(last_message: dict) -> bool:
    """
    Detects the moment when a new paid order appears.

    Starvell notification names may vary, so detection uses:
    - notification metadata/type;
    - message content;
    - order/payment status embedded in the message.

    Refund/cancel/completed/review events are explicitly excluded.
    """
    if not isinstance(last_message, dict):
        return False

    ntype = get_notification_type(last_message).upper()
    content = str(last_message.get("content") or "").upper()
    metadata = last_message.get("metadata") if isinstance(last_message.get("metadata"), dict) else {}

    status_values = []
    for key in ("status", "orderStatus", "paymentStatus", "state", "event", "type"):
        value = metadata.get(key)
        if value not in (None, ""):
            status_values.append(str(value).upper())
    order = metadata.get("order") if isinstance(metadata.get("order"), dict) else {}
    for key in ("status", "orderStatus", "paymentStatus", "state"):
        value = order.get(key)
        if value not in (None, ""):
            status_values.append(str(value).upper())

    haystack = " ".join([ntype, content, *status_values])

    blocked = (
        "REFUND", "CANCEL", "CANCELED", "CANCELLED", "DECLINED",
        "FAILED", "REVIEW", "COMPLETED", "FINISHED", "CLOSED",
        "SELLER_CONFIRMED", "CLIENT_CONFIRMED", "BUYER_CONFIRMED",
    )
    if any(marker in haystack for marker in blocked):
        return False

    paid_markers = (
        "ORDER_PAID",
        "PAYMENT_RECEIVED",
        "PAYMENT_SUCCESS",
        "PAID",
        "ORDER_PURCHASED",
        "PURCHASE_CREATED",
        "ORDER_CREATED",
        "NEW_ORDER",
        "ORDER_OPENED",
        "WAITING_SELLER",
        "PROCESSING",
        "ACTIVE",
        "ОПЛАЧЕН",
        "НОВЫЙ ЗАКАЗ",
        "ПОКУПАТЕЛЬ ОПЛАТИЛ",
    )
    if any(marker in haystack for marker in paid_markers):
        return True

    # Some Starvell notifications only contain ORDER_* without a stable suffix.
    return (
        last_message.get("type") == "NOTIFICATION"
        and "ORDER" in haystack
        and not any(marker in haystack for marker in blocked)
    )


async def maybe_send_auto_delivery_message(
    client: StarvellClient,
    db: Database,
    account: StarvellAccount,
    chat: dict,
    last_message: dict,
    *,
    my_user_id: int | None,
    my_username: str,
) -> None:
    """
    Sends one configured message after a paid/new purchase event for selected products.
    It does not transfer files or keys; it only sends a Starvell chat message.
    """
    if not is_purchase_paid_event(last_message):
        return

    if not await db.get_bool_account_setting(account.user_id, "auto_delivery"):
        return

    if not await auto_delivery_product_allowed(db, account, chat, last_message):
        return

    product = extract_chat_product_info(chat, last_message)
    offer_id = str(product.get("offerPublicId") or "").strip()
    category_id = str(product.get("categoryId") or "").strip()

    template = ""
    if offer_id:
        template = await db.get_account_setting(
            account.user_id,
            f"auto_delivery_product_message:{offer_id}",
            "",
        )
    if not template and category_id:
        template = await db.get_account_setting(
            account.user_id,
            f"auto_delivery_category_message:{category_id}",
            "",
        )
    if not template:
        template = await db.get_account_setting(account.user_id, "auto_delivery_message")

    if not template or not template.strip():
        return

    chat_id = str(chat.get("id") or "")
    message_id = str(last_message.get("id") or "")
    order_id = get_order_id_from_message(last_message)
    if not chat_id or not message_id:
        return

    dedupe_key = f"auto_delivery_sent:{account.id}:{order_id or message_id}"
    if await db.get_account_setting(account.user_id, dedupe_key):
        return

    interlocutor = get_interlocutor(chat, my_user_id=my_user_id, my_username=my_username)
    await client.send_chat_message(
        chat_id,
        render_template_text(
            template,
            username=interlocutor,
            seller=my_username,
            order_id=order_id,
        ),
    )
    await db.set_account_setting(account.user_id, dedupe_key, "1")


async def maybe_auto_confirm_orders(
    client: StarvellClient,
    db: Database,
    account: StarvellAccount,
    chat: dict,
    *,
    my_user_id: int | None,
    my_username: str,
) -> None:
    """Подтверждает активные заказы продавца только для выбранных offerPublicId."""
    enabled = await db.get_bool_account_setting(
        account.user_id,
        "auto_confirm",
        default=False,
    )
    if not enabled:
        return

    selected = _csv_set(
        await db.get_account_setting(account.user_id, "auto_confirm_offer_ids")
    )
    if not selected:
        return

    participants = chat.get("participants") or []
    interlocutor_id = None
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant_id = participant.get("id")
        username = str(participant.get("username") or "").strip().lower()
        if my_user_id is not None and participant_id == my_user_id:
            continue
        if my_username and username == my_username.lower():
            continue
        if participant_id is not None:
            interlocutor_id = int(participant_id)
            break

    if interlocutor_id is None:
        return

    page = await client.get_chat_page(interlocutor_id)
    additional = page.get("additionalData") if isinstance(page, dict) else None
    orders = additional.get("activeOrdersAsSeller") if isinstance(additional, dict) else None
    if not isinstance(orders, list):
        return

    for order in orders:
        if not isinstance(order, dict):
            continue

        order_id = str(order.get("id") or "").strip()
        offer_id = str(order.get("offerPublicId") or "").strip()
        seller_completed_at = order.get("sellerCompletedAt")

        if not order_id or not offer_id:
            continue
        if offer_id not in selected:
            continue
        if seller_completed_at:
            continue

        dedupe_key = f"auto_confirm_sent:{account.id}:{order_id}"
        if await db.get_account_setting(account.user_id, dedupe_key):
            continue

        result = await client.mark_seller_completed(order_id)
        await db.set_account_setting(account.user_id, dedupe_key, "1")
        logger.info(
            "AUTO_CONFIRM_SUCCESS account_id=%s order_id=%s offer_id=%s result=%s",
            account.id,
            order_id,
            offer_id,
            json.dumps(result, ensure_ascii=False, default=str)[:1000],
        )


async def maybe_send_event_auto_message(client: StarvellClient, db: Database, account: StarvellAccount, chat: dict, last_message: dict, *, my_user_id: int | None, my_username: str) -> None:
    """
    Sends configured chat texts after Starvell notification events:
    - after_5_stars for REVIEW_CREATED / 5-star review events
    - problem_text for refunds/disputes/problems
    - after_client_confirm for order completed/client confirmation
    - after_seller_confirm for seller delivery/confirmation events
    """
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




def _find_offer_public_id_in_order_context(chat: dict, last_message: dict) -> str:
    """
    Prefer offer/order objects so we don't accidentally use participant/user publicId.
    """
    containers = []
    order = last_message.get("order") if isinstance(last_message.get("order"), dict) else None
    if order:
        containers.append(order)
        for key in ("offer", "offerDetails", "details"):
            if isinstance(order.get(key), dict):
                containers.append(order.get(key))
    for key in ("offer", "offerDetails", "details"):
        if isinstance(last_message.get(key), dict):
            containers.append(last_message.get(key))
        if isinstance(chat.get(key), dict):
            containers.append(chat.get(key))

    for obj in containers:
        found = _find_nested_first(obj, ("offerPublicId", "offer_public_id", "publicId", "public_id", "uuid"))
        if found:
            return str(found).strip()
    return ""


def extract_chat_product_info(chat: dict, last_message: dict) -> dict:
    source = {"chat": chat, "lastMessage": last_message}
    offer_public_id = _find_offer_public_id_in_order_context(chat, last_message) or _find_nested_first(source, ("offerPublicId", "offer_public_id", "public_id", "uuid"))
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


async def check_auto_confirm_open_orders(
    db: Database,
    account: StarvellAccount,
) -> None:
    enabled = await db.get_bool_account_setting(account.user_id, "auto_confirm", default=False)
    if not enabled:
        logger.info("AUTO_CONFIRM_SKIP account_id=%s reason=disabled", account.id)
        return

    selected = _csv_set(await db.get_account_setting(account.user_id, "auto_confirm_offer_ids"))
    if not selected:
        logger.info("AUTO_CONFIRM_SKIP account_id=%s reason=no_selected_products", account.id)
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats = await client.get_open_order_chats(offset=0, limit=50, role="SELLER")
        logger.warning(
            "AUTO_CONFIRM_SCAN account_id=%s open_chats=%s selected_products=%s",
            account.id, len(chats), len(selected)
        )

        for chat in chats:
            participants = chat.get("participants") or []
            interlocutor_id = None

            for participant in participants:
                if not isinstance(participant, dict):
                    continue
                username = str(participant.get("username") or "").strip().lower()
                if account.username and username == account.username.lower():
                    continue
                if participant.get("id") is not None:
                    interlocutor_id = int(participant["id"])
                    break

            if interlocutor_id is None:
                logger.info(
                    "AUTO_CONFIRM_SKIP account_id=%s chat_id=%s reason=no_interlocutor",
                    account.id, chat.get("id")
                )
                continue

            page = await client.get_chat_page(interlocutor_id)
            additional = page.get("additionalData") if isinstance(page, dict) else {}
            orders = additional.get("activeOrdersAsSeller") if isinstance(additional, dict) else []
            if not isinstance(orders, list):
                orders = []

            logger.warning(
                "AUTO_CONFIRM_CHAT account_id=%s chat_id=%s orders=%s",
                account.id, chat.get("id"), len(orders)
            )

            for order in orders:
                if not isinstance(order, dict):
                    continue

                order_id = str(order.get("id") or "").strip()
                offer_id = str(order.get("offerPublicId") or "").strip()
                seller_completed_at = order.get("sellerCompletedAt")

                if not order_id or not offer_id:
                    continue
                if offer_id not in selected:
                    logger.info(
                        "AUTO_CONFIRM_SKIP account_id=%s order_id=%s offer_id=%s reason=product_not_selected",
                        account.id, order_id, offer_id
                    )
                    continue
                if seller_completed_at:
                    logger.info(
                        "AUTO_CONFIRM_SKIP account_id=%s order_id=%s reason=already_completed",
                        account.id, order_id
                    )
                    continue

                dedupe_key = f"auto_confirm_sent:{account.id}:{order_id}"
                if await db.get_account_setting(account.user_id, dedupe_key):
                    logger.info(
                        "AUTO_CONFIRM_SKIP account_id=%s order_id=%s reason=deduplicated",
                        account.id, order_id
                    )
                    continue

                result = await client.mark_seller_completed(order_id)
                await db.set_account_setting(account.user_id, dedupe_key, "1")
                logger.warning(
                    "AUTO_CONFIRM_SUCCESS account_id=%s order_id=%s offer_id=%s result=%s",
                    account.id,
                    order_id,
                    offer_id,
                    json.dumps(result, ensure_ascii=False, default=str)[:1000],
                )
    finally:
        await client.close()


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

            saved_id = await db.get_last_message_id(account.id, chat_id)

            # "Текст при игноре" проверяется каждый цикл, а не только в момент
            # появления сообщения. Иначе задержка 60 минут никогда не срабатывала.
            try:
                await maybe_send_ignore_followup(
                    client,
                    db,
                    account,
                    chat,
                    last_message,
                    my_user_id=my_user_id,
                    my_username=my_username,
                )
            except StarvellApiError as exc:
                logger.exception(
                    "AUTO_MESSAGE ignore_text failed account_id=%s chat_id=%s error=%s",
                    account.id,
                    chat_id,
                    exc,
                )

            if saved_id == last_message_id:
                continue

            await db.save_last_message_id(account.id, chat_id, last_message_id)

            logger.info(
                "STARVELL_NEW_MESSAGE account_id=%s chat_id=%s type=%s "
                "notification_type=%s payload=%s",
                account.id,
                chat_id,
                last_message.get("type"),
                get_notification_type(last_message),
                json.dumps(last_message, ensure_ascii=False, default=str)[:4000],
            )

            # Системные события Starvell часто уже помечены прочитанными.
            # Автовыдача и тексты после событий должны работать независимо от unread.
            try:
                await maybe_send_auto_delivery_message(
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
            except StarvellApiError as exc:
                logger.exception(
                    "AUTO_MESSAGE event/delivery failed account_id=%s chat_id=%s "
                    "message=%s error=%s",
                    account.id,
                    chat_id,
                    json.dumps(last_message, ensure_ascii=False, default=str)[:4000],
                    exc,
                )

            # Приветствие отправляется только на новое непрочитанное сообщение клиента.
            if not is_unread_chat(chat, unread_chat_ids):
                continue

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
            except StarvellApiError as exc:
                logger.exception(
                    "AUTO_MESSAGE greeting failed account_id=%s chat_id=%s "
                    "message=%s error=%s",
                    account.id,
                    chat_id,
                    json.dumps(last_message, ensure_ascii=False, default=str)[:4000],
                    exc,
                )

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
