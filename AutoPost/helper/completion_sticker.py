"""Title-change stickers for Auto Forward.

A sticker is sent immediately BEFORE the first file of a new movie title
or a new series season — not after a quiet timeout, and not on every
quality / episode.

  Thor (2011) 480p / 720p / 1080p     → one sticker (same title)
  Man of Steel (2013)                 → new sticker (title changed)
  Thor (2011) 480p                    → new sticker (title changed back)
  Mirzapur S01 E01 / E02 / Combined   → one sticker (same season)
  Mirzapur S02 E01                    → new sticker (season changed)

Requires parsett==1.8.5 for stable title / season keys (via content_type).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Dict, Optional, Tuple

from AutoPost.Database.database import get_db
from AutoPost.helper.content_type import get_content_group_key, title_changed

logger = logging.getLogger("CompletionSticker")
db = get_db()

# (source_id, target_id) → Lock
_locks: Dict[Tuple[int, int], asyncio.Lock] = {}
# (source_id, target_id) → {last_key: str|None, need_retry: bool}
_state: Dict[Tuple[int, int], dict] = {}


def _rt(source_id: int, target_id: int) -> Tuple[int, int]:
    return (int(source_id), int(target_id))


def _lock_for(source_id: int, target_id: int) -> asyncio.Lock:
    key = _rt(source_id, target_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _state_for(source_id: int, target_id: int) -> dict:
    key = _rt(source_id, target_id)
    st = _state.get(key)
    if st is None:
        st = {"last_key": None, "need_retry": False}
        _state[key] = st
    return st


def _stickers_from_rule(rule: dict) -> list:
    raw = rule.get("completion_stickers") or []
    out = []
    for s in raw:
        sid = str(s).strip() if s is not None else ""
        if sid:
            out.append(sid)
    return out


def feature_on(rule: Optional[dict]) -> bool:
    if not rule:
        return False
    return bool(rule.get("completion_sticker_enabled")) and bool(_stickers_from_rule(rule))


def pick_sticker(file_ids: list) -> Optional[str]:
    ids = [str(x).strip() for x in (file_ids or []) if x]
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    return secrets.choice(ids)


def _fresh_rule(source_id: int, target_id: int) -> dict:
    return db.get_forward_rule(source_id, target_id) or {}


def _load_feature(source_id: int, target_id: int, rule: Optional[dict] = None) -> Tuple[bool, list]:
    live = rule if rule is not None else _fresh_rule(source_id, target_id)
    return bool(live.get("completion_sticker_enabled")), _stickers_from_rule(live)


async def send_completion_sticker(client, target_chat_id: int, file_id: str) -> bool:
    if not client or not file_id:
        return False
    try:
        await client.send_sticker(int(target_chat_id), file_id)
        return True
    except Exception as e:
        name = type(e).__name__
        if name in ("FloodWait", "SlowmodeWait"):
            wait = int(getattr(e, "value", 0) or 0)
            logger.warning(
                "title-change sticker FloodWait %ss target=%s", wait, target_chat_id
            )
            try:
                await asyncio.sleep(min(max(wait, 1), 120))
                await client.send_sticker(int(target_chat_id), file_id)
                return True
            except Exception:
                logger.exception("title-change sticker retry failed")
                return False
        logger.exception("title-change sticker send failed target=%s", target_chat_id)
        return False


async def flush_active_group(client, source_id: int, target_id: int) -> None:
    """No open batches in the title-change model — kept for call-site compatibility."""
    return


async def prepare_before_forward(client, rule: dict, message: Any) -> None:
    """Send sticker before the first file of a new movie title / series season."""
    source_id = int(rule["source_chat_id"])
    target_id = int(rule["target_chat_id"])

    async with _lock_for(source_id, target_id):
        enabled, stickers = _load_feature(source_id, target_id, rule)
        if not enabled or not stickers:
            return

        group_key = get_content_group_key(message)
        st = _state_for(source_id, target_id)
        last = st.get("last_key")

        if not title_changed(last, group_key):
            return

        # Advance last_key even if send fails so we do not spam retries on every file
        st["last_key"] = group_key
        fid = pick_sticker(stickers)
        if not fid:
            return

        ok = await send_completion_sticker(client, target_id, fid)
        if ok:
            logger.info(
                "title-change sticker %s → %s group=%s",
                source_id,
                target_id,
                group_key,
            )
            st["need_retry"] = False
        else:
            st["need_retry"] = True
            logger.warning(
                "title-change sticker FAILED %s → %s group=%s",
                source_id,
                target_id,
                group_key,
            )


async def on_successful_forward(client, rule: dict, message: Any) -> None:
    """Keep last_key in sync. Never send here — sticker must precede the files."""
    source_id = int(rule["source_chat_id"])
    target_id = int(rule["target_chat_id"])

    async with _lock_for(source_id, target_id):
        enabled, _ = _load_feature(source_id, target_id, rule)
        if not enabled:
            return
        group_key = get_content_group_key(message)
        if not group_key:
            return
        st = _state_for(source_id, target_id)
        if st.get("last_key") is None:
            st["last_key"] = group_key
