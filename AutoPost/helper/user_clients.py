"""
user_clients.py
Manages per-user Telegram clients (MTProto user sessions).

Used for:
- Rules with forward_via = "user_account"
- Global Copy (all incoming → one target)

Main management bot does not forward.
User bots are handled separately in user_bots.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Set

from pyrogram import Client, filters
from pyrogram.errors import (
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
    UserDeactivatedBan,
    AuthKeyDuplicated,
    FloodWait,
)
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from AutoPost import API_ID, API_HASH
from AutoPost.Database.database import Database, get_db

LOGGER = logging.getLogger("UserClientManager")


class UserClientManager:
    """One MTProto user client per bot user_id."""

    def __init__(
        self,
        db: Optional[Database] = None,
        api_id: int = API_ID,
        api_hash: str = API_HASH,
        max_clients: int = 100,
        workers_per_client: int = 8,
    ) -> None:
        self.db = db or get_db()
        self.api_id = api_id
        self.api_hash = api_hash
        self.max_clients = max_clients
        self.workers_per_client = workers_per_client

        self._clients: Dict[int, Client] = {}
        self._starting: Set[int] = set()
        self._lock = asyncio.Lock()
        self._started = False

        # Ordered global-copy queue (preserves message.id order on bulk uploads)
        self._gcopy_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._gcopy_seq = 0
        self._gcopy_seq_lock = asyncio.Lock()
        self._gcopy_worker_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Handler attachment
    # ------------------------------------------------------------------

    def _attach_handler(self, client: Client, owner_id: int) -> None:
        """
        1) Rules with forward_via=user_account (channels/groups)
        2) Global Copy (any incoming chat: private, group, channel, bots)
        """
        from AutoPost.plugins.post import (
            process_and_forward,
            _handle_album_message,
            get_message_type,
            is_type_allowed,
        )

        # ----- Rule-based forward (user_account only) -----
        async def _user_rules_wrapper(c: Client, message: Message):
            if message.media_group_id:
                await _handle_album_message(c, message)
                return

            source_id = message.chat.id
            rules = self.db.get_rules_by_source(source_id, only_enabled=True)

            my_rules = [
                r
                for r in rules
                if r.get("owner_id") == owner_id
                and r.get("forward_via") == "user_account"
            ]
            if not my_rules:
                return

            async def _run_rule(rule: dict) -> None:
                try:
                    await process_and_forward(c, message, rule)
                except Exception as e:
                    LOGGER.exception(
                        f"User-account rule failed | owner={owner_id} | "
                        f"{source_id} → {rule.get('target_chat_id')}: {e}"
                    )

            await asyncio.gather(
                *(_run_rule(r) for r in my_rules),
                return_exceptions=True,
            )

        client.add_handler(
            MessageHandler(
                _user_rules_wrapper,
                filters.incoming
                & (filters.channel | filters.group)
                & ~filters.service
                & ~filters.me
                & ~filters.bot,
            ),
            group=-1,
        )

        # ----- Global Copy (broader: private + groups + channels + bots) -----
        async def _global_copy_wrapper(c: Client, message: Message):
            await self._handle_global_copy(c, message, owner_id)

        client.add_handler(
            MessageHandler(
                _global_copy_wrapper,
                filters.incoming & ~filters.me & ~filters.service,
            ),
            group=1,
        )

        LOGGER.info(
            f"Handlers attached to user client {owner_id} "
            f"(user_account rules + global copy)"
        )

    async def _ensure_gcopy_worker(self) -> None:
        """Start the single ordered global-copy worker if not running."""
        if self._gcopy_worker_task is None or self._gcopy_worker_task.done():
            self._gcopy_worker_task = asyncio.create_task(self._gcopy_worker())
            LOGGER.info("🚀 Global-copy ordered worker started")

    async def _gcopy_worker(self) -> None:
        """Always process the lowest message.id first → preserves bulk order."""
        while True:
            try:
                # item: (message.id, seq, client, message, owner_id, target)
                msg_id, seq, client, message, owner_id, target = await self._gcopy_queue.get()
                try:
                    await message.copy(chat_id=target)
                    self.db.touch_session(owner_id)
                    LOGGER.info(
                        f"✅ GCopy ordered msg.id={msg_id} "
                        f"owner={owner_id} → {target}"
                    )
                except FloodWait as e:
                    LOGGER.warning(
                        f"Global copy FloodWait {e.value}s owner={owner_id} → {target}"
                    )
                    await asyncio.sleep(e.value + 1)
                    try:
                        await message.copy(chat_id=target)
                        self.db.touch_session(owner_id)
                    except Exception as e2:
                        LOGGER.warning(
                            f"Global copy retry failed owner={owner_id}: {e2}"
                        )
                except Exception as e:
                    LOGGER.warning(
                        f"Global copy failed owner={owner_id} "
                        f"{getattr(message.chat, 'id', '?')} → {target}: {e}"
                    )
                finally:
                    # small delay so Telegram does not reshuffle under load
                    await asyncio.sleep(0.45)
                    self._gcopy_queue.task_done()
            except asyncio.CancelledError:
                LOGGER.info("Global-copy worker cancelled")
                break
            except Exception as e:
                LOGGER.error(f"Global-copy worker error: {e}")

    async def _handle_global_copy(
        self,
        client: Client,
        message: Message,
        owner_id: int,
    ) -> None:
        """Enqueue matching messages; worker copies them in message.id order."""
        gc = self.db.get_global_copy(owner_id)
        if not gc or not gc.get("enabled") or not gc.get("target_chat_id"):
            return

        target = gc["target_chat_id"]
        allowed = gc.get("allowed_types") or ["all"]

        # Skip messages already in the target chat
        if message.chat and message.chat.id == target:
            return

        from AutoPost.plugins.post import get_message_type, is_type_allowed

        msg_type = get_message_type(message)
        if not is_type_allowed(msg_type, allowed):
            return

        # Enqueue with priority = message.id so bulk uploads stay ordered
        await self._ensure_gcopy_worker()
        async with self._gcopy_seq_lock:
            self._gcopy_seq += 1
            seq = self._gcopy_seq

        await self._gcopy_queue.put(
            (message.id, seq, client, message, owner_id, target)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def startup_all(self) -> None:
        if self._started:
            return

        LOGGER.info("Loading active user sessions from database...")
        sessions = self.db.get_all_sessions_for_startup()

        if not sessions:
            LOGGER.info("No active user sessions found.")
            self._started = True
            return

        LOGGER.info(f"Found {len(sessions)} active session(s). Starting clients...")

        sem = asyncio.Semaphore(8)

        async def _start_one(session: dict) -> None:
            async with sem:
                user_id = session["user_id"]
                try:
                    await self.start_user_client(
                        user_id=user_id,
                        session_string=session["session_string"],
                        from_startup=True,
                    )
                except Exception as e:
                    LOGGER.error(f"Failed to start client for user {user_id}: {e}")
                    self.db.mark_session_inactive(user_id)

        await asyncio.gather(*[_start_one(s) for s in sessions])
        self._started = True
        LOGGER.info(f"UserClientManager ready. Active clients: {len(self._clients)}")

    async def get_client(self, user_id: int) -> Optional[Client]:
        client = self._clients.get(user_id)
        if client and client.is_connected:
            self.db.touch_session(user_id)
            return client
        return await self.start_user_client(user_id)

    async def start_user_client(
        self,
        user_id: int,
        session_string: Optional[str] = None,
        from_startup: bool = False,
    ) -> Optional[Client]:
        async with self._lock:
            existing = self._clients.get(user_id)
            if existing and existing.is_connected:
                return existing

            if user_id in self._starting:
                for _ in range(30):
                    await asyncio.sleep(0.2)
                    if user_id in self._clients and self._clients[user_id].is_connected:
                        return self._clients[user_id]
                return None

            self._starting.add(user_id)

        try:
            if len(self._clients) >= self.max_clients and user_id not in self._clients:
                LOGGER.warning(
                    f"Max clients limit ({self.max_clients}) reached. "
                    f"Cannot start client for user {user_id}"
                )
                return None

            if session_string is None:
                session_string = self.db.get_decrypted_session_string(user_id)
                if not session_string:
                    if not from_startup:
                        LOGGER.debug(f"No session found for user {user_id}")
                    return None

            client = Client(
                name=f"user_{user_id}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                session_string=session_string,
                in_memory=True,
                workers=self.workers_per_client,
                max_concurrent_transmissions=5,
                sleep_threshold=30,
                # no_updates omitted → receive updates
            )

            try:
                await client.start()
            except (
                AuthKeyUnregistered,
                SessionRevoked,
                UserDeactivated,
                UserDeactivatedBan,
                AuthKeyDuplicated,
            ) as e:
                LOGGER.warning(
                    f"Session for user {user_id} is dead: {type(e).__name__}"
                )
                self.db.mark_session_inactive(user_id)
                return None
            except FloodWait as e:
                LOGGER.warning(
                    f"FloodWait while starting client for {user_id}: {e.value}s"
                )
                return None
            except Exception as e:
                LOGGER.error(f"Unexpected error starting client for {user_id}: {e}")
                return None

            self._attach_handler(client, user_id)

            async with self._lock:
                old = self._clients.pop(user_id, None)
                if old and old.is_connected:
                    try:
                        await old.stop()
                    except Exception:
                        pass

                self._clients[user_id] = client
                self.db.mark_session_active(user_id)
                self.db.touch_session(user_id)

            me = await client.get_me()
            LOGGER.info(
                f"User client started → bot_user={user_id} | "
                f"tg_user={me.id} (@{me.username or 'N/A'})"
            )
            return client

        finally:
            async with self._lock:
                self._starting.discard(user_id)

    async def stop_user_client(
        self, user_id: int, *, delete_session: bool = False
    ) -> bool:
        async with self._lock:
            client = self._clients.pop(user_id, None)

        if client is None:
            if delete_session:
                self.db.delete_user_session(user_id)
            return False

        try:
            if client.is_connected:
                await client.stop()
        except Exception as e:
            LOGGER.warning(f"Error while stopping client {user_id}: {e}")

        if delete_session:
            self.db.delete_user_session(user_id)
            # Also disable global copy if session is fully removed
            try:
                self.db.disable_global_copy(user_id)
            except Exception:
                pass
            LOGGER.info(f"Session deleted for user {user_id}")
        else:
            self.db.mark_session_inactive(user_id)

        LOGGER.info(f"User client stopped → {user_id}")
        return True

    async def disconnect_and_delete(self, user_id: int) -> bool:
        return await self.stop_user_client(user_id, delete_session=True)

    async def stop_all(self) -> None:
        LOGGER.info("Stopping all user clients...")
        user_ids = list(self._clients.keys())
        await asyncio.gather(
            *[self.stop_user_client(uid) for uid in user_ids],
            return_exceptions=True,
        )
        self._clients.clear()
        LOGGER.info("All user clients stopped.")

    def is_running(self, user_id: int) -> bool:
        client = self._clients.get(user_id)
        return bool(client and client.is_connected)

    def get_running_count(self) -> int:
        return sum(1 for c in self._clients.values() if c.is_connected)

    def get_running_user_ids(self) -> list[int]:
        return [uid for uid, c in self._clients.items() if c.is_connected]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: Optional[UserClientManager] = None


def get_user_client_manager() -> UserClientManager:
    global _manager
    if _manager is None:
        _manager = UserClientManager()
    return _manager
