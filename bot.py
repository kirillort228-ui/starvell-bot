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
from aiogram.exceptions import TelegramBadRequest

from chat_watcher import baseline_account_messages, check_one_account, extract_chats, extract_user, get_chat_link, get_interlocutor, format_notification_message, get_notification_type, extract_chat_product_info, is_purchase_paid_event, CHAT_FEATURES_VERSION
from config import load_config
from crypto_pay import CryptoPayClient, CryptoPayError
from database import Database
from keyboards import (
    account_actions_keyboard,
    accounts_keyboard,
    admin_topup_keyboard,
    cancel_keyboard,
    crypto_invoice_keyboard,
    account_settings_back_keyboard,
    account_setting_text_keyboard,
    account_settings_menu_keyboard,
    auto_raise_interval_keyboard,
    auto_raise_settings_keyboard,
    auto_raise_products_keyboard,
    auto_delivery_menu_keyboard,
    auto_delivery_products_keyboard,
    confirm_reminder_menu_keyboard,
    confirm_reminder_period_keyboard,
    confirm_reminder_time_keyboard,
    main_keyboard,
    no_subscription_keyboard,
    profile_back_keyboard,
    profile_chats_orders_keyboard,
    profile_menu_keyboard,
    product_select_menu_keyboard,
    product_select_found_keyboard,
    proxies_keyboard,
    seller_profile_keyboard,
    statistics_period_keyboard,
    top_sellers_menu_keyboard,
    top_up_keyboard,
)
from proxy_utils import check_proxy, hide_proxy, normalize_proxy, validate_proxy
from starvell_client import StarvellClient, StarvellApiError, extract_offer_public_id, find_profile_user_in_data

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


class AutoRaiseSettings(StatesGroup):
    waiting_game_id = State()
    waiting_category_ids = State()


class ProductSelectSettings(StatesGroup):
    waiting_offer_ids = State()
    waiting_category_ids = State()


class AutoDeliverySettings(StatesGroup):
    waiting_offer_ids = State()
    waiting_category_ids = State()
    waiting_product_message = State()


SALES_STATS_VERSION = "sales-fallback-notifications-v4"
STATS_FIX_VERSION = "profile-stats-notifications-v4"
PROFILE_FIX_VERSION = "profile-sales-fallback-v4"
AUTO_RAISE_VERSION = "autoraise-bump-api-4h-v2"
CLEAN_UI_VERSION = "clean-edit-ui-v1"
TOP_FIX_VERSION = "top-profile-strict-v4"
CRYPTO_PAY_VERSION = "crypto-bot-fiat-rub-v1"
PRODUCT_SELECT_VERSION = "product-select-autoreply-bump-v1"
NO_SUPPORT_VERSION = "no-support-section-v1"
AUTORAISE_CATEGORIES_ONLY_VERSION = "autoraise-categories-only-v1"
BUMP_DEFAULTS_VERSION = "bump-defaults-game16-category208-v1"
AUTO_BUMP_DETECT_VERSION = "auto-detect-bump-categories-v1"
BUMP_COOLDOWN_TEXT_VERSION = "friendly-bump-cooldown-v1"
TOP_AUTOREPLY_PRODUCTS_VERSION = "top-strict-and-autoreply-products-v2"
AUTO_BUMP_ALL_CATEGORIES_VERSION = "auto-bump-all-game-categories-v1"
AUTO_DELIVERY_MESSAGE_VERSION = "auto-delivery-message-products-v1"
AUTO_DELIVERY_MENU_CLEAN_VERSION = "autodelivery-no-manual-ids-v1"
AUTO_DELIVERY_ACTIVE_PRODUCTS_VERSION = "autodelivery-active-seller-products-v2"
AUTO_DELIVERY_TRADE_FALLBACK_VERSION = "autodelivery-trade-html-fallback-v3"
STARVELL_NESTED_TRADE_ROUTE_VERSION = "nested-trade-route-catchall-v4"
ALL_GAMES_ACTIVE_PRODUCTS_VERSION = "all-games-active-products-v5"
AUTO_DELIVERY_MESSAGE_BUTTON_VERSION = "autodelivery-message-button-v1"
STATISTICS_STYLE_VERSION = "compact-profit-stats-v1"
STATISTICS_PROFIT_VERSION = "profit-after-starvell-commission-v2"
AUTO_RAISE_PRODUCT_SELECTION_VERSION = "autoraise-product-selection-v1"
AUTO_RAISE_SELECT_ALL_VERSION = "autoraise-select-all-v1"
AUTO_DELIVERY_EVENT_FIX_VERSION = "autodelivery-read-chat-paid-event-v2"
AUTO_RAISE_PROFILE_MODE_VERSION = "autoraise-profile-search-v2"
AUTO_DELIVERY_ALL_PRODUCTS_VERSION = "autodelivery-all-products-pagination-v1"
AUTO_DELIVERY_PERSONAL_MESSAGE_VERSION = "autodelivery-personal-product-message-v1"
CLEAN_PUBLIC_UI_VERSION = "remove-top-and-technical-labels-v1"
STATS_NO_DOUBLE_COMMISSION_VERSION = "stats-no-double-commission-v1"
STATS_REVENUE_BASEPRICE_VERSION = "stats-revenue-baseprice-v2"
STATS_CORRECT_COMMISSION_VERSION = "stats-starvell-commission-2-9-v3"
FINAL_MENU_REVIEWS_VERSION = "menu-no-support-reviews-profile-only-v2"

SUBSCRIPTION_PLANS = {
    14: 39900,
    30: 69900,
    90: 139900,
    180: 269900,
}


async def safe_edit_or_answer(message: Message, text: str, reply_markup=None, **kwargs) -> None:
    """
    Makes inline sections cleaner: tries to edit the previous bot message.
    If Telegram cannot edit it, sends a normal new message.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup, **kwargs)
    except Exception:
        await message.answer(text, reply_markup=reply_markup, **kwargs)


async def clean_callback_answer(callback: CallbackQuery, text: str, reply_markup=None, alert_text: str | None = None) -> None:
    await safe_edit_or_answer(callback.message, text, reply_markup=reply_markup)
    await callback.answer(alert_text or "")



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
        "Отзывы и рейтинг берутся только из публичного профиля продавца. "
        "Числа из карточек категорий не используются.\n\n"
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
        source_note = ""
        text += f"🏆 <b>#{index}: {escape(username)}</b> — {rating_text}⭐ | отзывов: {reviews_text}{source_note}\n"

    if len(sellers) > 30:
        text += f"\nПоказано 30 из {len(sellers)} найденных продавцов."
    text += f"\n\n<i>Версия топа: {TOP_FIX_VERSION}</i>"
    return text


def extract_order_date(order: dict) -> str | None:
    return order.get("createdAt") or order.get("created_at") or order.get("updatedAt") or order.get("paidAt")


def extract_order_gross_amount_kopecks(order: dict) -> int:
    """
    Tries to read the full amount paid before Starvell commission.
    Falls back to the seller amount when Starvell does not expose a separate gross total.
    """
    for key in ("totalPrice", "totalRubAmount", "total", "grossAmount", "amountWithFee"):
        value = order.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return extract_order_amount_kopecks(order)


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



def order_is_refund(order: dict) -> bool:
    status = str(order.get("status") or order.get("state") or order.get("orderStatus") or "").upper()
    return status in {"REFUND", "REFUNDED", "RETURNED", "CHARGEBACK"}


def order_in_stats_period(order: dict, period: str) -> bool:
    if period == "all":
        return True

    dt = parse_starvell_datetime(extract_order_date(order))
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if period == "today":
        return dt.astimezone(timezone.utc).date() == now.date()

    try:
        days = max(1, int(period))
    except Exception:
        days = 30
    return dt >= now - timedelta(days=days)


def stats_period_title(period: str) -> str:
    return {
        "today": "Сегодня",
        "7": "7 дней",
        "30": "30 дней",
        "all": "Всё время",
    }.get(period, "30 дней")


def format_compact_profit_stats(
    username: str,
    orders_data: dict,
    chats_data: dict | None,
    *,
    period: str = "7",
) -> str:
    """
    Compact statistics layout inspired by the requested screenshot.

    The first marketplace commission row is intentionally omitted.
    The listing-bump, KOSell and extra withdrawal-fee rows are omitted.
Final profit uses the seller amount already returned after Starvell commission.
    """
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    orders = page_props.get("orders") or []
    user = page_props.get("user") or {}
    my_user_id = user.get("id")

    period_orders = [order for order in orders if order_in_stats_period(order, period)]
    sales = [
        order for order in period_orders
        if is_sale_order(order, username, my_user_id)
        and is_successful_sale(order)
        and not order_is_refund(order)
    ]
    refunds = [
        order for order in period_orders
        if is_sale_order(order, username, my_user_id) and order_is_refund(order)
    ]

    # basePrice is the seller's listed revenue before Starvell commission.
    revenue = sum(extract_order_amount_kopecks(order) for order in sales)

    # Starvell commission is 2.9% of seller revenue.
    starvell_commission = round(revenue * 0.029)
    profit_after_commissions = max(0, revenue - starvell_commission)

    # Fallback counts from chat notifications when orders endpoint is empty.
    if not period_orders and chats_data:
        _, fallback = get_notification_fallback_from_chats_data(chats_data)
        sales_count = fallback["completed"]
        refunds_count = fallback["refund"]
    else:
        sales_count = len(sales)
        refunds_count = len(refunds)

    title = stats_period_title(period)
    return (
        f"📊 <b>Статистика ({title})</b>\n\n"
        f"🛒 Продаж: <b>{sales_count}</b>\n"
        f"🔄 Возвратов: <b>{refunds_count}</b>\n"
        f"💰 Выручка: <b>{format_rub(revenue)}</b>\n\n"
        f"✅ <b>Прибыль с учётом комиссии Starvell: {format_rub(profit_after_commissions)}</b>"
    )


async def send_compact_statistics(message: Message, user_id: int, period: str = "7") -> None:
    if not await require_subscription(message):
        return

    accounts = await db.list_user_accounts(user_id)
    if not accounts:
        await message.answer(
            "😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты."
        )
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        orders_data = await client.get_orders()
        chats_data = await client.get_chats()
        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        user = page_props.get("user") or extract_user(chats_data) or {}
        username = user.get("username") or account.username or "Starvell"

        await message.answer(
            format_compact_profit_stats(
                username,
                orders_data,
                chats_data,
                period=period,
            ),
            reply_markup=statistics_period_keyboard(),
        )
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка статистики: <code>{error}</code>")
    finally:
        await client.close()



def format_sales_stats(username: str, orders_data: dict, chats_data: dict | None = None, *, days: int = 30) -> str:
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    all_orders = page_props.get("orders") or []
    user = page_props.get("user") or {}
    my_user_id = user.get("id")

    recent_sales = get_recent_sales_from_orders(username, orders_data, my_user_id)
    total_gross_kopecks = sum(extract_order_amount_kopecks(order) for order in recent_sales)
    with_reviews = [order for order in recent_sales if extract_order_review(order) != (None, None)]

    notify_counts = {}
    notify_fallback = {"completed": 0, "refund": 0, "review": 0, "paid": 0, "orders_total": 0}
    if chats_data:
        notify_counts, notify_fallback = get_notification_fallback_from_chats_data(chats_data)

    use_fallback = not recent_sales and notify_fallback["orders_total"] > 0
    display_sales_count = notify_fallback["completed"] if use_fallback else len(recent_sales)
    display_reviews_count = notify_fallback["review"] if use_fallback else len(with_reviews)

    text = (
        f"📈 <b>Продажи за последние {days} дней</b>\n\n"
        f"👤 Аккаунт: <b>{escape(username)}</b>\n"
        f"🛍 Количество продаж: <b>{display_sales_count}</b>\n"
        f"⭐ Отзывов клиентов: <b>{display_reviews_count}</b>\n"
        f"💰 Заработано без учёта комиссии проекта: <b>{format_rub(total_gross_kopecks)}</b>\n\n"
    )

    if use_fallback:
        text += (
            "\n<i>Starvell не отдал список заказов с ценами, поэтому количество продаж и отзывов взято из уведомлений чата.</i>\n"
        )

    if not recent_sales:
        if use_fallback:
            return text + "\nСумма осталась 0 RUB, потому что в уведомлениях нет цены заказа."
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

def get_order_notification_counts(chats: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chat in chats:
        last = chat.get("lastMessage") or {}
        if not is_last_30_days(last.get("createdAt")):
            continue
        if last.get("type") != "NOTIFICATION":
            continue
        notification_type = ((last.get("metadata") or {}).get("notificationType") or "NOTIFICATION")
        notification_type = str(notification_type).upper()
        counts[notification_type] = counts.get(notification_type, 0) + 1
    return counts


def count_sales_from_notifications(notification_counts: dict[str, int]) -> dict[str, int]:
    completed = int(notification_counts.get("ORDER_COMPLETED", 0))
    refund = int(notification_counts.get("ORDER_REFUND", 0) or notification_counts.get("ORDER_REFUNDED", 0))
    review = int(notification_counts.get("REVIEW_CREATED", 0))
    paid = int(
        notification_counts.get("ORDER_PAID", 0)
        or notification_counts.get("ORDER_CREATED", 0)
        or notification_counts.get("ORDER_STARTED", 0)
        or notification_counts.get("ORDER_ACCEPTED", 0)
    )
    return {
        "completed": completed,
        "refund": refund,
        "review": review,
        "paid": paid,
        "orders_total": completed + refund + paid,
    }


def get_notification_fallback_from_chats_data(chats_data: dict) -> tuple[dict[str, int], dict[str, int]]:
    chats = extract_chats(chats_data)
    notification_counts = get_order_notification_counts(chats)
    return notification_counts, count_sales_from_notifications(notification_counts)


def get_recent_sales_from_orders(username: str, orders_data: dict, my_id=None) -> list[dict]:
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    all_orders = page_props.get("orders") or []
    user = page_props.get("user") or {}
    my_user_id = my_id or user.get("id")
    result = []
    for order in all_orders:
        if not is_last_30_days(extract_order_date(order)):
            continue
        if not is_sale_order(order, username, my_user_id):
            continue
        if not is_successful_sale(order):
            continue
        result.append(order)
    return result


def get_recent_orders_from_orders(orders_data: dict) -> list[dict]:
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    all_orders = page_props.get("orders") or []
    return [order for order in all_orders if is_last_30_days(extract_order_date(order))]


def get_buyer_username_from_order(order: dict) -> str:
    buyer = order.get("buyer") or order.get("customer") or order.get("client") or order.get("user") or {}
    if isinstance(buyer, dict):
        return buyer.get("username") or buyer.get("name") or "Неизвестно"
    return "Неизвестно"


def format_month_stats(username: str, chats_data: dict, orders_data: dict) -> str:
    chats = extract_chats(chats_data)
    page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
    orders_list = page_props.get("orders") or []
    orders_user = page_props.get("user") or {}

    recent_chats = []
    unread_total = 0
    incoming_messages = 0
    notification_counts = {}

    chat_user = extract_user(chats_data)
    my_id = orders_user.get("id") or chat_user.get("id")

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

    recent_sales = [
        order for order in recent_orders
        if is_sale_order(order, username, my_id) and is_successful_sale(order)
    ]

    # If Starvell did not return orders but chat notifications contain order events,
    # use notifications as a visible fallback instead of showing zeros.
    notify_fallback = count_sales_from_notifications(notification_counts)
    using_notify_fallback = not recent_orders and notify_fallback["orders_total"] > 0

    total_amount = sum(extract_order_amount_kopecks(order) for order in recent_orders)
    sales_gross_amount = sum(extract_order_amount_kopecks(order) for order in recent_sales)
    sales_reviews = sum(1 for order in recent_sales if extract_order_review(order) != (None, None))

    completed = sum(1 for order in recent_orders if str(order.get("status") or order.get("state") or "").upper() in ("COMPLETED", "DONE", "FINISHED"))
    paid = sum(1 for order in recent_orders if str(order.get("status") or order.get("state") or "").upper() in ("PAID", "ACTIVE", "PROCESSING"))

    if using_notify_fallback:
        completed = notify_fallback["completed"]
        paid = notify_fallback["paid"]
        sales_reviews = notify_fallback["review"]
        recent_order_count = notify_fallback["orders_total"]
        recent_sales_count = notify_fallback["completed"]
        fallback_note = "\n<i>Заказы Starvell не отдал в orders.json, поэтому количество взято из уведомлений чата.</i>"
    else:
        recent_order_count = len(recent_orders)
        recent_sales_count = len(recent_sales)
        fallback_note = ""

    extra = ""
    if notification_counts:
        extra = "\n\n🔔 <b>Уведомления за месяц:</b>\n" + "\n".join(
            f"• {escape(str(k))}: {v}" for k, v in sorted(notification_counts.items())
        )

    source = page_props.get("_ordersSource") or orders_data.get("_ordersSource") or "account/orders"

    return (
        f"📊 <b>Статистика аккаунта за последние 30 дней</b>\n\n"
        f"👤 Аккаунт: <b>{escape(username)}</b>\n\n"
        f"💬 Чаты: {len(recent_chats)}\n"
        f"📩 Непрочитано сейчас: {unread_total}\n"
        f"📨 Последних входящих сообщений: {incoming_messages}\n\n"
        f"🛒 Заказы: {recent_order_count}\n"
        f"✅ Завершено: {completed}\n"
        f"⏳ В работе/оплачено: {paid}\n"
        f"💰 Сумма заказов: {format_rub(total_amount)}\n\n"
        f"📈 <b>Продажи</b>\n"
        f"🛍 Количество продаж: {recent_sales_count}\n"
        f"⭐ Отзывов клиентов: {sales_reviews}\n"
        f"💰 Заработано без учёта комиссии проекта: {format_rub(sales_gross_amount)}"
        f"{fallback_note}\n\n"
        f"<i>Версия статистики: {STATS_FIX_VERSION}</i>\n"
        f"<i>Источник заказов: {escape(str(source))}; заказов в ответе: {len(orders_list)}</i>"
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


def extract_profile_user(data: dict, requested_username: str | None = None) -> tuple[dict, dict]:
    page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
    user = find_profile_user_in_data(data, requested_username)
    return page_props, user


def format_seller_profile_text(requested_username: str, data: dict) -> str:
    page_props, user = extract_profile_user(data, requested_username)
    username = user.get("username") or requested_username
    rank = profile_rank_text(page_props, user)
    title = f"🏆 <b>Топ #{rank}: {escape(str(username))}</b>" if rank else f"🏆 <b>Профиль продавца: {escape(str(username))}</b>"

    rating = user.get("rating")
    if rating is None:
        rating_text = "—"
    else:
        try:
            rating_text = f"{float(rating):.2f} ⭐"
        except Exception:
            rating_text = f"{rating} ⭐"

    reviews = int(user.get("reviewsCount") or 0)
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






def csv_set_from_text(value: str | None) -> set[str]:
    return {part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()}


def find_nested_first(obj, keys: tuple[str, ...]):
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        for value in obj.values():
            found = find_nested_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_nested_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def extract_products_from_chats_data(data: dict) -> list[dict]:
    products: dict[str, dict] = {}
    for chat in extract_chats(data):
        last_message = chat.get("lastMessage") or {}
        source = {"chat": chat, "lastMessage": last_message}
        offer_public_id = find_nested_first(source, ("offerPublicId", "publicId", "uuid"))
        category_id = find_nested_first(source, ("categoryId", "category_id"))
        game_id = find_nested_first(source, ("gameId", "game_id"))
        title = (
            find_nested_first(source, ("briefDescription", "title", "name"))
            or get_order_short_info(last_message)
            or "Товар из чата"
        )

        if offer_public_id:
            key = f"offer:{offer_public_id}"
            products[key] = {
                "offerPublicId": str(offer_public_id),
                "categoryId": str(category_id or ""),
                "gameId": str(game_id or ""),
                "title": str(title)[:90],
            }
        elif category_id:
            key = f"cat:{category_id}"
            products[key] = {
                "offerPublicId": "",
                "categoryId": str(category_id),
                "gameId": str(game_id or ""),
                "title": f"{title} / categoryId {category_id}",
            }
    return list(products.values())


def extract_products_from_offers_data(data: dict) -> list[dict]:
    products: dict[str, dict] = {}
    def walk(obj):
        if isinstance(obj, dict):
            offer_public_id = obj.get("offerPublicId") or obj.get("publicId") or obj.get("uuid")
            category_id = obj.get("categoryId") or obj.get("category_id")
            game_id = obj.get("gameId") or obj.get("game_id")
            title = (
                obj.get("title")
                or obj.get("name")
                or (((obj.get("descriptions") or {}).get("rus") or {}).get("briefDescription") if isinstance(obj.get("descriptions"), dict) else None)
                or find_nested_first(obj, ("briefDescription", "title", "name"))
                or "Товар"
            )
            if offer_public_id:
                products[f"offer:{offer_public_id}"] = {
                    "offerPublicId": str(offer_public_id),
                    "categoryId": str(category_id or ""),
                    "gameId": str(game_id or ""),
                    "title": str(title)[:90],
                }
            elif category_id and (obj.get("category") or obj.get("offer") or obj.get("price") or obj.get("status")):
                products[f"cat:{category_id}"] = {
                    "offerPublicId": "",
                    "categoryId": str(category_id),
                    "gameId": str(game_id or ""),
                    "title": f"{str(title)[:70]} / categoryId {category_id}",
                }
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(data)
    return list(products.values())


async def scan_user_products(user_id: int) -> list[dict]:
    accounts = await db.list_user_accounts(user_id)
    if not accounts:
        return []

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    found: list[dict] = []
    try:
        # Main source: only active offers belonging to the connected seller.
        try:
            active_offers = await asyncio.wait_for(
                client.collect_active_seller_offers(account.username),
                timeout=75,
            )
            found.extend(active_offers)
        except Exception:
            pass

        # Extra fallback: chats may contain an offer that is not visible in profile JSON yet.
        try:
            chats_data = await asyncio.wait_for(client.get_chats(), timeout=15)
            found.extend(extract_products_from_chats_data(chats_data))
        except Exception:
            pass
    finally:
        await client.close()

    dedup: dict[str, dict] = {}
    for item in found:
        offer_id = str(item.get("offerPublicId") or "").strip()
        category_id = str(item.get("categoryId") or "").strip()
        key = offer_id or (f"cat:{category_id}" if category_id else "")
        if key and key not in dedup:
            dedup[key] = item
    return list(dedup.values())



async def build_product_select_text(user_id: int) -> str:
    offer_ids = await db.get_account_setting(user_id, "auto_responder_offer_ids", "")
    category_ids = await db.get_account_setting(user_id, "auto_responder_category_ids", "")
    return (
        "🎯 <b>Товары для автоответчика</b>\n\n"
        "<b>Автоответчик будет работать:</b>\n"
        f"offerPublicId: <code>{escape(offer_ids) if offer_ids else 'все товары'}</code>\n"
        f"categoryIds: <code>{escape(category_ids) if category_ids else 'не ограничено'}</code>\n\n"
        "Если список пустой — автоответчик отвечает на все товары.\n"
        "Если выбрать товары — автоответчик будет включаться только в чатах/заказах по выбранным товарам."
    )





async def build_auto_delivery_text(user_id: int) -> str:
    enabled = await db.get_bool_account_setting(user_id, "auto_delivery")
    message_text = await db.get_account_setting(user_id, "auto_delivery_message", "")
    offer_ids = csv_set_from_text(
        await db.get_account_setting(user_id, "auto_delivery_offer_ids", "")
    )
    return (
        "📦 <b>Автовыдача сообщением</b>\n\n"
        f"Статус: {'✅ включена' if enabled else '❌ выключена'}\n"
        f"Сообщение: {'✅ настроено' if message_text and message_text.strip() else '❌ не настроено'}\n"
        f"Выбрано товаров: <b>{len(offer_ids)}</b>\n\n"
        "Сообщение отправляется один раз после события оплаты/покупки выбранного товара.\n"
        "Доступные переменные: <code>{username}</code>, <code>{seller}</code>, <code>{order_id}</code>."
    )


async def show_auto_delivery_menu(message: Message, user_id: int) -> None:
    enabled = await db.get_bool_account_setting(user_id, "auto_delivery")
    await message.answer(
        await build_auto_delivery_text(user_id),
        reply_markup=auto_delivery_menu_keyboard(enabled),
    )


ACCOUNT_SETTING_TEXTS = {
    "greeting": "👋 Приветствие",
    "auto_responder": "🤖 Автоответчик",
    "auto_delivery_message": "📦 Сообщение автовыдачи",
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






def parse_int_list(value: str) -> list[int]:
    result = []
    for part in re.split(r"[,\s;]+", str(value or "").strip()):
        if not part:
            continue
        result.append(int(part))
    return result




def format_duration_ru(seconds: int | str | None) -> str:
    try:
        total = max(0, int(seconds or 0))
    except Exception:
        total = 0

    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days} д.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes or not parts:
        parts.append(f"{minutes} мин.")
    return " ".join(parts)


def extract_bump_cooldown_seconds(error: Exception | str) -> int | None:
    text = str(error)
    if "OFFERS_BUMP_COOLDOWN" not in text and "retryAfterSeconds" not in text:
        return None

    patterns = [
        r'"retryAfterSeconds"\s*:\s*(\d+)',
        r"'retryAfterSeconds'\s*:\s*(\d+)",
        r"retryAfterSeconds['\"]?\s*[:=]\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return 0


def format_bump_cooldown_message(seconds: int | None) -> str:
    wait_text = format_duration_ru(seconds)
    return (
        "⏳ <b>Поднятие пока недоступно.</b>\n"
        f"Starvell разрешит снова через <b>{wait_text}</b>."
    )




async def get_auto_raise_selected_products(user_id: int) -> list[dict]:
    raw = await db.get_account_setting(user_id, "auto_raise_selected_products_json", "[]")
    try:
        data = json.loads(raw or "[]")
    except Exception:
        data = []

    result = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        offer_id = str(item.get("offerPublicId") or "").strip()
        game_id = str(item.get("gameId") or "").strip()
        category_id = str(item.get("categoryId") or "").strip()
        if not game_id or not category_id:
            continue
        key = offer_id or f"{game_id}:{category_id}"
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "offerPublicId": offer_id,
            "gameId": game_id,
            "categoryId": category_id,
            "title": str(item.get("title") or "Товар"),
            "gameName": str(item.get("gameName") or item.get("gameSlug") or ""),
            "categoryName": str(item.get("categoryName") or ""),
        })
    return result


async def save_auto_raise_selected_products(user_id: int, items: list[dict]) -> None:
    await db.set_account_setting(
        user_id,
        "auto_raise_selected_products_json",
        json.dumps(items, ensure_ascii=False),
    )


def group_selected_products_for_bump(items: list[dict]) -> dict[int, set[int]]:
    groups: dict[int, set[int]] = {}
    for item in items:
        try:
            game_id = int(item.get("gameId") or 0)
            category_id = int(item.get("categoryId") or 0)
        except Exception:
            continue
        if game_id > 0 and category_id > 0:
            groups.setdefault(game_id, set()).add(category_id)
    return groups


async def get_auto_raise_config(user_id: int) -> dict:
    enabled = await db.get_bool_account_setting(user_id, "auto_raise_lots")
    game_id_raw = await db.get_account_setting(user_id, "auto_raise_game_id", "16")
    categories_raw = await db.get_account_setting(user_id, "auto_raise_category_ids", "208")
    interval_raw = await db.get_account_setting(user_id, "auto_raise_interval_hours", "4")
    try:
        game_id = int(game_id_raw or 0)
    except Exception:
        game_id = 0
    try:
        category_ids = parse_int_list(categories_raw or "")
    except Exception:
        category_ids = []
    try:
        # Starvell allows bumping lots only once every 4 hours.
        interval_hours = max(4, int(interval_raw or 4))
    except Exception:
        interval_hours = 6
    return {
        "enabled": enabled,
        "game_id": game_id,
        "category_ids": category_ids,
        "interval_hours": interval_hours,
    }


async def build_auto_raise_text(user_id: int, account_id: int | None = None) -> str:
    cfg = await get_auto_raise_config(user_id)
    last_at = None
    last_result = None
    if account_id:
        last_at, last_result = await db.get_auto_raise_state(account_id)

    return (
        "🚀 <b>Автоподнятие лотов</b>\n\n"
        f"Статус: {'✅ включено' if cfg['enabled'] else '❌ выключено'}\n"
        f"Поиск товаров: <b>через профиль продавца Starvell</b>\n"
        f"Интервал: <b>{cfg['interval_hours']} ч.</b>\n\n"
        f"Последний запуск: <code>{escape(str(last_at or 'ещё не запускалось'))}</code>\n"
        f"Результат: <code>{escape(str(last_result or '—'))}</code>"
    )


async def run_auto_raise_for_account(account) -> str:
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        username = str(account.username or "").strip()
        if not username:
            try:
                chats_data = await client.get_chats()
                username = str((extract_user(chats_data) or {}).get("username") or "").strip()
            except Exception:
                username = ""

        if not username:
            raise StarvellApiError(
                "Не удалось определить username подключённого профиля Starvell."
            )

        # Profile-first discovery: the seller username/profile identifies the owner,
        # then active offers across that seller's games/categories are collected.
        active_offers = await client.collect_active_seller_offers(username)

        groups: dict[int, set[int]] = {}
        for offer in active_offers:
            try:
                game_id = int(offer.get("gameId") or 0)
                category_id = int(offer.get("categoryId") or 0)
            except Exception:
                continue
            if game_id > 0 and category_id > 0:
                groups.setdefault(game_id, set()).add(category_id)

        if not groups:
            raise StarvellApiError(
                f"В профиле {username} не найдены активные публичные лоты для поднятия."
            )

        results = [
            f"Профиль: {username}",
            f"Найдено активных лотов: {len(active_offers)}",
            f"Найдено игр: {len(groups)}",
            f"Найдено категорий: {sum(len(v) for v in groups.values())}",
        ]

        for game_id, category_ids_set in sorted(groups.items()):
            category_ids = sorted(category_ids_set)
            try:
                result = await client.bump_offers(game_id, category_ids)
                results.append(
                    f"✅ gameId={game_id}, categoryIds={category_ids}: {str(result)[:300]}"
                )
            except Exception as grouped_error:
                if extract_bump_cooldown_seconds(grouped_error) is not None:
                    raise

                split_results = []
                for category_id in category_ids:
                    try:
                        await client.bump_offers(game_id, [category_id])
                        split_results.append(f"{category_id}: ✅")
                    except Exception as one_error:
                        if extract_bump_cooldown_seconds(one_error) is not None:
                            raise
                        split_results.append(
                            f"{category_id}: ❌ {type(one_error).__name__}: {str(one_error)[:100]}"
                        )
                    await asyncio.sleep(0.7)
                results.append(f"gameId={game_id}: " + "; ".join(split_results))

            await asyncio.sleep(0.7)

        short_result = "\n".join(results)
        await db.save_auto_raise_state(account.id, short_result)
        return short_result
    finally:
        await client.close()


async def auto_raise_loop() -> None:
    await asyncio.sleep(20)
    while True:
        accounts = await db.list_enabled_accounts()
        now = datetime.now(timezone.utc)
        for account in accounts:
            try:
                cfg = await get_auto_raise_config(account.user_id)
                if not cfg["enabled"]:
                    continue
                last_at_raw, _ = await db.get_auto_raise_state(account.id)
                should_run = True
                if last_at_raw:
                    last_at = datetime.fromisoformat(str(last_at_raw))
                    if last_at.tzinfo is None:
                        last_at = last_at.replace(tzinfo=timezone.utc)
                    should_run = (now - last_at) >= timedelta(hours=cfg["interval_hours"])
                if should_run:
                    await run_auto_raise_for_account(account)
            except Exception as error:
                try:
                    cooldown_seconds = extract_bump_cooldown_seconds(error)
                    if cooldown_seconds is not None:
                        await db.save_auto_raise_state(
                            account.id,
                            format_bump_cooldown_message(cooldown_seconds).replace("<b>", "").replace("</b>", ""),
                        )
                    else:
                        await db.save_auto_raise_state(account.id, f"ERROR: {type(error).__name__}: {error}")
                except Exception:
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
    base_text = await build_profile_main_text(message.from_user)

    account = await get_primary_account(user_id)
    if not account:
        await message.answer(
            base_text + f"\n\n<i>Версия профиля: {PROFILE_FIX_VERSION}</i>",
            reply_markup=profile_menu_keyboard(always_online),
        )
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats_data = await asyncio.wait_for(client.get_chats(), timeout=25)
        orders_data = await asyncio.wait_for(client.get_orders(), timeout=25)

        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        star_user = page_props.get("user") or extract_user(chats_data) or {}
        username = star_user.get("username") or account.username or "Starvell"
        rating = star_user.get("rating", "—")
        reviews = star_user.get("reviewsCount", 0)
        kyc = "✅ пройдена" if str(star_user.get("kycStatus") or "").upper() == "VERIFIED" else "⚠️ не пройдена"
        balance = star_user.get("balance") or {}
        available = format_rub(balance.get("rubBalance") or 0)
        holded = format_rub(balance.get("holdedRubBalance") or 0)
        withdrawable = format_rub(balance.get("withdrawableRubBalance") or 0)

        notify_counts, notify_fallback = get_notification_fallback_from_chats_data(chats_data)
        recent_sales = get_recent_sales_from_orders(username, orders_data, star_user.get("id"))
        sales_count = len(recent_sales) if recent_sales else notify_fallback["completed"]
        reviews_count = len([o for o in recent_sales if extract_order_review(o) != (None, None)]) if recent_sales else notify_fallback["review"]
        earned = sum(extract_order_amount_kopecks(o) for o in recent_sales)

        extra = (
            "\n\n🌐 <b>Starvell аккаунт</b>\n"
            f"👤 Username: <b>{escape(username)}</b>\n"
            f"⭐ Рейтинг: <b>{escape(str(rating))}</b>\n"
            f"💬 Отзывов профиля: <b>{reviews}</b>\n"
            f"🛡 KYC: {kyc}\n\n"
            "💰 <b>Баланс Starvell</b>\n"
            f"├ Доступно: <b>{available}</b>\n"
            f"├ В холде: <b>{holded}</b>\n"
            f"└ Можно вывести: <b>{withdrawable}</b>\n\n"
            "📈 <b>Продажи за 30 дней</b>\n"
            f"🛍 Продаж: <b>{sales_count}</b>\n"
            f"⭐ Отзывов клиентов: <b>{reviews_count}</b>\n"
            f"💵 Заработано без комиссии: <b>{format_rub(earned)}</b>\n"
        )
        if not recent_sales and notify_fallback["orders_total"] > 0:
            extra += "<i>Количество взято из уведомлений, потому что Starvell не отдал список заказов с ценами.</i>\n"
        extra += f"\n<i>Версия профиля: {PROFILE_FIX_VERSION}</i>"

        await message.answer(base_text + extra, reply_markup=profile_menu_keyboard(always_online))
    except Exception as error:
        await message.answer(
            base_text + f"\n\n⚠️ Не удалось получить расширенный профиль Starvell: <code>{type(error).__name__}: {escape(str(error))}</code>\n"
            f"<i>Версия профиля: {PROFILE_FIX_VERSION}</i>",
            reply_markup=profile_menu_keyboard(always_online),
        )
    finally:
        await client.close()



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
        orders = get_recent_orders_from_orders(orders_data)
        unread = sum(int(chat.get('unreadMessageCount') or 0) for chat in chats)
        username = extract_user(chats_data).get('username') or account.username or 'Starvell'
        notify_counts, notify_fallback = get_notification_fallback_from_chats_data(chats_data)

        orders_count = len(orders)
        completed_count = sum(1 for order in orders if is_successful_sale(order))
        fallback_line = ""
        if orders_count == 0 and notify_fallback["orders_total"] > 0:
            orders_count = notify_fallback["orders_total"]
            completed_count = notify_fallback["completed"]
            fallback_line = "\n<i>Заказы взяты из уведомлений, потому что Starvell отдал пустой orders.json.</i>"

        text = (
            "💬 <b>Чаты и заказы</b>\n\n"
            f"👤 Аккаунт: <b>{escape(username)}</b>\n"
            f"💬 Чатов всего: {len(chats)}\n"
            f"📩 Непрочитано сейчас: {unread}\n"
            f"🛒 Заказов за 30 дней: {orders_count}\n"
            f"✅ Завершено: {completed_count}\n"
            f"{fallback_line}\n\n"
            f"<i>Версия профиля: {PROFILE_FIX_VERSION}</i>\n\n"
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
            buyer = get_buyer_username_from_order(order)
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




def get_support_contacts() -> tuple[str, str]:
    username = (getattr(config, "support_username", "") or "").strip().lstrip("@")
    url = (getattr(config, "support_url", "") or "").strip()
    if not url and username:
        url = f"https://t.me/{username}"
    return username, url






@dp.message(F.text.contains("Поддержка"))
async def old_support_button_cleanup(message: Message) -> None:
    await message.answer(
        "✅ Раздел поддержки удалён. Обновляю меню без этой кнопки.",
        reply_markup=main_keyboard,
    )



@dp.message(F.text == "/clean_ui_debug")
async def clean_ui_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика интерфейса</b>\n\n"
        f"Версия: <code>{CLEAN_PUBLIC_UI_VERSION}</code>\n"
        "Топ продавцов: удалён\n"
        "Технические версии и источники: скрыты\n"
        "offerPublicId/categoryIds в меню автовыдачи: скрыты"
    )



@dp.message(F.text == "/stats_commission_debug")
async def stats_commission_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика комиссии</b>\n\n"
        f"Версия: <code>{STATS_NO_DOUBLE_COMMISSION_VERSION}</code>\n"
        "Дополнительные 2,9% больше не вычитаются.\n"
        "Используется сумма продавца, которую Starvell уже вернул после своей комиссии."
    )



@dp.message(F.text == "/stats_revenue_debug")
async def stats_revenue_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика выручки</b>\n\n"
        f"Версия: <code>{STATS_REVENUE_BASEPRICE_VERSION}</code>\n"
        "Выручка считается по basePrice — реальной цене продажи продавца.\n"
        "totalPrice с комиссией покупателя больше не используется."
    )



@dp.message(F.text == "/stats_correct_commission_debug")
async def stats_correct_commission_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика комиссии Starvell</b>\n\n"
        f"Версия: <code>{STATS_CORRECT_COMMISSION_VERSION}</code>\n"
        "Расчёт: выручка − 2,9% комиссии Starvell.\n"
        "Пример: 4,00 ₽ − 0,116 ₽ = 3,884 ₽."
    )

@dp.message(CommandStart())
async def start(message: Message) -> None:
    await db.ensure_user(message.from_user.id)
    await message.answer(
        "👋 Привет!\n\n"
        "Это бот для уведомлений Starvell. Он проверяет чаты и присылает ссылку, когда появляется новое непрочитанное сообщение.",
        reply_markup=main_keyboard,
    )








@dp.message(F.text == "/ui_debug")
async def ui_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>UI диагностика</b>\n\n"
        f"Версия интерфейса: <code>{CLEAN_UI_VERSION}</code>\n"
        "Inline-разделы теперь стараются редактировать текущее сообщение, а не отправлять много новых."
    )


@dp.message(F.text == "/chat_auto_debug")
async def chat_auto_debug(message: Message) -> None:
    user_id = message.from_user.id
    keys = [
        "greeting",
        "auto_responder",
        "ignore_text",
        "after_5_stars",
        "problem_text",
        "after_seller_confirm",
        "after_client_confirm",
    ]
    lines = [
        "🧪 <b>Диагностика чат-автофункций</b>",
        "",
        f"Версия: <code>{CHAT_FEATURES_VERSION}</code>",
        f"Задержка текста при игноре: <code>{escape(str(await db.get_account_setting(user_id, 'ignore_delay_minutes', '60')))} мин.</code>",
        "",
    ]
    for key in keys:
        value = await db.get_account_setting(user_id, key)
        lines.append(f"{ACCOUNT_SETTING_TEXTS.get(key, key)}: {'✅ задано' if value and value.strip() else '❌ не задано'}")
    await message.answer("\n".join(lines))




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

    if not config.crypto_pay_token:
        await message.answer(
            "❌ <b>Оплата криптой пока не настроена.</b>\n\n"
            "Добавь в .env строку:\n"
            "<code>CRYPTO_PAY_TOKEN=токен_из_CryptoBot</code>\n\n"
            "Токен создаётся в @CryptoBot → Crypto Pay → My Apps / Create App."
        )
        return

    amount_rub = amount_kopecks / 100
    crypto = CryptoPayClient(config.crypto_pay_token, config.crypto_pay_api_base)
    try:
        invoice = await crypto.create_fiat_invoice(
            amount_rub=amount_rub,
            payload=f"topup:{request_id}:{user_id}:{amount_kopecks}",
            description=f"Пополнение баланса Starvell Bot на {amount_rub:.2f} RUB",
            accepted_assets=config.crypto_pay_accepted_assets,
            expires_in=3600,
        )
    except CryptoPayError as error:
        await message.answer(
            "❌ <b>Не удалось создать счёт Crypto Bot.</b>\n\n"
            f"<code>{escape(str(error))}</code>\n\n"
            "Проверь CRYPTO_PAY_TOKEN в .env."
        )
        return
    finally:
        await crypto.close()

    invoice_id = str(invoice.get("invoice_id") or "")
    pay_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("pay_url")
        or invoice.get("mini_app_invoice_url")
        or invoice.get("web_app_invoice_url")
        or ""
    )

    if not invoice_id or not pay_url:
        await message.answer(
            "❌ Crypto Bot создал счёт, но не вернул ссылку на оплату.\n\n"
            f"<code>{escape(str(invoice)[:900])}</code>"
        )
        return

    await db.set_topup_crypto_invoice(request_id, invoice_id, pay_url)

    await message.answer(
        "💎 <b>Счёт на оплату криптой создан</b>\n\n"
        f"🆔 Заявка: <code>{request_id}</code>\n"
        f"🧾 Invoice ID: <code>{invoice_id}</code>\n"
        f"💵 Сумма: <b>{format_rub(amount_kopecks)}</b>\n\n"
        "Нажми кнопку оплаты ниже. После оплаты бот сам зачислит баланс.\n"
        "Также можно нажать «🔄 Проверить оплату».",
        reply_markup=crypto_invoice_keyboard(pay_url, request_id),
    )

    if config.admin_id:
        try:
            await bot.send_message(
                config.admin_id,
                "💎 <b>Создан счёт Crypto Bot</b>\n\n"
                f"🆔 Заявка: <code>{request_id}</code>\n"
                f"🧾 Invoice ID: <code>{invoice_id}</code>\n"
                f"👤 Telegram ID: <code>{user_id}</code>\n"
                f"💵 Сумма: <b>{format_rub(amount_kopecks)}</b>",
            )
        except Exception:
            pass


async def check_crypto_topup_request(request_id: int, *, notify_user: bool = True) -> bool:
    request = await db.get_topup_request(request_id)
    if not request:
        return False

    if request.status != "pending":
        return True

    pending = await db.list_pending_crypto_topups()
    invoice_id = None
    for item in pending:
        if int(item["id"]) == int(request_id):
            invoice_id = str(item.get("crypto_invoice_id") or "")
            break

    if not invoice_id:
        return False

    crypto = CryptoPayClient(config.crypto_pay_token, config.crypto_pay_api_base)
    try:
        invoice = await crypto.get_invoice(int(invoice_id))
    finally:
        await crypto.close()

    if not invoice or str(invoice.get("status") or "").lower() != "paid":
        return False

    await db.add_bot_balance(request.user_id, request.amount_kopecks)
    await db.set_topup_status(request.id, "approved")

    if notify_user:
        try:
            await bot.send_message(
                request.user_id,
                "✅ <b>Оплата криптой получена</b>\n\n"
                f"🆔 Заявка: <code>{request.id}</code>\n"
                f"🧾 Invoice ID: <code>{invoice_id}</code>\n"
                f"💵 Зачислено: <b>{format_rub(request.amount_kopecks)}</b>",
            )
        except Exception:
            pass

    if config.admin_id:
        try:
            await bot.send_message(
                config.admin_id,
                "✅ <b>Crypto Bot оплата зачислена</b>\n\n"
                f"🆔 Заявка: <code>{request.id}</code>\n"
                f"🧾 Invoice ID: <code>{invoice_id}</code>\n"
                f"👤 Telegram ID: <code>{request.user_id}</code>\n"
                f"💵 Сумма: <b>{format_rub(request.amount_kopecks)}</b>",
            )
        except Exception:
            pass

    return True


async def crypto_pay_checker_loop() -> None:
    await asyncio.sleep(25)
    while True:
        if config.crypto_pay_token:
            try:
                pending = await db.list_pending_crypto_topups()
                for item in pending:
                    try:
                        await check_crypto_topup_request(int(item["id"]), notify_user=True)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
            except Exception:
                pass
        await asyncio.sleep(60)


@dp.callback_query(F.data == "topup:menu")
async def topup_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpBalance.waiting_amount)
    await callback.message.answer(
        "💎 <b>Пополнение через Crypto Bot</b>\n\n"
        "Отправь сумму в рублях. После этого бот создаст счёт на оплату криптой.\n"
        "Пример: <code>100</code>",
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




@dp.callback_query(F.data.startswith("topup:check_crypto:"))
async def topup_check_crypto(callback: CallbackQuery) -> None:
    request_id = int(callback.data.split(":")[-1])
    request = await db.get_topup_request(request_id)
    if not request or request.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if request.status != "pending":
        await callback.message.answer("✅ Эта заявка уже обработана.")
        await callback.answer()
        return

    try:
        paid = await check_crypto_topup_request(request_id, notify_user=False)
    except Exception as error:
        await callback.message.answer(
            "❌ <b>Не удалось проверить оплату.</b>\n\n"
            f"<code>{escape(str(error))}</code>"
        )
        await callback.answer()
        return

    if paid:
        new_balance = await db.get_bot_balance(callback.from_user.id)
        await callback.message.answer(
            "✅ <b>Оплата найдена, баланс зачислен.</b>\n\n"
            f"💰 Баланс бота: <b>{format_rub(new_balance)}</b>"
        )
    else:
        await callback.answer("Платёж пока не найден", show_alert=True)


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


@dp.message(F.text == "🏆 Топ продавцов")
async def removed_top_sellers(message: Message) -> None:
    await message.answer("Раздел «Топ продавцов» удалён.", reply_markup=main_keyboard)


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




@dp.message(F.text == "/profile_debug")
async def profile_debug(message: Message) -> None:
    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("Нет подключённых аккаунтов.")
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        chats_data = await client.get_chats()
        orders_data = await client.get_orders()
        notify_counts, notify_fallback = get_notification_fallback_from_chats_data(chats_data)
        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        orders = page_props.get("orders") or []
        user = page_props.get("user") or extract_user(chats_data) or {}
        username = user.get("username") or account.username or "Starvell"
        recent_sales = get_recent_sales_from_orders(username, orders_data, user.get("id"))
        await message.answer(
            "🧪 <b>Диагностика профиля</b>\n\n"
            f"Версия: <code>{PROFILE_FIX_VERSION}</code>\n"
            f"Аккаунт: <b>{escape(username)}</b>\n"
            f"Заказов в orders.json: <b>{len(orders)}</b>\n"
            f"Продаж из orders.json: <b>{len(recent_sales)}</b>\n"
            f"Продаж из уведомлений ORDER_COMPLETED: <b>{notify_fallback['completed']}</b>\n"
            f"Отзывов из уведомлений REVIEW_CREATED: <b>{notify_fallback['review']}</b>\n"
            f"Уведомления: <code>{escape(str(notify_counts))}</code>"
        )
    except Exception as error:
        await message.answer(f"❌ Ошибка диагностики профиля: <code>{type(error).__name__}: {escape(str(error))}</code>")
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
        chats_data = await client.get_chats()
        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        user = page_props.get("user") or extract_user(chats_data) or {}
        username = user.get("username") or account.username or "Starvell"

        await message.answer(format_sales_stats(username, orders_data, chats_data))
    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка статистики продаж: <code>{error}</code>")
    finally:
        await client.close()


@dp.message(F.text == "📊 Статистика")
async def stats(message: Message) -> None:
    await send_compact_statistics(message, message.from_user.id, "7")


@dp.callback_query(F.data.startswith("stats_period:"))
async def stats_period_callback(callback: CallbackQuery) -> None:
    period = callback.data.split(":", 1)[1]

    if period == "back":
        await callback.message.answer(
            "Используй главное меню 👇",
            reply_markup=main_keyboard,
        )
        await callback.answer()
        return

    if period not in {"today", "7", "30", "all"}:
        await callback.answer("Неизвестный период", show_alert=True)
        return

    accounts = await db.list_user_accounts(callback.from_user.id)
    if not accounts:
        await callback.message.answer(
            "😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты."
        )
        await callback.answer()
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        orders_data = await client.get_orders()
        chats_data = await client.get_chats()
        page_props = orders_data.get("pageProps", {}) if isinstance(orders_data, dict) else {}
        user = page_props.get("user") or extract_user(chats_data) or {}
        username = user.get("username") or account.username or "Starvell"

        await clean_callback_answer(
            callback,
            format_compact_profit_stats(
                username,
                orders_data,
                chats_data,
                period=period,
            ),
            reply_markup=statistics_period_keyboard(),
        )
    except StarvellApiError as error:
        await callback.message.answer(f"❌ Ошибка статистики: <code>{error}</code>")
        await callback.answer()




@dp.callback_query(F.data == "profile:back")
async def profile_back(callback: CallbackQuery) -> None:
    text = await build_profile_main_text(callback.from_user)
    always_online = await db.get_always_online_enabled(callback.from_user.id)
    await clean_callback_answer(callback, text, reply_markup=profile_menu_keyboard(always_online))


@dp.callback_query(F.data == "profile:notifications")
async def profile_notifications(callback: CallbackQuery) -> None:
    accounts = await db.list_user_accounts(callback.from_user.id)
    if not accounts:
        await clean_callback_answer(
            callback,
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
    await clean_callback_answer(callback, text, reply_markup=profile_back_keyboard())


@dp.callback_query(F.data == "profile:chats_orders")
async def profile_chats_orders(callback: CallbackQuery) -> None:
    await callback.answer("Открываю...")
    await send_profile_chats_orders_section(callback.message, callback.from_user.id)


@dp.callback_query(F.data == "profile:clients")
async def profile_clients(callback: CallbackQuery) -> None:
    await callback.answer("Открываю...")
    await send_profile_clients_section(callback.message, callback.from_user.id)


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
    text = await build_account_settings_text(callback.from_user.id)
    await clean_callback_answer(callback, text, reply_markup=account_settings_menu_keyboard())


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

    if key == "auto_raise_lots":
        account = await get_primary_account(callback.from_user.id)
        await clean_callback_answer(
            callback,
            await build_auto_raise_text(callback.from_user.id, account.id if account else None),
            reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")),
        )
        return

    enabled = await db.toggle_bool_account_setting(callback.from_user.id, key)
    title = ACCOUNT_SETTING_TOGGLES[key]
    await clean_callback_answer(
        callback,
        f"{title}\n\nСтатус: {'✅ включено' if enabled else '❌ выключено'}",
        reply_markup=account_settings_back_keyboard(),
        alert_text="Настройка обновлена",
    )




@dp.callback_query(F.data == "autoraise:toggle")
async def auto_raise_toggle(callback: CallbackQuery) -> None:
    enabled = await db.toggle_bool_account_setting(callback.from_user.id, "auto_raise_lots")
    account = await get_primary_account(callback.from_user.id)
    await clean_callback_answer(
        callback,
        await build_auto_raise_text(callback.from_user.id, account.id if account else None),
        reply_markup=auto_raise_settings_keyboard(enabled),
        alert_text=("Автоподнятие включено" if enabled else "Автоподнятие выключено"),
    )


@dp.callback_query(F.data == "autoraise:set_game")
async def auto_raise_set_game(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoRaiseSettings.waiting_game_id)
    await callback.message.answer(
        "🎮 Отправь <b>gameId</b> одним числом.\n\n"
        "Для твоего запроса из Network сейчас подходит: <code>31</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AutoRaiseSettings.waiting_game_id)
async def auto_raise_save_game(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit():
        await message.answer("❌ Нужно отправить число. Пример: <code>31</code>")
        return
    await db.set_account_setting(message.from_user.id, "auto_raise_game_id", value)
    await state.clear()
    account = await get_primary_account(message.from_user.id)
    await message.answer(
        await build_auto_raise_text(message.from_user.id, account.id if account else None),
        reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(message.from_user.id, "auto_raise_lots")),
    )


@dp.callback_query(F.data == "autoraise:set_categories")
async def auto_raise_set_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoRaiseSettings.waiting_category_ids)
    await callback.message.answer(
        "📂 Отправь <b>categoryIds</b> через запятую.\n\n"
        "Для твоего запроса из Network сейчас подходит: <code>208</code>\n"
        "Если категорий несколько: <code>208,209</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AutoRaiseSettings.waiting_category_ids)
async def auto_raise_save_categories(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    try:
        ids = parse_int_list(value)
    except Exception:
        ids = []
    if not ids:
        await message.answer("❌ Нужно отправить одно или несколько чисел. Пример: <code>208</code>")
        return
    await db.set_account_setting(message.from_user.id, "auto_raise_category_ids", ",".join(map(str, ids)))
    await state.clear()
    account = await get_primary_account(message.from_user.id)
    await message.answer(
        await build_auto_raise_text(message.from_user.id, account.id if account else None),
        reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(message.from_user.id, "auto_raise_lots")),
    )




@dp.callback_query(F.data == "autoraise:scan_products")
async def auto_raise_scan_products(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Ищу активные товары...")
    items = await scan_user_products(callback.from_user.id)
    await state.update_data(auto_raise_scan_items=items)

    if not items:
        await callback.message.answer(
            "❌ Не удалось найти активные товары продавца.",
            reply_markup=auto_raise_settings_keyboard(
                await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")
            ),
        )
        return

    text = "📋 <b>Выбери товары для автоподнятия</b>\\n\\n"
    for idx, item in enumerate(items[:20], start=1):
        text += (
            f"{idx}. <b>{escape(str(item.get('title') or 'Товар'))}</b>\\n"
            f"   offerPublicId: <code>{escape(str(item.get('offerPublicId') or '—'))}</code>\\n"
            f"   игра: <code>{escape(str(item.get('gameName') or item.get('gameSlug') or item.get('gameId') or '—'))}</code>\\n"
            f"   категория: <code>{escape(str(item.get('categoryName') or item.get('categoryId') or '—'))}</code>\\n\\n"
        )

    text += (
        "Нажимай на нужные товары по одному. "
        "Если несколько товаров находятся в одной категории, Starvell поднимет всю эту категорию."
    )
    await callback.message.answer(text, reply_markup=auto_raise_products_keyboard(items))




@dp.callback_query(F.data == "autoraise:select_all_products")
async def auto_raise_select_all_products(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    items = data.get("auto_raise_scan_items") or []

    if not items:
        await callback.answer("Список товаров пуст", show_alert=True)
        return

    selected = []
    seen = set()

    for item in items:
        offer_id = str(item.get("offerPublicId") or "").strip()
        game_id = str(item.get("gameId") or "").strip()
        category_id = str(item.get("categoryId") or "").strip()

        if not game_id or not category_id:
            continue

        key = offer_id or f"{game_id}:{category_id}"
        if key in seen:
            continue
        seen.add(key)

        selected.append({
            "offerPublicId": offer_id,
            "gameId": game_id,
            "categoryId": category_id,
            "title": str(item.get("title") or "Товар"),
            "gameName": str(item.get("gameName") or item.get("gameSlug") or ""),
            "categoryName": str(item.get("categoryName") or ""),
        })

    if not selected:
        await callback.answer("Не удалось выбрать товары", show_alert=True)
        return

    await save_auto_raise_selected_products(callback.from_user.id, selected)

    account = await get_primary_account(callback.from_user.id)
    await callback.message.answer(
        f"✅ Выбраны все найденные товары: <b>{len(selected)}</b>.\n\n"
        + await build_auto_raise_text(
            callback.from_user.id,
            account.id if account else None,
        ),
        reply_markup=auto_raise_settings_keyboard(
            await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")
        ),
    )
    await callback.answer("Все товары выбраны")


@dp.callback_query(F.data.startswith("autoraise:add_product:"))
async def auto_raise_add_product(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Неверный товар", show_alert=True)
        return

    data = await state.get_data()
    items = data.get("auto_raise_scan_items") or []
    if idx < 0 or idx >= len(items):
        await callback.answer("Товар не найден", show_alert=True)
        return

    item = items[idx]
    selected = await get_auto_raise_selected_products(callback.from_user.id)

    offer_id = str(item.get("offerPublicId") or "").strip()
    game_id = str(item.get("gameId") or "").strip()
    category_id = str(item.get("categoryId") or "").strip()
    key = offer_id or f"{game_id}:{category_id}"

    existing_keys = {
        str(x.get("offerPublicId") or "").strip()
        or f"{x.get('gameId')}:{x.get('categoryId')}"
        for x in selected
    }

    if key in existing_keys:
        await callback.answer("Этот товар уже выбран", show_alert=True)
        return

    selected.append({
        "offerPublicId": offer_id,
        "gameId": game_id,
        "categoryId": category_id,
        "title": str(item.get("title") or "Товар"),
        "gameName": str(item.get("gameName") or item.get("gameSlug") or ""),
        "categoryName": str(item.get("categoryName") or ""),
    })
    await save_auto_raise_selected_products(callback.from_user.id, selected)

    account = await get_primary_account(callback.from_user.id)
    await callback.message.answer(
        "✅ Товар добавлен в автоподнятие.\\n\\n"
        + await build_auto_raise_text(
            callback.from_user.id,
            account.id if account else None,
        ),
        reply_markup=auto_raise_settings_keyboard(
            await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")
        ),
    )
    await callback.answer()


@dp.callback_query(F.data == "autoraise:clear_products")
async def auto_raise_clear_products(callback: CallbackQuery) -> None:
    await save_auto_raise_selected_products(callback.from_user.id, [])
    account = await get_primary_account(callback.from_user.id)
    await clean_callback_answer(
        callback,
        "🧹 Выбор товаров для автоподнятия очищен.\\n\\n"
        + await build_auto_raise_text(
            callback.from_user.id,
            account.id if account else None,
        ),
        reply_markup=auto_raise_settings_keyboard(
            await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")
        ),
        alert_text="Очищено",
    )


@dp.callback_query(F.data == "autoraise:interval_menu")
async def auto_raise_interval_menu(callback: CallbackQuery) -> None:
    await clean_callback_answer(
        callback,
        "⏱ <b>Выбери интервал автоподнятия</b>\n\n"
        "Starvell разрешает поднимать лоты только раз в 4 часа, поэтому минимальный интервал — 4 часа.",
        reply_markup=auto_raise_interval_keyboard(),
    )


@dp.callback_query(F.data.startswith("autoraise:interval:"))
async def auto_raise_interval_set(callback: CallbackQuery) -> None:
    hours = callback.data.split(":", 2)[2]
    if hours not in {"4", "6", "8", "12", "24"}:
        await callback.answer("Неверный интервал", show_alert=True)
        return
    await db.set_account_setting(callback.from_user.id, "auto_raise_interval_hours", hours)
    account = await get_primary_account(callback.from_user.id)
    await clean_callback_answer(
        callback,
        await build_auto_raise_text(callback.from_user.id, account.id if account else None),
        reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")),
        alert_text="Интервал сохранён",
    )


@dp.callback_query(F.data == "autoraise:run_now")
async def auto_raise_run_now(callback: CallbackQuery) -> None:
    account = await get_primary_account(callback.from_user.id)
    if not account:
        await callback.message.answer("😴 Сначала добавь Starvell аккаунт в разделе 🔐 Мои аккаунты.")
        await callback.answer()
        return
    await callback.message.answer("🚀 Ищу активные товары через твой профиль Starvell и поднимаю их...")
    try:
        result = await run_auto_raise_for_account(account)
        await callback.message.answer(
            "✅ <b>Автоподнятие выполнено.</b>\n\n"
            f"<code>{escape(str(result)[:900])}</code>",
            reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")),
        )
    except Exception as error:
        cooldown_seconds = extract_bump_cooldown_seconds(error)
        if cooldown_seconds is not None:
            friendly_text = format_bump_cooldown_message(cooldown_seconds)
            await db.save_auto_raise_state(account.id, friendly_text.replace("<b>", "").replace("</b>", ""))
            await callback.message.answer(
                friendly_text,
                reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")),
            )
        else:
            await db.save_auto_raise_state(account.id, f"ERROR: {type(error).__name__}: {error}")
            await callback.message.answer(
                "❌ <b>Не удалось поднять лоты.</b>\n\n"
                f"<code>{type(error).__name__}: {escape(str(error))}</code>\n\n"
                "Проверь, что в подключённом профиле есть активные публичные лоты.",
                reply_markup=auto_raise_settings_keyboard(await db.get_bool_account_setting(callback.from_user.id, "auto_raise_lots")),
            )
    await callback.answer()




@dp.callback_query(F.data == "prodsel:menu")
async def product_select_menu(callback: CallbackQuery) -> None:
    await clean_callback_answer(
        callback,
        await build_product_select_text(callback.from_user.id),
        reply_markup=product_select_menu_keyboard(),
    )


@dp.callback_query(F.data == "prodsel:scan")
async def product_select_scan(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Ищу товары...")
    items = await scan_user_products(callback.from_user.id)
    await state.update_data(product_scan_items=items)

    if not items:
        await callback.message.answer(
            "❌ Не удалось найти товары автоматически.\n\n"
            "Можно ввести вручную:\n"
            "• <b>offerPublicId</b> для автоответчика;\n"
            "• <b>categoryIds</b> для автоответчика.\n\n"
            "Например, для Steam ключей categoryId: <code>208</code>.",
            reply_markup=product_select_menu_keyboard(),
        )
        return

    text = "📋 <b>Найденные товары/категории</b>\n\n"
    for idx, item in enumerate(items[:12], start=1):
        text += (
            f"{idx}. <b>{escape(str(item.get('title') or 'Товар'))}</b>\n"            f"   categoryId: <code>{escape(str(item.get('categoryId') or '—'))}</code>\n"
            f"   gameId: <code>{escape(str(item.get('gameId') or '—'))}</code>\n\n"
        )
    text += "Нажми на товар ниже, чтобы добавить его в фильтр автоответчика."
    await callback.message.answer(text, reply_markup=product_select_found_keyboard(items))


@dp.callback_query(F.data.startswith("prodsel:add_offer:"))
async def product_select_add_offer(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[-1])
    data = await state.get_data()
    items = data.get("product_scan_items") or []
    if idx >= len(items):
        await callback.answer("Товар не найден", show_alert=True)
        return
    item = items[idx]
    offer_id = str(item.get("offerPublicId") or "").strip()
    category_id = str(item.get("categoryId") or "").strip()
    if offer_id:
        current = csv_set_from_text(await db.get_account_setting(callback.from_user.id, "auto_responder_offer_ids", ""))
        current.add(offer_id)
        await db.set_account_setting(callback.from_user.id, "auto_responder_offer_ids", ",".join(sorted(current)))
    if category_id:
        current_cat = csv_set_from_text(await db.get_account_setting(callback.from_user.id, "auto_responder_category_ids", ""))
        current_cat.add(category_id)
        await db.set_account_setting(callback.from_user.id, "auto_responder_category_ids", ",".join(sorted(current_cat)))
    await callback.message.answer(
        "✅ Товар добавлен в фильтр автоответчика.\n\n"
        + await build_product_select_text(callback.from_user.id),
        reply_markup=product_select_menu_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("prodsel:add_cat:"))
async def product_select_add_cat(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[-1])
    data = await state.get_data()
    items = data.get("product_scan_items") or []
    if idx >= len(items):
        await callback.answer("Категория не найдена", show_alert=True)
        return
    category_id = str(items[idx].get("categoryId") or "").strip()
    if not category_id:
        await callback.answer("У этого товара нет categoryId", show_alert=True)
        return
    current_cat = csv_set_from_text(await db.get_account_setting(callback.from_user.id, "auto_responder_category_ids", ""))
    current_cat.add(category_id)
    await db.set_account_setting(callback.from_user.id, "auto_responder_category_ids", ",".join(sorted(current_cat)))
    await callback.message.answer(
        "✅ categoryId добавлен в фильтр автоответчика.\n\n"
        + await build_product_select_text(callback.from_user.id),
        reply_markup=product_select_menu_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "prodsel:set_offers")
async def product_select_set_offers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductSelectSettings.waiting_offer_ids)
    await callback.message.answer(
        "✏️ Отправь offerPublicId товаров через запятую.\n\n"
        "Пример:\n"
        "<code>bc1f26c2-b56d-405b-9d72-6018f518ac26, 0b7d...</code>\n\n"
        "Автоответчик будет отвечать только по этим товарам.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(ProductSelectSettings.waiting_offer_ids)
async def product_select_save_offers(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    ids = [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
    await db.set_account_setting(message.from_user.id, "auto_responder_offer_ids", ",".join(ids))
    await state.clear()
    await message.answer(
        "✅ Список товаров для автоответчика сохранён.\n\n"
        + await build_product_select_text(message.from_user.id),
        reply_markup=product_select_menu_keyboard(),
    )


@dp.callback_query(F.data == "prodsel:set_categories")
async def product_select_set_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductSelectSettings.waiting_category_ids)
    await callback.message.answer(
        "📂 Отправь categoryIds через запятую.\n\n"
        "Пример для твоего bump-запроса: <code>208</code>\n\n"
        "Эти categoryIds будут использоваться как фильтр автоответчика.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(ProductSelectSettings.waiting_category_ids)
async def product_select_save_categories(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    try:
        ids = parse_int_list(value)
    except Exception:
        ids = []
    if not ids:
        await message.answer("❌ Нужно отправить одно или несколько чисел. Пример: <code>208</code>")
        return
    await db.set_account_setting(message.from_user.id, "auto_responder_category_ids", ",".join(map(str, ids)))
    await state.clear()
    await message.answer(
        "✅ categoryIds для автоответчика сохранены.\n\n"
        + await build_product_select_text(message.from_user.id),
        reply_markup=product_select_menu_keyboard(),
    )


@dp.callback_query(F.data == "prodsel:clear")
async def product_select_clear(callback: CallbackQuery) -> None:
    await db.set_account_setting(callback.from_user.id, "auto_responder_offer_ids", "")
    await db.set_account_setting(callback.from_user.id, "auto_responder_category_ids", "")
    await clean_callback_answer(
        callback,
        "🧹 Фильтр очищен.\n\nТеперь автоответчик снова работает для всех товаров.",
        reply_markup=product_select_menu_keyboard(),
        alert_text="Очищено",
    )






@dp.callback_query(F.data == "autodelivery:menu")
async def auto_delivery_menu(callback: CallbackQuery) -> None:
    enabled = await db.get_bool_account_setting(callback.from_user.id, "auto_delivery")
    await clean_callback_answer(
        callback,
        await build_auto_delivery_text(callback.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(enabled),
    )


@dp.callback_query(F.data == "autodelivery:toggle")
async def auto_delivery_toggle(callback: CallbackQuery) -> None:
    enabled = await db.toggle_bool_account_setting(callback.from_user.id, "auto_delivery")
    await clean_callback_answer(
        callback,
        await build_auto_delivery_text(callback.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(enabled),
        alert_text=("Автовыдача включена" if enabled else "Автовыдача выключена"),
    )




def build_auto_delivery_products_page_text(
    items: list[dict],
    page: int = 0,
    page_size: int = 8,
) -> str:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start_index = page * page_size
    end_index = min(total, start_index + page_size)

    text = (
        "📋 <b>Выбери товар для автовыдачи</b>\n\n"
        f"Найдено товаров: <b>{total}</b>\n"
        f"Страница: <b>{page + 1}/{total_pages}</b>\n\n"
    )

    for idx in range(start_index, end_index):
        item = items[idx]
        text += (
            f"{idx + 1}. <b>{escape(str(item.get('title') or 'Товар'))}</b>\n"            f"   игра: <code>{escape(str(item.get('gameName') or item.get('gameSlug') or item.get('gameId') or '—'))}</code>\n"
            f"   категория: <code>{escape(str(item.get('categoryName') or item.get('categoryId') or '—'))}</code>\n"
            f"   модерация: <code>{escape(str(item.get('moderationStatus') or '—'))}</code>\n\n"
        )

    text += (
        "Можно выбирать товары по одному, листать страницы стрелками "
        "или нажать «✅ Выбрать все»."
    )
    return text


@dp.callback_query(F.data == "autodelivery:scan")
async def auto_delivery_scan(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Ищу все активные товары...")
    items = await scan_user_products(callback.from_user.id)

    # Stable ordering makes pagination predictable.
    items = sorted(
        items,
        key=lambda item: (
            str(item.get("gameName") or item.get("gameSlug") or item.get("gameId") or "").lower(),
            str(item.get("categoryName") or item.get("categoryId") or "").lower(),
            str(item.get("title") or item.get("offerPublicId") or "").lower(),
        ),
    )

    await state.update_data(
        auto_delivery_scan_items=items,
        auto_delivery_scan_page=0,
    )

    if not items:
        await callback.message.answer(
            "❌ Не удалось найти активные товары продавца.\n\n"
            "Бот проверил подключённый профиль и категории найденных игр, "
            "но Starvell не вернул список активных лотов.",
            reply_markup=auto_delivery_menu_keyboard(
                await db.get_bool_account_setting(callback.from_user.id, "auto_delivery")
            ),
        )
        return

    await callback.message.answer(
        build_auto_delivery_products_page_text(items, 0),
        reply_markup=auto_delivery_products_keyboard(items, 0),
    )


@dp.callback_query(F.data.startswith("autodelivery:page:"))
async def auto_delivery_products_page(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Неверная страница", show_alert=True)
        return

    data = await state.get_data()
    items = data.get("auto_delivery_scan_items") or []
    if not items:
        await callback.answer("Сначала нажми «Выбрать товары»", show_alert=True)
        return

    await state.update_data(auto_delivery_scan_page=page)
    await clean_callback_answer(
        callback,
        build_auto_delivery_products_page_text(items, page),
        reply_markup=auto_delivery_products_keyboard(items, page),
    )


@dp.callback_query(F.data == "autodelivery:page_info")
async def auto_delivery_page_info(callback: CallbackQuery) -> None:
    await callback.answer("Используй стрелки для перехода между страницами.")


@dp.callback_query(F.data == "autodelivery:select_all")
async def auto_delivery_select_all(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    items = data.get("auto_delivery_scan_items") or []

    if not items:
        await callback.answer("Список товаров пуст", show_alert=True)
        return

    offer_ids = {
        str(item.get("offerPublicId") or "").strip()
        for item in items
        if str(item.get("offerPublicId") or "").strip()
    }
    category_ids = {
        str(item.get("categoryId") or "").strip()
        for item in items
        if str(item.get("categoryId") or "").strip()
    }

    await db.set_account_setting(
        callback.from_user.id,
        "auto_delivery_offer_ids",
        ",".join(sorted(offer_ids)),
    )
    await db.set_account_setting(
        callback.from_user.id,
        "auto_delivery_category_ids",
        ",".join(sorted(category_ids)),
    )

    await callback.message.answer(
        f"✅ Для автовыдачи выбраны все найденные товары: <b>{len(items)}</b>.\n"
        "Для них будет использоваться общее сообщение, кроме товаров с уже сохранённым персональным текстом.\n\n"
        + await build_auto_delivery_text(callback.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(
            await db.get_bool_account_setting(callback.from_user.id, "auto_delivery")
        ),
    )
    await callback.answer("Все товары выбраны")




@dp.callback_query(F.data.startswith("autodelivery:add:"))
async def auto_delivery_add_product(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Неверный товар", show_alert=True)
        return

    data = await state.get_data()
    items = data.get("auto_delivery_scan_items") or []
    if idx < 0 or idx >= len(items):
        await callback.answer("Товар не найден", show_alert=True)
        return

    item = items[idx]
    offer_id = str(item.get("offerPublicId") or "").strip()
    category_id = str(item.get("categoryId") or "").strip()
    title = str(item.get("title") or "Товар").strip()

    await state.update_data(
        auto_delivery_personal_offer_id=offer_id,
        auto_delivery_personal_category_id=category_id,
        auto_delivery_personal_title=title,
        auto_delivery_scan_page=int(data.get("auto_delivery_scan_page") or 0),
    )
    await state.set_state(AutoDeliverySettings.waiting_product_message)

    await callback.message.answer(
        "📝 <b>Персональное сообщение для товара</b>\n\n"
        f"Товар: <b>{escape(title)}</b>\n"
        "\n"
        "Отправь текст, который бот должен посылать покупателю именно для этого товара.\n\n"
        "Доступные переменные:\n"
        "<code>{username}</code> — покупатель\n"
        "<code>{seller}</code> — продавец\n"
        "<code>{order_id}</code> — номер заказа\n\n"
        "Чтобы использовать общее сообщение автовыдачи, отправь: <code>/default</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AutoDeliverySettings.waiting_product_message)
async def auto_delivery_save_personal_product_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    offer_id = str(data.get("auto_delivery_personal_offer_id") or "").strip()
    category_id = str(data.get("auto_delivery_personal_category_id") or "").strip()
    title = str(data.get("auto_delivery_personal_title") or "Товар").strip()
    text = (message.text or "").strip()

    if not offer_id and not category_id:
        await state.clear()
        await message.answer("❌ Не удалось определить выбранный товар.")
        return

    if not text:
        await message.answer("❌ Отправь непустое сообщение.")
        return

    # Save selection.
    if offer_id:
        current = csv_set_from_text(
            await db.get_account_setting(message.from_user.id, "auto_delivery_offer_ids", "")
        )
        current.add(offer_id)
        await db.set_account_setting(
            message.from_user.id,
            "auto_delivery_offer_ids",
            ",".join(sorted(current)),
        )

    if category_id:
        current_categories = csv_set_from_text(
            await db.get_account_setting(message.from_user.id, "auto_delivery_category_ids", "")
        )
        current_categories.add(category_id)
        await db.set_account_setting(
            message.from_user.id,
            "auto_delivery_category_ids",
            ",".join(sorted(current_categories)),
        )

    use_default = text.lower() == "/default"
    if offer_id:
        await db.set_account_setting(
            message.from_user.id,
            f"auto_delivery_product_message:{offer_id}",
            "" if use_default else text,
        )
    elif category_id:
        await db.set_account_setting(
            message.from_user.id,
            f"auto_delivery_category_message:{category_id}",
            "" if use_default else text,
        )

    page = int(data.get("auto_delivery_scan_page") or 0)
    items = data.get("auto_delivery_scan_items") or []
    await state.clear()

    result_text = (
        f"✅ Товар <b>{escape(title)}</b> добавлен в автовыдачу.\n"
        + (
            "Для него будет использоваться общее сообщение."
            if use_default
            else "Персональное сообщение сохранено."
        )
    )

    if items:
        await message.answer(
            result_text + "\n\nМожно выбрать ещё товары.",
            reply_markup=auto_delivery_products_keyboard(items, page),
        )
    else:
        await message.answer(
            result_text + "\n\n" + await build_auto_delivery_text(message.from_user.id),
            reply_markup=auto_delivery_menu_keyboard(
                await db.get_bool_account_setting(message.from_user.id, "auto_delivery")
            ),
        )




@dp.callback_query(F.data == "autodelivery:set_offers")
async def auto_delivery_set_offers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoDeliverySettings.waiting_offer_ids)
    await callback.message.answer(
        "✏️ Отправь offerPublicId товаров через запятую.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AutoDeliverySettings.waiting_offer_ids)
async def auto_delivery_save_offers(message: Message, state: FSMContext) -> None:
    values = [x.strip() for x in (message.text or "").replace(";", ",").split(",") if x.strip()]
    await db.set_account_setting(message.from_user.id, "auto_delivery_offer_ids", ",".join(values))
    await state.clear()
    await message.answer(
        "✅ offerPublicId сохранены.\n\n" + await build_auto_delivery_text(message.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(
            await db.get_bool_account_setting(message.from_user.id, "auto_delivery")
        ),
    )


@dp.callback_query(F.data == "autodelivery:set_categories")
async def auto_delivery_set_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoDeliverySettings.waiting_category_ids)
    await callback.message.answer(
        "📂 Отправь categoryIds через запятую. Например: <code>208,214</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@dp.message(AutoDeliverySettings.waiting_category_ids)
async def auto_delivery_save_categories(message: Message, state: FSMContext) -> None:
    try:
        values = parse_int_list((message.text or "").strip())
    except Exception:
        values = []
    if not values:
        await message.answer("❌ Отправь числа через запятую. Например: <code>208,214</code>")
        return
    await db.set_account_setting(
        message.from_user.id,
        "auto_delivery_category_ids",
        ",".join(map(str, values)),
    )
    await state.clear()
    await message.answer(
        "✅ categoryIds сохранены.\n\n" + await build_auto_delivery_text(message.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(
            await db.get_bool_account_setting(message.from_user.id, "auto_delivery")
        ),
    )


@dp.callback_query(F.data == "autodelivery:clear_products")
async def auto_delivery_clear_products(callback: CallbackQuery) -> None:
    await db.set_account_setting(callback.from_user.id, "auto_delivery_offer_ids", "")
    await db.set_account_setting(callback.from_user.id, "auto_delivery_category_ids", "")
    await clean_callback_answer(
        callback,
        "🧹 Выбор товаров для автовыдачи очищен. Персональные тексты сохранены и снова применятся, если выбрать эти товары.\n\n"
        + await build_auto_delivery_text(callback.from_user.id),
        reply_markup=auto_delivery_menu_keyboard(
            await db.get_bool_account_setting(callback.from_user.id, "auto_delivery")
        ),
        alert_text="Очищено",
    )


@dp.callback_query(F.data.startswith("accset:text:"))
async def account_settings_text_view(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in ACCOUNT_SETTING_TEXTS:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return

    if key == "confirm_reminder":
        await clean_callback_answer(
            callback,
            await build_confirm_reminder_text(callback.from_user.id),
            reply_markup=confirm_reminder_menu_keyboard(),
        )
        return

    value = await db.get_account_setting(callback.from_user.id, key)
    title = ACCOUNT_SETTING_TEXTS[key]
    text_value = escape(value) if value else "текст не задан"
    await clean_callback_answer(
        callback,
        f"{title}\n\n<b>Текущий текст:</b>\n{text_value}",
        reply_markup=account_setting_text_keyboard(key),
    )



@dp.callback_query(F.data == "reminder:toggle")
async def confirm_reminder_toggle(callback: CallbackQuery) -> None:
    enabled = await db.toggle_bool_account_setting(callback.from_user.id, "confirm_reminder_enabled")
    await clean_callback_answer(
        callback,
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
        alert_text=("Включено" if enabled else "Выключено"),
    )


@dp.callback_query(F.data == "reminder:time_menu")
async def confirm_reminder_time_menu(callback: CallbackQuery) -> None:
    await clean_callback_answer(
        callback,
        "🕐 <b>Выберите время отправки</b>\n\n"
        "Время указывается по времени сервера. Для Bhost обычно это UTC.\n"
        "Пример: <code>13:00</code>",
        reply_markup=confirm_reminder_time_keyboard(),
    )


@dp.callback_query(F.data.startswith("reminder:time:"))
async def confirm_reminder_time_set(callback: CallbackQuery) -> None:
    time_value = callback.data.split(":", 2)[2]
    await db.set_account_setting(callback.from_user.id, "confirm_reminder_time", time_value)
    await clean_callback_answer(
        callback,
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
        alert_text="Время сохранено",
    )


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
    await clean_callback_answer(
        callback,
        "📅 <b>Выберите дни отправки</b>\n\n"
        "Пример: <b>спустя день в 13:00</b> — бот будет отправлять повторный запрос не чаще одного раза в 2 дня.",
        reply_markup=confirm_reminder_period_keyboard(),
    )


@dp.callback_query(F.data.startswith("reminder:period:"))
async def confirm_reminder_period_set(callback: CallbackQuery) -> None:
    days = callback.data.split(":", 2)[2]
    if days not in {"1", "2", "3", "7"}:
        await callback.answer("Неверный период", show_alert=True)
        return
    await db.set_account_setting(callback.from_user.id, "confirm_reminder_period_days", days)
    await clean_callback_answer(
        callback,
        await build_confirm_reminder_text(callback.from_user.id),
        reply_markup=confirm_reminder_menu_keyboard(),
        alert_text="Дни сохранены",
    )


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
    await clean_callback_answer(
        callback,
        f"{ACCOUNT_SETTING_TEXTS[key]}\n\n🗑 Текст очищен.",
        reply_markup=account_settings_back_keyboard(),
        alert_text="Очищено",
    )


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






































@dp.message(F.text == "/autoraise_select_all_debug")
async def autoraise_select_all_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика кнопки «Выбрать все»</b>\n\n"
        f"Версия: <code>{AUTO_RAISE_SELECT_ALL_VERSION}</code>\n"
        "В списке товаров должна быть кнопка «✅ Выбрать все»."
    )


@dp.message(F.text == "/autoraise_products_debug")
async def autoraise_products_debug(message: Message) -> None:
    selected = await get_auto_raise_selected_products(message.from_user.id)
    groups = group_selected_products_for_bump(selected)

    lines = []
    for game_id, category_ids in sorted(groups.items()):
        lines.append(
            f"gameId <code>{game_id}</code> → categoryIds "
            f"<code>{','.join(map(str, sorted(category_ids)))}</code>"
        )

    await message.answer(
        "🧪 <b>Диагностика выбранного автоподнятия</b>\\n\\n"
        f"Версия: <code>{AUTO_RAISE_PRODUCT_SELECTION_VERSION}</code>\\n"
        f"Выбрано товаров: <b>{len(selected)}</b>\\n"
        f"Групп для bump: <b>{len(groups)}</b>\\n\\n"
        + ("\\n".join(lines) if lines else "Товары не выбраны.")
    )


@dp.message(F.text == "/stats_profit_debug")
async def stats_profit_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика прибыли</b>\n\n"
        f"Версия: <code>{STATISTICS_PROFIT_VERSION}</code>\n"
        "Строка поднятий объявлений удалена.\n"
        "Итоговая строка: прибыль с учётом комиссии Starvell."
    )


@dp.message(F.text == "/stats_style_debug")
async def stats_style_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика статистики</b>\n\n"
        f"Версия: <code>{STATISTICS_STYLE_VERSION}</code>\n"
        "Комиссия маркетплейса: скрыта\n"
        "Комиссия за вывод: 2,9%\n"
        "Строка поднятий объявлений: удалена\n"
        "Итог: прибыль с учётом комиссии Starvell"
    )


@dp.message(F.text == "/autodelivery_message_button_debug")
async def autodelivery_message_button_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика кнопки сообщения</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_MESSAGE_BUTTON_VERSION}</code>\n"
        "В меню автовыдачи должна быть кнопка «📝 Настроить сообщение»."
    )


@dp.message(F.text == "/active_products_debug")
async def active_products_debug(message: Message) -> None:
    await message.answer(
        "🔄 Проверяю аккаунт, профиль и страницы категорий Starvell..."
    )
    items = await scan_user_products(message.from_user.id)
    if not items:
        await message.answer(
            "🧪 <b>Диагностика активных товаров</b>\n\n"
            f"Версия: <code>{ALL_GAMES_ACTIVE_PRODUCTS_VERSION}</code>\n"
            "Активные товары не найдены даже через HTML-страницы категорий."
        )
        return

    text = (
        "🧪 <b>Диагностика активных товаров</b>\n\n"
        f"Версия: <code>{ALL_GAMES_ACTIVE_PRODUCTS_VERSION}</code>\n"
        f"Найдено: <b>{len(items)}</b>\n\n"
    )
    for index, item in enumerate(items[:15], start=1):
        text += (
            f"{index}. <b>{escape(str(item.get('title') or 'Товар'))}</b>\n"            f"categoryId: <code>{escape(str(item.get('categoryId') or '—'))}</code>\n\n"
        )
    await message.answer(text)


@dp.message(F.text == "/autodelivery_menu_debug")
async def autodelivery_menu_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика меню автовыдачи</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_MENU_CLEAN_VERSION}</code>\n"
        "Ручной ввод offerPublicId и categoryIds из меню удалён."
    )










@dp.message(F.text == "/autodelivery_personal_debug")
async def autodelivery_personal_debug(message: Message) -> None:
    offer_ids = sorted(csv_set_from_text(
        await db.get_account_setting(message.from_user.id, "auto_delivery_offer_ids", "")
    ))
    personal = 0
    for offer_id in offer_ids:
        value = await db.get_account_setting(
            message.from_user.id,
            f"auto_delivery_product_message:{offer_id}",
            "",
        )
        if value and value.strip():
            personal += 1

    await message.answer(
        "🧪 <b>Диагностика персональных сообщений</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_PERSONAL_MESSAGE_VERSION}</code>\n"
        f"Выбрано товаров: <b>{len(offer_ids)}</b>\n"
        f"Персональных сообщений: <b>{personal}</b>\n\n"
        "Для остальных выбранных товаров используется общее сообщение автовыдачи."
    )


@dp.message(F.text == "/autodelivery_products_debug")
async def autodelivery_products_debug(message: Message) -> None:
    items = await scan_user_products(message.from_user.id)
    await message.answer(
        "🧪 <b>Диагностика списка товаров автовыдачи</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_ALL_PRODUCTS_VERSION}</code>\n"
        f"Найдено товаров: <b>{len(items)}</b>\n"
        "В меню показываются все товары постранично по 8 штук."
    )


@dp.message(F.text == "/autoraise_profile_debug")
async def autoraise_profile_debug(message: Message) -> None:
    account = await get_primary_account(message.from_user.id)
    if not account:
        await message.answer("Сначала подключи Starvell аккаунт.")
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        username = str(account.username or "").strip()
        items = await client.collect_active_seller_offers(username)
    finally:
        await client.close()

    groups: dict[int, set[int]] = {}
    for item in items:
        try:
            game_id = int(item.get("gameId") or 0)
            category_id = int(item.get("categoryId") or 0)
        except Exception:
            continue
        if game_id > 0 and category_id > 0:
            groups.setdefault(game_id, set()).add(category_id)

    lines = [
        f"gameId <code>{game_id}</code> → categoryIds "
        f"<code>{','.join(map(str, sorted(category_ids)))}</code>"
        for game_id, category_ids in sorted(groups.items())
    ]

    await message.answer(
        "🧪 <b>Диагностика автоподнятия через профиль</b>\n\n"
        f"Версия: <code>{AUTO_RAISE_PROFILE_MODE_VERSION}</code>\n"
        f"Профиль: <b>{escape(str(account.username or '—'))}</b>\n"
        f"Активных лотов: <b>{len(items)}</b>\n"
        f"Игр: <b>{len(groups)}</b>\n"
        f"Категорий: <b>{sum(len(v) for v in groups.values())}</b>\n\n"
        + ("\n".join(lines) if lines else "Активные категории не найдены.")
    )


@dp.message(F.text == "/autodelivery_event_debug")
async def autodelivery_event_debug(message: Message) -> None:
    account = await get_primary_account(message.from_user.id)
    if not account:
        await message.answer("Сначала подключи Starvell аккаунт.")
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        data = await client.get_chats()
    finally:
        await client.close()

    chats = extract_chats(data)
    text = (
        "🧪 <b>Диагностика автовыдачи</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_EVENT_FIX_VERSION}</code>\n"
        f"Статус: <b>{'включена' if await db.get_bool_account_setting(message.from_user.id, 'auto_delivery') else 'выключена'}</b>\n"
        f"Сообщение: <b>{'настроено' if await db.get_account_setting(message.from_user.id, 'auto_delivery_message', '') else 'не настроено'}</b>\n"
        f"offerPublicId: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_delivery_offer_ids', '') or 'не выбраны'))}</code>\n"
        f"categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_delivery_category_ids', '') or 'не выбраны'))}</code>\n\n"
        "<b>Последние события:</b>\n"
    )

    for index, chat in enumerate(chats[:8], start=1):
        last = chat.get("lastMessage") or {}
        product = extract_chat_product_info(chat, last)
        text += (
            f"\n{index}. type=<code>{escape(str(last.get('type') or '—'))}</code>\n"
            f"event=<code>{escape(str(get_notification_type(last) or '—'))}</code>\n"
            f"покупка/оплата: <b>{'да' if is_purchase_paid_event(last) else 'нет'}</b>\n"
            f"offer=<code>{escape(str(product.get('offerPublicId') or '—'))}</code>, "
            f"category=<code>{escape(str(product.get('categoryId') or '—'))}</code>\n"
        )

    await message.answer(text[:4000])


@dp.message(F.text == "/autodelivery_debug")
async def autodelivery_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика автовыдачи</b>\n\n"
        f"Версия: <code>{AUTO_DELIVERY_MESSAGE_VERSION}</code>\n"
        f"Статус: <b>{'включена' if await db.get_bool_account_setting(message.from_user.id, 'auto_delivery') else 'выключена'}</b>\n"
        f"offerPublicId: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_delivery_offer_ids', '') or 'не выбраны'))}</code>\n"
        f"categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_delivery_category_ids', '') or 'не выбраны'))}</code>\n"
        f"Сообщение: <b>{'настроено' if await db.get_account_setting(message.from_user.id, 'auto_delivery_message', '') else 'не настроено'}</b>"
    )


@dp.message(F.text == "/top_autoreply_debug")
async def top_autoreply_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика топа и автоответчика</b>\n\n"
        f"Версия: <code>{TOP_AUTOREPLY_PRODUCTS_VERSION}</code>\n"
        f"Топ продавцов: <code>{TOP_FIX_VERSION}</code>\n"
        "Отзывы в топе берутся только из подтверждённого публичного профиля.\n\n"
        f"Автоответчик offerPublicId: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_responder_offer_ids', '') or 'все товары'))}</code>\n"
        f"Автоответчик categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_responder_category_ids', '') or 'не ограничено'))}</code>"
    )


@dp.message(F.text == "/cooldown_text_debug")
async def cooldown_text_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика cooldown-текста</b>\n\n"
        f"Версия: <code>{BUMP_COOLDOWN_TEXT_VERSION}</code>\n"
        "Пример ответа:\n\n"
        + format_bump_cooldown_message(117303)
    )


@dp.message(F.text == "/auto_bump_detect_debug")
async def auto_bump_detect_debug(message: Message) -> None:
    account = await get_primary_account(message.from_user.id)
    if not account:
        await message.answer(
            "🧪 <b>Диагностика автоподнятия</b>\n\n"
            f"Версия: <code>{AUTO_BUMP_DETECT_VERSION}</code>\n"
            "Starvell аккаунт ещё не подключён."
        )
        return

    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)
    try:
        groups = await client.collect_auto_bump_groups(username=account.username)
    finally:
        await client.close()

    if not groups:
        groups_text = "не найдено"
    else:
        groups_text = "\n".join(
            f"gameId <code>{game_id}</code> → categoryIds <code>{','.join(map(str, sorted(category_ids)))}</code>"
            for game_id, category_ids in sorted(groups.items())
        )

    await message.answer(
        "🧪 <b>Диагностика автоподнятия</b>\n\n"
        f"Версия: <code>{AUTO_BUMP_DETECT_VERSION}</code>\n"
        "Режим: автоматический поиск лотов + поднятие всех категорий найденной игры.\n\n"
        f"Найдено:\n{groups_text}"
    )


@dp.message(F.text == "/bump_defaults_debug")
async def bump_defaults_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика автоподнятия</b>\n\n"
        f"Версия: <code>{BUMP_DEFAULTS_VERSION}</code>\n"
        "Новый bump-запрос Starvell:\n"
        "gameId: <code>16</code>\n"
        "categoryIds: <code>208</code>\n\n"
        f"Сейчас сохранено:\n"
        f"gameId: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_raise_game_id', '16') or '16'))}</code>\n"
        f"categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_raise_category_ids', '208') or '208'))}</code>"
    )


@dp.message(F.text == "/final_fix_debug")
async def final_fix_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Финальная диагностика</b>\n\n"
        f"Версия: <code>{FINAL_MENU_REVIEWS_VERSION}</code>\n"
        "Кнопка поддержки удалена из main_keyboard. Старую кнопку Telegram очищает команда /start.\n"
        "Отзывы в топе теперь не берутся из карточек категорий, если профиль продавца не подтвердил число отзывов.",
        reply_markup=main_keyboard,
    )

@dp.message(F.text == "/autoraise_categories_debug")
async def autoraise_categories_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика автоподнятия</b>\n\n"
        f"Версия: <code>{AUTORAISE_CATEGORIES_ONLY_VERSION}</code>\n"
        "Автоподнятие теперь настраивается только через gameId и categoryIds.\n"
        f"Текущие categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_raise_category_ids', '208') or 'не заданы'))}</code>"
    )


@dp.message(F.text == "/no_support_debug")
async def no_support_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика меню</b>\n\n"
        f"Версия: <code>{NO_SUPPORT_VERSION}</code>\n"
        "Раздел поддержки удалён из главного меню."
    )


@dp.message(F.text == "/product_select_debug")
async def product_select_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика выбора товаров</b>\n\n"
        f"Версия: <code>{PRODUCT_SELECT_VERSION}</code>\n"
        f"Автоответчик offerPublicId: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_responder_offer_ids', '') or 'все'))}</code>\n"
        f"Автоответчик categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_responder_category_ids', '') or 'не ограничено'))}</code>\n"
        f"Автоподнятие categoryIds: <code>{escape(str(await db.get_account_setting(message.from_user.id, 'auto_raise_category_ids', '208') or 'не заданы'))}</code>"
    )


@dp.message(F.text == "/crypto_pay_debug")
async def crypto_pay_debug(message: Message) -> None:
    token_status = "✅ задан" if config.crypto_pay_token else "❌ не задан"
    await message.answer(
        "🧪 <b>Crypto Bot Pay диагностика</b>\n\n"
        f"Версия: <code>{CRYPTO_PAY_VERSION}</code>\n"
        f"CRYPTO_PAY_TOKEN: <b>{token_status}</b>\n"
        f"API: <code>{escape(config.crypto_pay_api_base)}</code>\n"
        f"Валюты оплаты: <code>{escape(config.crypto_pay_accepted_assets)}</code>\n\n"
        "Оплата создаётся как fiat invoice в RUB, а клиент платит выбранной криптой."
    )


@dp.message(F.text == "/top_debug")
async def top_debug(message: Message) -> None:
    await message.answer(
        "🧪 <b>Диагностика топа продавцов</b>\n\n"
        f"Версия: <code>{TOP_FIX_VERSION}</code>\n"
        "Строгий режим: отзывы и рейтинг берутся только из подтверждённого публичного профиля продавца. "
        "Если профиль не подтвердился, продавец не попадает в топ."
    )


@dp.callback_query(F.data == "seller:top1000")
async def seller_top1000(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "🔄 Собираю топ продавцов STARVELL...\n\n"
        "Это может занять 30–90 секунд: бот проходит по открытым категориям и проверяет отзывы через профиль каждого продавца."
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
    asyncio.create_task(auto_raise_loop())
    asyncio.create_task(confirm_reminder_loop())
    asyncio.create_task(crypto_pay_checker_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
