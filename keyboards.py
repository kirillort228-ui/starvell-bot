from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📈 Продажи")],
        [KeyboardButton(text="🔐 Мои аккаунты"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="⭐ Настройка аккаунта")],
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
            [InlineKeyboardButton(text="📦 Автовыдача сообщением", callback_data="autodelivery:menu")],
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


def auto_confirm_menu_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("❌ Выключить" if enabled else "✅ Включить"),
                    callback_data="autoconfirm:toggle",
                )
            ],
            [InlineKeyboardButton(text="📦 Выбрать товары", callback_data="autoconfirm:select_products")],
            [InlineKeyboardButton(text="🧹 Очистить выбор", callback_data="autoconfirm:clear_products")],
            [InlineKeyboardButton(text="‹ Назад к настройкам", callback_data="accset:menu")],
        ]
    )


def account_setting_text_keyboard(key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"accset:edit:{key}")],
    ]
    if key == "auto_responder":
        rows.append([InlineKeyboardButton(text="📦 Выбрать товары", callback_data="prodsel:menu")])
    rows.append([InlineKeyboardButton(text="🗑 Очистить текст", callback_data=f"accset:clear:{key}")])
    rows.append([InlineKeyboardButton(text="‹ Назад к настройкам", callback_data="accset:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_reminder_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅/❌ Включить или выключить", callback_data="reminder:toggle")],
            [InlineKeyboardButton(text="💬 Найти чаты без подтверждения", callback_data="reminder:find_chats")],
            [InlineKeyboardButton(text="‹ Назад к настройкам", callback_data="accset:menu")],
        ]
    )


def confirm_reminder_product_menu_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕐 Выбрать время", callback_data=f"reminder:time_menu:{offer_id}")],
            [InlineKeyboardButton(text="📅 Выбрать дни", callback_data=f"reminder:period_menu:{offer_id}")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"reminder:edit:{offer_id}")],
            [InlineKeyboardButton(text="🗑 Очистить текст", callback_data=f"reminder:clear:{offer_id}")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="accset:text:confirm_reminder")],
        ]
    )


def confirm_reminder_time_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="09:00", callback_data=f"reminder:time:{offer_id}:09:00"),
                InlineKeyboardButton(text="13:00", callback_data=f"reminder:time:{offer_id}:13:00"),
                InlineKeyboardButton(text="18:00", callback_data=f"reminder:time:{offer_id}:18:00"),
            ],
            [
                InlineKeyboardButton(text="21:00", callback_data=f"reminder:time:{offer_id}:21:00"),
                InlineKeyboardButton(text="✍️ своё время", callback_data=f"reminder:time_custom:{offer_id}"),
            ],
            [InlineKeyboardButton(text="‹ Назад", callback_data=f"reminder:product:{offer_id}")],
        ]
    )


def confirm_reminder_period_keyboard(offer_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Каждый день", callback_data=f"reminder:period:{offer_id}:1")],
            [InlineKeyboardButton(text="Спустя день", callback_data=f"reminder:period:{offer_id}:2")],
            [InlineKeyboardButton(text="Раз в 3 дня", callback_data=f"reminder:period:{offer_id}:3")],
            [InlineKeyboardButton(text="Раз в неделю", callback_data=f"reminder:period:{offer_id}:7")],
            [InlineKeyboardButton(text="‹ Назад", callback_data=f"reminder:product:{offer_id}")],
        ]
    )




def auto_raise_interval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="4 часа", callback_data="autoraise:interval:4"),
                InlineKeyboardButton(text="6 часов", callback_data="autoraise:interval:6"),
            ],
            [
                InlineKeyboardButton(text="8 часов", callback_data="autoraise:interval:8"),
                InlineKeyboardButton(text="12 часов", callback_data="autoraise:interval:12"),
            ],
            [InlineKeyboardButton(text="24 часа", callback_data="autoraise:interval:24")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="accset:toggle:auto_raise_lots")],
        ]
    )



def crypto_invoice_keyboard(pay_url: str, request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Оплатить через Crypto Bot", url=pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"topup:check_crypto:{request_id}")],
        ]
    )



def product_select_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Найти мои товары", callback_data="prodsel:scan")],
            [InlineKeyboardButton(text="✏️ Ввести offerPublicId вручную", callback_data="prodsel:set_offers")],
            [InlineKeyboardButton(text="📂 Ввести categoryIds вручную", callback_data="prodsel:set_categories")],
            [InlineKeyboardButton(text="🧹 Автоответчик на все товары", callback_data="prodsel:clear")],
            [InlineKeyboardButton(text="‹ Назад к автонастройкам", callback_data="accset:menu")],
        ]
    )

def product_select_found_keyboard(items: list[dict], mode: str = "reply") -> InlineKeyboardMarkup:
    rows = []
    for idx, item in enumerate(items[:12]):
        title = str(item.get("title") or item.get("name") or item.get("label") or item.get("offerPublicId") or item.get("categoryId") or "Товар")
        title = title.replace("\n", " ").strip()
        if len(title) > 42:
            title = title[:39] + "..."
        offer_id = str(item.get("offerPublicId") or "")
        category_id = str(item.get("categoryId") or "")
        if offer_id:
            rows.append([InlineKeyboardButton(text=f"✅ {title}", callback_data=f"prodsel:add_offer:{idx}" if mode == "reply" else f"autoraise:add_cat:{idx}")])
        elif category_id:
            rows.append([InlineKeyboardButton(text=f"✅ {title}", callback_data=f"prodsel:add_cat:{idx}" if mode == "reply" else f"autoraise:add_cat:{idx}")])
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="prodsel:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def auto_raise_settings_keyboard(enabled: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=("✅ Выключить автоподнятие" if enabled else "🚀 Включить автоподнятие"),
                callback_data="autoraise:toggle",
            )],
            [InlineKeyboardButton(text="⏱ Интервал", callback_data="autoraise:interval_menu")],
            [InlineKeyboardButton(text="🚀 Поднять лоты сейчас", callback_data="autoraise:run_now")],
            [InlineKeyboardButton(text="‹ Назад к автонастройкам", callback_data="accset:menu")],
        ]
    )



def auto_delivery_menu_keyboard(enabled: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=("✅ Выключить автовыдачу" if enabled else "📦 Включить автовыдачу"),
                callback_data="autodelivery:toggle",
            )],
            [InlineKeyboardButton(text="📝 Настроить сообщение", callback_data="accset:edit:auto_delivery_message")],
            [InlineKeyboardButton(text="📋 Выбрать товары", callback_data="autodelivery:scan")],
            [InlineKeyboardButton(text="🧹 Очистить выбор товаров", callback_data="autodelivery:clear_products")],
            [InlineKeyboardButton(text="‹ Назад к автонастройкам", callback_data="accset:menu")],
        ]
    )


def auto_delivery_products_keyboard(
    items: list[dict],
    page: int = 0,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start_index = page * page_size
    end_index = min(total, start_index + page_size)

    rows = [
        [InlineKeyboardButton(
            text=f"✅ Выбрать все ({total})",
            callback_data="autodelivery:select_all",
        )]
    ]

    for idx in range(start_index, end_index):
        item = items[idx]
        title = str(
            item.get("title")
            or item.get("name")
            or item.get("offerPublicId")
            or item.get("categoryId")
            or "Товар"
        ).replace("\n", " ").strip()

        game = str(item.get("gameName") or item.get("gameSlug") or "").strip()
        if game:
            title = f"{game}: {title}"

        if len(title) > 42:
            title = title[:39] + "..."

        rows.append([
            InlineKeyboardButton(
                text=f"✅ {title}",
                callback_data=f"autodelivery:add:{idx}",
            )
        ])

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"autodelivery:page:{page - 1}",
            )
        )

    navigation.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="autodelivery:page_info",
        )
    )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"autodelivery:page:{page + 1}",
            )
        )

    rows.append(navigation)
    rows.append([
        InlineKeyboardButton(
            text="‹ Назад",
            callback_data="autodelivery:menu",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def statistics_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="stats_period:today"),
                InlineKeyboardButton(text="7 дней", callback_data="stats_period:7"),
            ],
            [
                InlineKeyboardButton(text="30 дней", callback_data="stats_period:30"),
                InlineKeyboardButton(text="Всё время", callback_data="stats_period:all"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="stats_period:back")],
        ]
    )



def auto_raise_products_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Выбрать все", callback_data="autoraise:select_all_products")]
    ]
    for idx, item in enumerate(items[:20]):
        title = str(
            item.get("title")
            or item.get("name")
            or item.get("offerPublicId")
            or item.get("categoryId")
            or "Товар"
        ).replace("\n", " ").strip()
        if len(title) > 42:
            title = title[:39] + "..."
        rows.append([
            InlineKeyboardButton(
                text=f"✅ {title}",
                callback_data=f"autoraise:add_product:{idx}",
            )
        ])
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="autoraise:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
