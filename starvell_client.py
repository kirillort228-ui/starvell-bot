import asyncio
import html
import json
import re
import time
import secrets
import string
from typing import Any
from urllib.parse import urljoin, urlparse

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


def _extract_next_data_from_html(source: str) -> dict | None:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', source, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except Exception:
        return None


# get_profile_user_verified_public_no_cookie

def _make_client_socket_id(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class StarvellClient:
    """Client for Starvell Next.js JSON endpoints.

    Uses Cookie-based authorization from the browser session.
    Supports http://, https://, socks5:// proxies through httpx[socks].
    """

    def __init__(self, cookie: str, proxy_url: str | None = None):
        self.base_url = "https://starvell.com"
        self.cookie = cookie.strip()
        self.proxy_url = proxy_url
        self._build_id: str | None = None
        self._build_id_ts: float = 0

        self.client = httpx.AsyncClient(
            timeout=20,
            proxy=proxy_url,
            headers={
                "Accept": "*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/149.0.0.0 Safari/537.36"
                ),
                "X-Nextjs-Data": "1",
                "Referer": "https://starvell.com/",
                "Cookie": self.cookie,
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _json_request(self, url: str, *, params: dict | None = None) -> Any:
        try:
            response = await self.client.get(url, params=params)
        except httpx.ProxyError:
            raise StarvellApiError("Ошибка прокси. Проверь тип, IP, порт, логин и пароль.")
        except httpx.TimeoutException:
            raise StarvellApiError("Starvell долго не отвечает. Возможна проблема с прокси.")
        except httpx.RequestError as error:
            raise StarvellApiError(f"Ошибка запроса к Starvell: {error}")

        if response.status_code >= 400:
            raise StarvellApiError(f"Starvell вернул {response.status_code}: {response.text[:300]}")

        try:
            return response.json()
        except json.JSONDecodeError:
            raise StarvellApiError("Starvell вернул не JSON. Возможно, cookie устарели.")

    async def get_build_id(self) -> str:
        # Cache for 10 minutes. If site rebuilds, request methods will refresh it on retry in future versions.
        if self._build_id and time.time() - self._build_id_ts < 600:
            return self._build_id

        try:
            response = await self.client.get(self.base_url)
            response.raise_for_status()
        except Exception as error:
            raise StarvellApiError(f"Не удалось получить главную страницу Starvell: {error}")

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
        )
        if not match:
            raise StarvellApiError("Не удалось найти buildId Next.js на странице Starvell.")

        try:
            data = json.loads(match.group(1))
            build_id = data["buildId"]
        except Exception as error:
            raise StarvellApiError(f"Не удалось прочитать buildId: {error}")

        self._build_id = build_id
        self._build_id_ts = time.time()
        return build_id

    async def get_chats(self) -> dict:
        build_id = await self.get_build_id()
        url = f"{self.base_url}/_next/data/{build_id}/chat.json"
        return await self._json_request(url)

    async def get_orders(self) -> dict:
        """
        Starvell can return an empty orders list from /account/orders.json depending on tab/filter.
        Try the default endpoint first, then several common seller/history variants.
        The first response with a non-empty pageProps.orders is returned.
        """
        build_id = await self.get_build_id()
        candidates = [
            ("account/orders", f"{self.base_url}/_next/data/{build_id}/account/orders.json", None),
            ("account/orders?type=sales", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"type": "sales"}),
            ("account/orders?type=sell", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"type": "sell"}),
            ("account/orders?role=seller", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"role": "seller"}),
            ("account/orders?tab=sales", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"tab": "sales"}),
            ("account/orders?tab=sell", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"tab": "sell"}),
            ("account/orders?seller=true", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"seller": "true"}),
            ("account/orders?status=completed", f"{self.base_url}/_next/data/{build_id}/account/orders.json", {"status": "completed"}),
            ("account/orders/sales", f"{self.base_url}/_next/data/{build_id}/account/orders/sales.json", None),
            ("account/orders/sell", f"{self.base_url}/_next/data/{build_id}/account/orders/sell.json", None),
            ("account/sales", f"{self.base_url}/_next/data/{build_id}/account/sales.json", None),
            ("account/sells", f"{self.base_url}/_next/data/{build_id}/account/sells.json", None),
            ("account/orders/seller", f"{self.base_url}/_next/data/{build_id}/account/orders/seller.json", None),
            ("account/orders/history", f"{self.base_url}/_next/data/{build_id}/account/orders/history.json", None),
            ("account/orders/completed", f"{self.base_url}/_next/data/{build_id}/account/orders/completed.json", None),
        ]

        first_data = None
        first_source = None
        for source, url, params in candidates:
            try:
                data = await self._json_request(url, params=params)
                if isinstance(data, dict):
                    data["_ordersSource"] = source
                    if isinstance(data.get("pageProps"), dict):
                        data["pageProps"]["_ordersSource"] = source
                if first_data is None:
                    first_data = data
                    first_source = source

                orders = (data.get("pageProps", {}) if isinstance(data, dict) else {}).get("orders") or []
                if orders:
                    return data
            except Exception:
                continue

        if isinstance(first_data, dict):
            first_data["_ordersSource"] = first_source or "account/orders"
            if isinstance(first_data.get("pageProps"), dict):
                first_data["pageProps"]["_ordersSource"] = first_source or "account/orders"
            return first_data

        url = f"{self.base_url}/_next/data/{build_id}/account/orders.json"
        return await self._json_request(url)



    async def _post_json_request(self, url: str, *, json_data: dict | None = None) -> Any:
        try:
            response = await self.client.post(
                url,
                json=json_data or {},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "X-Nextjs-Data": "0",
                    "Referer": "https://starvell.com/chat",
                    "Cookie": self.cookie,
                },
            )
        except httpx.ProxyError:
            raise StarvellApiError("Ошибка прокси. Проверь тип, IP, порт, логин и пароль.")
        except httpx.TimeoutException:
            raise StarvellApiError("Starvell долго не отвечает. Возможна проблема с прокси.")
        except httpx.RequestError as error:
            raise StarvellApiError(f"Ошибка POST-запроса к Starvell: {error}")

        if response.status_code >= 400:
            raise StarvellApiError(f"Starvell вернул {response.status_code}: {response.text[:300]}")

        try:
            return response.json()
        except json.JSONDecodeError:
            return {"ok": True, "text": response.text[:300]}

    async def find_chat_id_by_user(self, buyer_id: int | None = None, buyer_username: str | None = None) -> str | None:
        data = await self.get_chats()
        chats = data.get("pageProps", {}).get("chats") or []
        buyer_username_l = (buyer_username or "").strip().lower()
        for chat in chats:
            participants = chat.get("participants") or []
            for participant in participants:
                user = participant.get("user") if isinstance(participant, dict) else participant
                if not isinstance(user, dict):
                    continue
                if buyer_id is not None and int(user.get("id") or 0) == int(buyer_id):
                    return str(chat.get("id"))
                if buyer_username_l and str(user.get("username") or "").strip().lower() == buyer_username_l:
                    return str(chat.get("id"))
        return None

    async def send_chat_message(self, chat_id: str, content: str, client_socket_id: str | None = None) -> Any:
        """
        Real Starvell message sender from browser Network:
        POST https://starvell.com/api/messages/send
        Payload:
        {
            "chatId": "...",
            "clientSocketId": "...",
            "content": "..."
        }
        """
        chat_id = str(chat_id).strip()
        content = str(content or "").strip()
        if not chat_id:
            raise StarvellApiError("chatId пустой, сообщение не отправлено.")
        if not content:
            raise StarvellApiError("content пустой, сообщение не отправлено.")

        payload = {
            "chatId": chat_id,
            "clientSocketId": client_socket_id or _make_client_socket_id(),
            "content": content,
        }
        return await self._post_json_request(
            f"{self.base_url}/api/messages/send",
            json_data=payload,
        )



    async def _put_json_request(self, url: str, *, json_data: dict | None = None) -> Any:
        try:
            response = await self.client.put(
                url,
                json=json_data or {},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "X-Nextjs-Data": "0",
                    "Referer": "https://starvell.com/account/offers",
                    "Cookie": self.cookie,
                },
            )
        except httpx.ProxyError:
            raise StarvellApiError("Ошибка прокси. Проверь тип, IP, порт, логин и пароль.")
        except httpx.TimeoutException:
            raise StarvellApiError("Starvell долго не отвечает. Возможна проблема с прокси.")
        except httpx.RequestError as error:
            raise StarvellApiError(f"Ошибка PUT-запроса к Starvell: {error}")

        if response.status_code >= 400:
            raise StarvellApiError(f"Starvell вернул {response.status_code}: {response.text[:300]}")

        try:
            return response.json()
        except json.JSONDecodeError:
            return {"ok": True, "text": response.text[:300]}


    async def bump_offers(self, game_id: int, category_ids: list[int]) -> Any:
        """
        Real Starvell endpoint from browser Network:
        POST https://starvell.com/api/offers/bump
        Payload:
        {
            "gameId": 16,
            "categoryIds": [208]
        }
        """
        try:
            game_id = int(game_id)
            category_ids = [int(x) for x in category_ids if str(x).strip()]
        except Exception:
            raise StarvellApiError("Неверный gameId или categoryIds для автоподнятия.")

        if not game_id or not category_ids:
            raise StarvellApiError("Для автоподнятия нужны gameId и categoryIds.")

        return await self._post_json_request(
            f"{self.base_url}/api/offers/bump",
            json_data={"gameId": game_id, "categoryIds": category_ids},
        )


    async def get_offer_by_public_id(self, offer_public_id: str | dict) -> Any:
        """
        New Starvell API format:
        GET /offers/{offerPublicId}
        """
        public_id = ensure_offer_public_id(offer_public_id)
        url = f"{self.base_url}/offers/{public_id}"
        return await self._json_request(url)

    async def update_offer_by_public_id(self, offer_public_id: str | dict, payload: dict) -> Any:
        """
        New Starvell API format:
        POST /offers/{offerPublicId}/update
        """
        public_id = ensure_offer_public_id(offer_public_id)
        url = f"{self.base_url}/offers/{public_id}/update"
        return await self._post_json_request(url, json_data=payload)

    async def partial_update_offer_by_public_id(self, offer_public_id: str | dict, payload: dict) -> Any:
        """
        New Starvell API format:
        POST /offers/{offerPublicId}/partial-update
        """
        public_id = ensure_offer_public_id(offer_public_id)
        url = f"{self.base_url}/offers/{public_id}/partial-update"
        return await self._post_json_request(url, json_data=payload)

    async def raise_offer_by_public_id(self, offer_public_id: str | dict) -> Any:
        """
        Best-effort route for future auto-raise feature.
        Uses offerPublicId only; never numeric offerId.
        """
        public_id = ensure_offer_public_id(offer_public_id)
        candidates = [
            (f"{self.base_url}/offers/{public_id}/raise", {}),
            (f"{self.base_url}/offers/{public_id}/up", {}),
            (f"{self.base_url}/offers/{public_id}/bump", {}),
        ]
        last_error = None
        for url, payload in candidates:
            try:
                return await self._post_json_request(url, json_data=payload)
            except StarvellApiError as error:
                last_error = error
                continue
        raise StarvellApiError(f"Не удалось поднять предложение через offerPublicId. Последняя ошибка: {last_error}")


    async def get_next_data_path(self, path: str, params: dict | None = None) -> dict:
        build_id = await self.get_build_id()
        clean_path = path.strip("/")
        url = f"{self.base_url}/_next/data/{build_id}/{clean_path}.json"
        return await self._json_request(url, params=params)

    async def _text_request(self, url: str) -> str:
        try:
            response = await self.client.get(url, headers={"X-Nextjs-Data": "0"})
        except httpx.ProxyError:
            raise StarvellApiError("Ошибка прокси. Проверь тип, IP, порт, логин и пароль.")
        except httpx.TimeoutException:
            raise StarvellApiError("Starvell долго не отвечает. Возможна проблема с прокси.")
        except httpx.RequestError as error:
            raise StarvellApiError(f"Ошибка запроса к Starvell: {error}")

        if response.status_code >= 400:
            raise StarvellApiError(f"Starvell вернул {response.status_code}: {response.text[:300]}")
        return response.text

    def _clean_html_text(self, value: str) -> str:
        value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
        value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
        value = re.sub(r"<[^>]+>", "\n", value)
        value = html.unescape(value)
        value = re.sub(r"[\t\r ]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value

    def _extract_marketplace_links(self, source: str) -> list[str]:
        links: list[str] = []
        blocked = (
            "/profile", "/chat", "/account", "/login", "/orders", "/tickets",
            "/rules", "/privacy", "/offer", "/support", "/api", "/_next",
        )
        for href in re.findall(r'href=["\']([^"\']+)["\']', source, flags=re.I):
            if not href.startswith("/"):
                continue
            if any(href.startswith(prefix) for prefix in blocked):
                continue
            if href == "/" or "." in href.rsplit("/", 1)[-1]:
                continue
            if len(href.strip("/").split("/")) < 2:
                continue
            if href not in links:
                links.append(href)
        return links

    def _add_seller_candidate(self, sellers: dict[str, dict], username: str, rating, reviews, completion=None) -> None:
        username = html.unescape(str(username)).strip()
        if not username or len(username) < 2:
            return
        if username.lower() in {"image", "starvell", "global", "все", "фильтры"}:
            return
        try:
            reviews_int = int(str(reviews).replace(" ", ""))
        except Exception:
            reviews_int = 0
        try:
            rating_float = float(str(rating).replace(",", "."))
        except Exception:
            rating_float = 0.0
        if reviews_int <= 0 or rating_float <= 0:
            return
        old = sellers.get(username.lower())
        item = {
            "username": username,
            "rating": rating_float,
            "reviewsCount": reviews_int,
            "completionRate": completion,
            "isBanned": False,
            "kycStatus": "VERIFIED",
            "source": "marketplace",
        }
        if not old or reviews_int > int(old.get("reviewsCount") or 0):
            sellers[username.lower()] = item

    def _extract_sellers_from_page(self, source: str) -> dict[str, dict]:
        sellers: dict[str, dict] = {}

        # 1) Try to parse data embedded in scripts/Next.js JSON.
        # Starvell item objects often contain seller/user objects with username, rating and reviewsCount.
        user_blocks = re.findall(r'\{[^{}]{0,1200}?"username"\s*:\s*"([^"\\]+)"[^{}]{0,1200}?\}', source)
        for username in user_blocks:
            pos = source.find(f'"username":"{username}"')
            block = source[max(0, pos - 1000): pos + 2000]
            rating_match = re.search(r'"(?:rating|sellerRating|stars)"\s*:\s*([0-9]+(?:\.[0-9]+)?)', block)
            reviews_match = re.search(r'"(?:reviewsCount|reviewCount|reviews)"\s*:\s*([0-9]+)', block)
            if rating_match and reviews_match:
                self._add_seller_candidate(sellers, username, rating_match.group(1), reviews_match.group(1))

        # 2) Parse visible SSR text. Pattern from marketplace rows:
        # seller_name / rating / "123 отзыва, 95.00% выполнено" / price.
        text = self._clean_html_text(source)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for i in range(1, len(lines) - 2):
            username = lines[i]
            rating = lines[i + 1]
            reviews_line = lines[i + 2]
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{1,31}", username):
                continue
            if not re.fullmatch(r"\d+(?:[\.,]\d+)?", rating):
                continue
            reviews_match = re.search(r"([0-9][0-9\s]*)\s+отзыв", reviews_line, flags=re.I)
            if not reviews_match:
                continue
            completion_match = re.search(r"([0-9]+(?:[\.,][0-9]+)?)%\s*выполн", reviews_line, flags=re.I)
            completion = None
            if completion_match:
                completion = float(completion_match.group(1).replace(",", "."))
            self._add_seller_candidate(sellers, username, rating, reviews_match.group(1), completion)

        return sellers

    async def collect_marketplace_top_sellers(self, *, max_pages: int = 18, limit: int = 1000) -> list[dict]:
        """
        Builds seller top from public marketplace pages, then verifies each seller
        through the public profile JSON. This fixes wrong duplicated review counts
        caused by parsing marketplace cards only.
        """
        seed_paths = [
            "/roblox/packages",
            "/roblox/accounts",
            "/roblox/items",
            "/roblox/services",
            "/roblox/gift-cards",
            "/telegram/accounts",
            "/telegram/channels",
            "/steam/accounts",
            "/minecraft/accounts",
            "/fortnite/accounts",
            "/brawl-stars/accounts",
            "/pubg-mobile/accounts",
        ]
        queue = list(seed_paths)
        visited: set[str] = set()
        candidates: dict[str, dict] = {}

        while queue and len(visited) < max_pages:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            url = urljoin(self.base_url, path)
            try:
                source = await self._text_request(url)
            except Exception:
                continue

            for key, seller in self._extract_sellers_from_page(source).items():
                username = str(seller.get("username") or "").strip()
                if not username:
                    continue
                old = candidates.get(username.lower())
                if not old or int(seller.get("reviewsCount") or 0) > int(old.get("reviewsCount") or 0):
                    candidates[username.lower()] = seller

            if len(visited) <= 4:
                for link in self._extract_marketplace_links(source):
                    if link not in visited and link not in queue and len(queue) < max_pages * 3:
                        queue.append(link)

        # Verify sellers through profile JSON. This is slower, but much more accurate.
        enriched: dict[str, dict] = {}
        candidate_items = list(candidates.values())[: min(len(candidates), 260)]
        for seller in candidate_items:
            username = str(seller.get("username") or "").strip()
            if not username:
                continue
            try:
                profile_user = await self.get_profile_user_verified(username)
                if int(profile_user.get("reviewsCount") or 0) > 0 or profile_user.get("rating") is not None:
                    enriched[username.lower()] = {
                        **seller,
                        **profile_user,
                        "source": "profile-json",
                    }
                    continue
            except Exception:
                pass
            # Do NOT trust marketplace review counters here: Starvell category pages can duplicate
            # wrong huge review counts between sellers. Keep the seller, but reset unverified reviews.
            enriched[username.lower()] = {
                **seller,
                "reviewsCount": 0,
                "rating": None,
                "source": "profile-unverified",
            }

        result = sorted(
            enriched.values(),
            key=lambda item: (int(item.get("reviewsCount") or 0), float(item.get("rating") or 0)),
            reverse=True,
        )
        for rank, seller in enumerate(result, start=1):
            seller["rank"] = rank
        return result[:limit]



    async def get_trade_page(self, game_slug: str, category_slug: str | None = None) -> dict:
        """
        Exact Starvell Next.js route format observed in browser Network.

        Category example:
        /_next/data/{buildId}/steam/accounts/trade.json
        ?game=steam&game=accounts&game=trade

        The repeated `game` query parameters are required because the page uses
        a Next.js catch-all route. A normal `category=accounts` parameter does not work.
        """
        build_id = await self.get_build_id()
        game_slug = str(game_slug or "").strip().strip("/")
        category_slug = str(category_slug or "").strip().strip("/") or None

        if not game_slug:
            raise StarvellApiError("Не указан slug игры для страницы товаров.")

        if category_slug:
            url = (
                f"{self.base_url}/_next/data/{build_id}/"
                f"{game_slug}/{category_slug}/trade.json"
            )
            params = [
                ("game", game_slug),
                ("game", category_slug),
                ("game", "trade"),
            ]
        else:
            # A category route is the reliable form. For the game root we first try
            # /{game}/trade.json, then callers can fall back to a known category.
            url = f"{self.base_url}/_next/data/{build_id}/{game_slug}/trade.json"
            params = [
                ("game", game_slug),
                ("game", "trade"),
            ]

        return await self._json_request(url, params=params)


    async def get_trade_page_robust(self, game_slug: str, category_slug: str | None = None) -> dict:
        """
        Opens Starvell trade data using the exact nested Next.js route.
        If a game-root route is unavailable, it opens a known category page,
        which still contains pageProps.game.categories for discovering all categories.
        """
        errors = []

        try:
            return await self.get_trade_page(game_slug, category_slug)
        except Exception as error:
            errors.append(str(error))

        # Known seed categories let us discover all categories dynamically.
        if not category_slug:
            seed_by_game = {
                "steam": "accounts",
                "roblox": "accounts",
                "telegram": "accounts",
                "minecraft": "accounts",
                "fortnite": "accounts",
                "brawl-stars": "accounts",
                "pubg-mobile": "accounts",
                "valorant": "accounts",
                "genshin-impact": "accounts",
                "world-of-tanks": "accounts",
            }
            seed_category = seed_by_game.get(game_slug)
            if seed_category:
                try:
                    return await self.get_trade_page(game_slug, seed_category)
                except Exception as error:
                    errors.append(str(error))

        html_candidates = []
        if category_slug:
            html_candidates.extend([
                f"{self.base_url}/{game_slug}/{category_slug}/trade",
                f"{self.base_url}/{game_slug}/{category_slug}",
            ])
        else:
            html_candidates.extend([
                f"{self.base_url}/{game_slug}/accounts/trade",
                f"{self.base_url}/{game_slug}/trade",
                f"{self.base_url}/{game_slug}",
            ])

        for url in html_candidates:
            try:
                source = await self._text_request(url)
                next_data = _extract_next_data_from_html(source)
                if isinstance(next_data, dict):
                    return next_data
            except Exception as error:
                errors.append(str(error))

        raise StarvellApiError(
            "Не удалось открыть страницу товаров Starvell: " + "; ".join(errors[-4:])
        )


    async def collect_bump_groups_from_open_trade(self, my_user_id: int | None = None) -> dict[int, set[int]]:
        """
        Scans public trade pages and returns bump groups.

        Important behavior:
        - if it finds at least one active offer of the seller in a game,
          it adds ALL categoryIds of that game, not only the category where the offer was found.
        This fixes the problem where only one category such as 208 was bumped.
        """
        groups: dict[int, set[int]] = {}
        pages_to_scan: list[tuple[str, str | None]] = []

        # Known game slugs that commonly have trade/lot categories.
        seed_games = [
            "steam",
            "roblox",
            "telegram",
            "minecraft",
            "fortnite",
            "brawl-stars",
            "pubg-mobile",
            "valorant",
            "genshin-impact",
            "world-of-tanks",
        ]
        for slug in seed_games:
            pages_to_scan.append((slug, None))

        seen_pages: set[tuple[str, str | None]] = set()
        pages_checked = 0

        while pages_to_scan and pages_checked < 80:
            game_slug, category_slug = pages_to_scan.pop(0)
            key = (game_slug, category_slug)
            if key in seen_pages:
                continue
            seen_pages.add(key)
            pages_checked += 1

            try:
                data = await self.get_trade_page_robust(game_slug, category_slug)
            except Exception:
                continue

            # First collect exact seller offer categories from this page.
            seller_groups = extract_bump_groups_from_data(data, my_user_id=my_user_id)
            for game_id, category_ids in seller_groups.items():
                groups.setdefault(game_id, set()).update(category_ids)

            # If seller has at least one offer in this game, add all categories of that game.
            if seller_groups:
                all_categories_for_game = extract_game_category_groups_from_trade_data(data)
                for game_id, category_ids in all_categories_for_game.items():
                    if game_id in seller_groups:
                        groups.setdefault(game_id, set()).update(category_ids)

            # Queue category pages for this game, so seller's offer can be discovered in other categories too.
            page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
            game = page_props.get("game", {}) if isinstance(page_props.get("game"), dict) else {}
            for category in game.get("categories", []) or []:
                if not isinstance(category, dict):
                    continue
                slug = category.get("slug")
                if slug:
                    next_key = (game_slug, str(slug))
                    if next_key not in seen_pages and next_key not in pages_to_scan:
                        pages_to_scan.append(next_key)

        return groups

    async def collect_auto_bump_groups(self, username: str | None = None) -> dict[int, set[int]]:
        """
        Automatically finds the seller's active offer categories and returns:
        {gameId: {categoryId, ...}}

        Sources:
        1. account/offers JSON if available;
        2. profile JSON;
        3. chat JSON;
        4. public trade pages as fallback.
        """
        groups: dict[int, set[int]] = {}
        my_user_id: int | None = None

        # Current account user is usually available from chats JSON.
        try:
            chats_data = await self.get_chats()
            page_props = chats_data.get("pageProps", {}) if isinstance(chats_data, dict) else {}
            user = page_props.get("user", {}) if isinstance(page_props.get("user"), dict) else {}
            if user.get("id") is not None:
                my_user_id = int(user.get("id"))
            for game_id, category_ids in extract_bump_groups_from_data(chats_data, my_user_id=my_user_id).items():
                groups.setdefault(game_id, set()).update(category_ids)
        except Exception:
            pass

        # Account offers endpoint may exist/change; use it when available.
        try:
            offers_data = await self.get_account_offers()
            for game_id, category_ids in extract_bump_groups_from_data(offers_data, my_user_id=my_user_id).items():
                groups.setdefault(game_id, set()).update(category_ids)
        except Exception:
            pass

        if username:
            try:
                profile_data = await self.get_profile(username)
                if my_user_id is None:
                    page_props = profile_data.get("pageProps", {}) if isinstance(profile_data, dict) else {}
                    user = page_props.get("user", {}) if isinstance(page_props.get("user"), dict) else {}
                    if user.get("id") is not None:
                        my_user_id = int(user.get("id"))
                for game_id, category_ids in extract_bump_groups_from_data(profile_data, my_user_id=my_user_id).items():
                    groups.setdefault(game_id, set()).update(category_ids)
            except Exception:
                pass

        # Fallback for the Steam trade pages like trade.json?game=steam&category=keys.
        try:
            trade_groups = await self.collect_bump_groups_from_open_trade(my_user_id=my_user_id)
            for game_id, category_ids in trade_groups.items():
                groups.setdefault(game_id, set()).update(category_ids)
        except Exception:
            pass

        groups = await self.expand_bump_groups_with_trade_categories(groups)
        return groups



    async def expand_bump_groups_with_trade_categories(self, groups: dict[int, set[int]]) -> dict[int, set[int]]:
        """
        For found games, open public trade pages and add all categoryIds of those games.
        Currently Starvell bump endpoint works by categoryIds, so this makes bump cover all categories in the game.
        """
        if not groups:
            return groups

        game_slug_by_id = {
            16: "steam",
        }

        expanded: dict[int, set[int]] = {int(game_id): set(category_ids) for game_id, category_ids in groups.items()}
        for game_id in list(expanded.keys()):
            slug = game_slug_by_id.get(int(game_id))
            if not slug:
                continue
            try:
                data = await self.get_trade_page(slug)
                all_categories = extract_game_category_groups_from_trade_data(data)
                if int(game_id) in all_categories:
                    expanded.setdefault(int(game_id), set()).update(all_categories[int(game_id)])
            except Exception:
                pass
        return expanded




    async def discover_public_game_slugs(self) -> set[str]:
        """
        Finds game slugs from public Starvell HTML links.
        This avoids a hardcoded Steam-only list.
        """
        slugs: set[str] = set()
        pages = [
            self.base_url,
            f"{self.base_url}/trade",
            f"{self.base_url}/catalog",
        ]
        ignored = {
            "api", "_next", "profile", "account", "chat", "tickets", "support",
            "login", "register", "terms", "privacy", "about", "news",
        }

        for url in pages:
            try:
                source = await self._text_request(url)
            except Exception:
                continue

            # Common nested trade/category links: /steam/accounts/trade
            for match in re.finditer(
                r'href=["\']/([a-z0-9][a-z0-9-]{1,60})/([a-z0-9][a-z0-9-]{1,60})/trade(?:[?#"\']|$)',
                source,
                flags=re.I,
            ):
                slug = match.group(1).lower()
                if slug not in ignored:
                    slugs.add(slug)

            # Game root links can also reveal slugs.
            for match in re.finditer(
                r'href=["\']/([a-z0-9][a-z0-9-]{1,60})(?:/|["\'])',
                source,
                flags=re.I,
            ):
                slug = match.group(1).lower()
                if slug not in ignored:
                    slugs.add(slug)

        return slugs

    async def discover_seller_game_slugs(self, username: str | None = None) -> dict[int, str]:
        """
        Discovers games dynamically from:
        - authenticated chats;
        - account offers;
        - seller profile JSON/HTML;
        - public Starvell game links.

        Returns {gameId: gameSlug}. Unknown public slugs are returned later
        through the traversal even before their numeric ID is known.
        """
        discovered: dict[int, str] = {}
        datasets: list[dict] = []

        try:
            datasets.append(await self.get_chats())
        except Exception:
            pass
        try:
            datasets.append(await self.get_account_offers())
        except Exception:
            pass
        if username:
            try:
                datasets.append(await self.get_profile(username))
            except Exception:
                pass
            try:
                source = await self._text_request(f"{self.base_url}/profile/{username}")
                next_data = _extract_next_data_from_html(source)
                if next_data:
                    datasets.append(next_data)
            except Exception:
                pass

        for data in datasets:
            discovered.update(extract_game_slugs_from_data(data))

        return discovered


    async def collect_seller_offers_from_category_pages(
        self,
        username: str,
        category_pages: list[tuple[int, int, str, str, str]],
        *,
        include_inactive: bool = False,
    ) -> list[dict]:
        """Перебирает переданные trade.json и возвращает товары текущего продавца."""
        profile = await self.get_profile_user(username)
        profile_user_id = None
        if isinstance(profile, dict):
            profile_user_id = (
                profile.get("id")
                or profile.get("userId")
                or (profile.get("user") or {}).get("id")
            )
        try:
            profile_user_id = int(profile_user_id) if profile_user_id is not None else None
        except Exception:
            profile_user_id = None

        build_id = await self.get_build_id()
        found: dict[str, dict] = {}

        semaphore = asyncio.Semaphore(6)

        async def fetch_category_page(item):
            expected_game_id, expected_category_id, game_slug, category_slug, title = item
            url = (
                f"https://starvell.com/_next/data/{build_id}/"
                f"{game_slug}/{category_slug}/trade.json"
            )
            params = [("game", game_slug), ("game", category_slug), ("game", "trade")]
            async with semaphore:
                try:
                    response = await self._request("GET", url, params=params)
                    payload = response.json()
                except Exception:
                    return item, []

            page_props = payload.get("pageProps") if isinstance(payload, dict) else None
            offers = page_props.get("offers") if isinstance(page_props, dict) else None
            return item, offers if isinstance(offers, list) else []

        category_results = await asyncio.gather(
            *(fetch_category_page(item) for item in category_pages)
        )

        for category_item, offers in category_results:
            expected_game_id, expected_category_id, game_slug, category_slug, title = category_item
            for offer in offers:
                if not isinstance(offer, dict):
                    continue

                try:
                    offer_user_id = int(offer.get("userId")) if offer.get("userId") is not None else None
                except Exception:
                    offer_user_id = None

                # Основная проверка владельца — userId профиля. Если Starvell не вернул id
                # профиля, оставляем только лоты с совпадающими game/category из страницы.
                if profile_user_id is not None and offer_user_id != profile_user_id:
                    continue

                if not include_inactive:
                    if offer.get("isActive") is False:
                        continue
                    if offer.get("isHidden") is True:
                        continue
                    if str(offer.get("visibility") or "").upper() not in {"", "PUBLIC"}:
                        continue

                public_id = str(
                    offer.get("publicId")
                    or offer.get("offerPublicId")
                    or offer.get("id")
                    or ""
                ).strip()
                if not public_id:
                    continue

                normalized = dict(offer)
                normalized["publicId"] = public_id
                normalized["offerPublicId"] = public_id
                normalized["gameId"] = offer.get("gameId") or expected_game_id
                normalized["categoryId"] = offer.get("categoryId") or expected_category_id
                normalized["_categoryTitle"] = title
                found[public_id] = normalized

        # trade.json может вернуть пустой offers даже при наличии товаров.
        # Поэтому объединяем результаты полного перебора категорий с личными
        # источниками подключённого аккаунта и профиля.
        try:
            profile_offers = await self.collect_active_seller_offers(username)
        except Exception:
            profile_offers = []

        category_titles = {
            (int(game_id), int(category_id)): title
            for game_id, category_id, _game_slug, _category_slug, title in category_pages
        }

        for offer in profile_offers:
            if not isinstance(offer, dict):
                continue

            public_id = str(
                offer.get("publicId")
                or offer.get("offerPublicId")
                or offer.get("id")
                or ""
            ).strip()
            if not public_id:
                continue

            if not include_inactive:
                if offer.get("isActive") is False:
                    continue
                if offer.get("isHidden") is True:
                    continue
                if str(offer.get("visibility") or "").upper() not in {"", "PUBLIC"}:
                    continue

            normalized = dict(offer)
            normalized["publicId"] = public_id
            normalized["offerPublicId"] = public_id

            try:
                key = (
                    int(normalized.get("gameId") or 0),
                    int(normalized.get("categoryId") or 0),
                )
            except Exception:
                key = (0, 0)

            normalized.setdefault(
                "_categoryTitle",
                category_titles.get(key, "Товар из профиля Starvell"),
            )
            found[public_id] = normalized

        return list(found.values())

    async def collect_active_seller_offers(self, username: str | None = None) -> list[dict]:
        """
        Finds active offers belonging to the connected seller across ALL discovered games/categories.
        """
        seller_username = str(username or "").strip()
        seller_username_l = seller_username.lower()
        seller_user_id: int | None = None
        datasets: list[dict] = []

        # Account/profile datasets.
        for getter in (
            self.get_chats,
            lambda: self.get_profile(seller_username) if seller_username else self.get_chats(),
        ):
            try:
                data = await getter()
                datasets.append(data)
                page_props = data.get("pageProps", {}) if isinstance(data, dict) else {}
                page_user = page_props.get("user", {}) if isinstance(page_props.get("user"), dict) else {}
                page_username = str(page_user.get("username") or "").strip().lower()
                if page_user.get("id") is not None and (
                    not seller_username_l or not page_username or page_username == seller_username_l
                ):
                    seller_user_id = int(page_user.get("id"))
            except Exception:
                pass

        try:
            datasets.append(await self.get_account_offers())
        except Exception:
            pass

        if seller_username:
            try:
                profile_data = await self.get_profile(seller_username)
                datasets.append(profile_data)
            except Exception:
                pass
            try:
                source = await self._text_request(f"{self.base_url}/profile/{seller_username}")
                next_data = _extract_next_data_from_html(source)
                if next_data:
                    datasets.append(next_data)
            except Exception:
                pass

        def offer_owner_matches(obj: dict, *, trusted_account_payload: bool = False) -> bool:
            owner_id = obj.get("userId") or obj.get("sellerId") or obj.get("ownerId")
            if seller_user_id is not None and owner_id is not None:
                try:
                    return int(owner_id) == int(seller_user_id)
                except Exception:
                    return False

            owner_username = str(
                obj.get("sellerUsername")
                or ((obj.get("seller") or {}).get("username") if isinstance(obj.get("seller"), dict) else "")
                or ((obj.get("user") or {}).get("username") if isinstance(obj.get("user"), dict) else "")
                or ""
            ).strip().lower()
            if seller_username_l and owner_username:
                return owner_username == seller_username_l

            return trusted_account_payload and owner_id is None and not owner_username

        def extract_offers(data: dict, *, trusted_account_payload: bool = False) -> list[dict]:
            result: list[dict] = []
            for obj in _iter_dicts(data):
                if not isinstance(obj, dict):
                    continue

                public_id = obj.get("offerPublicId") or obj.get("publicId") or obj.get("offer_public_id")
                game_id = obj.get("gameId") or obj.get("game_id")
                category_id = obj.get("categoryId") or obj.get("category_id")
                taxonomy = obj.get("taxonomySnapshot") if isinstance(obj.get("taxonomySnapshot"), dict) else {}
                if not game_id and isinstance(taxonomy.get("game"), dict):
                    game_id = taxonomy["game"].get("id")
                if not category_id and isinstance(taxonomy.get("category"), dict):
                    category_id = taxonomy["category"].get("id")

                if not public_id or not game_id or not category_id:
                    continue
                if not offer_owner_matches(obj, trusted_account_payload=trusted_account_payload):
                    continue
                if obj.get("isActive") is False or obj.get("isHidden") is True:
                    continue
                if str(obj.get("visibility") or "").upper() in {"PRIVATE", "HIDDEN"}:
                    continue

                moderation = str(obj.get("moderationStatus") or "").upper()
                if moderation and moderation not in {"APPROVED", "ACTIVE", "PUBLISHED", "PENDING"}:
                    continue

                descriptions = obj.get("descriptions") if isinstance(obj.get("descriptions"), dict) else {}
                rus = descriptions.get("rus") if isinstance(descriptions.get("rus"), dict) else {}
                taxonomy_game = taxonomy.get("game") if isinstance(taxonomy.get("game"), dict) else {}
                taxonomy_category = taxonomy.get("category") if isinstance(taxonomy.get("category"), dict) else {}
                title = (
                    rus.get("briefDescription")
                    or obj.get("title")
                    or obj.get("name")
                    or f"Товар {public_id}"
                )
                result.append({
                    "offerPublicId": str(public_id),
                    "categoryId": str(category_id),
                    "gameId": str(game_id),
                    "gameSlug": str(taxonomy_game.get("slug") or ""),
                    "gameName": str(taxonomy_game.get("name") or ""),
                    "categoryName": str(taxonomy_category.get("name") or ""),
                    "title": str(title)[:100],
                    "isActive": True,
                    "moderationStatus": moderation or "UNKNOWN",
                })
            return result

        found: dict[str, dict] = {}
        game_slugs: dict[int, str] = {}

        # First extract from account/profile payloads.
        for index, data in enumerate(datasets):
            for item in extract_offers(data, trusted_account_payload=index < 3):
                found[item["offerPublicId"]] = item
                try:
                    gid = int(item.get("gameId") or 0)
                    gsl = str(item.get("gameSlug") or "").strip()
                    if gid > 0 and gsl:
                        game_slugs[gid] = gsl
                except Exception:
                    pass
            game_slugs.update(extract_game_slugs_from_data(data))

        # Dynamic discovery from seller data and public site links.
        try:
            game_slugs.update(await self.discover_seller_game_slugs(seller_username))
        except Exception:
            pass

        public_slugs: set[str] = set(game_slugs.values())
        try:
            public_slugs.update(await self.discover_public_game_slugs())
        except Exception:
            pass

        # Keep only reasonable candidates; Steam remains a guaranteed fallback.
        public_slugs.add("steam")
        public_slugs = {
            slug for slug in public_slugs
            if slug and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", slug)
        }

        # For each game, open a seed category, discover its category list, then scan all categories.
        async def scan_game(game_slug: str) -> list[dict]:
            local_found: list[dict] = []
            try:
                base_data = await self.get_trade_page_robust(game_slug)
            except Exception:
                return local_found

            local_found.extend(extract_offers(base_data))

            page_props = base_data.get("pageProps", {}) if isinstance(base_data, dict) else {}
            game = page_props.get("game", {}) if isinstance(page_props.get("game"), dict) else {}
            category_slugs = [
                str(category.get("slug"))
                for category in (game.get("categories", []) or [])
                if isinstance(category, dict) and category.get("slug")
            ]

            async def fetch_category(slug: str):
                try:
                    return await self.get_trade_page_robust(game_slug, slug)
                except Exception:
                    return None

            category_pages = await asyncio.gather(
                *(fetch_category(slug) for slug in category_slugs[:40]),
                return_exceptions=False,
            )
            for page in category_pages:
                if isinstance(page, dict):
                    local_found.extend(extract_offers(page))
            return local_found

        # Limit concurrency to avoid hammering Starvell.
        semaphore = asyncio.Semaphore(4)

        async def guarded_scan(slug: str):
            async with semaphore:
                return await scan_game(slug)

        scan_results = await asyncio.gather(
            *(guarded_scan(slug) for slug in sorted(public_slugs)[:60]),
            return_exceptions=True,
        )

        for batch in scan_results:
            if isinstance(batch, Exception):
                continue
            for item in batch:
                found[item["offerPublicId"]] = item

        return list(found.values())



    async def get_profile_user_verified(self, username: str) -> dict:
        """
        Returns profile data only if we can verify the requested username.
        Falls back from Next.js JSON to normal profile HTML/__NEXT_DATA__.
        """
        username_l = (username or "").strip().lower()
        errors = []

        try:
            data = await self.get_profile(username)
            user = find_profile_user_in_data(data, username)
            if str(user.get("username") or "").strip().lower() == username_l:
                return user
        except Exception as error:
            errors.append(str(error))

        try:
            source = await self._text_request(f"{self.base_url}/profile/{username}")
            next_data = _extract_next_data_from_html(source)
            if next_data:
                user = find_profile_user_in_data(next_data, username)
                if str(user.get("username") or "").strip().lower() == username_l:
                    return user
        except Exception as error:
            errors.append(str(error))

        # Last attempt: public profile without authenticated cookie.
        # This prevents Starvell from returning the logged-in account in pageProps.user.
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"},
                proxy=self.proxy_url,
            ) as public_client:
                response = await public_client.get(f"{self.base_url}/profile/{username}")
                response.raise_for_status()
                next_data = _extract_next_data_from_html(response.text)
                if next_data:
                    user = find_profile_user_in_data(next_data, username)
                    if str(user.get("username") or "").strip().lower() == username_l:
                        return user
        except Exception as error:
            errors.append(str(error))

        raise StarvellApiError("Не удалось подтвердить отзывы через профиль продавца: " + "; ".join(errors[-2:]))


    async def collect_marketplace_top_sellers_profile_only(self, *, max_pages: int = 18, limit: int = 1000) -> list[dict]:
        """
        Strict top sellers mode:
        1. Collect only usernames from open marketplace pages.
        2. Open the public profile for each username.
        3. Use ONLY reviewsCount/rating confirmed by that exact profile.
        4. Sellers whose profile can't be verified are skipped, not shown with category-page counters.
        """
        seed_paths = [
            "/roblox/packages",
            "/roblox/accounts",
            "/roblox/items",
            "/roblox/services",
            "/roblox/gift-cards",
            "/telegram/accounts",
            "/telegram/channels",
            "/steam/accounts",
            "/steam/trade",
            "/minecraft/accounts",
            "/fortnite/accounts",
            "/brawl-stars/accounts",
        ]
        queue = list(seed_paths)
        visited: set[str] = set()
        usernames: dict[str, str] = {}

        while queue and len(visited) < max_pages:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            url = urljoin(self.base_url, path)
            try:
                source = await self._text_request(url)
            except Exception:
                continue

            for key, seller in self._extract_sellers_from_page(source).items():
                username = str(seller.get("username") or "").strip()
                if username:
                    usernames[username.lower()] = username

            if len(visited) <= 4:
                for link in self._extract_marketplace_links(source):
                    if link not in visited and link not in queue and len(queue) < max_pages * 3:
                        queue.append(link)

        verified: dict[str, dict] = {}
        for username in list(usernames.values())[:350]:
            try:
                profile_user = await self.get_profile_user_verified(username)
            except Exception:
                continue

            exact_username = str(profile_user.get("username") or "").strip()
            if not exact_username or exact_username.lower() != username.lower():
                continue

            # Only profile-confirmed counts are used. Category/card counts are ignored completely.
            reviews = _to_int_safe(profile_user.get("reviewsCount"), 0)
            rating = profile_user.get("rating")
            try:
                rating_value = float(rating) if rating is not None else 0.0
            except Exception:
                rating_value = 0.0

            verified[exact_username.lower()] = {
                **profile_user,
                "username": exact_username,
                "reviewsCount": reviews,
                "rating": rating_value if rating is not None else None,
                "source": "profile-json-strict",
            }

        result = sorted(
            verified.values(),
            key=lambda item: (int(item.get("reviewsCount") or 0), float(item.get("rating") or 0)),
            reverse=True,
        )
        for rank, seller in enumerate(result, start=1):
            seller["rank"] = rank
        return result[:limit]


    async def get_top_sellers(self) -> dict:
        sellers = await self.collect_marketplace_top_sellers_profile_only()
        if not sellers:
            raise StarvellApiError(
                "Не удалось собрать продавцов с открытых страниц Starvell. "
                "Попробуй позже или подключи прокси, если Railway IP блокируется."
            )
        return {"pageProps": {"sellers": sellers}}


    async def get_account_offers(self) -> dict:
        """
        Best-effort: tries to fetch account offers/products from Next.js JSON.
        Starvell may change these paths, so chat scanning is used as fallback in bot.py.
        """
        build_id = await self.get_build_id()
        candidates = [
            (f"{self.base_url}/_next/data/{build_id}/account/offers.json", {}),
            (f"{self.base_url}/_next/data/{build_id}/account/offers.json", {"status": "active"}),
            (f"{self.base_url}/_next/data/{build_id}/account/offers.json", {"tab": "active"}),
        ]
        last_error = None
        for url, params in candidates:
            try:
                return await self._json_request(url, params=params)
            except Exception as error:
                last_error = error
                continue
        raise StarvellApiError(f"Не удалось получить список товаров. Последняя ошибка: {last_error}")


    async def get_profile(self, username: str) -> dict:
        build_id = await self.get_build_id()
        url = f"{self.base_url}/_next/data/{build_id}/profile/{username}.json"
        return await self._json_request(url, params={"username": username})

    async def get_profile_user(self, username: str) -> dict:
        data = await self.get_profile(username)
        return find_profile_user_in_data(data, username)

