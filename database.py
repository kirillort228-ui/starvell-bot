from __future__ import annotations

import aiosqlite
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class TopUpRequest:
    id: int
    user_id: int
    amount_kopecks: int
    status: str


@dataclass
class StarvellAccount:
    id: int
    user_id: int
    username: str | None
    cookie: str
    proxy_url: str | None
    notifications_enabled: bool
    last_error: str | None


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    timezone TEXT DEFAULT 'UTC',
                    subscription_until TEXT,
                    trial_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    cookie TEXT NOT NULL,
                    proxy_url TEXT,
                    notifications_enabled INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS message_state (
                    account_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    last_message_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, chat_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    proxy_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS topup_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_kopecks INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS account_settings (
                    user_id INTEGER NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, setting_key),
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS confirm_reminder_state (
                    account_id INTEGER NOT NULL,
                    order_id TEXT NOT NULL,
                    last_sent_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, order_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auto_raise_state (
                    account_id INTEGER PRIMARY KEY,
                    last_bumped_at TEXT,
                    last_result TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );
                """
            )
            try:
                await db.execute("ALTER TABLE users ADD COLUMN bot_balance_kopecks INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE users ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE users ADD COLUMN always_online_enabled INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            await db.commit()

    async def ensure_user(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?, ?)",
                (user_id, now),
            )
            await db.commit()


    async def get_bot_balance(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT bot_balance_kopecks FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0

    async def add_bot_balance(self, user_id: int, amount_kopecks: int) -> None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET bot_balance_kopecks = bot_balance_kopecks + ? WHERE user_id = ?",
                (int(amount_kopecks), user_id),
            )
            await db.commit()



    async def has_trial_used(self, user_id: int) -> bool:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT trial_used FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return bool(row and int(row[0] or 0))

    async def set_trial_used(self, user_id: int, used: bool = True) -> None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET trial_used = ? WHERE user_id = ?",
                (1 if used else 0, user_id),
            )
            await db.commit()


    async def get_always_online_enabled(self, user_id: int) -> bool:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT always_online_enabled FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return bool(row and int(row[0] or 0))

    async def set_always_online_enabled(self, user_id: int, enabled: bool) -> None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET always_online_enabled = ? WHERE user_id = ?",
                (1 if enabled else 0, user_id),
            )
            await db.commit()

    async def toggle_always_online_enabled(self, user_id: int) -> bool:
        current = await self.get_always_online_enabled(user_id)
        new_value = not current
        await self.set_always_online_enabled(user_id, new_value)
        return new_value

    async def get_subscription_until(self, user_id: int) -> str | None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT subscription_until FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

    async def set_subscription_until(self, user_id: int, subscription_until: str | None) -> None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET subscription_until = ? WHERE user_id = ?",
                (subscription_until, user_id),
            )
            await db.commit()

    async def spend_bot_balance(self, user_id: int, amount_kopecks: int) -> bool:
        await self.ensure_user(user_id)
        amount_kopecks = int(amount_kopecks)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT bot_balance_kopecks FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            current = int(row[0] or 0) if row else 0
            if current < amount_kopecks:
                return False
            await db.execute(
                "UPDATE users SET bot_balance_kopecks = bot_balance_kopecks - ? WHERE user_id = ?",
                (amount_kopecks, user_id),
            )
            await db.commit()
            return True

    async def add_topup_request(self, user_id: int, amount_kopecks: int) -> int:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                INSERT INTO topup_requests(user_id, amount_kopecks, status, created_at, updated_at)
                VALUES(?, ?, 'pending', ?, ?)
                """,
                (user_id, int(amount_kopecks), now, now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def get_topup_request(self, request_id: int) -> TopUpRequest | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM topup_requests WHERE id = ?", (request_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return TopUpRequest(
                id=int(row["id"]),
                user_id=int(row["user_id"]),
                amount_kopecks=int(row["amount_kopecks"]),
                status=row["status"],
            )

    async def set_topup_status(self, request_id: int, status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE topup_requests SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, request_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_account_setting(self, user_id: int, setting_key: str, default: str | None = None) -> str | None:
        await self.ensure_user(user_id)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT setting_value FROM account_settings WHERE user_id = ? AND setting_key = ?",
                (user_id, setting_key),
            )
            row = await cursor.fetchone()
            return row[0] if row else default

    async def set_account_setting(self, user_id: int, setting_key: str, setting_value: str | None) -> None:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO account_settings(user_id, setting_key, setting_value, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(user_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                """,
                (user_id, setting_key, setting_value, now),
            )
            await db.commit()

    async def get_bool_account_setting(self, user_id: int, setting_key: str, default: bool = False) -> bool:
        value = await self.get_account_setting(user_id, setting_key, "1" if default else "0")
        return str(value).lower() in ("1", "true", "yes", "on", "вкл")

    async def toggle_bool_account_setting(self, user_id: int, setting_key: str) -> bool:
        current = await self.get_bool_account_setting(user_id, setting_key)
        new_value = not current
        await self.set_account_setting(user_id, setting_key, "1" if new_value else "0")
        return new_value


    async def get_confirm_reminder_last_sent(self, account_id: int, order_id: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT last_sent_at FROM confirm_reminder_state WHERE account_id = ? AND order_id = ?",
                (account_id, str(order_id)),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_confirm_reminder_sent(self, account_id: int, order_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO confirm_reminder_state(account_id, order_id, last_sent_at)
                VALUES(?, ?, ?)
                ON CONFLICT(account_id, order_id)
                DO UPDATE SET last_sent_at = excluded.last_sent_at
                """,
                (account_id, str(order_id), now),
            )
            await db.commit()


    async def get_auto_raise_state(self, account_id: int) -> tuple[str | None, str | None]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT last_bumped_at, last_result FROM auto_raise_state WHERE account_id = ?",
                (account_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None, None
            return row[0], row[1]

    async def save_auto_raise_state(self, account_id: int, result: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO auto_raise_state(account_id, last_bumped_at, last_result)
                VALUES(?, ?, ?)
                ON CONFLICT(account_id)
                DO UPDATE SET last_bumped_at = excluded.last_bumped_at, last_result = excluded.last_result
                """,
                (account_id, now, str(result)[:500]),
            )
            await db.commit()

    async def add_account(self, user_id: int, cookie: str, username: str | None, proxy_url: str | None) -> int:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                INSERT INTO accounts(user_id, username, cookie, proxy_url, notifications_enabled, created_at)
                VALUES(?, ?, ?, ?, 1, ?)
                """,
                (user_id, username, cookie, proxy_url, now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def update_account_username(self, account_id: int, username: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE accounts SET username = ? WHERE id = ?", (username, account_id))
            await db.commit()

    async def update_account_proxy(self, account_id: int, proxy_url: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE accounts SET proxy_url = ? WHERE id = ?", (proxy_url, account_id))
            await db.commit()

    async def set_account_error(self, account_id: int, error: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE accounts SET last_error = ? WHERE id = ?", (error, account_id))
            await db.commit()

    async def list_user_accounts(self, user_id: int) -> list[StarvellAccount]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts WHERE user_id = ? ORDER BY id DESC", (user_id,))
            rows = await cursor.fetchall()
            return [self._row_to_account(row) for row in rows]

    async def list_enabled_accounts(self) -> list[StarvellAccount]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts WHERE notifications_enabled = 1 ORDER BY id ASC")
            rows = await cursor.fetchall()
            return [self._row_to_account(row) for row in rows]

    async def get_account(self, account_id: int) -> StarvellAccount | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            row = await cursor.fetchone()
            return self._row_to_account(row) if row else None

    async def delete_account(self, account_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id))
            await db.commit()
            return cursor.rowcount > 0

    async def toggle_notifications(self, account_id: int, user_id: int) -> bool | None:
        account = await self.get_account(account_id)
        if not account or account.user_id != user_id:
            return None
        new_value = 0 if account.notifications_enabled else 1
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE accounts SET notifications_enabled = ? WHERE id = ?", (new_value, account_id))
            await db.commit()
        return bool(new_value)

    async def save_last_message_id(self, account_id: int, chat_id: str, message_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO message_state(account_id, chat_id, last_message_id, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(account_id, chat_id)
                DO UPDATE SET last_message_id = excluded.last_message_id, updated_at = excluded.updated_at
                """,
                (account_id, chat_id, message_id, now),
            )
            await db.commit()

    async def get_last_message_id(self, account_id: int, chat_id: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT last_message_id FROM message_state WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def add_proxy(self, user_id: int, proxy_url: str) -> int:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO proxies(user_id, proxy_url, created_at) VALUES(?, ?, ?)",
                (user_id, proxy_url, now),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list_user_proxies(self, user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM proxies WHERE user_id = ? ORDER BY id DESC", (user_id,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _row_to_account(row: aiosqlite.Row) -> StarvellAccount:
        return StarvellAccount(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            username=row["username"],
            cookie=row["cookie"],
            proxy_url=row["proxy_url"],
            notifications_enabled=bool(row["notifications_enabled"]),
            last_error=row["last_error"],
        )
