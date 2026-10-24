import json
import re
import secrets
import string
from typing import Any

import httpx


class StarvellApiError(Exception):
    pass






def extract_offer_public_id(item: dict | None) -> str | None:
    """
    Starvell migrated offer routes from integer offerId to UUID offerPublicId.
    This helper always prefers UUID/public id fields and never falls back to numeric id.
    """
    if not isinstance(item, dict):
        return None

    direct_keys = (
        "offerPublicId",
        "offer_public_id",
        "publicId",
        "public_id",
        "uuid",
    )
    for key in direct_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    nested_keys = ("offer", "offerDetails", "details")
    for key in nested_keys:
        value = item.get(key)
        if isinstance(value, dict):
            found = extract_offer_public_id(value)
            if found:
                return found

    return None


def ensure_offer_public_id(value: str | dict | None) -> str:
    """
    Accepts either a UUID string or an object containing offerPublicId/publicId.
    Raises an explicit error if only old numeric offerId is available.
    """
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        # UUID is expected. We do not accept plain int offerId anymore.
        if raw.isdigit():
            raise StarvellApiError(
                "Нужен offerPublicId UUID, а не старый числовой offerId. "
                "Открой товар заново или обнови данные предложений."
            )
        return raw

    public_id = extract_offer_public_id(value if isinstance(value, dict) else None)
    if public_id:
        return public_id

    old_id = None
    if isinstance(value, dict):
        old_id = value.get("offerId") or value.get("offer_id") or value.get("id")
    if old_id is not None:
        raise StarvellApiError(
            "В данных найден только старый offerId/int. "
            "После изменения API Starvell нужно использовать offerPublicId UUID."
        )

    raise StarvellApiError("Не найден offerPublicId для предложения.")



def extract_bump_groups_from_data(data: Any, my_user_id: int | None = None) -> dict[int, set[int]]:
    """
    Finds active offer/category pairs in any Starvell JSON.
    Returns {gameId: {categoryId, ...}}.
    If my_user_id is known, only offers belonging to that user are used when userId is present.
    """
    groups: dict[int, set[int]] = {}

    for obj in _iter_dicts(data):
        if not isinstance(obj, dict):
            continue

        user_id = obj.get("userId") or obj.get("sellerId")
        if my_user_id is not None and user_id is not None:
            try:
                if int(user_id) != int(my_user_id):
                    continue
            except Exception:
                continue

        # Ignore inactive/hidden/deleted offers when the fields are present.
        if obj.get("isActive") is False or obj.get("isHidden") is True:
            continue
        moderation_status = str(obj.get("moderationStatus") or "").upper()
        if moderation_status and moderation_status not in {"APPROVED", "ACTIVE", "PUBLISHED"}:
            continue

        game_id = obj.get("gameId") or obj.get("game_id")
        category_id = obj.get("categoryId") or obj.get("category_id")

        taxonomy = obj.get("taxonomySnapshot") if isinstance(obj.get("taxonomySnapshot"), dict) else {}
        if not game_id and isinstance(taxonomy.get("game"), dict):
            game_id = taxonomy["game"].get("id")
        if not category_id and isinstance(taxonomy.get("category"), dict):
            category_id = taxonomy["category"].get("id")

        # Treat as an offer only if object has offer-like fields, or it belongs to the seller.
        offer_like = any(k in obj for k in (
            "publicId", "offerPublicId", "offer_public_id", "price", "availability",
            "moderationStatus", "listedAt", "nextListedAt", "descriptions"
        ))
        if not offer_like and user_id is None:
            continue

        try:
            game_i = int(game_id)
            category_i = int(category_id)
        except Exception:
            continue

        if game_i > 0 and category_i > 0:
            groups.setdefault(game_i, set()).add(category_i)

    return groups



def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _first_present(obj: dict, keys: tuple[str, ...]):
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_int_safe(value, default: int = 0) -> int:
    try:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            for key in ("count", "total", "itemsCount", "reviewsCount", "reviewCount"):
                if key in value:
                    return _to_int_safe(value.get(key), default)
            if "items" in value and isinstance(value.get("items"), list):
                return len(value.get("items"))
            return default
        if isinstance(value, str):
            value = value.replace(" ", "").replace("\u00a0", "")
            match = re.search(r"-?\d+(?:[\.,]\d+)?", value)
            if match:
                value = match.group(0).replace(",", ".")
        return int(float(value))
    except Exception:
        return default


def _to_float_safe(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _normalize_profile_user_dict(user: dict, requested_username: str | None = None) -> dict:
    source_user = user.get("user") if isinstance(user.get("user"), dict) else {}
    merged = {}
    merged.update(source_user)
    merged.update(user)

    username = (
        _first_present(merged, ("username", "name", "sellerUsername"))
        or requested_username
        or "unknown"
    )
    rating = _first_present(merged, ("rating", "stars", "sellerRating", "averageRating"))
    reviews = _first_present(merged, ("reviewsCount", "reviewCount", "reviews", "sellerReviewsCount", "feedbacksCount", "feedbackCount", "reviewsTotal", "totalReviews"))
    created = _first_present(merged, ("createdAt", "created_at", "registrationDate"))
    kyc = _first_present(merged, ("kycStatus", "verificationStatus", "kyc"))
    banned = bool(_first_present(merged, ("isBanned", "banned", "isBlocked")) or False)

    return {
        **merged,
        "username": str(username),
        "rating": _to_float_safe(rating, 0.0) if rating is not None else None,
        "reviewsCount": _to_int_safe(reviews, 0),
        "createdAt": created,
        "kycStatus": kyc,
        "isBanned": banned,
    }


def find_profile_user_in_data(data: dict, requested_username: str | None = None) -> dict:
    """
    Starvell profile JSON can store the public seller in different fields.
    pageProps.user is sometimes the logged-in account, so we search recursively
    for a dict whose username matches the requested seller and has profile fields.
    """
    username_l = (requested_username or "").strip().lower()

    exact_candidates = []
    fallback_candidates = []

    for item in _iter_dicts(data):
        if not isinstance(item, dict):
            continue

        # Flatten direct dict or nested {"user": {...}} structure.
        nested_user = item.get("user") if isinstance(item.get("user"), dict) else None
        candidates = [item]
        if nested_user:
            merged = {}
            merged.update(nested_user)
            merged.update(item)
            candidates.append(merged)

        for cand in candidates:
            cand_username = str(
                cand.get("username")
                or cand.get("name")
                or cand.get("sellerUsername")
                or ""
            ).strip()
            if not cand_username:
                continue

            has_profile_fields = any(k in cand for k in (
                "rating", "stars", "sellerRating", "averageRating",
                "reviewsCount", "reviewCount", "reviews", "sellerReviewsCount", "feedbacksCount",
                "kycStatus", "createdAt", "description", "isBanned",
            ))
            if not has_profile_fields:
                continue

            normalized = _normalize_profile_user_dict(cand, requested_username)
            if username_l and cand_username.lower() == username_l:
                exact_candidates.append(normalized)
            else:
                fallback_candidates.append(normalized)

    # Prefer exact username with most reviews.
    if exact_candidates:
        return max(exact_candidates, key=lambda x: int(x.get("reviewsCount") or 0))

    page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
    for key in ("profileUser", "seller", "profile", "account", "publicUser"):
        value = page_props.get(key) if isinstance(page_props, dict) else None
        if isinstance(value, dict):
            return _normalize_profile_user_dict(value, requested_username)

    # Last fallback: avoid authenticated pageProps.user unless username matches or nothing else exists.
    value = page_props.get("user") if isinstance(page_props, dict) else None
    if isinstance(value, dict):
        user_name = str(value.get("username") or "").strip().lower()
        if not username_l or user_name == username_l:
            return _normalize_profile_user_dict(value, requested_username)

    if fallback_candidates:
        return max(fallback_candidates, key=lambda x: int(x.get("reviewsCount") or 0))

    return {"username": requested_username or "unknown", "rating": None, "reviewsCount": 0}



def extract_game_category_groups_from_trade_data(data: Any) -> dict[int, set[int]]:
    """
    From trade.json game/category pages returns all category IDs for the game.
    This is useful for Starvell bump endpoint: if the seller has at least one active lot in this game,
    we can send all categoryIds of that game so Starvell bumps any seller lots inside those categories.
    """
    groups: dict[int, set[int]] = {}
    if not isinstance(data, dict):
        return groups

    page_props = data.get("pageProps", {}) if isinstance(data.get("pageProps"), dict) else {}

    game_id = None
    game = page_props.get("game") if isinstance(page_props.get("game"), dict) else {}
    if isinstance(game, dict):
        game_id = game.get("id")
        try:
            game_i = int(game_id)
        except Exception:
            game_i = 0

        if game_i:
            for category in game.get("categories", []) or []:
                if not isinstance(category, dict):
                    continue
                try:
                    cat_i = int(category.get("id"))
                except Exception:
                    continue
                if cat_i > 0:
                    groups.setdefault(game_i, set()).add(cat_i)

    # Current page category.
    category = page_props.get("category") if isinstance(page_props.get("category"), dict) else {}
    try:
        game_i = int(category.get("gameId") or game_id or 0)
        cat_i = int(category.get("id") or 0)
        if game_i > 0 and cat_i > 0:
            groups.setdefault(game_i, set()).add(cat_i)
    except Exception:
        pass

    # categoriesWithOrders contains detailed categories for the same game.
    for category in page_props.get("categoriesWithOrders", []) or []:
        if not isinstance(category, dict):
            continue
        try:
            game_i = int(category.get("gameId") or game_id or 0)
            cat_i = int(category.get("id") or 0)
        except Exception:
            continue
        if game_i > 0 and cat_i > 0:
            groups.setdefault(game_i, set()).add(cat_i)

    return groups



def extract_game_slugs_from_data(data: Any) -> dict[int, str]:
    """
    Finds game id/slug pairs anywhere in Starvell JSON.
    """
    result: dict[int, str] = {}
    for obj in _iter_dicts(data):
        if not isinstance(obj, dict):
            continue

        # Direct game object.
        if obj.get("id") is not None and obj.get("slug") and any(
            key in obj for key in ("categories", "isNew", "icon", "background", "type")
        ):
            try:
                game_id = int(obj.get("id"))
                slug = str(obj.get("slug")).strip()
                if game_id > 0 and slug:
                    result[game_id] = slug
            except Exception:
                pass

        # Offer taxonomy snapshot.
        taxonomy = obj.get("taxonomySnapshot") if isinstance(obj.get("taxonomySnapshot"), dict) else {}
        game = taxonomy.get("game") if isinstance(taxonomy.get("game"), dict) else {}
        try:
            game_id = int(game.get("id"))
            slug = str(game.get("slug") or "").strip()
            if game_id > 0 and slug:
                result[game_id] = slug
        except Exception:
            pass

        # Nested game field.
        nested_game = obj.get("game") if isinstance(obj.get("game"), dict) else {}
        try:
            game_id = int(nested_game.get("id"))
            slug = str(nested_game.get("slug") or "").strip()
            if game_id > 0 and slug:
                result[game_id] = slug
        except Exception:
            pass

    return result


def _make_client_socket_id(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class StarvellClient:
    """Cookie-authenticated client for the API used by the Starvell website.

    pageProps is a local compatibility adapter for existing Telegram formatters;
    no Next.js page data or HTML is fetched by this client.
    """

    def __init__(self, cookie: str, proxy_url: str | None = None):
        self.base_url = "https://starvell.com"
        self.cookie = cookie.strip()
        self.proxy_url = proxy_url
        self._user: dict | None = None
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10), proxy=proxy_url,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "Origin": self.base_url,
                "Referer": self.base_url + "/chat",
                "Cookie": self.cookie,
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _api(self, method: str, path: str, payload: dict | None = None) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be relative")
        try:
            response = await self.client.request(
                method, self.base_url + "/api" + path,
                **({"params": payload} if method == "GET" else {"json": payload or {}}),
            )
        except httpx.RequestError as exc:
            raise StarvellApiError(
                "Не удалось связаться с API Starvell. Проверь соединение и прокси."
            ) from exc
        if response.is_success and not response.content and (
            path in {"/offers/bump", "/chats/read"} or path.endswith("/mark-seller-completed")
        ):
            return {"ok": True}
        try:
            data = response.json()
        except ValueError as exc:
            raise StarvellApiError(
                f"API Starvell вернул HTTP {response.status_code} без JSON. "
                "Возможна блокировка или изменение API."
            ) from exc
        details = data.get("data") if isinstance(data, dict) else None
        code = details.get("code") if isinstance(details, dict) else None
        if response.status_code == 401 or code == "SESSION_NOT_FOUND":
            self._user = None
            raise StarvellApiError("Сессия Starvell истекла. Подключи аккаунт заново со свежими Cookie.")
        if (response.status_code >= 300 or
                isinstance(data, dict) and (data.get("success") is False or data.get("ok") is False)):
            message = str(data.get("message") or "Ошибка API") if isinstance(data, dict) else "Ошибка API"
            # Preserve structured cooldown data without logging cookies or raw HTML.
            extra = json.dumps(details, ensure_ascii=False)[:400] if isinstance(details, dict) else ""
            raise StarvellApiError(f"Starvell HTTP {response.status_code}: {message[:200]} {extra}")
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    @staticmethod
    def _items(data: Any, *keys: str) -> list[dict]:
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = next((data[k] for k in ("items", *keys) if isinstance(data.get(k), list)), None)
        else:
            items = None
        if items is None or any(not isinstance(item, dict) for item in items):
            raise StarvellApiError("API Starvell вернул неожиданный формат списка.")
        return items

    async def _list_all(self, path: str, payload: dict | None = None, *, keys: tuple = (), page_size: int = 50) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for page in range(200):
            data = await self._api("POST", path, {**(payload or {}), "offset": page * page_size, "limit": page_size})
            batch = self._items(data, *keys)
            for item in batch:
                key = str(item.get("id") or item.get("publicId") or json.dumps(item, sort_keys=True))
                if key in seen:
                    raise StarvellApiError("API повторил элемент при пагинации; список мог измениться. Повтори запрос.")
                seen.add(key)
                result.append(item)
            if isinstance(data, dict):
                total = data.get("totalCount", data.get("total"))
                if data.get("hasMore") is False or data.get("hasNextPage") is False:
                    return result
                if isinstance(total, int) and len(result) >= total:
                    return result
            if len(batch) < page_size:
                return result
        raise StarvellApiError("Список Starvell превысил лимит 10000 элементов; сузь выборку.")

    async def get_current_user(self, *, refresh: bool = False) -> dict:
        if self._user is not None and not refresh:
            return self._user
        data = await self._api("GET", "/profiles/me")
        if isinstance(data, dict):
            user = data.get("user") if isinstance(data.get("user"), dict) else data
            if user.get("id") is not None and user.get("username"):
                user = dict(user)
                for key in ("unreadChatIds", "balance", "offersHides", "isSeller"):
                    if key in data:
                        user[key] = data[key]
                if "orderCountsByType" in data:
                    user["ordersCount"] = data["orderCountsByType"]
                self._user = user
                return user
        raise StarvellApiError("API не подтвердил аккаунт Starvell. Обнови Cookie.")

    async def get_chats(self) -> dict:
        user = await self.get_current_user()
        chats = await self._list_all("/chats/list", keys=("chats",))
        return {"pageProps": {"user": user, "chats": chats}, "_source": "api/chats/list"}

    async def get_orders(self) -> dict:
        user = await self.get_current_user()
        orders = await self._list_all(
            "/orders/list", {"filter": {"userType": "seller"}, "with": {"buyer": True}}, keys=("orders",),
        )
        source = "api/orders/list (seller)"
        return {"pageProps": {"user": user, "orders": orders, "_ordersSource": source}, "_ordersSource": source}

    async def get_open_order_chats(self, *, offset: int = 0, limit: int = 50, role: str = "SELLER") -> list[dict]:
        return self._items(await self._api("POST", "/chats/list-open-order", {
            "offset": offset, "limit": limit, "role": role,
        }), "chats")

    async def get_all_open_order_chats(self) -> list[dict]:
        return await self._list_all("/chats/list-open-order", {"role": "SELLER"}, keys=("chats",))

    async def get_chat_page(self, interlocutor_id: int) -> dict:
        data = await self._api("POST", "/bff/chat-page", {"interlocutorId": int(interlocutor_id)})
        if not isinstance(data, dict):
            raise StarvellApiError("Неожиданный ответ API чата.")
        return data

    async def get_messages(self, chat_id: str, *, before_cursor: str | None = None, limit: int = 50) -> dict:
        payload: dict = {"chatId": str(chat_id), "limit": limit}
        if before_cursor is not None:
            payload["beforeId"] = before_cursor
        data = await self._api("POST", "/messages/list-v2", payload)
        if not isinstance(data, dict):
            raise StarvellApiError("API истории сообщений вернул неожиданный формат.")
        self._items(data)
        return data

    async def get_messages_since(self, chat_id: str, saved_id: str | None) -> list[dict]:
        newest_first: list[dict] = []
        seen: set[str] = set()
        cursor = None
        for _ in range(200):
            data = await self.get_messages(chat_id, before_cursor=cursor)
            for message in data["items"]:
                message_id = str(message.get("id") or "")
                if not message_id:
                    # Virtual order-feedback rows are not delivery events.
                    continue
                if message_id == str(saved_id):
                    return list(reversed(newest_first))
                if message_id not in seen:
                    seen.add(message_id)
                    newest_first.append(message)
            if data.get("hasMoreBefore") is False:
                if saved_id is not None:
                    raise StarvellApiError(
                        "Сохранённое сообщение больше недоступно в истории. "
                        "Нажми «Прочитать все сообщения», чтобы обновить точку отсчёта."
                    )
                return list(reversed(newest_first))
            next_cursor = data.get("nextCursor")
            if not next_cursor or next_cursor == cursor:
                raise StarvellApiError("API истории не вернул курсор следующей страницы.")
            cursor = next_cursor
        raise StarvellApiError("Слишком большая история чата; обработка не завершена.")

    async def mark_chat_read(self, chat_id: str) -> Any:
        return await self._api("POST", "/chats/read", {"chatId": str(chat_id)})

    async def send_chat_message(self, chat_id: str, content: str, client_socket_id: str | None = None) -> Any:
        if not str(chat_id).strip() or not str(content).strip():
            raise StarvellApiError("chatId и текст сообщения не могут быть пустыми.")
        return await self._api("POST", "/messages/send", {
            "chatId": str(chat_id), "content": str(content),
            "clientSocketId": client_socket_id or _make_client_socket_id(),
        })

    async def mark_seller_completed(self, order_id: str) -> Any:
        from urllib.parse import quote
        if not str(order_id).strip():
            raise StarvellApiError("Не указан заказ.")
        return await self._api("POST", f"/orders/{quote(str(order_id), safe='')}/mark-seller-completed", {"id": str(order_id)})

    async def bump_offers(self, game_id: int, category_ids: list[int]) -> Any:
        if game_id <= 0 or not category_ids or any(int(value) <= 0 for value in category_ids):
            raise StarvellApiError("Не указаны игра и категории для поднятия.")
        return await self._api("POST", "/offers/bump", {"gameId": int(game_id), "categoryIds": sorted(set(map(int, category_ids)))})

    async def find_chat_id_by_user(self, buyer_id: int | None = None, buyer_username: str | None = None) -> str | None:
        data = await self.get_chats()
        for chat in data["pageProps"]["chats"]:
            for participant in chat.get("participants") or []:
                user = participant.get("user", participant)
                if (buyer_id is not None and str(user.get("id")) == str(buyer_id) or
                        buyer_username and str(user.get("username", "")).lower() == buyer_username.lower()):
                    return str(chat["id"])
        return None

    async def get_profile(self, username: str) -> dict:
        from urllib.parse import quote
        data = await self._api("GET", "/profiles/" + quote(username.strip(), safe=""))
        user = find_profile_user_in_data({"pageProps": {"profile": data}}, username)
        if str(user.get("username", "")).lower() != username.strip().lower() or user.get("id") is None:
            raise StarvellApiError("API вернул профиль другого пользователя или неизвестный формат.")
        return {"pageProps": {"profileUser": user}, "_source": "api/profiles"}

    async def get_profile_user(self, username: str) -> dict:
        return (await self.get_profile(username))["pageProps"]["profileUser"]

    async def get_profile_user_verified(self, username: str) -> dict:
        return await self.get_profile_user(username)

    async def collect_profile_page_offers(self, username: str, *, include_inactive: bool = False) -> list[dict]:
        """Catalog API paginates groups of offers by category, as the website does."""
        found: dict[str, dict] = {}
        seen_categories: set[str] = set()
        for page in range(200):
            data = await self._api("POST", "/catalog/list-user-profile-offers", {
                "username": username, "limit": 10, "offset": page * 10,
            })
            categories = self._items(data, "categories")
            for category in categories:
                category_key = str(category.get("id") or category.get("categoryId") or json.dumps(category, sort_keys=True))
                if category_key in seen_categories:
                    raise StarvellApiError("API повторил категорию товаров при пагинации.")
                seen_categories.add(category_key)
                offers = category.get("offers")
                if not isinstance(offers, list):
                    raise StarvellApiError("API каталога не вернул список товаров категории.")
                for offer in offers:
                    if not isinstance(offer, dict):
                        raise StarvellApiError("Неожиданный формат товара API.")
                    public_id = extract_offer_public_id(offer)
                    if not public_id:
                        raise StarvellApiError("API товара не вернул offerPublicId.")
                    if not include_inactive and (offer.get("isActive") is False or offer.get("isHidden") is True):
                        continue
                    if not include_inactive and offer.get("availability") is not None and _to_int_safe(offer["availability"]) <= 0:
                        continue
                    taxonomy = offer.get("taxonomySnapshot") or {}
                    game = category.get("game") or taxonomy.get("game") or {}
                    cat = taxonomy.get("category") or {}
                    descriptions = offer.get("descriptions") or {}
                    rus = descriptions.get("rus") or {}
                    found[public_id] = {
                        **offer, "offerPublicId": public_id, "publicId": public_id,
                        "gameId": offer.get("gameId") or category.get("gameId") or game.get("id"),
                        "categoryId": offer.get("categoryId") or category.get("id") or cat.get("id"),
                        "gameName": game.get("name", ""), "gameSlug": game.get("slug", ""),
                        "categoryName": category.get("name") or cat.get("name", ""),
                        "title": rus.get("briefDescription") or offer.get("title") or category.get("name") or public_id,
                    }
            if not isinstance(data, dict) or not isinstance(data.get("hasMore"), bool):
                raise StarvellApiError("API каталога не вернул признак hasMore.")
            if data["hasMore"] is False:
                return list(found.values())
            if not categories:
                raise StarvellApiError("API вернул пустую страницу каталога с hasMore=true.")
        raise StarvellApiError("Слишком много категорий товаров.")

    async def collect_active_seller_offers(self, username: str | None = None) -> list[dict]:
        user = await self.get_current_user()
        if username and username.lower() != str(user["username"]).lower():
            raise StarvellApiError("Можно выбирать только товары подключённого аккаунта.")
        return await self.collect_profile_page_offers(user["username"])

    async def collect_seller_offers_from_category_pages(self, username: str, category_pages: list, *, include_inactive: bool = False) -> list[dict]:
        # Compatibility entry point. There is no category scan or HTML fallback.
        user = await self.get_current_user()
        if username.lower() != str(user["username"]).lower():
            raise StarvellApiError("Запрошены товары другого аккаунта.")
        return await self.collect_profile_page_offers(username, include_inactive=include_inactive)

    async def get_account_offers(self) -> dict:
        return {"pageProps": {"offers": await self.collect_active_seller_offers()}}

    async def collect_auto_bump_groups(self, username: str | None = None) -> dict[int, set[int]]:
        result: dict[int, set[int]] = {}
        for offer in await self.collect_active_seller_offers(username):
            game = _to_int_safe(offer.get("gameId"))
            category = _to_int_safe(offer.get("categoryId"))
            if game > 0 and category > 0:
                result.setdefault(game, set()).add(category)
        return result

    async def update_offer_by_public_id(self, offer_public_id: str | dict, payload: dict) -> Any:
        public_id = ensure_offer_public_id(offer_public_id)
        return await self._api("POST", f"/offers/{public_id}/update", payload)

    async def partial_update_offer_by_public_id(self, offer_public_id: str | dict, payload: dict) -> Any:
        public_id = ensure_offer_public_id(offer_public_id)
        return await self._api("POST", f"/offers/{public_id}/partial-update", payload)

    async def get_top_sellers(self) -> dict:
        raise StarvellApiError(
            "API общего рейтинга продавцов не подтверждено. Доступен поиск профиля по username."
        )
