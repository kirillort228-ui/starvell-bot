from __future__ import annotations

import asyncio
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
    main_keyboard,
    no_subscription_keyboard,
    proxies_keyboard,
    seller_profile_keyboard,
    top_sellers_menu_keyboard,
    top_up_keyboard,
)
from proxy_utils import check_proxy, hide_proxy, normalize_proxy, validate_proxy
from starvell_client import StarvellClient, StarvellApiError

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
    await message.answer(
        "👤 <b>Мой профиль</b>\n\n"
        f"╭ Ваш ID: <code>{message.from_user.id}</code>\n"
        "├ Часовой пояс: 🌍 UTC\n"
        "╰ Подписка: отсутствует\n\n"
        "💰 <b>Баланс бота</b>\n"
        f"╰ {format_rub(bot_balance)}\n\n"
        "📊 <b>Статистика</b>\n\n"
        f"╭ Подключено аккаунтов: {len(accounts)}\n"
        f"╰ Уведомления включены: {enabled}",
        reply_markup=top_up_keyboard(),
    )

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

    await message.answer("🔄 Проверяю аккаунт Starvell...")
    client = StarvellClient(cookie=cookie, proxy_url=None)
    try:
        chats_data = await client.get_chats()
        user = extract_user(chats_data)
        username = user.get("username")
        if not username:
            raise StarvellApiError("Не удалось определить username. Возможно, cookie устарели.")

        account_id = await db.add_account(
            user_id=message.from_user.id,
            cookie=cookie,
            username=username,
            proxy_url=None,
        )
        await baseline_account_messages(db, account_id, chats_data)
        await state.clear()

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
    await message.answer("Выбери аккаунт в разделе 🔐 Мои аккаунты и нажми «💬 Проверить чаты».")


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
        "REFUNDED": "Возврат",
        "DISPUTE": "Спор",
    }
    return statuses.get(raw, raw or "Неизвестно")


def format_order(order: dict, index: int) -> str:
    order_id = order.get("id") or order.get("orderId") or "—"
    status = get_order_status(order)
    price = order.get("price") or order.get("amount") or order.get("rubAmount") or order.get("total") or "—"

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
    if not await require_subscription(message):
        return
    accounts = await db.list_user_accounts(message.from_user.id)
    if not accounts:
        await message.answer("😴 Сначала добавь Starvell аккаунт.")
        return

    account = accounts[0]
    client = StarvellClient(cookie=account.cookie, proxy_url=account.proxy_url)

    try:
        data = await client.get_orders()
        page_props = data.get("pageProps", {})
        orders_list = page_props.get("orders") or []
        user = page_props.get("user") or {}
        username = user.get("username") or account.username or "Starvell"
        counts = user.get("ordersCount") or {}
        balance = user.get("balance") or {}

        if not orders_list:
            await message.answer(
                "🛒 <b>Заказы Starvell</b>\n\n"
                f"👤 Аккаунт: <b>{username}</b>\n"
                "Заказов в этом разделе сейчас нет.\n\n"
                f"🛍 Покупки: {counts.get('purchaseOrdersCount', 0)}\n"
                f"💼 Продажи: {counts.get('salesOrdersCount', 0)}\n"
                f"💰 Баланс всего: {format_total_balance(balance)}\n"
                f"├ Доступно: {format_rub(balance.get('rubBalance', 0))}\n"
                f"├ В холде: {format_rub(balance.get('holdedRubBalance', 0))}\n"
                f"└ Можно вывести: {format_rub(balance.get('withdrawableRubBalance', 0))}"
            )
            return

        text = f"🛒 <b>Заказы Starvell — {username}</b>\n\n"
        for index, order in enumerate(orders_list[:5], start=1):
            text += format_order(order, index) + "\n\n"

        if len(orders_list) > 5:
            text += f"Показано 5 из {len(orders_list)} заказов."

        await message.answer(text)

    except StarvellApiError as error:
        await message.answer(f"❌ Ошибка заказов: <code>{error}</code>")
    finally:
        await client.close()


@dp.message(F.text == "📊 Статистика")
async def stats(message: Message) -> None:
    if not await require_subscription(message):
        return
    accounts = await db.list_user_accounts(message.from_user.id)
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Подключено аккаунтов: {len(accounts)}\n"
        f"Активных уведомлений: {sum(1 for a in accounts if a.notifications_enabled)}\n"
        f"Интервал проверки: {config.check_interval_seconds} сек."
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
        "Здесь ты можешь найти профиль продавца и быстро открыть его на сайте.",
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
    await state.set_state(FindSellerProfile.waiting_username)
    await callback.message.answer(
        "🏆 <b>Топ 1000 продавцов</b>\n\n"
        "Пока в боте доступен просмотр профиля продавца по username.\n"
        "Отправь username продавца из топа, и я покажу карточку как в STARVELL.",
        reply_markup=cancel_keyboard(),
    )
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
