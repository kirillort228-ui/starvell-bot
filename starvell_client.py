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


def _extract_next_data_from_html(source: str) -> dict | None:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', source, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except Exception:
        return None


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
            "gameId": 31,
            "categoryIds": [335]
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

        raise StarvellApiError("Не удалось подтвердить отзывы через профиль продавца: " + "; ".join(errors[-2:]))

    async def get_top_sellers(self) -> dict:
        sellers = await self.collect_marketplace_top_sellers()
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

