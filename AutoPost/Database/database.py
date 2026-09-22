"""
database.py
MongoDB database layer for Telegram Auto-Forward Bot
PyMongo 4.17.0 | Python 3.14.0 | Kurigram (Pyrogram fork)

Supports:
- Forward rules (one-to-many / many-to-one / many-to-many)
- Caption / replacements / block / whitelist / buttons / delay / anti-dupe
- User sessions (MTProto) + User bots (Bot API tokens)
- Global Copy with full filter parity vs rules
- Per-user external MongoDB for permanent anti-dupe history
- Quota, stats, bot admins
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.fernet import Fernet, InvalidToken
from pymongo import ASCENDING, IndexModel, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError
from AutoPost import DB_URL, BOT_OWNER_IDS, DAILY_FORWARD_LIMIT

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGO_URI: str = DB_URL
DB_NAME: str = "telegram_auto_forward"

RULE_LIMIT: int = 10
MAX_TARGETS_PER_SOURCE: int = 10
RULE_CACHE_TTL_SECONDS: float = 30.0

_RULE_FORWARD_PROJECTION: Dict[str, int] = {
    "_id": 1,
    "source_chat_id": 1,
    "target_chat_id": 1,
    "owner_id": 1,
    "enabled": 1,
    "add_caption": 1,
    "caption_position": 1,
    "custom_caption": 1,
    "remove_old_caption": 1,
    "replacements": 1,
    "block_words": 1,
    "whitelist_words": 1,
    "buttons": 1,
    "forward_tag": 1,
    "remove_links": 1,
    "allowed_types": 1,
    "delay": 1,
    "anti_dupe": 1,
    "forward_via": 1,
    "content_type": 1,
    "size_filter_enabled": 1,
    "min_media_size": 1,
    "completion_sticker_enabled": 1,
    "completion_stickers": 1,
}

ALLOWED_CAPTION_POSITIONS = {"start", "end", "end_with_gap"}

ALLOWED_MEDIA_TYPES = {
    "all", "photo", "video", "document", "sticker", "animation",
    "audio", "voice", "text", "poll", "contact", "location", "venue",
}

ALLOWED_FORWARD_VIA = {"user_bot", "user_account"}

ALLOWED_CONTENT_TYPES = {"all", "movies", "series", "movies_series"}

_GLOBAL_COPY_FILTER_KEYS = {
    "block_words", "whitelist_words", "replacements",
    "add_caption", "caption_position", "custom_caption",
    "remove_old_caption", "remove_links", "buttons",
    "delay", "anti_dupe", "forward_tag", "allowed_types",
    "target_chat_id", "enabled",
    "content_type", "size_filter_enabled", "min_media_size",
}


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    key = os.getenv("SESSION_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY environment variable is required "
            "for storing secrets securely."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt secret (invalid key or corrupted data)"
        ) from e


encrypt_session = encrypt_secret
decrypt_session = decrypt_secret


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, uri: str = MONGO_URI, db_name: str = DB_NAME) -> None:
        self.client: MongoClient = MongoClient(
            uri,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=20000,
            retryWrites=True,
            retryReads=True,
        )
        self.db: Database = self.client[db_name]

        self.users: Collection = self.db["users"]
        self.forward_rules: Collection = self.db["forward_rules"]
        self.channel_admins: Collection = self.db["channel_admins"]
        self.bot_admins: Collection = self.db["bot_admins"]
        self.message_hashes: Collection = self.db["message_hashes"]
        self.stats: Collection = self.db["stats"]
        self.user_sessions: Collection = self.db["user_sessions"]
        self.user_bots: Collection = self.db["user_bots"]

        self._rules_by_source_cache: Dict[int, Tuple[float, List[Dict[str, Any]]]] = {}
        # user_id → external MongoClient for permanent anti-dupe
        self._dupe_clients: Dict[int, MongoClient] = {}

        self._ensure_indexes()
        self._ensure_owners()
        self._ensure_stats_doc()

    def _ensure_indexes(self) -> None:
        self.users.create_indexes([
            IndexModel([("user_id", ASCENDING)], unique=True, name="user_id_unique"),
        ])

        self.forward_rules.create_indexes([
            IndexModel(
                [("source_chat_id", ASCENDING), ("target_chat_id", ASCENDING)],
                unique=True,
                name="source_target_unique",
            ),
            IndexModel([("source_chat_id", ASCENDING)], name="source_idx"),
            IndexModel(
                [("source_chat_id", ASCENDING)],
                partialFilterExpression={"enabled": True},
                name="source_enabled_partial_idx",
            ),
            IndexModel([("target_chat_id", ASCENDING)], name="target_idx"),
            IndexModel([("owner_id", ASCENDING)], name="owner_idx"),
            IndexModel([("enabled", ASCENDING)], name="enabled_idx"),
        ])

        self.channel_admins.create_indexes([
            IndexModel(
                [("chat_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="chat_user_unique",
            ),
            IndexModel([("chat_id", ASCENDING)], name="chat_idx"),
            IndexModel([("user_id", ASCENDING)], name="user_idx"),
        ])

        self.bot_admins.create_indexes([
            IndexModel([("user_id", ASCENDING)], unique=True, name="bot_admin_unique"),
        ])

        try:
            existing = {idx["name"] for idx in self.message_hashes.list_indexes()}
            if "hash_created_idx" in existing:
                self.message_hashes.drop_index("hash_created_idx")
        except Exception:
            pass

        self.message_hashes.create_indexes([
            IndexModel(
                [("hash", ASCENDING), ("target_chat_id", ASCENDING)],
                unique=True,
                name="hash_target_unique",
            ),
            IndexModel([("target_chat_id", ASCENDING)], name="hash_target_idx"),
            IndexModel(
                [("created_at", ASCENDING)],
                expireAfterSeconds=7 * 24 * 60 * 60,
                name="hash_ttl_7days",
            ),
        ])

        self.user_sessions.create_indexes([
            IndexModel([("user_id", ASCENDING)], unique=True, name="user_session_unique"),
            IndexModel([("tg_user_id", ASCENDING)], name="tg_user_id_idx"),
            IndexModel([("is_active", ASCENDING)], name="session_active_idx"),
        ])

        self.user_bots.create_indexes([
            IndexModel([("user_id", ASCENDING)], unique=True, name="user_bot_unique"),
            IndexModel([("bot_id", ASCENDING)], name="bot_id_idx"),
            IndexModel([("is_active", ASCENDING)], name="user_bot_active_idx"),
        ])

    def _ensure_owners(self) -> None:
        now = datetime.now(timezone.utc)
        for owner_id in BOT_OWNER_IDS:
            self.bot_admins.update_one(
                {"user_id": owner_id},
                {
                    "$set": {"is_owner": True, "updated_at": now},
                    "$setOnInsert": {
                        "user_id": owner_id,
                        "added_by": None,
                        "created_at": now,
                    },
                },
                upsert=True,
            )

    def _ensure_stats_doc(self) -> None:
        self.stats.update_one(
            {"_id": "global"},
            {
                "$setOnInsert": {
                    "_id": "global",
                    "total_forwarded": 0,
                    "total_blocked": 0,
                    "total_failed": 0,
                    "total_duplicates_skipped": 0,
                    "daily_forwarded": {},
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        self.stats.update_one(
            {"_id": "global", "daily_forwarded": {"$exists": False}},
            {"$set": {"daily_forwarded": {}}},
        )

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    def add_or_update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        today = self._today_str()
        self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "created_at": now,
                    "daily_forwards": 0,
                    "quota_date": today,
                },
            },
            upsert=True,
        )

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.users.find_one({"user_id": user_id})

    def get_total_users(self) -> int:
        return self.users.count_documents({})

    # -----------------------------------------------------------------------
    # Daily Forward Quota
    # -----------------------------------------------------------------------

    @staticmethod
    def _today_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_user_quota_fields(self, user_id: int) -> None:
        today = self._today_str()
        self.users.update_one(
            {"user_id": user_id},
            {
                "$setOnInsert": {
                    "user_id": user_id,
                    "created_at": datetime.now(timezone.utc),
                },
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        self.users.update_one(
            {"user_id": user_id, "daily_forwards": {"$exists": False}},
            {"$set": {"daily_forwards": 0, "quota_date": today}},
        )
        self.users.update_one(
            {"user_id": user_id, "quota_date": {"$exists": False}},
            {"$set": {"quota_date": today}},
        )

    def _reset_quota_if_needed(self, user_id: int) -> None:
        today = self._today_str()
        self.users.update_one(
            {"user_id": user_id, "quota_date": {"$ne": today}},
            {
                "$set": {
                    "daily_forwards": 0,
                    "quota_date": today,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

    def get_user_daily_forwards(self, user_id: int) -> int:
        self._ensure_user_quota_fields(user_id)
        self._reset_quota_if_needed(user_id)
        doc = self.users.find_one({"user_id": user_id}, {"daily_forwards": 1})
        return int(doc.get("daily_forwards", 0)) if doc else 0

    def increment_user_daily_forwards(self, user_id: int, amount: int = 1) -> int:
        if amount <= 0:
            return self.get_user_daily_forwards(user_id)
        self._ensure_user_quota_fields(user_id)
        self._reset_quota_if_needed(user_id)
        today = self._today_str()
        result = self.users.find_one_and_update(
            {"user_id": user_id},
            {
                "$inc": {"daily_forwards": amount},
                "$set": {
                    "quota_date": today,
                    "updated_at": datetime.now(timezone.utc),
                },
            },
            return_document=True,
            projection={"daily_forwards": 1},
        )
        return int(result.get("daily_forwards", 0)) if result else amount

    def can_user_forward(self, user_id: int) -> bool:
        if self.is_bot_admin(user_id):
            return True
        return self.get_user_daily_forwards(user_id) < DAILY_FORWARD_LIMIT

    def try_consume_quota(self, user_id: int) -> bool:
        if self.is_bot_admin(user_id):
            return True
        today = self._today_str()
        now = datetime.now(timezone.utc)
        self._ensure_user_quota_fields(user_id)
        result = self.users.find_one_and_update(
            {
                "user_id": user_id,
                "$or": [
                    {"quota_date": {"$ne": today}},
                    {"daily_forwards": {"$lt": DAILY_FORWARD_LIMIT}},
                ],
            },
            [
                {
                    "$set": {
                        "quota_date": today,
                        "updated_at": now,
                        "daily_forwards": {
                            "$cond": [
                                {"$ne": ["$quota_date", today]},
                                1,
                                {"$add": [{"$ifNull": ["$daily_forwards", 0]}, 1]},
                            ]
                        },
                    }
                }
            ],
            return_document=True,
            projection={"daily_forwards": 1, "quota_date": 1},
        )
        return result is not None

    def get_user_quota_info(self, user_id: int) -> Dict[str, Any]:
        is_unlimited = self.is_bot_admin(user_id)
        used = self.get_user_daily_forwards(user_id)
        today = self._today_str()
        if is_unlimited:
            return {
                "user_id": user_id,
                "is_unlimited": True,
                "used": used,
                "limit": None,
                "remaining": None,
                "quota_date": today,
                "limit_reached": False,
            }
        remaining = max(0, DAILY_FORWARD_LIMIT - used)
        return {
            "user_id": user_id,
            "is_unlimited": False,
            "used": used,
            "limit": DAILY_FORWARD_LIMIT,
            "remaining": remaining,
            "quota_date": today,
            "limit_reached": used >= DAILY_FORWARD_LIMIT,
        }

    # -----------------------------------------------------------------------
    # Global Copy (full filter parity with rules)
    # -----------------------------------------------------------------------

    def _default_global_copy(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "target_chat_id": None,
            "allowed_types": ["all"],
            "block_words": [],
            "whitelist_words": [],
            "replacements": [],
            "add_caption": None,
            "caption_position": "end",
            "custom_caption": None,
            "remove_old_caption": False,
            "remove_links": False,
            "buttons": [],
            "delay": 0.0,
            "anti_dupe": False,
            "forward_tag": False,
            "content_type": "all",
            "size_filter_enabled": False,
            "min_media_size": 0,
        }

    def set_global_copy(
        self,
        user_id: int,
        enabled: bool,
        target_chat_id: Optional[int] = None,
        allowed_types: Optional[List[str]] = None,
        **extra: Any,
    ) -> bool:
        if enabled and target_chat_id is None:
            existing = self.get_global_copy(user_id)
            if not existing or not existing.get("target_chat_id"):
                raise ValueError(
                    "target_chat_id is required when enabling global copy"
                )
            target_chat_id = existing["target_chat_id"]

        if allowed_types is None:
            allowed_types = ["all"]
        for t in allowed_types:
            if t not in ALLOWED_MEDIA_TYPES:
                raise ValueError(f"Invalid media type: {t}")

        filter_data = {
            k: v for k, v in extra.items()
            if k in _GLOBAL_COPY_FILTER_KEYS and k not in (
                "enabled", "target_chat_id", "allowed_types",
            )
        }
        if filter_data:
            self._validate_rule_data(filter_data)

        self._ensure_user_quota_fields(user_id)

        update: Dict[str, Any] = {
            "global_copy.enabled": enabled,
            "global_copy.allowed_types": allowed_types,
            "updated_at": datetime.now(timezone.utc),
        }
        if target_chat_id is not None:
            update["global_copy.target_chat_id"] = target_chat_id

        for k, v in filter_data.items():
            if k in ("block_words", "whitelist_words") and isinstance(v, list):
                v = self._normalize_word_list(v)
            update[f"global_copy.{k}"] = v

        result = self.users.update_one(
            {"user_id": user_id},
            {"$set": update},
            upsert=True,
        )
        return result.modified_count > 0 or result.upserted_id is not None

    def update_global_copy_filters(
        self,
        user_id: int,
        updates: Dict[str, Any],
    ) -> bool:
        clean = {k: v for k, v in updates.items() if k in _GLOBAL_COPY_FILTER_KEYS}
        if not clean:
            return False
        self._validate_rule_data({
            k: v for k, v in clean.items()
            if k not in ("enabled", "target_chat_id")
        })
        if "block_words" in clean and isinstance(clean["block_words"], list):
            clean["block_words"] = self._normalize_word_list(clean["block_words"])
        if "whitelist_words" in clean and isinstance(clean["whitelist_words"], list):
            clean["whitelist_words"] = self._normalize_word_list(
                clean["whitelist_words"]
            )
        set_ops = {f"global_copy.{k}": v for k, v in clean.items()}
        set_ops["updated_at"] = datetime.now(timezone.utc)
        result = self.users.update_one({"user_id": user_id}, {"$set": set_ops})
        return result.modified_count > 0

    def get_global_copy(self, user_id: int) -> Optional[Dict[str, Any]]:
        doc = self.users.find_one({"user_id": user_id}, {"global_copy": 1})
        if not doc:
            return None
        gc = doc.get("global_copy") or {}
        base = self._default_global_copy()
        for k in base:
            if k in gc:
                base[k] = gc[k]
        for key in (
            "block_words", "whitelist_words", "replacements",
            "buttons", "allowed_types",
        ):
            if not isinstance(base.get(key), list):
                base[key] = [] if key != "allowed_types" else ["all"]
        base["enabled"] = bool(base.get("enabled", False))
        base["delay"] = float(base.get("delay") or 0)
        return base

    def disable_global_copy(self, user_id: int) -> bool:
        result = self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "global_copy.enabled": False,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    def get_all_global_copy_users(self) -> List[Dict[str, Any]]:
        cursor = self.users.find(
            {"global_copy.enabled": True},
            {"user_id": 1, "global_copy": 1},
        )
        result = []
        for doc in cursor:
            gc = self.get_global_copy(doc["user_id"])
            if gc and gc.get("target_chat_id"):
                result.append({"user_id": doc["user_id"], **gc})
        return result

    # -----------------------------------------------------------------------
    # Per-user external Duplicate DB (permanent anti-dupe)
    # -----------------------------------------------------------------------

    def set_dupe_db(
        self,
        user_id: int,
        mongo_uri: str,
        db_name: Optional[str] = None,
    ) -> bool:
        """Validate URI, create indexes, encrypt + save. Never log the URI."""
        uri = mongo_uri.strip()
        if not uri.startswith("mongodb"):
            raise ValueError("URI must start with mongodb:// or mongodb+srv://")

        try:
            test = MongoClient(
                uri,
                serverSelectionTimeoutMS=8000,
                connectTimeoutMS=8000,
            )
            test.admin.command("ping")
            if db_name is None:
                try:
                    default_db = test.get_default_database()
                    db_name = default_db.name if default_db is not None else "dupedb"
                except Exception:
                    db_name = "dupedb"
            coll = test[db_name]["message_hashes"]
            coll.create_indexes([
                IndexModel(
                    [("hash", ASCENDING), ("target_chat_id", ASCENDING)],
                    unique=True,
                    name="hash_target_unique",
                ),
                IndexModel([("target_chat_id", ASCENDING)], name="hash_target_idx"),
                IndexModel([("owner_id", ASCENDING)], name="hash_owner_idx"),
            ])
            test.close()
        except Exception as e:
            raise ValueError(
                f"Could not connect to dupe DB: {type(e).__name__}: {e}"
            ) from e

        self._close_dupe_client(user_id)
        encrypted = encrypt_secret(uri)
        self._ensure_user_quota_fields(user_id)
        self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "dupe_db.uri_encrypted": encrypted,
                    "dupe_db.db_name": db_name,
                    "dupe_db.enabled": True,
                    "dupe_db.updated_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return True

    def get_dupe_db_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Public info only — never returns the URI."""
        doc = self.users.find_one({"user_id": user_id}, {"dupe_db": 1})
        if not doc or not doc.get("dupe_db"):
            return None
        d = doc["dupe_db"]
        if not d.get("uri_encrypted"):
            return None
        return {
            "enabled": bool(d.get("enabled", False)),
            "db_name": d.get("db_name") or "dupedb",
            "updated_at": d.get("updated_at"),
            "has_uri": True,
        }

    def disable_dupe_db(self, user_id: int) -> bool:
        self._close_dupe_client(user_id)
        result = self.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "dupe_db.enabled": False,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    def remove_dupe_db(self, user_id: int) -> bool:
        self._close_dupe_client(user_id)
        result = self.users.update_one(
            {"user_id": user_id},
            {
                "$unset": {"dupe_db": ""},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        return result.modified_count > 0

    def _close_dupe_client(self, user_id: int) -> None:
        client = self._dupe_clients.pop(user_id, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _get_dupe_collection(self, user_id: int) -> Optional[Collection]:
        """External message_hashes, or None → use main DB."""
        doc = self.users.find_one({"user_id": user_id}, {"dupe_db": 1})
        if not doc:
            return None
        d = doc.get("dupe_db") or {}
        if not d.get("enabled") or not d.get("uri_encrypted"):
            return None

        if user_id in self._dupe_clients:
            client = self._dupe_clients[user_id]
        else:
            try:
                uri = decrypt_secret(d["uri_encrypted"])
                client = MongoClient(
                    uri,
                    serverSelectionTimeoutMS=8000,
                    connectTimeoutMS=8000,
                    socketTimeoutMS=15000,
                    retryWrites=True,
                )
                client.admin.command("ping")
                self._dupe_clients[user_id] = client
            except Exception:
                self._dupe_clients.pop(user_id, None)
                return None

        db_name = d.get("db_name") or "dupedb"
        return client[db_name]["message_hashes"]

    def try_claim_hash_for_owner(
        self,
        owner_id: int,
        content_hash: str,
        target_chat_id: int,
        source_chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> bool:
        """
        Prefer user's external permanent dupe DB.
        Fallback: main bot message_hashes (7-day TTL).
        True = claimed (forward OK), False = duplicate.
        """
        coll = self._get_dupe_collection(owner_id)
        if coll is None:
            return self.try_claim_hash(
                content_hash, target_chat_id, source_chat_id, message_id
            )

        now = datetime.now(timezone.utc)
        try:
            coll.insert_one({
                "hash": content_hash,
                "target_chat_id": target_chat_id,
                "source_chat_id": source_chat_id,
                "message_id": message_id,
                "owner_id": owner_id,
                "created_at": now,
            })
            return True
        except DuplicateKeyError:
            return False
        except Exception:
            return self.try_claim_hash(
                content_hash, target_chat_id, source_chat_id, message_id
            )

    def clear_dupe_for_owner(
        self,
        owner_id: int,
        target_chat_id: Optional[int] = None,
    ) -> int:
        """Wipe external dupe hashes (or one target). Permanent until this call."""
        coll = self._get_dupe_collection(owner_id)
        if coll is not None:
            query: Dict[str, Any] = {}
            if target_chat_id is not None:
                query["target_chat_id"] = target_chat_id
            return coll.delete_many(query).deleted_count

        if target_chat_id is not None:
            return self.clear_hashes_for_target(target_chat_id)
        return 0

    # -----------------------------------------------------------------------
    # Channel Admins Cache
    # -----------------------------------------------------------------------

    def set_channel_admin(
        self,
        chat_id: int,
        user_id: int,
        is_owner: bool = False,
        status: str = "administrator",
    ) -> None:
        now = datetime.now(timezone.utc)
        self.channel_admins.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": {
                    "is_owner": is_owner,
                    "status": status,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    def remove_channel_admin(self, chat_id: int, user_id: int) -> None:
        self.channel_admins.delete_one({"chat_id": chat_id, "user_id": user_id})

    def is_chat_admin(self, chat_id: int, user_id: int) -> bool:
        return self.channel_admins.find_one(
            {"chat_id": chat_id, "user_id": user_id}
        ) is not None

    def is_chat_owner(self, chat_id: int, user_id: int) -> bool:
        return self.channel_admins.find_one(
            {"chat_id": chat_id, "user_id": user_id, "is_owner": True}
        ) is not None

    def get_chat_admins(self, chat_id: int) -> List[Dict[str, Any]]:
        return list(self.channel_admins.find({"chat_id": chat_id}))

    def clear_chat_admins(self, chat_id: int) -> None:
        self.channel_admins.delete_many({"chat_id": chat_id})

    # -----------------------------------------------------------------------
    # Bot Admins / Owners
    # -----------------------------------------------------------------------

    def is_bot_admin(self, user_id: int) -> bool:
        if user_id in BOT_OWNER_IDS:
            return True
        return self.bot_admins.find_one({"user_id": user_id}) is not None

    def is_bot_owner(self, user_id: int) -> bool:
        if user_id in BOT_OWNER_IDS:
            return True
        doc = self.bot_admins.find_one({"user_id": user_id, "is_owner": True})
        return doc is not None

    def add_bot_admin(self, user_id: int, added_by: int) -> bool:
        if not self.is_bot_owner(added_by):
            return False
        now = datetime.now(timezone.utc)
        try:
            self.bot_admins.insert_one({
                "user_id": user_id,
                "is_owner": False,
                "added_by": added_by,
                "created_at": now,
                "updated_at": now,
            })
            return True
        except DuplicateKeyError:
            return False

    def remove_bot_admin(self, user_id: int, removed_by: int) -> bool:
        if not self.is_bot_owner(removed_by):
            return False
        if self.is_bot_owner(user_id):
            return False
        result = self.bot_admins.delete_one(
            {"user_id": user_id, "is_owner": False}
        )
        return result.deleted_count > 0

    def get_bot_admins(self) -> List[Dict[str, Any]]:
        return list(self.bot_admins.find({}))

    # -----------------------------------------------------------------------
    # Bot settings (admin-only mode, etc.)
    # -----------------------------------------------------------------------

    def is_admin_only(self) -> bool:
        """When True, only bot owners/admins may use the bot."""
        doc = self.stats.find_one({"_id": "global"}, {"admin_only": 1}) or {}
        return bool(doc.get("admin_only", False))

    def set_admin_only(self, enabled: bool) -> None:
        self.stats.update_one(
            {"_id": "global"},
            {
                "$set": {
                    "admin_only": bool(enabled),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    # -----------------------------------------------------------------------
    # User Telegram Sessions (MTProto)
    # -----------------------------------------------------------------------

    def save_user_session(
        self,
        user_id: int,
        session_string: str,
        phone_number: str,
        tg_user_id: int,
        tg_username: Optional[str] = None,
        tg_first_name: Optional[str] = None,
        tg_last_name: Optional[str] = None,
        dc_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        encrypted = encrypt_secret(session_string)
        doc = {
            "user_id": user_id,
            "phone_number": phone_number,
            "session_string": encrypted,
            "tg_user_id": tg_user_id,
            "tg_username": tg_username,
            "tg_first_name": tg_first_name,
            "tg_last_name": tg_last_name,
            "dc_id": dc_id,
            "is_active": True,
            "updated_at": now,
            "last_used": now,
        }
        self.user_sessions.update_one(
            {"user_id": user_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return self.get_user_session(user_id, include_session=False)  # type: ignore

    def get_user_session(
        self,
        user_id: int,
        *,
        include_session: bool = False,
    ) -> Optional[Dict[str, Any]]:
        projection = None if include_session else {"session_string": 0}
        doc = self.user_sessions.find_one({"user_id": user_id}, projection)
        if not doc:
            return None
        if include_session and "session_string" in doc:
            doc["session_string"] = decrypt_secret(doc["session_string"])
        return doc

    def get_decrypted_session_string(self, user_id: int) -> Optional[str]:
        doc = self.get_user_session(user_id, include_session=True)
        return doc.get("session_string") if doc else None

    def has_active_session(self, user_id: int) -> bool:
        return self.user_sessions.find_one(
            {"user_id": user_id, "is_active": True}, {"_id": 1}
        ) is not None

    def mark_session_inactive(self, user_id: int) -> bool:
        result = self.user_sessions.update_one(
            {"user_id": user_id},
            {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0

    def mark_session_active(self, user_id: int) -> bool:
        result = self.user_sessions.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "is_active": True,
                    "updated_at": datetime.now(timezone.utc),
                    "last_used": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    def touch_session(self, user_id: int) -> None:
        self.user_sessions.update_one(
            {"user_id": user_id},
            {"$set": {"last_used": datetime.now(timezone.utc)}},
        )

    def delete_user_session(self, user_id: int) -> bool:
        return self.user_sessions.delete_one({"user_id": user_id}).deleted_count > 0

    def get_all_active_sessions(self) -> List[Dict[str, Any]]:
        return list(
            self.user_sessions.find({"is_active": True}, {"session_string": 0})
        )

    def get_all_sessions_for_startup(self) -> List[Dict[str, Any]]:
        docs = list(self.user_sessions.find({"is_active": True}))
        result = []
        for doc in docs:
            if "session_string" not in doc:
                continue
            try:
                doc["session_string"] = decrypt_secret(doc["session_string"])
                result.append(doc)
            except ValueError:
                self.mark_session_inactive(doc["user_id"])
        return result

    def get_session_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        doc = self.get_user_session(user_id, include_session=False)
        if not doc:
            return None
        return {
            "user_id": doc["user_id"],
            "phone_number": doc.get("phone_number"),
            "tg_user_id": doc.get("tg_user_id"),
            "tg_username": doc.get("tg_username"),
            "tg_first_name": doc.get("tg_first_name"),
            "tg_last_name": doc.get("tg_last_name"),
            "is_active": doc.get("is_active", False),
            "created_at": doc.get("created_at"),
            "last_used": doc.get("last_used"),
            "dc_id": doc.get("dc_id"),
        }

    # -----------------------------------------------------------------------
    # User Forwarding Bots
    # -----------------------------------------------------------------------

    def save_user_bot(
        self,
        user_id: int,
        bot_token: str,
        bot_id: int,
        bot_username: Optional[str] = None,
        bot_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        encrypted = encrypt_secret(bot_token)
        doc = {
            "user_id": user_id,
            "bot_token": encrypted,
            "bot_id": bot_id,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "is_active": True,
            "updated_at": now,
            "last_used": now,
        }
        self.user_bots.update_one(
            {"user_id": user_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return self.get_user_bot_info(user_id)  # type: ignore

    def get_user_bot(
        self,
        user_id: int,
        *,
        include_token: bool = False,
    ) -> Optional[Dict[str, Any]]:
        projection = None if include_token else {"bot_token": 0}
        doc = self.user_bots.find_one({"user_id": user_id}, projection)
        if not doc:
            return None
        if include_token and "bot_token" in doc:
            doc["bot_token"] = decrypt_secret(doc["bot_token"])
        return doc

    def get_decrypted_bot_token(self, user_id: int) -> Optional[str]:
        doc = self.get_user_bot(user_id, include_token=True)
        return doc.get("bot_token") if doc else None

    def has_active_bot(self, user_id: int) -> bool:
        return self.user_bots.find_one(
            {"user_id": user_id, "is_active": True}, {"_id": 1}
        ) is not None

    def get_user_bot_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        doc = self.get_user_bot(user_id, include_token=False)
        if not doc:
            return None
        return {
            "user_id": doc["user_id"],
            "bot_id": doc.get("bot_id"),
            "bot_username": doc.get("bot_username"),
            "bot_name": doc.get("bot_name"),
            "is_active": doc.get("is_active", False),
            "created_at": doc.get("created_at"),
            "last_used": doc.get("last_used"),
        }

    def mark_bot_inactive(self, user_id: int) -> bool:
        result = self.user_bots.update_one(
            {"user_id": user_id},
            {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0

    def mark_bot_active(self, user_id: int) -> bool:
        result = self.user_bots.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "is_active": True,
                    "updated_at": datetime.now(timezone.utc),
                    "last_used": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    def touch_bot(self, user_id: int) -> None:
        self.user_bots.update_one(
            {"user_id": user_id},
            {"$set": {"last_used": datetime.now(timezone.utc)}},
        )

    def delete_user_bot(self, user_id: int) -> bool:
        return self.user_bots.delete_one({"user_id": user_id}).deleted_count > 0

    def get_all_bots_for_startup(self) -> List[Dict[str, Any]]:
        docs = list(self.user_bots.find({"is_active": True}))
        result = []
        for doc in docs:
            if "bot_token" not in doc:
                continue
            try:
                doc["bot_token"] = decrypt_secret(doc["bot_token"])
                result.append(doc)
            except ValueError:
                self.mark_bot_inactive(doc["user_id"])
        return result

    def get_all_active_bots(self) -> List[Dict[str, Any]]:
        return list(self.user_bots.find({"is_active": True}, {"bot_token": 0}))

    # -----------------------------------------------------------------------
    # Forward Rules — Validation
    # -----------------------------------------------------------------------

    def _validate_rule_data(self, data: Dict[str, Any]) -> None:
        if "caption_position" in data and data["caption_position"] not in ALLOWED_CAPTION_POSITIONS:
            raise ValueError(
                f"caption_position must be one of {ALLOWED_CAPTION_POSITIONS}"
            )
        if "allowed_types" in data:
            types = data["allowed_types"]
            if not isinstance(types, list) or not types:
                raise ValueError("allowed_types must be a non-empty list")
            for t in types:
                if t not in ALLOWED_MEDIA_TYPES:
                    raise ValueError(
                        f"Invalid media type: {t}. Allowed: {ALLOWED_MEDIA_TYPES}"
                    )
        if "replacements" in data:
            if not isinstance(data["replacements"], list):
                raise ValueError("replacements must be a list of dicts")
            for item in data["replacements"]:
                if (
                    not isinstance(item, dict)
                    or "from" not in item
                    or "to" not in item
                ):
                    raise ValueError(
                        "Each replacement must be "
                        "{'from': str, 'to': str, 'is_regex': bool}"
                    )
                if "is_regex" in item and not isinstance(item["is_regex"], bool):
                    raise ValueError("is_regex must be a boolean")
        if "block_words" in data:
            self._validate_word_list(data["block_words"], "block_words")
        if "whitelist_words" in data:
            self._validate_word_list(data["whitelist_words"], "whitelist_words")
        if "buttons" in data:
            if not isinstance(data["buttons"], list):
                raise ValueError("buttons must be list of rows")
            for row in data["buttons"]:
                if not isinstance(row, list):
                    raise ValueError("Each button row must be a list")
                for btn in row:
                    if (
                        not isinstance(btn, dict)
                        or "text" not in btn
                        or "url" not in btn
                    ):
                        raise ValueError(
                            "Each button must be {'text': str, 'url': str}"
                        )
        if "delay" in data:
            delay = data["delay"]
            if not isinstance(delay, (int, float)) or delay < 0:
                raise ValueError("delay must be a non-negative number (seconds)")
        if "remove_old_caption" in data and not isinstance(
            data["remove_old_caption"], bool
        ):
            raise ValueError("remove_old_caption must be a boolean")
        if "anti_dupe" in data and not isinstance(data["anti_dupe"], bool):
            raise ValueError("anti_dupe must be a boolean")
        if "forward_tag" in data and not isinstance(data["forward_tag"], bool):
            raise ValueError("forward_tag must be a boolean")
        if "remove_links" in data and not isinstance(data["remove_links"], bool):
            raise ValueError("remove_links must be a boolean")
        if "enabled" in data and not isinstance(data["enabled"], bool):
            raise ValueError("enabled must be a boolean")
        if "custom_caption" in data:
            cc = data["custom_caption"]
            if cc is not None and not isinstance(cc, str):
                raise ValueError("custom_caption must be a string or None")
        if "add_caption" in data:
            ac = data["add_caption"]
            if ac is not None and not isinstance(ac, str):
                raise ValueError("add_caption must be a string or None")
        if "forward_via" in data:
            if data["forward_via"] not in ALLOWED_FORWARD_VIA:
                raise ValueError(
                    f"forward_via must be one of {ALLOWED_FORWARD_VIA}"
                )
        if "content_type" in data:
            from AutoPost.helper.content_type import normalize_content_type
            data["content_type"] = normalize_content_type(data.get("content_type"))
        if "size_filter_enabled" in data and not isinstance(
            data["size_filter_enabled"], bool
        ):
            raise ValueError("size_filter_enabled must be a boolean")
        if "min_media_size" in data:
            try:
                ms = int(data["min_media_size"] or 0)
            except (TypeError, ValueError):
                raise ValueError("min_media_size must be an integer (bytes)")
            if ms < 0:
                raise ValueError("min_media_size must be non-negative")
            data["min_media_size"] = ms
        if "completion_sticker_enabled" in data and not isinstance(
            data["completion_sticker_enabled"], bool
        ):
            raise ValueError("completion_sticker_enabled must be a boolean")
        if "completion_stickers" in data:
            stickers = data["completion_stickers"]
            if not isinstance(stickers, list):
                raise ValueError("completion_stickers must be a list of file_ids")
            data["completion_stickers"] = [
                str(s).strip() for s in stickers if s and str(s).strip()
            ]

    def _validate_word_list(self, words: Any, field_name: str) -> None:
        if not isinstance(words, list):
            raise ValueError(f"{field_name} must be a list")
        for item in words:
            if isinstance(item, str):
                continue
            if isinstance(item, dict):
                if "pattern" not in item:
                    raise ValueError(
                        f"Each {field_name} dict must have 'pattern'"
                    )
                if "is_regex" in item and not isinstance(item["is_regex"], bool):
                    raise ValueError("is_regex must be a boolean")
            else:
                raise ValueError(
                    f"Each item in {field_name} must be str or "
                    "{{'pattern': str, 'is_regex': bool}}"
                )

    # -----------------------------------------------------------------------
    # Forward Rules — CRUD
    # -----------------------------------------------------------------------

    def create_forward_rule(
        self,
        source_chat_id: int,
        target_chat_id: int,
        owner_id: int,
        *,
        enabled: bool = True,
        add_caption: Optional[str] = None,
        caption_position: str = "end",
        custom_caption: Optional[str] = None,
        remove_old_caption: bool = False,
        replacements: Optional[List[Dict[str, Any]]] = None,
        block_words: Optional[List[Union[str, Dict[str, Any]]]] = None,
        whitelist_words: Optional[List[Union[str, Dict[str, Any]]]] = None,
        buttons: Optional[List[List[Dict[str, str]]]] = None,
        forward_tag: bool = False,
        remove_links: bool = False,
        allowed_types: Optional[List[str]] = None,
        delay: float = 0,
        anti_dupe: bool = False,
        forward_via: str = "user_bot",
        content_type: str = "all",
        size_filter_enabled: bool = False,
        min_media_size: int = 0,
        completion_sticker_enabled: bool = False,
        completion_stickers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not self.is_bot_admin(owner_id):
            current_rules = self.forward_rules.count_documents(
                {"owner_id": owner_id}
            )
            if current_rules >= RULE_LIMIT:
                raise ValueError(f"Maximum {RULE_LIMIT} rules allowed per user")
            same_source = self.forward_rules.count_documents(
                {"owner_id": owner_id, "source_chat_id": source_chat_id}
            )
            if same_source >= MAX_TARGETS_PER_SOURCE:
                raise ValueError(
                    f"Maximum {MAX_TARGETS_PER_SOURCE} targets "
                    f"allowed for one source"
                )

        if allowed_types is None:
            allowed_types = ["all"]
        if custom_caption:
            add_caption = None
        if forward_via not in ALLOWED_FORWARD_VIA:
            forward_via = "user_bot"

        data = {
            "source_chat_id": source_chat_id,
            "target_chat_id": target_chat_id,
            "owner_id": owner_id,
            "enabled": enabled,
            "add_caption": add_caption,
            "caption_position": caption_position,
            "custom_caption": custom_caption,
            "remove_old_caption": remove_old_caption,
            "replacements": replacements or [],
            "block_words": block_words or [],
            "whitelist_words": whitelist_words or [],
            "buttons": buttons or [],
            "forward_tag": forward_tag,
            "remove_links": remove_links,
            "allowed_types": allowed_types,
            "delay": float(delay),
            "anti_dupe": anti_dupe,
            "forward_via": forward_via,
            "content_type": content_type or "all",
            "size_filter_enabled": bool(size_filter_enabled),
            "min_media_size": int(min_media_size or 0),
            "completion_sticker_enabled": bool(completion_sticker_enabled),
            "completion_stickers": list(completion_stickers or []),
            "completion_active": {},
            "forwarded_count": 0,
            "blocked_count": 0,
            "failed_count": 0,
            "duplicates_skipped": 0,
        }
        self._validate_rule_data(data)

        now = datetime.now(timezone.utc)
        data["created_at"] = now
        data["updated_at"] = now

        try:
            result = self.forward_rules.insert_one(data)
            data["_id"] = result.inserted_id
            self._invalidate_source_cache(source_chat_id)
            return data
        except DuplicateKeyError:
            raise ValueError(
                f"Forward rule already exists for "
                f"source={source_chat_id} → target={target_chat_id}"
            )

    def update_forward_rule(
        self,
        source_chat_id: int,
        target_chat_id: int,
        updates: Dict[str, Any],
    ) -> bool:
        for key in (
            "source_chat_id", "target_chat_id", "_id", "created_at",
            "forwarded_count", "blocked_count", "failed_count",
            "duplicates_skipped",
        ):
            updates.pop(key, None)

        self._validate_rule_data(updates)
        updates["updated_at"] = datetime.now(timezone.utc)

        result = self.forward_rules.update_one(
            {"source_chat_id": source_chat_id, "target_chat_id": target_chat_id},
            {"$set": updates},
        )
        if result.modified_count > 0 or result.matched_count > 0:
            self._invalidate_source_cache(source_chat_id)
        return result.modified_count > 0

    def delete_forward_rule(
        self, source_chat_id: int, target_chat_id: int
    ) -> bool:
        result = self.forward_rules.delete_one(
            {"source_chat_id": source_chat_id, "target_chat_id": target_chat_id}
        )
        if result.deleted_count > 0:
            self._invalidate_source_cache(source_chat_id)
        return result.deleted_count > 0

    def get_forward_rule(
        self, source_chat_id: int, target_chat_id: int
    ) -> Optional[Dict[str, Any]]:
        return self.forward_rules.find_one(
            {"source_chat_id": source_chat_id, "target_chat_id": target_chat_id}
        )

    def _invalidate_source_cache(
        self, source_chat_id: Optional[int] = None
    ) -> None:
        if source_chat_id is None:
            self._rules_by_source_cache.clear()
        else:
            self._rules_by_source_cache.pop(source_chat_id, None)

    def get_rules_by_source(
        self, source_chat_id: int, only_enabled: bool = True
    ) -> List[Dict[str, Any]]:
        if only_enabled:
            if RULE_CACHE_TTL_SECONDS > 0:
                now = time.monotonic()
                cached = self._rules_by_source_cache.get(source_chat_id)
                if cached is not None:
                    expires_at, rules = cached
                    if now < expires_at:
                        return rules
                rules = list(
                    self.forward_rules.find(
                        {"source_chat_id": source_chat_id, "enabled": True},
                        projection=_RULE_FORWARD_PROJECTION,
                    )
                )
                self._rules_by_source_cache[source_chat_id] = (
                    now + RULE_CACHE_TTL_SECONDS,
                    rules,
                )
                return rules
            return list(
                self.forward_rules.find(
                    {"source_chat_id": source_chat_id, "enabled": True},
                    projection=_RULE_FORWARD_PROJECTION,
                )
            )
        return list(
            self.forward_rules.find(
                {"source_chat_id": source_chat_id},
                projection=_RULE_FORWARD_PROJECTION,
            )
        )

    def get_rules_by_target(
        self, target_chat_id: int, only_enabled: bool = True
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"target_chat_id": target_chat_id}
        if only_enabled:
            query["enabled"] = True
        return list(self.forward_rules.find(query))

    def get_rules_by_owner(self, owner_id: int) -> List[Dict[str, Any]]:
        return list(self.forward_rules.find({"owner_id": owner_id}))

    def get_all_enabled_rules(self) -> List[Dict[str, Any]]:
        return list(self.forward_rules.find({"enabled": True}))

    def get_total_rules(self) -> int:
        return self.forward_rules.count_documents({})

    def get_total_enabled_rules(self) -> int:
        return self.forward_rules.count_documents({"enabled": True})

    def get_total_disabled_rules(self) -> int:
        return self.forward_rules.count_documents({"enabled": False})

    def set_rule_enabled(
        self, source_chat_id: int, target_chat_id: int, enabled: bool
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"enabled": enabled}
        )

    def delete_all_rules(self) -> int:
        result = self.forward_rules.delete_many({})
        self._invalidate_source_cache(None)
        return result.deleted_count

    def delete_all_rules_of_user(self, owner_id: int) -> int:
        sources = self.forward_rules.distinct(
            "source_chat_id", {"owner_id": owner_id}
        )
        result = self.forward_rules.delete_many({"owner_id": owner_id})
        for sid in sources:
            self._invalidate_source_cache(sid)
        return result.deleted_count

    # -----------------------------------------------------------------------
    # Caption / Replacements / Block / Whitelist / Buttons / etc.
    # -----------------------------------------------------------------------

    def set_add_caption(
        self,
        source_chat_id: int,
        target_chat_id: int,
        caption: Optional[str],
        position: str = "end",
    ) -> bool:
        if position not in ALLOWED_CAPTION_POSITIONS:
            raise ValueError(f"Invalid caption_position: {position}")
        return self.update_forward_rule(
            source_chat_id,
            target_chat_id,
            {
                "add_caption": caption,
                "caption_position": position,
                "custom_caption": None,
            },
        )

    def set_custom_caption(
        self,
        source_chat_id: int,
        target_chat_id: int,
        template: Optional[str],
    ) -> bool:
        updates: Dict[str, Any] = {"custom_caption": template}
        if template:
            updates["add_caption"] = None
        return self.update_forward_rule(source_chat_id, target_chat_id, updates)

    def clear_custom_caption(
        self, source_chat_id: int, target_chat_id: int
    ) -> bool:
        return self.set_custom_caption(source_chat_id, target_chat_id, None)

    def set_remove_old_caption(
        self, source_chat_id: int, target_chat_id: int, remove: bool
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"remove_old_caption": remove}
        )

    def set_replacements(
        self,
        source_chat_id: int,
        target_chat_id: int,
        replacements: List[Dict[str, Any]],
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"replacements": replacements}
        )

    def add_replacement(
        self,
        source_chat_id: int,
        target_chat_id: int,
        old: str,
        new: str,
        is_regex: bool = False,
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        replacements = rule.get("replacements", [])
        if any(
            r.get("from") == old
            and r.get("to") == new
            and r.get("is_regex", False) == is_regex
            for r in replacements
        ):
            return True
        replacements.append({"from": old, "to": new, "is_regex": is_regex})
        return self.set_replacements(source_chat_id, target_chat_id, replacements)

    def remove_replacement(
        self, source_chat_id: int, target_chat_id: int, old: str
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        replacements = [
            r for r in rule.get("replacements", []) if r.get("from") != old
        ]
        return self.set_replacements(source_chat_id, target_chat_id, replacements)

    def clear_replacements(
        self, source_chat_id: int, target_chat_id: int
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"replacements": []}
        )

    def set_block_words(
        self,
        source_chat_id: int,
        target_chat_id: int,
        words: List[Union[str, Dict[str, Any]]],
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id,
            target_chat_id,
            {"block_words": self._normalize_word_list(words)},
        )

    def add_block_word(
        self,
        source_chat_id: int,
        target_chat_id: int,
        word: Union[str, Dict[str, Any]],
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        words = list(rule.get("block_words", []))
        normalized = self._normalize_single_word(word)
        if normalized and normalized not in words:
            words.append(normalized)
        return self.set_block_words(source_chat_id, target_chat_id, words)

    def remove_block_word(
        self, source_chat_id: int, target_chat_id: int, word: str
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        words = []
        for w in rule.get("block_words", []):
            pattern = w if isinstance(w, str) else w.get("pattern", "")
            if pattern != word.strip():
                words.append(w)
        return self.set_block_words(source_chat_id, target_chat_id, words)

    def set_whitelist_words(
        self,
        source_chat_id: int,
        target_chat_id: int,
        words: List[Union[str, Dict[str, Any]]],
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id,
            target_chat_id,
            {"whitelist_words": self._normalize_word_list(words)},
        )

    def add_whitelist_word(
        self,
        source_chat_id: int,
        target_chat_id: int,
        word: Union[str, Dict[str, Any]],
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        words = list(rule.get("whitelist_words", []))
        normalized = self._normalize_single_word(word)
        if normalized and normalized not in words:
            words.append(normalized)
        return self.set_whitelist_words(source_chat_id, target_chat_id, words)

    def remove_whitelist_word(
        self, source_chat_id: int, target_chat_id: int, word: str
    ) -> bool:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return False
        words = []
        for w in rule.get("whitelist_words", []):
            pattern = w if isinstance(w, str) else w.get("pattern", "")
            if pattern != word.strip():
                words.append(w)
        return self.set_whitelist_words(source_chat_id, target_chat_id, words)

    def _normalize_word_list(
        self, words: List[Union[str, Dict[str, Any]]]
    ) -> List[Union[str, Dict[str, Any]]]:
        result = []
        for w in words:
            norm = self._normalize_single_word(w)
            if norm is not None:
                result.append(norm)
        return result

    def _normalize_single_word(
        self, word: Union[str, Dict[str, Any]]
    ) -> Optional[Union[str, Dict[str, Any]]]:
        if isinstance(word, str):
            w = word.strip()
            return w if w else None
        if isinstance(word, dict) and "pattern" in word:
            pattern = str(word["pattern"]).strip()
            if not pattern:
                return None
            return {
                "pattern": pattern,
                "is_regex": bool(word.get("is_regex", False)),
            }
        return None

    def set_buttons(
        self,
        source_chat_id: int,
        target_chat_id: int,
        buttons: List[List[Dict[str, str]]],
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"buttons": buttons}
        )

    def set_forward_tag(
        self, source_chat_id: int, target_chat_id: int, enabled: bool
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"forward_tag": enabled}
        )

    def set_remove_links(
        self, source_chat_id: int, target_chat_id: int, enabled: bool
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"remove_links": enabled}
        )

    def set_allowed_types(
        self, source_chat_id: int, target_chat_id: int, types: List[str]
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"allowed_types": types}
        )

    def set_delay(
        self, source_chat_id: int, target_chat_id: int, delay_seconds: float
    ) -> bool:
        if delay_seconds < 0:
            raise ValueError("delay must be non-negative")
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"delay": float(delay_seconds)}
        )

    def set_anti_dupe(
        self, source_chat_id: int, target_chat_id: int, enabled: bool
    ) -> bool:
        return self.update_forward_rule(
            source_chat_id, target_chat_id, {"anti_dupe": enabled}
        )

    def set_content_type(
        self, source_chat_id: int, target_chat_id: int, content_type: str
    ) -> bool:
        from AutoPost.helper.content_type import normalize_content_type
        return self.update_forward_rule(
            source_chat_id,
            target_chat_id,
            {"content_type": normalize_content_type(content_type)},
        )

    def set_size_filter(
        self,
        source_chat_id: int,
        target_chat_id: int,
        enabled: Optional[bool] = None,
        min_bytes: Optional[int] = None,
    ) -> bool:
        updates: Dict[str, Any] = {}
        if enabled is not None:
            updates["size_filter_enabled"] = bool(enabled)
        if min_bytes is not None:
            updates["min_media_size"] = int(min_bytes)
        if not updates:
            return False
        return self.update_forward_rule(source_chat_id, target_chat_id, updates)

    def set_completion_sticker(
        self,
        source_chat_id: int,
        target_chat_id: int,
        *,
        enabled: Optional[bool] = None,
        stickers: Optional[List[str]] = None,
    ) -> bool:
        updates: Dict[str, Any] = {}
        if enabled is not None:
            updates["completion_sticker_enabled"] = bool(enabled)
        if stickers is not None:
            updates["completion_stickers"] = list(stickers)
        if not updates:
            return False
        return self.update_forward_rule(source_chat_id, target_chat_id, updates)

    def set_completion_active(
        self,
        source_chat_id: int,
        target_chat_id: int,
        active: Dict[str, Any],
    ) -> bool:
        """Update batch tracker without invalidating the live-rule cache."""
        result = self.forward_rules.update_one(
            {
                "source_chat_id": source_chat_id,
                "target_chat_id": target_chat_id,
            },
            {
                "$set": {
                    "completion_active": active or {},
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.matched_count > 0

    # -----------------------------------------------------------------------
    # Anti-Duplication (main bot DB — 7 day TTL)
    # -----------------------------------------------------------------------

    def is_duplicate(self, content_hash: str, target_chat_id: int) -> bool:
        return self.message_hashes.find_one(
            {"hash": content_hash, "target_chat_id": target_chat_id}
        ) is not None

    def try_claim_hash(
        self,
        content_hash: str,
        target_chat_id: int,
        source_chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        try:
            self.message_hashes.insert_one({
                "hash": content_hash,
                "target_chat_id": target_chat_id,
                "source_chat_id": source_chat_id,
                "message_id": message_id,
                "created_at": now,
            })
            return True
        except DuplicateKeyError:
            return False

    def add_hash(
        self,
        content_hash: str,
        target_chat_id: int,
        source_chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> bool:
        return self.try_claim_hash(
            content_hash, target_chat_id, source_chat_id, message_id
        )

    def clear_hashes_for_target(self, target_chat_id: int) -> int:
        return self.message_hashes.delete_many(
            {"target_chat_id": target_chat_id}
        ).deleted_count

    def clear_old_hashes(self, older_than_days: int = 30) -> int:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        return self.message_hashes.delete_many(
            {"created_at": {"$lt": cutoff}}
        ).deleted_count

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------

    def increment_stat(
        self,
        field: str,
        amount: int = 1,
        source_chat_id: Optional[int] = None,
        target_chat_id: Optional[int] = None,
    ) -> None:
        allowed = {
            "total_forwarded",
            "total_blocked",
            "total_failed",
            "total_duplicates_skipped",
        }
        if field not in allowed:
            raise ValueError(f"Unknown stat field: {field}")

        now = datetime.now(timezone.utc)
        inc_ops: Dict[str, Any] = {field: amount}
        if field == "total_forwarded" and amount > 0:
            today = self._today_str()
            inc_ops[f"daily_forwarded.{today}"] = amount

        self.stats.update_one(
            {"_id": "global"},
            {"$inc": inc_ops, "$set": {"updated_at": now}},
            upsert=True,
        )

        if source_chat_id is not None and target_chat_id is not None:
            rule_field_map = {
                "total_forwarded": "forwarded_count",
                "total_blocked": "blocked_count",
                "total_failed": "failed_count",
                "total_duplicates_skipped": "duplicates_skipped",
            }
            self.forward_rules.update_one(
                {
                    "source_chat_id": source_chat_id,
                    "target_chat_id": target_chat_id,
                },
                {
                    "$inc": {rule_field_map[field]: amount},
                    "$set": {"updated_at": now},
                },
            )

    def record_forward_success(
        self,
        source_chat_id: int,
        target_chat_id: int,
        owner_id: Optional[int] = None,
    ) -> None:
        self.increment_stat(
            "total_forwarded",
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )

    def record_blocked(self, source_chat_id: int, target_chat_id: int) -> None:
        self.increment_stat(
            "total_blocked",
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )

    def record_failed(self, source_chat_id: int, target_chat_id: int) -> None:
        self.increment_stat(
            "total_failed",
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )

    def record_duplicate_skipped(
        self, source_chat_id: int, target_chat_id: int
    ) -> None:
        self.increment_stat(
            "total_duplicates_skipped",
            source_chat_id=source_chat_id,
            target_chat_id=target_chat_id,
        )

    def get_last_30_days_forwarded(self) -> int:
        from datetime import timedelta
        doc = self.stats.find_one({"_id": "global"}, {"daily_forwarded": 1}) or {}
        daily: Dict[str, Any] = doc.get("daily_forwarded") or {}
        if not daily:
            return 0
        today = datetime.now(timezone.utc).date()
        cutoff = today - timedelta(days=29)
        total = 0
        stale_keys: List[str] = []
        for day_str, count in daily.items():
            try:
                day = datetime.strptime(day_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                stale_keys.append(day_str)
                continue
            if day < cutoff:
                stale_keys.append(day_str)
            else:
                try:
                    total += int(count or 0)
                except (TypeError, ValueError):
                    pass
        if stale_keys:
            self.stats.update_one(
                {"_id": "global"},
                {"$unset": {f"daily_forwarded.{k}": "" for k in stale_keys}},
            )
        return total

    def get_stats(self) -> Dict[str, Any]:
        doc = self.stats.find_one({"_id": "global"}) or {}
        return {
            "total_users": self.get_total_users(),
            "total_rules": self.get_total_rules(),
            "enabled_rules": self.get_total_enabled_rules(),
            "disabled_rules": self.get_total_disabled_rules(),
            "total_bot_admins": self.bot_admins.count_documents({}),
            "total_forwarded": doc.get("total_forwarded", 0),
            "total_blocked": doc.get("total_blocked", 0),
            "total_failed": doc.get("total_failed", 0),
            "total_duplicates_skipped": doc.get("total_duplicates_skipped", 0),
            "last_30_days_forwarded": self.get_last_30_days_forwarded(),
            "stats_updated_at": doc.get("updated_at"),
            "daily_forward_limit": DAILY_FORWARD_LIMIT,
            "active_user_sessions": self.user_sessions.count_documents(
                {"is_active": True}
            ),
            "active_user_bots": self.user_bots.count_documents(
                {"is_active": True}
            ),
        }

    def get_rule_stats(
        self, source_chat_id: int, target_chat_id: int
    ) -> Optional[Dict[str, int]]:
        rule = self.get_forward_rule(source_chat_id, target_chat_id)
        if not rule:
            return None
        return {
            "forwarded_count": rule.get("forwarded_count", 0),
            "blocked_count": rule.get("blocked_count", 0),
            "failed_count": rule.get("failed_count", 0),
            "duplicates_skipped": rule.get("duplicates_skipped", 0),
        }

    # -----------------------------------------------------------------------
    # DB Stats / Wipe
    # -----------------------------------------------------------------------

    def get_db_stats(self) -> Dict[str, Any]:
        try:
            db_stats = self.db.command("dbStats")
        except Exception as e:
            return {"error": str(e)}

        collections_info = []
        total_docs = 0
        for coll_name in self.db.list_collection_names():
            try:
                coll_stats = self.db.command("collStats", coll_name)
                count = coll_stats.get("count", 0)
                size = coll_stats.get("size", 0)
                storage_size = coll_stats.get("storageSize", 0)
                total_docs += count
                collections_info.append({
                    "name": coll_name,
                    "documents": count,
                    "size_bytes": size,
                    "storage_size_bytes": storage_size,
                    "size_mb": round(size / (1024 * 1024), 3),
                    "storage_mb": round(storage_size / (1024 * 1024), 3),
                })
            except Exception:
                collections_info.append({
                    "name": coll_name,
                    "documents": 0,
                    "size_bytes": 0,
                    "storage_size_bytes": 0,
                    "size_mb": 0.0,
                    "storage_mb": 0.0,
                })
        collections_info.sort(key=lambda x: x["size_bytes"], reverse=True)
        return {
            "db_name": DB_NAME,
            "collections_count": len(collections_info),
            "total_documents": total_docs,
            "data_size_bytes": db_stats.get("dataSize", 0),
            "storage_size_bytes": db_stats.get("storageSize", 0),
            "index_size_bytes": db_stats.get("indexSize", 0),
            "data_size_mb": round(
                db_stats.get("dataSize", 0) / (1024 * 1024), 3
            ),
            "storage_size_mb": round(
                db_stats.get("storageSize", 0) / (1024 * 1024), 3
            ),
            "index_size_mb": round(
                db_stats.get("indexSize", 0) / (1024 * 1024), 3
            ),
            "collections": collections_info,
        }

    def wipe_database(self) -> Dict[str, int]:
        for uid in list(self._dupe_clients.keys()):
            self._close_dupe_client(uid)
        result = {}
        for coll_name in list(self.db.list_collection_names()):
            try:
                count = self.db[coll_name].count_documents({})
                self.db.drop_collection(coll_name)
                result[coll_name] = count
            except Exception:
                result[coll_name] = -1
        self._ensure_indexes()
        self._ensure_owners()
        self._ensure_stats_doc()
        return result

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except PyMongoError:
            return False

    def close(self) -> None:
        for uid in list(self._dupe_clients.keys()):
            self._close_dupe_client(uid)
        self.client.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_db_instance: Optional[Database] = None


def get_db(uri: str = MONGO_URI, db_name: str = DB_NAME) -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(uri, db_name)
    return _db_instance
