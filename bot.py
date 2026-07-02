from __future__ import annotations

import asyncio
import re
from html import escape
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from chat_watcher import baseline_account_messages, check_one_account, extract_chats, extract_user, get_chat_link, get_interlocutor, format_notification_message
from config import load_config
from database import Database
from keyboards import (
    account_actions_keyboard,
    accounts_keyboard,
    admin_topup_keyboard,
    cancel_keyboard,
    account_settings_back_keyboard,
    account_setting_text_keyboard,
    account_settings_menu_keyboard,
    confirm_reminder_menu_keyboard,
    confirm_reminder_period_keyboard,
    confirm_reminder_time_keyboard,
    main_keyboard,
    no_subscription_keyboard,
    profile_back_keyboard,
    profile_chats_orders_keyboard,
    profile_menu_keyboard,
    proxies_keyboard,
    seller_profile_keyboard,
    top_sellers_menu_keyboard,
    top_up_keyboard,
)
from proxy_utils import check_proxy, hide_proxy, normalize_proxy, validate_proxy
from starvell_client import StarvellClient, StarvellApiError, extract_offer_public_id

config = load_config()
bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database(config.database_path)


class AddAccount(StatesGroup):
    waiting_cookie = State()


class SetAccountProxy(StatesGroup):
    waiting_proxy = State()


class AddProxy(StatesGroup):
    waiting_proxy = State()


class TopUpBalance(StatesGroup):
    waiting_amount = State()


class FindSellerProfile(StatesGroup):
    waiting_username = State()


class AccountSettingText(StatesGroup):
    waiting_value = State()


class ConfirmReminderTime(StatesGroup):
    waiting_time = State()


SALES_STATS_VERSION = "sales-baseprice-v2"

SUBSCRIPTION_PLANS = {
    14: 39900,
    30: 69900,
    90: 139900,
    180: 269900,
}


async def get_subscription_text(user_id: int) -> str:
    raw = await db.get_subscription_until(user_id)
    if not raw:
        return "отсутствует"

    try:
        expires_at = datetime.fromisoformat(raw)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception:
        return "отсутствует"

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return "отсутствует"

    return f"до {expires_at.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"


async def has_active_subscription(user_id: int) -> bool:
    raw = await db.get_subscription_until(user_id)
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)
    except Exception:
        return False


async def is_free_access_user(message_or_user_id) -> bool:
    """
    Free access is checked by Telegram username, not by Starvell username.
    """
    free_usernames = config.free_usernames or set()

    # Accept Message, CallbackQuery.from_user, or raw user object.
    user = getattr(message_or_user_id, "from_user", None) or message_or_user_id
    username = getattr(user, "username", None)

    if not username:
        return False

    return username.strip().lower().lstrip("@") in free_usernames


async def show_no_subscription(message: Message) -> None:
    await message.answer(
        "😢 <b>У вас нет активной подписки!</b>",
        reply_markup=no_subscription_keyboard(),
    )


async def require_subscription(message: Message) -> bool:
    if await has_active_subscription(message.from_user.id):
        return True

    if await is_free_access_user(message):
        return True

    await show_no_subscription(message)
    return False



def parse_starvell_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def is_last_30_days(value: str | None) -> bool:
    dt = parse_starvell_datetime(value)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(days=30)


def collect_items_by_keys(obj, keys: tuple[str, ...]) -> list:
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, list):
                return value
        for value in obj.values():
            found = collect_items_by_keys(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        return obj
    return []


def extract_top_sellers(data: dict) -> list[dict]:
    page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
    return collect_items_by_keys(
        page_props,
        ("sellers", "topSellers", "users", "items", "leaders", "profiles"),
    )


def seller_username(seller: dict) -> str:
    return (
        seller.get("username")
        or seller.get("name")
        or (seller.get("user") or {}).get("username")
        or "unknown"
    )


def seller_rating(seller: dict):
    return seller.get("rating") or seller.get("stars") or (seller.get("user") or {}).get("rating")


def seller_reviews(seller: dict):
    return seller.get("reviewsCount") or seller.get("reviews") or (seller.get("user") or {}).get("reviewsCount") or 0


def format_top_sellers_list(sellers: list[dict]) -> str:
    if not sellers:
        return "🏆 <b>Топ продавцов STARVELL</b>\n\nНе удалось получить список продавцов."

    text = (
        "🏆 <b>Топ продавцов STARVELL</b>\n\n"
        "Топ собран автоматически с открытых категорий Starvell и отсортирован по количеству отзывов.\n\n"
    )
    for index, seller in enumerate(sellers[:30], start=1):
        username = seller_username(seller)
        rating = seller_rating(seller)
        reviews = seller_reviews(seller)
        try:
            rating_text = f"{float(rating):.2f}"
        except Exception:
            rating_text = str(rating) if rating else "—"
        try:
            reviews_text = f"{int(reviews):,}".replace(",", " ")
        except Exception:
            reviews_text = str(reviews)
        text += f"🏆 <b>#{index}: {escape(username)}</b> — {rating_text}⭐ | отзывов: {reviews_text}\n"

    if len(sellers) > 30:
        text += f"\nПоказано 30 из {len(sellers)} найденных продавцов."
    return text


def extract_order_date(order: dict) -> str | None:
    return order.get("createdAt") or order.get("created_at") or order.get("updatedAt") or order.get("paidAt")


def extract_order_amount_kopecks(order: dict) -> int:
    """
    Starvell order money values are in kopecks.
    basePrice is the seller's price without project commission.
    totalPrice includes service/project fee, so it is used only as a fallback.
    """
    for key in ("basePrice", "amount", "price", "rubAmount", "sellerAmount", "cost", "totalPrice", "total", "totalRubAmount"):
        value = order.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def extract_nested_value(obj, keys: tuple[str, ...]):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        for value in obj.values():
            found = extract_nested_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = extract_nested_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def extract_order_buyer_username(order: dict) -> str:
    # In seller order responses Starvell often stores buyer info in "user".
    buyer = order.get("buyer") or order.get("customer") or order.get("client") or order.get("user") or {}
    if isinstance(buyer, dict):
        return buyer.get("username") or buyer.get("name") or "—"
    return "—"


def extract_order_seller_username(order: dict) -> str:
    seller = order.get("seller") or order.get("vendor") or order.get("owner") or {}
    if isinstance(seller, dict):
        return seller.get("username") or seller.get("name") or ""
    return ""


def is_sale_order(order: dict, my_username: str | None = None, my_user_id: int | None = None) -> bool:
    """
    Tries to separate sales from purchases.
    In the current Starvell seller response, sale orders have sellerId equal to current user id
    and buyer data is stored in order["user"].
    """
    role = str(order.get("role") or order.get("orderRole") or order.get("type") or "").upper()
    if role in ("SELL", "SALE", "SALES", "SELLER"):
        return True
    if role in ("BUY", "PURCHASE", "BUYER"):
        return False

    if my_user_id is not None:
        try:
            if int(order.get("sellerId")) == int(my_user_id):
                return True
            if int(order.get("buyerId")) == int(my_user_id):
                return False
        except Exception:
            pass

    my_username_l = (my_username or "").strip().lower()
    seller_username = extract_order_seller_username(order).strip().lower()
    buyer_username = extract_order_buyer_username(order).strip().lower()

    if my_username_l and seller_username:
        return seller_username == my_username_l
    if my_username_l and buyer_username and buyer_username == my_username_l:
        return False

    # In account/orders for sellers Starvell commonly includes buyer data.
    return extract_order_buyer_username(order) != "—"


def is_successful_sale(order: dict) -> bool:
    raw = str(order.get("status") or order.get("state") or order.get("orderStatus") or "").upper()
    if not raw:
        return True
    return raw in ("COMPLETED", "DONE", "FINISHED", "CLOSED", "SUCCESS", "SUCCESSFUL")


def extract_order_review(order: dict) -> tuple[str | None, str | None]:
    review_obj = (
        order.get("review")
        or order.get("buyerReview")
        or order.get("customerReview")
        or order.get("feedback")
        or {}
    )

    rating = None
    text = None

    if isinstance(review_obj, dict):
        rating = review_obj.get("rating") or review_obj.get("stars") or review_obj.get("score")
        text = review_obj.get("text") or review_obj.get("content") or review_obj.get("comment") or review_obj.get("message")

    if not rating:
        rating = extract_nested_value(order, ("reviewRating", "ratingByBuyer", "buyerRating", "stars"))
    if not text:
        text = extract_nested_value(order, ("reviewText", "reviewComment", "feedbackText", "comment"))

    if text:
        text = str(text).strip()
    if rating:
        rating = str(rating).strip()

    return rating, text


def format_review_short(order: dict) -> str:
    rating, text = extract_order_review(order)
    if not rating and not text:
        return "отзыва пока нет"

    parts = []
    if rating:
        parts.append(f"{rating}⭐")
    if text:
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) > 90:
            cleaned = cleaned[:87] + "..."
        parts.append(f"«{escape(cleaned)}»")
    return " ".join(parts)


def format_sales_stats(username: str, orders_data: dict, *, days: int = 30) -> str:
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    all_orders = page_props.get("orders") or []
    user = page_props.get("user") or {}
    my_user_id = user.get("id")

    recent_sales = []
    for order in all_orders:
        if not is_last_30_days(extract_order_date(order)):
            continue
        if not is_sale_order(order, username, my_user_id):
            continue
        if not is_successful_sale(order):
            continue
        recent_sales.append(order)

    total_gross_kopecks = sum(extract_order_amount_kopecks(order) for order in recent_sales)
    with_reviews = [order for order in recent_sales if extract_order_review(order) != (None, None)]

    debug_source = page_props.get("_ordersSource") or orders_data.get("_ordersSource") or "account/orders"
    text = (
        f"📈 <b>Продажи за последние {days} дней</b>\n\n"
        f"👤 Аккаунт: <b>{escape(username)}</b>\n"
        f"🛍 Количество продаж: <b>{len(recent_sales)}</b>\n"
        f"⭐ Отзывов клиентов: <b>{len(with_reviews)}</b>\n"
        f"💰 Заработано без учёта комиссии проекта: <b>{format_rub(total_gross_kopecks)}</b>\n\n"
        f"<i>Версия: {SALES_STATS_VERSION}</i>\n"
        f"<i>Источник заказов: {escape(str(debug_source))}</i>\n"
        f"<i>Всего заказов в ответе Starvell: {len(all_orders)}</i>\n"
    )

    if not recent_sales:
        return text + "\nПродаж за выбранный период не найдено."

    text += "\n<b>Последние продажи:</b>\n"
    sorted_sales = sorted(
        recent_sales,
        key=lambda order: parse_starvell_datetime(extract_order_date(order)) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    for index, order in enumerate(sorted_sales[:7], start=1):
        order_id = order.get("id") or order.get("orderId") or "—"
        buyer = extract_order_buyer_username(order)
        amount = format_rub(extract_order_amount_kopecks(order))
        date = format_starvell_datetime(extract_order_date(order))
        review = format_review_short(order)

        text += (
            f"\n<b>#{index}</b> Заказ: <code>{order_id}</code>\n"
            f"👤 Клиент: {escape(buyer)}\n"
            f"💰 Сумма: {amount}\n"
            f"⭐ Отзыв клиента: {review}\n"
            f"🕒 {date}\n"
        )

    if len(sorted_sales) > 7:
        text += f"\nПоказано 7 из {len(sorted_sales)} продаж."

    text += "\n\n<i>Сумма считается по цене заказа до вычитания комиссии Starvell.</i>"
    return text


def format_month_stats(username: str, chats_data: dict, orders_data: dict) -> str:
    chats = extract_chats(chats_data)
    orders_list = (orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}).get("orders") or []

    recent_chats = []
    unread_total = 0
    incoming_messages = 0
    notification_counts = {}

    my_user = extract_user(chats_data)
    my_id = my_user.get("id")

    for chat in chats:
        last = chat.get("lastMessage") or {}
        if is_last_30_days(last.get("createdAt")):
            recent_chats.append(chat)
            unread_total += int(chat.get("unreadMessageCount") or 0)

            author_id = last.get("authorId")
            if author_id and my_id and author_id != my_id:
                incoming_messages += 1

            if last.get("type") == "NOTIFICATION":
                notification_type = ((last.get("metadata") or {}).get("notificationType") or "NOTIFICATION")
                notification_counts[notification_type] = notification_counts.get(notification_type, 0) + 1

    recent_orders = [order for order in orders_list if is_last_30_days(extract_order_date(order))]
    total_amount = sum(extract_order_amount_kopecks(order) for order in recent_orders)

    recent_sales = [
        order for order in recent_orders
        if is_sale_order(order, username, my_id) and is_successful_sale(order)
    ]
    sales_gross_amount = sum(extract_order_amount_kopecks(order) for order in recent_sales)
    sales_reviews = sum(1 for order in recent_sales if extract_order_review(order) != (None, None))

    completed = sum(1 for order in recent_orders if str(order.get("status") or order.get("state") or "").upper() in ("COMPLETED", "DONE", "FINISHED"))
    paid = sum(1 for order in recent_orders if str(order.get("status") or order.get("state") or "").upper() in ("PAID", "ACTIVE", "PROCESSING"))

    extra = ""
    if notification_counts:
        extra = "\n\n🔔 <b>Уведомления за месяц:</b>\n" + "\n".join(
            f"• {escape(str(k))}: {v}" for k, v in sorted(notification_counts.items())
        )

    return (
        f"📊 <b>Статистика аккаунта за последние 30 дней</b>\n\n"
        f"👤 Аккаунт: <b>{escape(username)}</b>\n\n"
        f"💬 Чаты: {len(recent_chats)}\n"
        f"📩 Непрочитано сейчас: {unread_total}\n"
        f"📨 Последних входящих сообщений: {incoming_messages}\n\n"
        f"🛒 Заказы: {len(recent_orders)}\n"
        f"✅ Завершено: {completed}\n"
        f"⏳ В работе/оплачено: {paid}\n"
        f"💰 Сумма заказов: {format_rub(total_amount)}\n\n"
        f"📈 <b>Продажи</b>\n"
        f"🛍 Количество продаж: {len(recent_sales)}\n"
        f"⭐ Отзывов клиентов: {sales_reviews}\n"
        f"💰 Заработано без учёта комиссии проекта: {format_rub(sales_gross_amount)}"
        f"{extra}"
    )



def format_starvell_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%H:%M %d.%m.%Y")
    except Exception:
        return str(value)


def profile_rank_text(page_props: dict, user: dict) -> str | None:
    candidate_keys = [
        "topPosition", "topSellerPosition", "topSellersPosition", "sellerTopPosition",
        "sellerRank", "rank", "leaderboardPosition", "topRank", "position"
    ]
    for src in (page_props, user):
        for key in candidate_keys:
            value = src.get(key) if isinstance(src, dict) else None
            if value not in (None, "", 0):
                return str(value)
    return None


def extract_profile_user(data: dict) -> tuple[dict, dict]:
    page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
    user = (
        page_props.get("user")
        or page_props.get("profileUser")
        or page_props.get("seller")
        or page_props.get("account")
        or {}
    )
    return page_props, user


def format_seller_profile_text(requested_username: str, data: dict) -> str:
    page_props, user = extract_profile_user(data)
    username = user.get("username") or requested_username
    rank = profile_rank_text(page_props, user)
    title = f"🏆 <b>Топ #{rank}: {username}</b>" if rank else f"🏆 <b>Профиль продавца: {username}</b>"

    rating = user.get("rating")
    if rating is None:
        rating_text = "—"
    else:
        try:
            rating_text = f"{float(rating):.2f} ⭐"
        except Exception:
            rating_text = f"{rating} ⭐"

    reviews = user.get("reviewsCount", 0)
    banned = "Да" if user.get("isBanned") else "Нет"
    created = format_starvell_datetime(user.get("createdAt"))
    kyc_ok = str(user.get("kycStatus") or "").upper() == "VERIFIED"

    lines = [
        title,
        "",
        f"└ Рейтинг: {rating_text}",
        f"└ Отзывы: {reviews}",
        f"└ Заблокирован: {banned}",
        f"└ Создан: {created}",
        "",
        ("✅ Пользователь прошёл KYC верификацию STARVELL" if kyc_ok else "⚠️ Пользователь не прошёл KYC верификацию STARVELL"),
    ]

    description = (user.get("description") or "").strip()
    if description:
        lines.extend(["", f"📝 {description}"])

    return "\n".join(lines)


async def show_seller_profile(message: Message, username: str, user_id: int) -> None:
    accounts = await db.list_user_accounts(user_id)
    if not accounts:
        await message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_profile(username)
        text = format_seller_profile_text(username, data)
        await message.answer(text, reply_markup=seller_profile_keyboard(username))
    except StarvellApiError as error:
        await message.answer(f"❌ Не удалось получить профиль продавца:\n<code>{error}</code>")
    finally:
        await client.close()




ACCOUNT_SETTING_TEXTS = {
    "greeting": "👋 Приветствие",
    "auto_responder": "🤖 Автоответчик",
    "confirm_reminder": "⏰ Напоминание о подтверждении",
    "ignore_text": "💬 Текст при игноре",
    "after_5_stars": "💬 Текст после 5 звёзд",
    "problem_text": "💬 Текст при проблеме",
    "after_seller_confirm": "💬 Текст после вашего подтверждения",
    "after_client_confirm": "💬 Текст после подтверждения клиентом",
}

ACCOUNT_SETTING_TOGGLES = {
    "auto_confirm": "🤝 Автоподтверждение",
    "auto_raise_lots": "🚀 Автоподнятие лотов",
    "auto_repost_lots": "🔄 Автовыставление лотов",
    "auto_delivery": "📦 Автовыдача",
}


async def build_account_settings_text(user_id: int) -> str:
    toggles = []
    for key, title in ACCOUNT_SETTING_TOGGLES.items():
        enabled = await db.get_bool_account_setting(user_id, key)
        toggles.append(f"{title}: {'✅ включено' if enabled else '❌ выключено'}")

    filled_texts = 0
    for key in ACCOUNT_SETTING_TEXTS:
        value = await db.get_account_setting(user_id, key)
        if value and value.strip():
            filled_texts += 1

    return (
        "⭐ <b>Автонастройки</b>\n\n"
        "Выберите нужный раздел:\n\n"
        "⚙️ <b>Статус функций</b>\n"
        + "\n".join(toggles)
        + "\n\n"
        f"💬 Заполнено текстов: {filled_texts}/{len(ACCOUNT_SETTING_TEXTS)}"
    )


async def show_account_settings_menu(message: Message, user_id: int) -> None:
    text = await build_account_settings_text(user_id)
    await message.answer(text, reply_markup=account_settings_menu_keyboard())


def account_setting_title(key: str) -> str:
    return ACCOUNT_SETTING_TEXTS.get(key) or ACCOUNT_SETTING_TOGGLES.get(key) or key


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def order_is_waiting_for_confirmation(order: dict) -> bool:
    """
    Do not send reminders for completed/refunded/canceled orders.
    Reminders are allowed only for active orders older than 24 hours.
    """
    status = str(order.get("status") or order.get("state") or order.get("orderStatus") or "").upper()
    blocked = {
        "COMPLETED", "DONE", "FINISHED", "CLOSED", "SUCCESS", "SUCCESSFUL",
        "REFUND", "REFUNDED", "CANCELLED", "CANCELED", "DECLINED", "FAILED",
    }
    return status not in blocked


def render_confirm_reminder_template(template: str, *, username: str, order_id: str, seller: str) -> str:
    template = template or "Здравствуйте! Пожалуйста, подтвердите выполнение заказа на Starvell, если всё получено."
    return (
        template
        .replace("{username}", username or "клиент")
        .replace("{order_id}", str(order_id or ""))
        .replace("{seller}", seller or "")
    )


async def build_confirm_reminder_text(user_id: int) -> str:
    enabled = await db.get_bool_account_setting(user_id, "confirm_reminder_enabled")
    time_value = await db.get_account_setting(user_id, "confirm_reminder_time", "13:00")
    period_days = int(await db.get_account_setting(user_id, "confirm_reminder_period_days", "1") or "1")
    text_value = await db.get_account_setting(user_id, "confirm_reminder")
    if period_days == 1:
        period_text = "каждый день"
    elif period_days == 2:
        period_text = "спустя день"
    elif period_days == 7:
        period_text = "раз в неделю"
    else:
        period_text = f"раз в {period_days} дня"

    return (
        "⏰ <b>Напоминание о подтверждении</b>\n\n"
        f"Статус: {'✅ включено' if enabled else '❌ выключено'}\n"
        f"Время отправки: <b>{escape(str(time_value))}</b>\n"
        f"Дни: <b>{escape(period_text)}</b>\n"
        "Задержка после заказа: <b>только спустя 24 часа</b>\n\n"
        "<b>Текущий текст:</b>\n"
        f"{escape(text_value) if text_value else 'текст не задан'}\n\n"
        "Переменные: <code>{username}</code>, <code>{order_id}</code>, <code>{seller}</code>."
    )


def should_run_confirm_reminder_now(time_value: str) -> bool:
    # Server timezone is usually UTC. We use the configured HH:MM as server time and allow a 10 minute window.
    try:
        hour, minute = [int(x) for x in str(time_value or "13:00").split(":", 1)]
    except Exception:
        hour, minute = 13, 0
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return 0 <= (now - target).total_seconds() < 600


async def process_confirm_reminders_for_account(account) -> None:
    enabled = await db.get_bool_account_setting(account.user_id, "confirm_reminder_enabled")
    if not enabled:
        return

    time_value = await db.get_account_setting(account.user_id, "confirm_reminder_time", "13:00")
    if not should_run_confirm_reminder_now(time_value):
        return

    period_days = int(await db.get_account_setting(account.user_id, "confirm_reminder_period_days", "1") or "1")
    template = await db.get_account_setting(account.user_id, "confirm_reminder")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_orders()
        page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
        orders = page_props.get("orders") or []
        user = page_props.get("user") or {}
        my_user_id = user.get("id")
        seller_username = user.get("username") or account.username or ""

        now = datetime.now(timezone.utc)
        for order in orders:
            order_id = str(order.get("id") or "")
            if not order_id:
                continue
            if not is_sale_order(order, seller_username, my_user_id):
                continue
            if not order_is_waiting_for_confirmation(order):
                continue

            created_at = parse_iso_datetime(order.get("createdAt") or order.get("sortAt"))
            if not created_at or now - created_at < timedelta(hours=24):
                continue

            last_sent_raw = await db.get_confirm_reminder_last_sent(account.id, order_id)
            last_sent = parse_iso_datetime(last_sent_raw)
            if last_sent and now - last_sent < timedelta(days=max(1, period_days)):
                continue

            buyer = order.get("user") or order.get("buyer") or {}
            buyer_username = buyer.get("username") if isinstance(buyer, dict) else ""
            buyer_id = order.get("buyerId")
            message_text = render_confirm_reminder_template(
                template or "",
                username=buyer_username or "клиент",
                order_id=order_id,
                seller=seller_username,
            )

            chat_id = await client.find_chat_id_by_user(buyer_id=buyer_id, buyer_username=buyer_username)
            if not chat_id:
                # Save nothing: if chat appears later, bot can still send.
                continue

            await client.send_chat_message(chat_id, message_text)
            await db.save_confirm_reminder_sent(account.id, order_id)
            await asyncio.sleep(1)
    finally:
        await client.close()


async def confirm_reminder_loop() -> None:
    await asyncio.sleep(15)
    while True:
        accounts = await db.list_enabled_accounts()
        for account in accounts:
            try:
                await process_confirm_reminders_for_account(account)
            except Exception:
                # Keep bot alive even if Starvell private send endpoint changes.
                pass
            await asyncio.sleep(1)
        await asyncio.sleep(300)




async def get_primary_account(user_id: int):
    accounts = await db.list_user_accounts(user_id)
    return accounts[0] if accounts else None


async def build_profile_main_text(message_or_user) -> str:
    user = getattr(message_or_user, "from_user", None) or message_or_user
    user_id = user.id
    await db.ensure_user(user_id)
    accounts = await db.list_user_accounts(user_id)
    enabled = sum(1 for a in accounts if a.notifications_enabled)
    bot_balance = await db.get_bot_balance(user_id)
    subscription_text = await get_subscription_text(user_id)
    if await is_free_access_user(user):
        if subscription_text == "отсутствует":
            subscription_text = "безлимитный доступ"
    always_online = await db.get_always_online_enabled(user_id)

    text = (
        "👀 <b>Профиль</b>\n\n"
        "Выберите нужный раздел.\n\n"
        f"💰 Баланс бота: {format_rub(bot_balance)}\n"
        f"🔐 Подключено аккаунтов: {len(accounts)}\n"
        f"🔔 Уведомления включены: {enabled}\n"
        f"📦 Подписка: {subscription_text}\n"
        f"🟢 Вечный онлайн: {'включён' if always_online else 'выключен'}"
    )
    return text


async def send_profile_main(message: Message, user_id: int) -> None:
    always_online = await db.get_always_online_enabled(user_id)
    text = await build_profile_main_text(message.from_user)
    await message.answer(text, reply_markup=profile_menu_keyboard(always_online))


async def send_profile_notifications_section(message: Message, user_id: int) -> None:
    accounts = await db.list_user_accounts(user_id)
    if not accounts:
        await message.answer(
            "🔔 <b>Уведомления</b>\n\nУ тебя пока нет подключённых Starvell-аккаунтов.",
            reply_markup=profile_back_keyboard(),
        )
        return

    text = "🔔 <b>Уведомления</b>\n\n"
    for index, account in enumerate(accounts, start=1):
        status = "🔔 включены" if account.notifications_enabled else "🔕 выключены"
        text += (
            f"<b>Аккаунт #{index}</b>\n"
            f"👤 {escape(account.username or 'username не определён')}\n"
            f"🔔 Уведомления: {status}\n"
            f"🌐 Прокси: {hide_proxy(account.proxy_url)}\n\n"
        )
    text += "Управление уведомлениями по каждому аккаунту доступно в разделе 🔐 Мои аккаунты."
    await message.answer(text, reply_markup=profile_back_keyboard())


async def send_profile_chats_orders_section(message: Message, user_id: int) -> None:
    account = await get_primary_account(user_id)
    if not account:
        await message.answer(
            "💬 <b>Чаты и заказы</b>\n\nСначала добавь Starvell-аккаунт.",
            reply_markup=profile_back_keyboard(),
        )
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats_data = await asyncio.wait_for(client.get_chats(), timeout=25)
        orders_data = await asyncio.wait_for(client.get_orders(), timeout=25)
        chats = extract_chats(chats_data)
        orders = (orders_data.get('pageProps', {}) if isinstance(orders_data, dict) else {}).get('orders') or []
        recent_orders = [order for order in orders if is_last_30_days(extract_order_date(order))]
        unread = sum(int(chat.get('unreadMessageCount') or 0) for chat in chats)
        username = extract_user(chats_data).get('username') or account.username or 'Starvell'
        text = (
            "💬 <b>Чаты и заказы</b>\n\n"
            f"👤 Аккаунт: <b>{escape(username)}</b>\n"
            f"💬 Чатов всего: {len(chats)}\n"
            f"📩 Непрочитано сейчас: {unread}\n"
            f"🛒 Заказов за 30 дней: {len(recent_orders)}\n\n"
            "Нажми кнопку ниже, чтобы открыть подробности."
        )
        await message.answer(text, reply_markup=profile_chats_orders_keyboard())
    except asyncio.TimeoutError:
        await message.answer(
            "❌ Starvell слишком долго отвечает. Попробуй позже или проверь прокси.",
            reply_markup=profile_back_keyboard(),
        )
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка: <code>{error}</code>", reply_markup=profile_back_keyboard())
    finally:
        await client.close()


async def send_profile_clients_section(message: Message, user_id: int) -> None:
    account = await get_primary_account(user_id)
    if not account:
        await message.answer(
            "📣 <b>Мои клиенты</b>\n\nСначала добавь Starvell-аккаунт.",
            reply_markup=profile_back_keyboard(),
        )
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats_data = await asyncio.wait_for(client.get_chats(), timeout=25)
        orders_data = await asyncio.wait_for(client.get_orders(), timeout=25)
        my_user = extract_user(chats_data)
        my_id = my_user.get('id')
        my_username = my_user.get('username') or account.username or 'Starvell'
        stats: dict[str, dict] = {}

        for chat in extract_chats(chats_data):
            last = chat.get('lastMessage') or {}
            if not is_last_30_days(last.get('createdAt')):
                continue
            name = get_interlocutor(chat, my_user_id=my_id, my_username=my_username)
            if not name or name == 'Неизвестно':
                continue
            item = stats.setdefault(name.lower(), {'username': name, 'chats': 0, 'orders': 0})
            item['chats'] += 1

        orders = (orders_data.get('pageProps', {}) if isinstance(orders_data, dict) else {}).get('orders') or []
        for order in orders:
            if not is_last_30_days(extract_order_date(order)):
                continue
            buyer = (order.get('buyer') or {}).get('username') or 'Неизвестно'
            if buyer == 'Неизвестно':
                continue
            item = stats.setdefault(buyer.lower(), {'username': buyer, 'chats': 0, 'orders': 0})
            item['orders'] += 1

        values = sorted(stats.values(), key=lambda x: (x['orders'], x['chats'], x['username'].lower()), reverse=True)
        if not values:
            await message.answer(
                "📣 <b>Мои клиенты</b>\n\nЗа последние 30 дней клиентов не найдено.",
                reply_markup=profile_back_keyboard(),
            )
            return

        text = "📣 <b>Мои клиенты за последние 30 дней</b>\n\n"
        for index, item in enumerate(values[:15], start=1):
            text += f"<b>{index}.</b> {escape(item['username'])} — заказов: {item['orders']}, чатов: {item['chats']}\n"
        if len(values) > 15:
            text += f"\nПоказано 15 из {len(values)} клиентов."
        await message.answer(text, reply_markup=profile_back_keyboard())
    except asyncio.TimeoutError:
        await message.answer(
            "❌ Starvell слишком долго отвечает. Попробуй позже или проверь прокси.",
            reply_markup=profile_back_keyboard(),
        )
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка: <code>{error}</code>", reply_markup=profile_back_keyboard())
    finally:
        await client.close()


async def send_latest_chats(message: Message, user_id: int) -> None:
    if not await require_subscription(message):
        return

    account = await get_primary_account(user_id)
    if not account:
        await message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        return

    await message.answer("🔄 Получаю последние чаты...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_chats()
        user = extract_user(data)
        my_id = user.get("id")
        my_username = user.get("username") or account.username or "Starvell"
        chats = extract_chats(data)

        if not chats:
            await message.answer("💬 Чатов нет.")
            return

        text = f"💬 <b>Последние чаты {escape(my_username)}</b>\n\n"
        for index, chat in enumerate(chats[:10], start=1):
            last = chat.get("lastMessage") or {}
            interlocutor = get_interlocutor(chat, my_user_id=my_id, my_username=my_username)
            msg = format_notification_message(last)
            if len(msg) > 120:
                msg = msg[:117] + "..."
            unread = int(chat.get("unreadMessageCount") or 0)
            created = format_starvell_datetime(last.get("createdAt"))
            text += (
                f"<b>#{index}</b> 👤 {escape(interlocutor)}\n"
                f"📩 Непрочитано: {unread}\n"
                f"🕒 {created}\n"
                f"💭 {escape(msg)}\n"
                f"🔗 {get_chat_link(chat.get('id'))}\n\n"
            )

        await message.answer(text)
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка чатов: <code>{error}</code>")
    finally:
        await client.close()


async def send_recent_orders(message: Message, user_id: int) -> None:
    if not await require_subscription(message):
        return
    account = await get_primary_account(user_id)
    if not account:
        await message.answer("😴 Сначала добавь Starvell аккаунт.")
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_orders()
        page_props = data.get("pageProps", {})
        all_orders = page_props.get("orders") or []
        orders_list = [order for order in all_orders if is_last_30_days(extract_order_date(order))]
        user = page_props.get("user") or {}
        username = user.get("username") or account.username or "Starvell"
        counts = user.get("ordersCount") or {}
        balance = user.get("balance") or {}

        if not orders_list:
            await message.answer(
                "🛒 <b>Заказы Starvell за последние 30 дней</b>\n\n"
                f"👤 Аккаунт: <b>{escape(username)}</b>\n"
                "Заказов за последний месяц сейчас нет.\n\n"
                f"🛍 Всего покупок: {counts.get('purchaseOrdersCount', 0)}\n"
                f"💼 Всего продаж: {counts.get('salesOrdersCount', 0)}\n"
                f"💰 Баланс всего: {format_total_balance(balance)}\n"
                f"├ Доступно: {format_rub(balance.get('rubBalance', 0))}\n"
                f"├ В холде: {format_rub(balance.get('holdedRubBalance', 0))}\n"
                f"└ Можно вывести: {format_rub(balance.get('withdrawableRubBalance', 0))}"
            )
            return

        text = f"🛒 <b>Заказы Starvell за последние 30 дней — {escape(username)}</b>\n\n"
        for index, order in enumerate(orders_list[:10], start=1):
            text += format_order(order, index) + "\n\n"

        if len(orders_list) > 10:
            text += f"Показано 10 из {len(orders_list)} заказов за месяц."

        await message.answer(text)

    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка заказов: <code>{error}</code>")
    finally:
        await client.close()


async def mark_all_messages_read_for_bot(message: Message, user_id: int) -> None:
    account = await get_primary_account(user_id)
    if not account:
        await message.answer(
            "✅ Прочитать все сообщения\n\nСначала добавь Starvell-аккаунт.",
            reply_markup=profile_back_keyboard(),
        )
        return

    await message.answer("🔄 Помечаю текущие сообщения как прочитанные для бота...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await asyncio.wait_for(client.get_chats(), timeout=25)
        await baseline_account_messages(db, account.id, data)
        await message.answer(
            "✅ Для бота все текущие сообщения помечены прочитанными.\n\n"
            "Важно: это сбрасывает уведомления только внутри бота. На самом сайте Starvell непрочитанные чаты могут остаться, пока ты их не откроешь.",
            reply_markup=profile_back_keyboard(),
        )
    except asyncio.TimeoutError:
        await message.answer(
            "❌ Starvell слишком долго отвечает. Попробуй позже или проверь прокси.",
            reply_markup=profile_back_keyboard(),
        )
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка: <code>{error}</code>", reply_markup=profile_back_keyboard())
    finally:
        await client.close()


async def watcher_loop() -> None:
    await asyncio.sleep(5)
    while True:
        accounts = await db.list_enabled_accounts()
        for account in accounts:
            await check_one_account(bot, db, account)
            await asyncio.sleep(1)
        await asyncio.sleep(config.check_interval_seconds)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await db.ensure_user(message.from_user.id)
    await message.answer(
        "👋 Привет!\n\n"
        "Это бот для уведомлений Starvell. Он проверяет чаты и присылает ссылку, когда появляется новое непрочитанное сообщение.",
        reply_markup=main_keyboard,
    )


@dp.message(F.text == "👤 Профиль")
async def profile(message: Message) -> None:
    await db.ensure_user(message.from_user.id)
    accounts = await db.list_user_accounts(message.from_user.id)
    enabled = sum(1 for a in accounts if a.notifications_enabled)
    bot_balance = await db.get_bot_balance(message.from_user.id)
    subscription_text = await get_subscription_text(message.from_user.id)
    free_access = await is_free_access_user(message)
    if free_access and subscription_text == "отсутствует":
        subscription_text = "безлимитный доступ"

    text = (
        "👤 <b>Мой профиль</b>\n\n"
        f"╭ Ваш ID: <code>{message.from_user.id}</code>\n"
        "├ Часовой пояс: 🌍 UTC\n"
        f"╰ Подписка: {subscription_text}\n\n"
        "💰 <b>Баланс бота</b>\n"
        f"╰ {format_rub(bot_balance)}\n\n"
        "🔐 <b>Аккаунты Starvell</b>\n"
    )

    if not accounts:
        text += "╰ Аккаунты не подключены\n"
    else:
        for index, account in enumerate(accounts, start=1):
            status = "🔔 включены" if account.notifications_enabled else "🔕 выключены"
            proxy = hide_proxy(account.proxy_url)
            username = account.username or "username не определён"
            text += (
                f"\n<b>Аккаунт #{index}</b>\n"
                f"👤 {escape(username)}\n"
                f"🌐 Прокси: {proxy}\n"
                f"🔔 Уведомления: {status}\n"
            )

    text += (
        "\n📊 <b>Статистика бота</b>\n"
        f"╭ Подключено аккаунтов: {len(accounts)}\n"
        f"╰ Уведомления включены: {enabled}"
    )

    await message.answer(text, reply_markup=top_up_keyboard())

async def create_topup_request(user_id: int, amount_kopecks: int, message: Message) -> None:
    request_id = await db.add_topup_request(user_id, amount_kopecks)

    details = config.top_up_payment_details or (
        "Реквизиты не настроены. Добавь их в файл .env в строку TOP_UP_PAYMENT_DETAILS."
    )

    await message.answer(
        "💰 <b>Заявка на пополнение баланса бота создана</b>\n\n"
        f"🆔 Заявка: <code>{request_id}</code>\n"
        f"💵 Сумма: <b>{format_rub(amount_kopecks)}</b>\n\n"
        "Оплати по реквизитам ниже и отправь администратору номер заявки.\n\n"
        f"<b>Реквизиты:</b>\n<code>{details}</code>"
    )

    if config.admin_id:
        try:
            await bot.send_message(
                config.admin_id,
                "💰 <b>Новая заявка на пополнение баланса бота</b>\n\n"
                f"🆔 Заявка: <code>{request_id}</code>\n"
                f"👤 Telegram ID: <code>{user_id}</code>\n"
                f"💵 Сумма: <b>{format_rub(amount_kopecks)}</b>",
                reply_markup=admin_topup_keyboard(request_id),
            )
        except Exception:
            pass


@dp.callback_query(F.data == "topup:menu")
async def topup_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpBalance.waiting_amount)
    await callback.message.answer(
        "💰 <b>Отправь сумму</b>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "topup:custom")
async def topup_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpBalance.waiting_amount)
    await callback.message.answer(
        "💰 <b>Введи сумму пополнения</b>\n\n"
        "Напиши только число в рублях, например:\n"
        "<code>100</code> или <code>250.50</code>"
    )
    await callback.answer()


@dp.message(TopUpBalance.waiting_amount)
async def topup_custom_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")

    try:
        amount_rub = float(raw)
    except ValueError:
        await message.answer("❌ Введи сумму числом. Например: <code>150</code>")
        return

    if amount_rub < 10:
        await message.answer("❌ Минимальная сумма пополнения — 10 ₽.")
        return

    if amount_rub > 100000:
        await message.answer("❌ Максимальная сумма пополнения — 100000 ₽.")
        return

    amount_kopecks = int(round(amount_rub * 100))
    await create_topup_request(message.from_user.id, amount_kopecks, message)
    await state.clear()


@dp.callback_query(F.data.startswith("topup:amount:"))
async def topup_amount(callback: CallbackQuery) -> None:
    amount_kopecks = int(callback.data.split(":")[-1])
    await create_topup_request(callback.from_user.id, amount_kopecks, callback.message)
    await callback.answer()


@dp.callback_query(F.data == "cancel")
async def cancel_any_state(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("🚫 Действие отменено")
    await callback.answer()


@dp.callback_query(F.data.startswith("sub:"))
async def buy_subscription(callback: CallbackQuery) -> None:
    plan = callback.data.split(":")[-1]

    if plan == "trial":
        if await has_active_subscription(callback.from_user.id):
            subscription_text = await get_subscription_text(callback.from_user.id)
            await callback.message.answer(
                "✅ <b>У вас уже есть активная подписка</b>\n\n"
                f"Подписка: <b>{subscription_text}</b>"
            )
            await callback.answer()
            return

        if await db.has_trial_used(callback.from_user.id):
            await callback.message.answer(
                "🎁 <b>Бесплатный пробный период уже был использован.</b>\n\n"
                "Чтобы продолжить пользоваться ботом, выбери платную подписку:",
                reply_markup=no_subscription_keyboard(),
            )
            await callback.answer()
            return

        trial_until = datetime.now(timezone.utc) + timedelta(days=7)
        await db.set_subscription_until(callback.from_user.id, trial_until.isoformat())
        await db.set_trial_used(callback.from_user.id, True)

        await callback.message.answer(
            "🎁 <b>Пробная подписка активирована</b>\n\n"
            "Срок: <b>7 дней бесплатно</b>\n"
            f"Активна до: <b>{trial_until.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}</b>\n\n"
            "После окончания пробного периода нужно будет выбрать платную подписку."
        )
        await callback.answer()
        return

    days = int(plan)
    price = SUBSCRIPTION_PLANS.get(days)
    if not price:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    current_balance = await db.get_bot_balance(callback.from_user.id)
    if current_balance < price:
        await callback.message.answer(
            "❌ <b>Недостаточно средств на балансе бота</b>\n\n"
            f"Тариф: <b>{days} дн.</b>\n"
            f"Стоимость: <b>{format_rub(price)}</b>\n"
            f"Баланс бота: <b>{format_rub(current_balance)}</b>",
            reply_markup=no_subscription_keyboard(),
        )
        await callback.answer()
        return

    spent = await db.spend_bot_balance(callback.from_user.id, price)
    if not spent:
        await callback.answer("Не удалось списать баланс", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    raw_until = await db.get_subscription_until(callback.from_user.id)
    if raw_until:
        try:
            current_until = datetime.fromisoformat(raw_until)
            if current_until.tzinfo is None:
                current_until = current_until.replace(tzinfo=timezone.utc)
            if current_until < now:
                current_until = now
        except Exception:
            current_until = now
    else:
        current_until = now

    new_until = current_until + timedelta(days=days)
    await db.set_subscription_until(callback.from_user.id, new_until.isoformat())
    new_balance = await db.get_bot_balance(callback.from_user.id)

    await callback.message.answer(
        "✅ <b>Подписка активирована</b>\n\n"
        f"📆 Срок: <b>{days} дней</b>\n"
        f"💵 Списано: <b>{format_rub(price)}</b>\n"
        f"💰 Остаток на балансе бота: <b>{format_rub(new_balance)}</b>\n"
        f"⏳ Активна до: <b>{new_until.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}</b>"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("topup_admin:"))
async def topup_admin_action(callback: CallbackQuery) -> None:
    if not config.admin_id or callback.from_user.id != config.admin_id:
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, action, request_id_raw = callback.data.split(":")
    request_id = int(request_id_raw)
    request = await db.get_topup_request(request_id)

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if request.status != "pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    if action == "approve":
        await db.add_bot_balance(request.user_id, request.amount_kopecks)
        await db.set_topup_status(request_id, "approved")
        await callback.message.answer(f"✅ Заявка #{request_id} зачислена: {format_rub(request.amount_kopecks)}")
        try:
            await bot.send_message(
                request.user_id,
                "✅ <b>Баланс пополнен</b>\n\n"
                f"🆔 Заявка: <code>{request_id}</code>\n"
                f"💵 Зачислено: <b>{format_rub(request.amount_kopecks)}</b>",
            )
        except Exception:
            pass
    else:
        await db.set_topup_status(request_id, "rejected")
        await callback.message.answer(f"❌ Заявка #{request_id} отклонена")
        try:
            await bot.send_message(
                request.user_id,
                "❌ <b>Заявка на пополнение отклонена</b>\n\n"
                f"🆔 Заявка: <code>{request_id}</code>",
            )
        except Exception:
            pass

    await callback.answer()



@dp.message(F.text == "🔐 Мои аккаунты")
async def my_accounts(message: Message) -> None:
    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("😴 У вас нет подключенных аккаунтов", reply_markup=accounts_keyboard())
        return

    await message.answer("🔐 <b>Ваши аккаунты Starvell:</b>", reply_markup=accounts_keyboard())
    for account in accounts:
        status = "🔔 включены" if account.notifications_enabled else "🔕 выключены"
        username = account.username or "username не определён"
        err = f"\n⚠️ Ошибка: {account.last_error}" if account.last_error else ""
        await message.answer(
            f"<b>Аккаунт #{account.id}</b>\n"
            f"👤 {username}\n"
            f"🌐 Прокси: {hide_proxy(account.proxy_url)}\n"
            f"🔔 Уведомления: {status}{err}",
            reply_markup=account_actions_keyboard(account.id, account.notifications_enabled),
        )


@dp.callback_query(F.data == "account:add")
async def add_account_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAccount.waiting_cookie)
    await callback.message.answer(
        "🔐 <b>Добавление аккаунта Starvell</b>\n\n"
        "Отправь Cookie от Starvell одним сообщением. После этого аккаунт сразу добавится без прокси.\n\n"
        "Где взять Cookie:\n"
        "1. Открой starvell.com в браузере\n"
        "2. Нажми F12 → Приложение/Application\n"
        "3. Cookies → https://starvell.com\n"
        "4. Скопируй строку cookie\n\n"
        "⚠️ Cookie — это почти как пароль. Не публикуй его и не отправляй никому."
    )
    await callback.answer()


@dp.message(AddAccount.waiting_cookie)
async def add_account_cookie(message: Message, state: FSMContext) -> None:
    cookie = (message.text or "").strip()
    if "=" not in cookie or len(cookie) < 20:
        await message.answer("❌ Это не похоже на Cookie. Отправь строку вида <code>name=value; name2=value2</code>")
        return

    # Try to delete the message containing cookie from private chat.
    try:
        await message.delete()
    except Exception:
        pass

    checking_message = await message.answer("🔄 Проверяю аккаунт Starvell...\n\nЕсли проверка длится больше 30 секунд, значит Starvell не отвечает или Cookie устарел.")
    client = StarvellClient(cookie=cookie, proxy_url=None)
    try:
        try:
            chats_data = await asyncio.wait_for(client.get_chats(), timeout=30)
        except asyncio.TimeoutError:
            raise StarvellApiError(
                "Проверка заняла больше 30 секунд. Возможные причины: Starvell блокирует IP хостинга, Cookie устарел, "
                "или сайт временно не отвечает. Попробуй обновить Cookie или поставить прокси для аккаунта."
            )

        user = extract_user(chats_data)
        username = user.get("username")
        if not username:
            raise StarvellApiError("Не удалось определить username. Возможно, Cookie скопирован не полностью или устарел.")

        account_id = await db.add_account(
            user_id=message.from_user.id,
            cookie=cookie,
            username=username,
            proxy_url=None,
        )
        await baseline_account_messages(db, account_id, chats_data)
        await state.clear()

        try:
            await checking_message.delete()
        except Exception:
            pass

        await message.answer(
            "✅ Аккаунт Starvell добавлен.\n\n"
            f"👤 Starvell: <b>{username}</b>\n"
            "🌐 Прокси: не используется\n"
            "🔔 Уведомления включены. Старые сообщения не будут отправлены повторно.\n\n"
            "Если нужен прокси — открой 🔐 Мои аккаунты и нажми «🌐 Прокси аккаунта».\n"
            + ("\n⭐ Для вашего Telegram-аккаунта доступен безлимитный режим без подписки." if await is_free_access_user(message) else "")
        )
    except StarvellApiError as error:
        await state.clear()
        await message.answer(f"❌ Не удалось подключить аккаунт:\n<code>{error}</code>")
    except Exception as error:
        await state.clear()
        await message.answer(
            "❌ Неожиданная ошибка при проверке аккаунта.\n\n"
            f"<code>{type(error).__name__}: {error}</code>"
        )
    finally:
        await client.close()


@dp.callback_query(F.data.startswith("account:toggle:"))
async def toggle_account(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[-1])
    new_state = await db.toggle_notifications(account_id, callback.from_user.id)
    if new_state is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await callback.message.answer("🔔 Уведомления включены" if new_state else "🔕 Уведомления выключены")
    await callback.answer()


@dp.callback_query(F.data.startswith("account:delete:"))
async def delete_account(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[-1])
    deleted = await db.delete_account(account_id, callback.from_user.id)
    await callback.message.answer("🗑 Аккаунт удалён" if deleted else "❌ Аккаунт не найден")
    await callback.answer()


@dp.callback_query(F.data.startswith("account:proxy:"))
async def set_account_proxy_start(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":")[-1])
    account = await db.get_account(account_id)
    if not account or account.user_id != callback.from_user.id:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await state.set_state(SetAccountProxy.waiting_proxy)
    await state.update_data(account_id=account_id)
    await callback.message.answer(
        "🌐 Отправь прокси для этого аккаунта или <code>-</code>, чтобы удалить прокси.\n\n"
        "Примеры:\n"
        "<code>http://ip:port</code>\n"
        "<code>https://ip:port</code>\n"
        "<code>socks5://ip:port</code>\n"
        "<code>socks5://login:password@ip:port</code>"
    )
    await callback.answer()


@dp.message(SetAccountProxy.waiting_proxy)
async def set_account_proxy_finish(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    account_id = int(data["account_id"])
    account = await db.get_account(account_id)
    if not account or account.user_id != message.from_user.id:
        await state.clear()
        await message.answer("❌ Аккаунт не найден.")
        return

    proxy_url = normalize_proxy(message.text or "")
    if proxy_url and not validate_proxy(proxy_url):
        await message.answer("❌ Неверный формат прокси. Отправь корректный прокси или <code>-</code> для удаления.")
        return

    if proxy_url:
        await message.answer("🔄 Проверяю прокси...")
        ok, result = await check_proxy(proxy_url)
        if not ok:
            await message.answer(f"❌ {result}")
            return

    await db.update_account_proxy(account_id, proxy_url)
    await state.clear()
    await message.answer(
        "✅ Прокси аккаунта обновлён.\n"
        f"🌐 Прокси: {hide_proxy(proxy_url)}"
    )


@dp.callback_query(F.data.startswith("account:chats:"))
async def account_chats(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":")[-1])
    account = await db.get_account(account_id)
    if not account or account.user_id != callback.from_user.id:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await callback.message.answer("🔄 Получаю чаты...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_chats()
        user = extract_user(data)
        my_id = user.get("id")
        my_username = user.get("username") or account.username or "Starvell"
        chats = extract_chats(data)
        if not chats:
            await callback.message.answer("💬 Чатов нет.")
            return

        text = f"💬 <b>Последние чаты {my_username}</b>\n\n"
        for chat in chats[:10]:
            last = chat.get("lastMessage") or {}
            interlocutor = get_interlocutor(chat, my_user_id=my_id, my_username=my_username)
            msg = format_notification_message(last)
            if len(msg) > 80:
                msg = msg[:77] + "..."
            unread = int(chat.get("unreadMessageCount") or 0)
            text += f"• {interlocutor} | непрочитано: {unread}\n  {msg}\n  {get_chat_link(chat.get('id'))}\n\n"
        await callback.message.answer(text)
    except StarvellApiError as error:
        await callback.message.answer(f"❌ Ошибка: <code>{error}</code>")
    finally:
        await client.close()
    await callback.answer()


@dp.callback_query(F.data == "account:check_all")
async def check_all_accounts(callback: CallbackQuery) -> None:
    accounts = await db.list_user_accounts(callback.from_user.id)
    if not accounts:
        await callback.message.answer("😴 Аккаунтов нет.")
        return
    await callback.message.answer("🔄 Проверяю аккаунты...")
    for account in accounts:
        await check_one_account(bot, db, account)
    await callback.message.answer("✅ Проверка завершена.")
    await callback.answer()


@dp.message(F.text == "💬 Чаты")
async def chats_menu(message: Message) -> None:
    if not await require_subscription(message):
        return

    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        return

    account = accounts[0]
    await message.answer("🔄 Получаю последние чаты...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_chats()
        user = extract_user(data)
        my_id = user.get("id")
        my_username = user.get("username") or account.username or "Starvell"
        chats = extract_chats(data)

        if not chats:
            await message.answer("💬 Чатов нет.")
            return

        text = f"💬 <b>Последние чаты {escape(my_username)}</b>\n\n"
        for index, chat in enumerate(chats[:10], start=1):
            last = chat.get("lastMessage") or {}
            interlocutor = get_interlocutor(chat, my_user_id=my_id, my_username=my_username)
            msg = format_notification_message(last)
            if len(msg) > 120:
                msg = msg[:117] + "..."
            unread = int(chat.get("unreadMessageCount") or 0)
            created = format_starvell_datetime(last.get("createdAt"))
            text += (
                f"<b>#{index}</b> 👤 {escape(interlocutor)}\n"
                f"📩 Непрочитано: {unread}\n"
                f"🕒 {created}\n"
                f"💭 {escape(msg)}\n"
                f"🔗 {get_chat_link(chat.get('id'))}\n\n"
            )

        await message.answer(text)
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка чатов: <code>{error}</code>")
    finally:
        await client.close()


@dp.message(F.text == "🌐 Мои прокси")
async def my_proxies(message: Message) -> None:
    proxies = await db.list_user_proxies(message.from_user.id)
    text = f"🌐 Для работы с прокси используй меню ниже\n\n• Кол-во твоих прокси: {len(proxies)} шт."
    if proxies:
        text += "\n\n" + "\n".join(f"{p['id']}. {hide_proxy(p['proxy_url'])}" for p in proxies[:10])
    await message.answer(text, reply_markup=proxies_keyboard())


@dp.callback_query(F.data == "proxy:add")
async def add_proxy_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddProxy.waiting_proxy)
    await callback.message.answer(
        "🌐 Отправь прокси:\n"
        "<code>http://ip:port</code>\n"
        "<code>https://ip:port</code>\n"
        "<code>socks5://login:password@ip:port</code>"
    )
    await callback.answer()


@dp.message(AddProxy.waiting_proxy)
async def add_proxy_finish(message: Message, state: FSMContext) -> None:
    proxy_url = normalize_proxy(message.text or "")
    if not proxy_url or not validate_proxy(proxy_url):
        await message.answer("❌ Неверный формат прокси.")
        return
    ok, result = await check_proxy(proxy_url)
    if not ok:
        await message.answer(f"❌ {result}")
        return
    await db.add_proxy(message.from_user.id, proxy_url)
    await state.clear()
    await message.answer(f"✅ Прокси добавлен.\n{result}")


@dp.callback_query(F.data == "proxy:check")
async def check_proxies(callback: CallbackQuery) -> None:
    proxies = await db.list_user_proxies(callback.from_user.id)
    if not proxies:
        await callback.message.answer("😴 Прокси не добавлены.")
        return
    await callback.message.answer("🔄 Проверяю прокси...")
    lines = []
    for item in proxies[:20]:
        ok, result = await check_proxy(item["proxy_url"])
        lines.append(f"{'✅' if ok else '❌'} {hide_proxy(item['proxy_url'])}: {result}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


def format_rub(value) -> str:
    """Starvell returns money in kopecks, not whole rubles. 10800 -> 108 RUB."""
    try:
        kopecks = int(value or 0)
        rubles = kopecks / 100
        if rubles.is_integer():
            return f"{int(rubles)} RUB"
        return f"{rubles:.2f} RUB"
    except Exception:
        return "0 RUB"


def format_total_balance(balance: dict) -> str:
    available = int(balance.get('rubBalance') or 0)
    holded = int(balance.get('holdedRubBalance') or 0)
    return format_rub(available + holded)


def get_order_status(order: dict) -> str:
    raw = str(order.get("status") or order.get("state") or order.get("orderStatus") or "").upper()
    statuses = {
        "PAID": "Оплачен",
        "PENDING": "Ожидает",
        "COMPLETED": "Завершён",
        "CANCELED": "Отменён",
        "CANCELLED": "Отменён",
        "REFUND": "Возврат",
        "REFUNDED": "Возврат",
        "DISPUTE": "Спор",
    }
    return statuses.get(raw, raw or "Неизвестно")


def format_order(order: dict, index: int) -> str:
    order_id = order.get("id") or order.get("orderId") or "—"
    status = get_order_status(order)
    price = format_rub(extract_order_amount_kopecks(order))

    offer = order.get("offer") or order.get("offerDetails") or {}
    game = (offer.get("game") or {}).get("name") or "—"
    category = (offer.get("category") or {}).get("name") or "—"
    desc = ((offer.get("descriptions") or {}).get("rus") or {}).get("briefDescription") or ""
    if len(desc) > 90:
        desc = desc[:87] + "..."

    buyer = (order.get("buyer") or {}).get("username") or "—"
    created_at = order.get("createdAt") or order.get("created_at") or "—"
    link = f"https://starvell.com/account/orders/{order_id}" if order_id != "—" else "https://starvell.com/account/orders"

    return (
        f"<b>#{index}</b>\n"
        f"📌 Статус: {status}\n"
        f"🎮 Игра: {game}\n"
        f"📦 Категория: {category}\n"
        f"💰 Сумма: {price}\n"
        f"👤 Покупатель: {buyer}\n"
        f"🕒 Дата: {created_at}\n"
        f"📝 {desc}\n"
        f"🔗 {link}"
    )


@dp.message(F.text == "🛒 Заказы")
async def orders(message: Message) -> None:
    await send_recent_orders(message, message.from_user.id)



@dp.message(F.text == "/sales_debug")
async def sales_debug(message: Message) -> None:
    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("Нет подключённых аккаунтов.")
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_orders()
        page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
        orders = page_props.get("orders") or []
        source = page_props.get("_ordersSource") or data.get("_ordersSource") or "unknown"
        statuses = {}
        for order in orders:
            status = str(order.get("status") or "NO_STATUS").upper()
            statuses[status] = statuses.get(status, 0) + 1

        await message.answer(
            "🧪 <b>Диагностика продаж</b>\n\n"
            f"Версия: <code>{SALES_STATS_VERSION}</code>\n"
            f"Источник: <code>{escape(str(source))}</code>\n"
            f"Всего заказов в ответе: <b>{len(orders)}</b>\n"
            f"Статусы: <code>{escape(str(statuses))}</code>"
        )
    except Exception as error:
        await message.answer(f"❌ Ошибка диагностики: <code>{type(error).__name__}: {error}</code>")
    finally:
        await client.close()


@dp.message(F.text == "📈 Продажи")
async def sales_stats(message: Message) -> None:
    if not await require_subscription(message):
        return

    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        return

    account = accounts[0]
    await message.answer("🔄 Получаю последние данные по продажам...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        orders_data = await client.get_orders()
        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        user = page_props.get("user") or {}
        username = user.get("username") or account.username or "Starvell"

        await message.answer(format_sales_stats(username, orders_data))
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка статистики продаж: <code>{error}</code>")
    finally:
        await client.close()


@dp.message(F.text == "📊 Статистика")
async def stats(message: Message) -> None:
    if not await require_subscription(message):
        return

    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        return

    account = accounts[0]
    await message.answer("🔄 Считаю статистику за последние 30 дней...")
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats_data = await client.get_chats()
        orders_data = await client.get_orders()
        user = extract_user(chats_data)
        username = user.get("username") or account.username or "Starvell"

        await message.answer(format_month_stats(username, chats_data, orders_data))
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка статистики: <code>{error}</code>")
    finally:
        await client.close()



@dp.callback_query(F.data == "profile:back")
async def profile_back(callback: CallbackQuery) -> None:
    await send_profile_main(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:notifications")
async def profile_notifications(callback: CallbackQuery) -> None:
    await send_profile_notifications_section(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:chats_orders")
async def profile_chats_orders(callback: CallbackQuery) -> None:
    await send_profile_chats_orders_section(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:clients")
async def profile_clients(callback: CallbackQuery) -> None:
    await send_profile_clients_section(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:always_online")
async def profile_always_online(callback: CallbackQuery) -> None:
    new_value = await db.toggle_always_online_enabled(callback.from_user.id)
    text = (
        "🟢 <b>Вечный онлайн</b>\n\n"
        f"Статус: {'включён' if new_value else 'выключен'}.\n\n"
        "Сейчас бот и так работает 24/7 и продолжает проверять чаты в фоне. "
        "Этот переключатель сохранён в профиле и может использоваться для будущих функций поддержки онлайна."
    )
    await callback.message.answer(text, reply_markup=profile_back_keyboard())
    await callback.answer("Настройка обновлена")


@dp.callback_query(F.data == "profile:read_all")
async def profile_read_all(callback: CallbackQuery) -> None:
    await mark_all_messages_read_for_bot(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:open_chats")
async def profile_open_chats(callback: CallbackQuery) -> None:
    await send_latest_chats(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "profile:open_orders")
async def profile_open_orders(callback: CallbackQuery) -> None:
    await send_recent_orders(callback.message, callback.from_user.id)
    await callback.answer()




@dp.message(F.text == "/api_check")
async def api_check(message: Message) -> None:
    await message.answer(
        "✅ <b>Проверка API Starvell</b>\n\n"
        "Бот обновлён под новое условие Starvell:\n"
        "• старый числовой <code>offerId</code> не используется;\n"
        "• для операций с предложениями подготовлен <code>offerPublicId</code> / UUID;\n"
        "• если Starvell отдаст только старый int ID, бот покажет понятную ошибку и не будет отправлять неправильный запрос.\n\n"
        "Важно: уведомления, статистика, продажи, профиль и топ продавцов не зависят от offer routes, поэтому они не должны сломаться после 10 июля."
    )


@dp.message(F.text == "⭐ Настройка аккаунта")
async def account_settings_menu_message(message: Message) -> None:
    if not await require_subscription(message):
        return
    await show_account_settings_menu(message, message.from_user.id)


@dp.callback_query(F.data == "accset:menu")
async def account_settings_menu_callback(callback: CallbackQuery) -> None:
    await show_account_settings_menu(callback.message, callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data == "accset:back")
async def account_settings_back(callback: CallbackQuery) -> None:
    await callback.message.answer("Главное меню открыто.", reply_markup=main_keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("accset:toggle:"))
async def account_settings_toggle(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in ACCOUNT_SETTING_TOGGLES:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    enabled = await db.toggle_bool_account_setting(callback.from_user.id, key)
    title = ACCOUNT_SETTING_TOGGLES[key]
    await callback.message.answer(
        f"{title}\n\nСтатус: {'✅ включено' if enabled else '❌ выключено'}",
        reply_markup=account_settings_back_keyboard(),
    )
    await callback.answer("Настройка обновлена")


@dp.callback_query(F.data.startswith("accset:text:"))
async def account_settings_text_view(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in ACCOUNT_SETTING_TEXTS:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    if key == "confirm_reminder":
        await callback.message.answer(
            await build_confirm_reminder_text(callback.from_user.id),
            reply_markup=confirm_reminder_menu_keyboard(),
        )
        await callback.answer()
        return

    value = await db.get_account_setting(callback.from_user.id, key)
    title = ACCOUNT_SETTING_TEXTS[key]
    text_value = escape(value) if value else "текст не задан"
    await callback.message.answer(
        f"{title}\n\n<b>Текущий текст:</b>\n{text_value}",
        reply_markup=account_setting_text_keyboard(key),
    )
    await callback.answer()



@dp.callback_query(F.data == "reminder:toggle")
async def confirm_reminder_toggle(callback: CallbackQuery) -> None:
    enabled = await db.toggle_bool_account_setting(callback.from_user.id, "confirm_reminder_enabled")
    await callback.message.answer(
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
    )
    await callback.answer("Включено" if enabled else "Выключено")


@dp.callback_query(F.data == "reminder:time_menu")
async def confirm_reminder_time_menu(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "🕐 <b>Выберите время отправки</b>\n\n"
        "Время указывается по времени сервера. Для Bhost обычно это UTC.\n"
        "Пример: <code>13:00</code>",
        reply_markup=confirm_reminder_time_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reminder:time:"))
async def confirm_reminder_time_set(callback: CallbackQuery) -> None:
    time_value = callback.data.split(":", 2)[2]
    await db.set_account_setting(callback.from_user.id, "confirm_reminder_time", time_value)
    await callback.message.answer(
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
    )
    await callback.answer("Время сохранено")


@dp.callback_query(F.data == "reminder:time_custom")
async def confirm_reminder_time_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ConfirmReminderTime.waiting_time)
    await callback.message.answer(
        "✍️ Отправь время в формате <code>HH:MM</code>.\n\n"
        "Пример: <code>13:00</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(ConfirmReminderTime.waiting_time)
async def confirm_reminder_time_custom_save(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        await message.answer("❌ Неверный формат. Отправь время так: <code>13:00</code>")
        return

    await db.set_account_setting(message.from_user.id, "confirm_reminder_time", value)
    await state.clear()
    await message.answer(
        await build_confirm_reminder_text(message.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
    )


@dp.callback_query(F.data == "reminder:period_menu")
async def confirm_reminder_period_menu(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "📅 <b>Выберите дни отправки</b>\n\n"
        "Пример: <b>спустя день в 13:00</b> — бот будет отправлять повторный запрос не чаще одного раза в 2 дня.",
        reply_markup=confirm_reminder_period_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reminder:period:"))
async def confirm_reminder_period_set(callback: CallbackQuery) -> None:
    days = callback.data.split(":", 2)[2]
    if days not in {"1", "2", "3", "7"}:
        await callback.answer("Неверный период", show_alert=True)
        return
    await db.set_account_setting(callback.from_user.id, "confirm_reminder_period_days", days)
    await callback.message.answer(
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
    )
    await callback.answer("Дни сохранены")


@dp.callback_query(F.data.startswith("accset:edit:"))
async def account_settings_text_edit(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in ACCOUNT_SETTING_TEXTS:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    await state.set_state(AccountSettingText.waiting_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(
        f"{ACCOUNT_SETTING_TEXTS[key]}\n\n"
        "Отправь новый текст одним сообщением.\n\n"
        "Можно использовать переменные:\n"
        "<code>{username}</code> — username клиента\n"
        "<code>{order_id}</code> — номер заказа\n"
        "<code>{seller}</code> — твой Starvell username",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("accset:clear:"))
async def account_settings_text_clear(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in ACCOUNT_SETTING_TEXTS:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    await db.set_account_setting(callback.from_user.id, key, "")
    await callback.message.answer(
        f"{ACCOUNT_SETTING_TEXTS[key]}\n\n🗑 Текст очищен.",
        reply_markup=account_settings_back_keyboard(),
    )
    await callback.answer("Очищено")


@dp.message(AccountSettingText.waiting_value)
async def account_settings_text_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("setting_key")
    if key not in ACCOUNT_SETTING_TEXTS:
        await state.clear()
        await message.answer("❌ Неизвестная настройка.")
        return

    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Текст не может быть пустым. Отправь текст или нажми «Отменить».")
        return

    await db.set_account_setting(message.from_user.id, key, value)
    await state.clear()
    await message.answer(
        f"✅ Сохранено: {ACCOUNT_SETTING_TEXTS[key]}\n\n"
        f"<b>Текст:</b>\n{escape(value)}",
        reply_markup=account_settings_back_keyboard(),
    )


@dp.message(F.text == "⚙️ Настройки")
async def settings(message: Message) -> None:
    if not await require_subscription(message):
        return
    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "Пока доступны настройки на уровне .env:\n"
        f"<code>CHECK_INTERVAL_SECONDS={config.check_interval_seconds}</code>\n\n"
        "Уведомления по каждому аккаунту включаются/выключаются в разделе 🔐 Мои аккаунты."
    )


@dp.message(F.text == "🏆 Топ продавцов")
async def top_sellers_menu(message: Message) -> None:
    await message.answer(
        "🏆 <b>Топ продавцов STARVELL</b>\n\n"
        "Здесь можно собрать топ продавцов с открытых категорий Starvell или найти продавца по username.",
        reply_markup=top_sellers_menu_keyboard(),
    )


@dp.callback_query(F.data == "seller:find")
async def seller_find_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FindSellerProfile.waiting_username)
    await callback.message.answer(
        "🔎 <b>Найти профиль продавца</b>\n\n"
        "Отправь username продавца STARVELL одним сообщением.\n"
        "Пример: <code>Suppa</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "seller:top1000")
async def seller_top1000(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "🔄 Собираю топ продавцов STARVELL...\n\n"
        "Это может занять 20–60 секунд: бот проходит по открытым категориям, собирает продавцов и сортирует их по отзывам."
    )

    accounts = await db.list_user_accounts(callback.from_user.id)
    account = accounts[0] if accounts else None
    client = StarvellClient(cookie=(account.cookie if account else ""), proxy_url=(account.proxy_url if account else None))
    try:
        try:
            data = await asyncio.wait_for(client.get_top_sellers(), timeout=70)
        except asyncio.TimeoutError:
            raise StarvellApiError("Сбор топа занял больше 70 секунд. Попробуй ещё раз или подключи прокси.")
        sellers = extract_top_sellers(data)
        await callback.message.answer(format_top_sellers_list(sellers), reply_markup=top_sellers_menu_keyboard())
    except StarvellApiError as error:
        await callback.message.answer(
            "❌ <b>Не удалось собрать топ продавцов.</b>\n\n"
            f"<code>{error}</code>\n\n"
            "Пока можно найти любого продавца по username:",
            reply_markup=top_sellers_menu_keyboard(),
        )
    finally:
        await client.close()
    await callback.answer()


@dp.message(FindSellerProfile.waiting_username)
async def seller_find_finish(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip().lstrip("@")
    if not username or len(username) < 2:
        await message.answer("❌ Отправь корректный username продавца. Например: <code>Suppa</code>")
        return

    await state.clear()
    await message.answer("🔄 Ищу профиль продавца...")
    await show_seller_profile(message, username, message.from_user.id)


@dp.callback_query(F.data.startswith("seller:refresh:"))
async def seller_refresh(callback: CallbackQuery) -> None:
    username = callback.data.split(":", 2)[-1]
    await callback.message.answer("🔄 Обновляю профиль продавца...")
    await show_seller_profile(callback.message, username, callback.from_user.id)
    await callback.answer()


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer("Используй меню ниже 👇", reply_markup=main_keyboard)


async def main() -> None:
    await db.init()
    asyncio.create_task(watcher_loop())
    asyncio.create_task(confirm_reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
