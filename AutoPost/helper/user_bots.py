"""
user_bots.py
Manages per-user Telegram Bots (Bot API tokens) for real forwarding.

Main Auto-Forward Bot = management only.
Each user's own bot receives source messages and forwards them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Set

from pyrogram import Client, filters
from pyrogram.errors import (
    AccessTokenInvalid,
    AccessTokenExpired,
    FloodWait,
)
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from AutoPost import API_ID, API_HASH
from AutoPost.Database.database import Database, get_db

LOGGER = logging.getLogger("UserBotManager")


class UserBotManager:
    """One running Bot Client per user_id (owner of the token)."""

    def __init__(
        self,
        db: Optional[Database] = None,
        api_id: int = API_ID,
        api_hash: str = API_HASH,
        max_bots: int = 150,
        workers_per_bot: int = 8,
    ) -> None:
        self.db = db or get_db()
        self.api_id = api_id
        self.api_hash = api_hash
        self.max_bots = max_bots
        self.workers_per_bot = workers_per_bot

        self._bots: Dict[int, Client] = {}
        self._starting: Set[int] = set()
        self._lock = asyncio.Lock()
        self._started = False

    # ------------------------------------------------------------------
    # Handler
    # ------------------------------------------------------------------

    def _attach_handler(self, client: Client, owner_id: int) -> None:
        from AutoPost.plugins.post import process_and_forward, _handle_album_message

        async def _bot_forward_wrapper(c: Client, message: Message):
            # Albums share a buffer in post.py (keyed by source + media_group_id).
            # Each bot only processes its own rules after flush via forward_via.
            if message.media_group_id:
                await _handle_album_message(c, message)
                return

            source_id = message.chat.id
            rules = self.db.get_rules_by_source(source_id, only_enabled=True)

            my_rules = [
                r
                for r in rules
                if r.get("owner_id") == owner_id
                and r.get("forward_via", "user_bot") == "user_bot"
            ]
            if not my_rules:
                return

            async def _run_rule(rule: dict) -> None:
                try:
                    await process_and_forward(c, message, rule)
                except Exception as e:
                    LOGGER.exception(
                        f"User-bot rule failed | owner={owner_id} | "
                        f"{source_id} → {rule.get('target_chat_id')}: {e}"
                    )

            await asyncio.gather(
                *(_run_rule(r) for r in my_rules),
                return_exceptions=True,
            )

        client.add_handler(
            MessageHandler(
                _bot_forward_wrapper,
                filters.incoming
                & (filters.channel | filters.group)
                & ~filters.service
                & ~filters.me
                & ~filters.bot,
            ),
            group=-1,
        )
        LOGGER.info(f"Forward handler attached to user bot (owner={owner_id})")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def startup_all(self) -> None:
        if self._started:
            return

        LOGGER.info("Loading active user bots from database...")
        bots = self.db.get_all_bots_for_startup()

        if not bots:
            LOGGER.info("No active user bots found.")
            self._started = True
            return

        LOGGER.info(f"Found {len(bots)} active user bot(s). Starting...")

        sem = asyncio.Semaphore(6)

        async def _start_one(doc: dict) -> None:
            async with sem:
                user_id = doc["user_id"]
                try:
                    await self.start_user_bot(
                        user_id=user_id,
                        bot_token=doc["bot_token"],
                        from_startup=True,
                    )
                except Exception as e:
                    LOGGER.error(f"Failed to start user bot for {user_id}: {e}")
                    self.db.mark_bot_inactive(user_id)

        await asyncio.gather(*[_start_one(b) for b in bots])
        self._started = True
        LOGGER.info(f"UserBotManager ready. Active bots: {len(self._bots)}")

    async def get_bot(self, user_id: int) -> Optional[Client]:
        client = self._bots.get(user_id)
        if client and client.is_connected:
            self.db.touch_bot(user_id)
            return client
        return await self.start_user_bot(user_id)

    async def start_user_bot(
        self,
        user_id: int,
        bot_token: Optional[str] = None,
        from_startup: bool = False,
    ) -> Optional[Client]:
        async with self._lock:
            existing = self._bots.get(user_id)
            if existing and existing.is_connected:
                return existing

            if user_id in self._starting:
                for _ in range(40):
                    await asyncio.sleep(0.25)
                    if user_id in self._bots and self._bots[user_id].is_connected:
                        return self._bots[user_id]
                return None

            self._starting.add(user_id)

        try:
            if len(self._bots) >= self.max_bots and user_id not in self._bots:
                LOGGER.warning(
                    f"Max bots limit ({self.max_bots}) reached. "
                    f"Cannot start bot for user {user_id}"
                )
                return None

            if bot_token is None:
                bot_token = self.db.get_decrypted_bot_token(user_id)
                if not bot_token:
                    if not from_startup:
                        LOGGER.debug(f"No bot token found for user {user_id}")
                    return None

            client = Client(
                name=f"userbot_{user_id}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                bot_token=bot_token,
                in_memory=True,
                workers=self.workers_per_bot,
                max_concurrent_transmissions=8,
                sleep_threshold=30,
            )

            try:
                await client.start()
            except (AccessTokenInvalid, AccessTokenExpired) as e:
                LOGGER.warning(
                    f"Invalid/expired bot token for user {user_id}: {type(e).__name__}"
                )
                self.db.mark_bot_inactive(user_id)
                return None
            except FloodWait as e:
                LOGGER.warning(
                    f"FloodWait while starting bot for {user_id}: {e.value}s"
                )
                return None
            except Exception as e:
                LOGGER.error(f"Unexpected error starting bot for {user_id}: {e}")
                return None

            self._attach_handler(client, user_id)

            async with self._lock:
                old = self._bots.pop(user_id, None)
                if old and old.is_connected:
                    try:
                        await old.stop()
                    except Exception:
                        pass

                self._bots[user_id] = client
                self.db.mark_bot_active(user_id)
                self.db.touch_bot(user_id)

            me = await client.get_me()
            LOGGER.info(
                f"User bot started → owner={user_id} | "
                f"bot=@{me.username} ({me.id})"
            )
            return client

        finally:
            async with self._lock:
                self._starting.discard(user_id)

    async def stop_user_bot(
        self, user_id: int, *, delete_from_db: bool = False
    ) -> bool:
        async with self._lock:
            client = self._bots.pop(user_id, None)

        if client is None:
            if delete_from_db:
                self.db.delete_user_bot(user_id)
            return False

        try:
            if client.is_connected:
                await client.stop()
        except Exception as e:
            LOGGER.warning(f"Error stopping user bot {user_id}: {e}")

        if delete_from_db:
            self.db.delete_user_bot(user_id)
            LOGGER.info(f"User bot deleted for owner {user_id}")
        else:
            self.db.mark_bot_inactive(user_id)

        LOGGER.info(f"User bot stopped → owner={user_id}")
        return True

    async def disconnect_and_delete(self, user_id: int) -> bool:
        return await self.stop_user_bot(user_id, delete_from_db=True)

    async def stop_all(self) -> None:
        LOGGER.info("Stopping all user bots...")
        user_ids = list(self._bots.keys())
        await asyncio.gather(
            *[self.stop_user_bot(uid) for uid in user_ids],
            return_exceptions=True,
        )
        self._bots.clear()
        LOGGER.info("All user bots stopped.")

    def is_running(self, user_id: int) -> bool:
        client = self._bots.get(user_id)
        return bool(client and client.is_connected)

    def get_running_count(self) -> int:
        return sum(1 for c in self._bots.values() if c.is_connected)

    def get_running_user_ids(self) -> list[int]:
        return [uid for uid, c in self._bots.items() if c.is_connected]


_manager: Optional[UserBotManager] = None


def get_user_bot_manager() -> UserBotManager:
    global _manager
    if _manager is None:
        _manager = UserBotManager()
    return _manager
