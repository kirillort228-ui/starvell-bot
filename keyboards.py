from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📈 Продажи")],
        [KeyboardButton(text="🔐 Мои аккаунты"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="⭐ Настройка аккаунта")],
        [KeyboardButton(text="🏆 Топ продавцов")],
        [KeyboardButton(text="🌐 Мои прокси")],
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


def profile_menu_keyboard(always_online_enabled: bool = False) -> InlineKeyboardMarkup:
    online_text = "🟢 Вечный онлайн" if always_online_enabled else "⚪️ Вечный онлайн"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="profile:notifications")],
            [InlineKeyboardButton(text="💬 Чаты и заказы", callback_data="profile:chats_orders")],
            [InlineKeyboardButton(text="📣 Мои клиенты", callback_data="profile:clients")],
            [InlineKeyboardButton(text=online_text, callback_data="profile:always_online")],
            [InlineKeyboardButton(text="✅ Прочитать все сообщения", callback_data="profile:read_all")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="profile:back")],
        ]
    )


def profile_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="profile:back")]]
    )


def profile_chats_orders_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Последние чаты", callback_data="profile:open_chats")],
            [InlineKeyboardButton(text="🛒 Заказы за 30 дней", callback_data="profile:open_orders")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="profile:back")],
        ]
    )


def account_settings_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👋 Приветствие", callback_data="accset:text:greeting")],
            [InlineKeyboardButton(text="🤝 Автоподтверждение", callback_data="accset:toggle:auto_confirm")],
            [InlineKeyboardButton(text="🚀 Автоподнятие лотов", callback_data="accset:toggle:auto_raise_lots")],
            [InlineKeyboardButton(text="🔄 Автовыставление лотов", callback_data="accset:toggle:auto_repost_lots")],
            [InlineKeyboardButton(text="📦 Автовыдача", callback_data="accset:toggle:auto_delivery")],
            [InlineKeyboardButton(text="🤖 Автоответчик", callback_data="accset:text:auto_responder")],
            [InlineKeyboardButton(text="⏰ Напоминание о подтверждении", callback_data="accset:text:confirm_reminder")],
            [InlineKeyboardButton(text="💬 Текст при игноре", callback_data="accset:text:ignore_text")],
            [InlineKeyboardButton(text="💬 Текст после 5 звёзд", callback_data="accset:text:after_5_stars")],
            [InlineKeyboardButton(text="💬 Текст при проблеме", callback_data="accset:text:problem_text")],
            [InlineKeyboardButton(text="💬 Текст после вашего подтверждения", callback_data="accset:text:after_seller_confirm")],
            [InlineKeyboardButton(text="💬 Текст после подтверждения клиентом", callback_data="accset:text:after_client_confirm")],
            [InlineKeyboardButton(text="‹ Назад в меню", callback_data="accset:back")],
        ]
    )


def account_settings_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ Назад к настройкам", callback_data="accset:menu")],
        ]
    )


def account_setting_text_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"accset:edit:{key}")],
            [InlineKeyboardButton(text="🗑 Очистить текст", callback_data=f"accset:clear:{key}")],
            [InlineKeyboardButton(text="‹ Назад к настройкам", callback_data="accset:menu")],
        ]
    )
