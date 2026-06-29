import json
import re
import time
from typing import Any

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
            timeout=30,
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

    async def get_profile(self, username: str) -> dict:
        build_id = await self.get_build_id()
        url = f"{self.base_url}/_next/data/{build_id}/profile/{username}.json"
        return await self._json_request(url, params={"username": username})
