import html
import json
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


class StarvellApiError(Exception):
    pass


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
        build_id = await self.get_build_id()
        url = f"{self.base_url}/_next/data/{build_id}/account/orders.json"
        return await self._json_request(url)


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
        Builds a seller rating from public Starvell marketplace pages.
        This replaces a non-existing official "top sellers" page: it collects sellers from category/item pages,
        deduplicates them by username, and sorts by reviewsCount, then rating.
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
        sellers: dict[str, dict] = {}

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
                old = sellers.get(key)
                if not old or int(seller.get("reviewsCount") or 0) > int(old.get("reviewsCount") or 0):
                    sellers[key] = seller

            # Discover additional marketplace pages from the first pages.
            if len(visited) <= 4:
                for link in self._extract_marketplace_links(source):
                    if link not in visited and link not in queue and len(queue) < max_pages * 3:
                        queue.append(link)

        result = sorted(
            sellers.values(),
            key=lambda item: (int(item.get("reviewsCount") or 0), float(item.get("rating") or 0)),
            reverse=True,
        )
        for rank, seller in enumerate(result, start=1):
            seller["rank"] = rank
        return result[:limit]

    async def get_top_sellers(self) -> dict:
        sellers = await self.collect_marketplace_top_sellers()
        if not sellers:
            raise StarvellApiError(
                "Не удалось собрать продавцов с открытых страниц Starvell. "
                "Попробуй позже или подключи прокси, если Railway IP блокируется."
            )
        return {"pageProps": {"sellers": sellers}}

    async def get_profile(self, username: str) -> dict:
        build_id = await self.get_build_id()
        url = f"{self.base_url}/_next/data/{build_id}/profile/{username}.json"
        return await self._json_request(url, params={"username": username})
