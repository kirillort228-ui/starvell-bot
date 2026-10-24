from __future__ import annotations

from typing import Any

import httpx


class CryptoPayError(Exception):
    pass


class CryptoPayClient:
    """
    Minimal async client for Crypto Bot Crypto Pay API.

    Docs:
    - Mainnet API base: https://pay.crypt.bot/api/
    - Testnet API base: https://testnet-pay.crypt.bot/api/
    - Auth header: Crypto-Pay-API-Token
    """

    def __init__(self, token: str, api_base: str = "https://pay.crypt.bot/api") -> None:
        self.token = (token or "").strip()
        self.api_base = (api_base or "https://pay.crypt.bot/api").rstrip("/")
        self.client = httpx.AsyncClient(timeout=20)

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise CryptoPayError("CRYPTO_PAY_TOKEN не настроен в .env")

        try:
            response = await self.client.post(
                f"{self.api_base}/{method}",
                json=payload or {},
                headers={
                    "Crypto-Pay-API-Token": self.token,
                    "Content-Type": "application/json",
                },
            )
        except httpx.TimeoutException:
            raise CryptoPayError("Crypto Bot долго не отвечает. Попробуй позже.")
        except httpx.RequestError as error:
            raise CryptoPayError(f"Ошибка запроса к Crypto Bot: {error}")

        try:
            data = response.json()
        except Exception:
            raise CryptoPayError(f"Crypto Bot вернул не JSON: {response.text[:300]}")

        if response.status_code >= 400 or not data.get("ok"):
            raise CryptoPayError(str(data.get("error") or data.get("description") or data)[:500])

        return data.get("result")

    async def get_me(self) -> dict[str, Any]:
        return await self._request("getMe")

    async def create_fiat_invoice(
        self,
        *,
        amount_rub: float,
        payload: str,
        description: str,
        accepted_assets: str = "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC",
        expires_in: int = 3600,
    ) -> dict[str, Any]:
        amount_text = f"{float(amount_rub):.2f}"
        return await self._request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "accepted_assets": accepted_assets,
                "amount": amount_text,
                "description": description[:1024],
                "hidden_message": "Оплата получена. Баланс в боте будет зачислен автоматически.",
                "payload": payload[:4096],
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": int(expires_in),
            },
        )

    async def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        result = await self._request(
            "getInvoices",
            {
                "invoice_ids": str(invoice_id),
                "count": 1,
            },
        )
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            return result["items"][0] if result["items"] else None
        if isinstance(result, list):
            return result[0] if result else None
        return None
