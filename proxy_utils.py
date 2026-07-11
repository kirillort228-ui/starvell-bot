import re
from urllib.parse import urlparse

import httpx

PROXY_PATTERN = re.compile(
    r"^(http|https|socks5)://"
    r"(?:(?P<login>[^:@/]+):(?P<password>[^@/]+)@)?"
    r"(?P<host>[^:/]+):(?P<port>\d{2,5})$",
    re.IGNORECASE,
)


def normalize_proxy(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if proxy_url in {"-", "нет", "no", "none", "skip", "пропустить"}:
        return None
    return proxy_url


def validate_proxy(proxy_url: str | None) -> bool:
    proxy_url = normalize_proxy(proxy_url)
    if proxy_url is None:
        return True

    match = PROXY_PATTERN.match(proxy_url)
    if not match:
        return False

    parsed = urlparse(proxy_url)
    try:
        port = int(parsed.port or 0)
    except ValueError:
        return False

    return 1 <= port <= 65535


async def check_proxy(proxy_url: str) -> tuple[bool, str]:
    if not validate_proxy(proxy_url):
        return False, "Неверный формат прокси. Используй http://, https:// или socks5://"

    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=15) as client:
            response = await client.get("https://api.ipify.org")
            response.raise_for_status()
            return True, f"Прокси работает. IP: {response.text.strip()}"
    except Exception as error:
        return False, f"Прокси не работает: {error}"


def hide_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "без прокси"
    if "@" not in proxy_url:
        return proxy_url
    scheme, rest = proxy_url.split("://", 1)
    _auth, host = rest.split("@", 1)
    return f"{scheme}://***:***@{host}"
