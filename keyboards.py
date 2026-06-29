from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔐 Мои аккаунты"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏆 Топ продавцов")],
        [KeyboardButton(text="🌐 Мои прокси")],
        [KeyboardButton(text="💬 Чаты"), KeyboardButton(text="🛒 Заказы")],
    ],
    resize_keyboard=True,
)


def accounts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="account:add")],
            [InlineKeyboardButton(text="🔄 Проверить аккаунты", callback_data="account:check_all")],
        ]
    )


def account_actions_keyboard(account_id: int, notifications_enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔕 Выключить уведомления" if notifications_enabled else "🔔 Включить уведомления"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Проверить чаты", callback_data=f"account:chats:{account_id}")],
            [InlineKeyboardButton(text="🌐 Прокси аккаунта", callback_data=f"account:proxy:{account_id}")],
            [InlineKeyboardButton(text=toggle_text, callback_data=f"account:toggle:{account_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"account:delete:{account_id}")],
        ]
    )


def proxies_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить прокси", callback_data="proxy:add")],
            [InlineKeyboardButton(text="✅ Проверить прокси", callback_data="proxy:check")],
            [InlineKeyboardButton(text="🛒 Купить прокси", url="https://px6.net/ru/")],
        ]
    )


def top_up_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс бота", callback_data="topup:menu")],
        ]
    )


def no_subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Попробовать 7 дней бесплатно", callback_data="sub:trial")],
            [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup:menu")],
            [InlineKeyboardButton(text="14 дней | 399 ₽", callback_data="sub:14")],
            [InlineKeyboardButton(text="1 месяц | 699 ₽ (-15% 🔥)", callback_data="sub:30")],
            [InlineKeyboardButton(text="3 месяца | 1399 ₽ (-20% 🔥)", callback_data="sub:90")],
            [InlineKeyboardButton(text="6 месяцев | 2699 ₽ (-20% 🔥)", callback_data="sub:180")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Отменить", callback_data="cancel")],
        ]
    )


def admin_topup_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Зачислить", callback_data=f"topup_admin:approve:{request_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"topup_admin:reject:{request_id}"),
            ]
        ]
    )


def chat_link_keyboard(chat_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Открыть чат", url=chat_url)]]
    )



def top_sellers_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏆 Топ 1000 продавцов", callback_data="seller:top1000")],
            [InlineKeyboardButton(text="🔎 Найти профиль", callback_data="seller:find")],
        ]
    )


def seller_profile_keyboard(username: str) -> InlineKeyboardMarkup:
    from urllib.parse import quote
    profile_url = f"https://starvell.com/profile/{username}?username={username}"
    share_url = f"https://t.me/share/url?url={quote(profile_url)}&text={quote('Профиль продавца STARVELL: ' + username)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"seller:refresh:{username}")],
            [InlineKeyboardButton(text="👤 Открыть профиль", url=profile_url)],
            [InlineKeyboardButton(text="↪️ Поделиться", url=share_url)],
            [InlineKeyboardButton(text="🏆 Топ 1000 продавцов", callback_data="seller:top1000")],
            [InlineKeyboardButton(text="🔎 Найти профиль", callback_data="seller:find")],
        ]
    )
