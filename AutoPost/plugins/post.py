"""
post.py (forward engine)
Complete Auto-Forward Engine with Daily Quota + Media Group (Album) Support
Kurigram (Pyrogram fork) | Python 3.14 | PyMongo

Architecture:
- Main management bot does NOT forward.
- Actual forwarding is done by:
    • User's own Bot  (forward_via = "user_bot")
    • User Account    (forward_via = "user_account")
- Handlers are attached inside UserBotManager / UserClientManager.
- This module only provides process_and_forward / album helpers.

Rules:
- forward_tag ON  → pure message.forward() (no editing).
- forward_tag OFF → full processing.

Caption modes (priority):
1. custom_caption (HTML template with {caption})
2. add_caption + caption_position
3. Original entities preserved when no text mutation
4. Rebuild path for replacements / remove_links

Media groups (albums):
- Buffer ~2.8s, then send_media_group().
- Anti-dupe + daily quota applied once per album.

Anti-duplication:
- Only for media that have file_unique_id (photo/video/document/...).
- Pure text is never treated as duplicate.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Union

from pyrogram import Client
from pyrogram.enums import MessageEntityType, ParseMode
from pyrogram.errors import (
    ChatWriteForbidden,
    FloodWait,
    MessageIdInvalid,
    PeerIdInvalid,
    RPCError,
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
    AccessTokenInvalid,
    AccessTokenExpired,
)
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    MessageEntity,
)

from AutoPost.Database.database import get_db
from AutoPost.helper import clean_file_name
from AutoPost.helper.user_clients import get_user_client_manager
from AutoPost.helper.user_bots import get_user_bot_manager
from AutoPost.helper.content_type import (
    apply_content_type_filter,
    apply_content_type_filter_album,
    content_filter_log_line,
    normalize_content_type,
)
from AutoPost.helper.media_size import get_message_file_size, passes_size_filter
from AutoPost.helper.completion_sticker import (
    on_successful_forward,
    prepare_before_forward,
)

logger = logging.getLogger(__name__)
db = get_db()

# ---------------------------------------------------------------------------
# Media-group buffer settings
# ---------------------------------------------------------------------------
ALBUM_WAIT_SECONDS = 2.8
_album_buffers: Dict[Tuple[int, str], List[Message]] = defaultdict(list)
_album_tasks: Dict[Tuple[int, str], asyncio.Task] = {}
_album_lock = asyncio.Lock()

FORWARD_CONCURRENCY = 1  # was 5 – sequential to preserve bulk order
_forward_sem = asyncio.Semaphore(FORWARD_CONCURRENCY)


# ---------------------------------------------------------------------------
# UTF-16 helpers
# ---------------------------------------------------------------------------

def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def clone_entity(e: MessageEntity, new_offset: Optional[int] = None) -> MessageEntity:
    return MessageEntity(
        type=e.type,
        offset=e.offset if new_offset is None else new_offset,
        length=e.length,
        url=getattr(e, "url", None),
        user=getattr(e, "user", None),
        language=getattr(e, "language", None),
        custom_emoji_id=getattr(e, "custom_emoji_id", None),
    )


def shift_entities(
    entities: Optional[List[MessageEntity]],
    delta: int,
) -> Optional[List[MessageEntity]]:
    if not entities or delta == 0:
        return entities
    return [clone_entity(e, e.offset + delta) for e in entities]


def _utf16_slice(text: str, start: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    start_b = start * 2
    end_b = (start + length) * 2
    return encoded[start_b:end_b].decode("utf-16-le")


def entities_to_html(
    text: Optional[str],
    entities: Optional[List[MessageEntity]],
) -> str:
    if not text:
        return ""
    if not entities:
        return html_lib.escape(text)

    events: List[Tuple[int, int, Optional[MessageEntity]]] = []
    for e in entities:
        events.append((e.offset, -1, e))
        events.append((e.offset + e.length, 1, e))
    events.sort(key=lambda x: (x[0], x[1]))

    parts: List[str] = []
    cursor = 0

    def _open_tag(e: MessageEntity) -> str:
        t = e.type
        if t == MessageEntityType.BOLD:
            return "<b>"
        if t == MessageEntityType.ITALIC:
            return "<i>"
        if t == MessageEntityType.UNDERLINE:
            return "<u>"
        if t == MessageEntityType.STRIKETHROUGH:
            return "<s>"
        if t == MessageEntityType.SPOILER:
            return "<spoiler>"
        if t == MessageEntityType.CODE:
            return "<code>"
        if t == MessageEntityType.PRE:
            lang = getattr(e, "language", None) or ""
            return f'<pre language="{html_lib.escape(lang)}">' if lang else "<pre>"
        if t == MessageEntityType.TEXT_LINK and getattr(e, "url", None):
            return f'<a href="{html_lib.escape(e.url)}">'
        if t == MessageEntityType.TEXT_MENTION and getattr(e, "user", None):
            return f'<a href="tg://user?id={e.user.id}">'
        if t == MessageEntityType.BLOCKQUOTE:
            return "<blockquote>"
        return ""

    def _close_tag(e: MessageEntity) -> str:
        t = e.type
        if t == MessageEntityType.BOLD:
            return "</b>"
        if t == MessageEntityType.ITALIC:
            return "</i>"
        if t == MessageEntityType.UNDERLINE:
            return "</u>"
        if t == MessageEntityType.STRIKETHROUGH:
            return "</s>"
        if t == MessageEntityType.SPOILER:
            return "</spoiler>"
        if t == MessageEntityType.CODE:
            return "</code>"
        if t == MessageEntityType.PRE:
            return "</pre>"
        if t in (MessageEntityType.TEXT_LINK, MessageEntityType.TEXT_MENTION):
            return "</a>"
        if t == MessageEntityType.BLOCKQUOTE:
            return "</blockquote>"
        return ""

    open_stack: List[MessageEntity] = []
    for pos, kind, ent in events:
        if pos > cursor:
            chunk = _utf16_slice(text, cursor, pos - cursor)
            parts.append(html_lib.escape(chunk))
            cursor = pos
        if kind == -1:
            tag = _open_tag(ent)
            if tag:
                parts.append(tag)
                open_stack.append(ent)
        else:
            if open_stack and open_stack[-1] is ent:
                open_stack.pop()
                tag = _close_tag(ent)
                if tag:
                    parts.append(tag)
            else:
                tag = _close_tag(ent)
                if tag:
                    parts.append(tag)

    total_u16 = utf16_len(text)
    if cursor < total_u16:
        chunk = _utf16_slice(text, cursor, total_u16 - cursor)
        parts.append(html_lib.escape(chunk))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def replace_whole_word(text: str, old: str, new: str) -> str:
    if not old:
        return text
    pattern = r"(?<![@\w])" + re.escape(old) + r"(?!\w)"
    try:
        return re.sub(pattern, new, text, flags=re.IGNORECASE)
    except re.error:
        return text


def apply_replacements(
    text: Optional[str],
    replacements: List[dict],
) -> Optional[str]:
    if not text or not replacements:
        return text
    result = text
    for item in replacements:
        old = item.get("from", "")
        new = item.get("to", "")
        is_regex = item.get("is_regex", False)
        if not old:
            continue
        try:
            if is_regex:
                result = re.sub(old, new, result, flags=re.IGNORECASE)
            else:
                result = replace_whole_word(result, old, new)
        except re.error as e:
            logger.warning(f"Invalid regex in replacement '{old}': {e}")
            result = replace_whole_word(result, old, new)
    return result


def process_original_text(original: Optional[str], rule: dict) -> Optional[str]:
    if rule.get("remove_old_caption"):
        return None
    processed = apply_replacements(original, rule.get("replacements", []))
    if rule.get("remove_links") and processed:
        processed = clean_file_name(processed)
    return processed


def can_preserve_entities(rule: dict) -> bool:
    if rule.get("custom_caption"):
        return False
    if (rule.get("add_caption") or "").strip():
        return False
    if rule.get("replacements"):
        return False
    if rule.get("remove_links"):
        return False
    if rule.get("remove_old_caption"):
        return False
    return True


def _merge_html_caption(original_html: str, add_html: str, position: str) -> str:
    add_html = add_html.strip()
    if not add_html:
        return original_html
    if not original_html:
        return add_html
    if position == "start":
        return f"{add_html}\n{original_html}"
    if position == "end_with_gap":
        return f"{original_html}\n\n{add_html}"
    return f"{original_html}\n{add_html}"


def build_final_caption_and_entities(
    message: Message,
    rule: dict,
) -> Tuple[Optional[str], Optional[List[MessageEntity]], bool]:
    original = message.caption or message.text
    raw_entities = message.caption_entities or message.entities
    entities: List[MessageEntity] = list(raw_entities) if raw_entities else []

    template = (rule.get("custom_caption") or "").strip()
    if template:
        processed = process_original_text(original, rule)
        if processed is None:
            caption_value = ""
        elif processed == original and entities:
            caption_value = entities_to_html(original, entities)
        else:
            caption_value = html_lib.escape(processed) if processed else ""
        return template.replace("{caption}", caption_value), None, True

    add = (rule.get("add_caption") or "").strip()
    if add:
        if (
            not rule.get("replacements")
            and not rule.get("remove_links")
            and not rule.get("remove_old_caption")
        ):
            original_html = entities_to_html(original, entities)
        else:
            processed = process_original_text(original, rule)
            original_html = html_lib.escape(processed) if processed else ""
        pos = rule.get("caption_position", "end")
        return _merge_html_caption(original_html, add, pos), None, True

    if can_preserve_entities(rule):
        return original, entities or None, False

    processed = process_original_text(original, rule)
    return processed, None, False


def _plain_word_match(word: str, text: str) -> bool:
    if not word:
        return False
    pattern = r"(?<![@\w])" + re.escape(word) + r"(?!\w)"
    try:
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    except re.error:
        return word.lower() in text.lower()


def _match_word_list(text: Optional[str], words: List[Union[str, dict]]) -> bool:
    if not text or not words:
        return False
    for item in words:
        if isinstance(item, str):
            if _plain_word_match(item, text):
                return True
        elif isinstance(item, dict):
            pattern = item.get("pattern", "")
            is_regex = item.get("is_regex", False)
            if not pattern:
                continue
            try:
                if is_regex:
                    if re.search(pattern, text, flags=re.IGNORECASE):
                        return True
                else:
                    if _plain_word_match(pattern, text):
                        return True
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                if _plain_word_match(pattern, text):
                    return True
    return False


def is_blocked(text: Optional[str], block_words: list) -> bool:
    return _match_word_list(text, block_words)


def is_whitelisted(text: Optional[str], whitelist_words: list) -> bool:
    if not whitelist_words:
        return True
    return _match_word_list(text, whitelist_words)


def build_keyboard(buttons: List[List[dict]]) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    keyboard = []
    for row in buttons:
        btn_row = []
        for btn in row:
            text = btn.get("text")
            url = btn.get("url")
            if text and url:
                btn_row.append(InlineKeyboardButton(text=text, url=url))
        if btn_row:
            keyboard.append(btn_row)
    if not keyboard:
        return None
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Media type helpers
# ---------------------------------------------------------------------------

def get_message_type(message: Message) -> str:
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.poll:
        return "poll"
    if message.contact:
        return "contact"
    if message.location:
        return "location"
    if message.venue:
        return "venue"
    if message.text or message.caption:
        return "text"
    return "unknown"


def is_type_allowed(msg_type: str, allowed_types: List[str]) -> bool:
    if not allowed_types or "all" in allowed_types:
        return True
    return msg_type in allowed_types


def _size_settings(rule: dict) -> dict:
    return {
        "size_filter_enabled": bool(rule.get("size_filter_enabled")),
        "min_media_size": int(rule.get("min_media_size") or 0),
    }


def album_passes_size_filter(messages: List[Message], rule: dict) -> bool:
    """Skip the album only if every sized file is below the minimum."""
    settings = _size_settings(rule)
    if not settings["size_filter_enabled"] or settings["min_media_size"] <= 0:
        return True
    any_known = False
    any_ok = False
    for m in messages:
        sz = get_message_file_size(m)
        if sz is None:
            continue
        any_known = True
        ok, _reason = passes_size_filter(m, settings)
        if ok:
            any_ok = True
    if not any_known:
        return True
    return any_ok


# ---------------------------------------------------------------------------
# Anti-duplication — ONLY media with file_unique_id
# ---------------------------------------------------------------------------

def get_content_hash(message: Message) -> Optional[str]:
    for attr in (
        "photo", "video", "document", "animation",
        "audio", "voice", "sticker",
    ):
        media = getattr(message, attr, None)
        if media and getattr(media, "file_unique_id", None):
            return f"file:{media.file_unique_id}"
    return None


def get_album_hash(messages: List[Message]) -> Optional[str]:
    ids = []
    for m in messages:
        h = get_content_hash(m)
        if h:
            ids.append(h)
    if not ids:
        return None
    ids.sort()
    return "album:" + hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# InputMedia builders
# ---------------------------------------------------------------------------

def _to_input_media(
    message: Message,
    caption: Optional[str] = None,
    caption_entities: Optional[List[MessageEntity]] = None,
    parse_mode: Optional[str] = None,
) -> Optional[object]:
    has_spoiler = getattr(message, "has_media_spoiler", False)
    ents = None if parse_mode else caption_entities
    pm = parse_mode

    if message.photo:
        return InputMediaPhoto(
            media=message.photo.file_id,
            caption=caption,
            caption_entities=ents,
            parse_mode=pm,
            has_spoiler=has_spoiler,
        )
    if message.video:
        return InputMediaVideo(
            media=message.video.file_id,
            caption=caption,
            caption_entities=ents,
            parse_mode=pm,
            duration=message.video.duration,
            width=message.video.width,
            height=message.video.height,
            has_spoiler=has_spoiler,
        )
    if message.animation:
        return InputMediaAnimation(
            media=message.animation.file_id,
            caption=caption,
            caption_entities=ents,
            parse_mode=pm,
            duration=message.animation.duration,
            width=message.animation.width,
            height=message.animation.height,
            has_spoiler=has_spoiler,
        )
    if message.document:
        return InputMediaDocument(
            media=message.document.file_id,
            caption=caption,
            caption_entities=ents,
            parse_mode=pm,
        )
    if message.audio:
        return InputMediaAudio(
            media=message.audio.file_id,
            caption=caption,
            caption_entities=ents,
            parse_mode=pm,
            duration=message.audio.duration,
            performer=message.audio.performer,
            title=message.audio.title,
        )
    return None


# ---------------------------------------------------------------------------
# Resolve send client from rule.forward_via
# Main management bot is NEVER used for sending.
# ---------------------------------------------------------------------------

class ForwardClientUnavailable(Exception):
    """Raised when the required user bot / user session is not available."""


async def _get_forward_client(owner_id: Optional[int], rule: dict) -> Client:
    """
    Return the Client that must perform the send.

    forward_via:
      - "user_bot"      → UserBotManager
      - "user_account"  → UserClientManager

    Never falls back to the main management bot.
    """
    if owner_id is None:
        raise ForwardClientUnavailable("Rule has no owner_id")

    forward_via = rule.get("forward_via") or "user_bot"

    if forward_via == "user_account":
        manager = get_user_client_manager()
        client = await manager.get_client(owner_id)
        if client and client.is_connected:
            return client
        raise ForwardClientUnavailable(
            f"User account session not available for owner {owner_id}"
        )

    # Default / "user_bot"
    manager = get_user_bot_manager()
    client = await manager.get_bot(owner_id)
    if client and client.is_connected:
        return client
    raise ForwardClientUnavailable(
        f"User bot not available for owner {owner_id}"
    )


async def _mark_client_dead(owner_id: int, rule: dict) -> None:
    """Mark the correct resource inactive after auth errors."""
    forward_via = rule.get("forward_via") or "user_bot"
    if forward_via == "user_account":
        db.mark_session_inactive(owner_id)
        try:
            await get_user_client_manager().stop_user_client(
                owner_id, delete_session=False
            )
        except Exception:
            pass
    else:
        db.mark_bot_inactive(owner_id)
        try:
            await get_user_bot_manager().stop_user_bot(
                owner_id, delete_from_db=False
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Single-message send
# ---------------------------------------------------------------------------

async def _send_single(
    client: Client,
    message: Message,
    rule: dict,
    target_id: int,
    source_id: int,
    owner_id: Optional[int],
) -> None:
    final_caption, caption_entities, use_html = build_final_caption_and_entities(
        message, rule
    )
    reply_markup = build_keyboard(rule.get("buttons", []))
    parse_mode = ParseMode.HTML if use_html else None
    ents = None if use_html else caption_entities

    delay = float(rule.get("delay", 0) or 0)
    if delay > 0:
        await asyncio.sleep(delay)

    if message.photo:
        await client.send_photo(
            chat_id=target_id,
            photo=message.photo.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            has_spoiler=getattr(message, "has_media_spoiler", False),
        )
    elif message.video:
        await client.send_video(
            chat_id=target_id,
            video=message.video.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            duration=message.video.duration,
            width=message.video.width,
            height=message.video.height,
            reply_markup=reply_markup,
            has_spoiler=getattr(message, "has_media_spoiler", False),
        )
    elif message.animation:
        await client.send_animation(
            chat_id=target_id,
            animation=message.animation.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            duration=message.animation.duration,
            width=message.animation.width,
            height=message.animation.height,
            reply_markup=reply_markup,
            has_spoiler=getattr(message, "has_media_spoiler", False),
        )
    elif message.document:
        await client.send_document(
            chat_id=target_id,
            document=message.document.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            file_name=message.document.file_name,
            reply_markup=reply_markup,
        )
    elif message.audio:
        await client.send_audio(
            chat_id=target_id,
            audio=message.audio.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            duration=message.audio.duration,
            performer=message.audio.performer,
            title=message.audio.title,
            reply_markup=reply_markup,
        )
    elif message.voice:
        await client.send_voice(
            chat_id=target_id,
            voice=message.voice.file_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            duration=message.voice.duration,
            reply_markup=reply_markup,
        )
    elif message.sticker:
        await client.send_sticker(
            chat_id=target_id,
            sticker=message.sticker.file_id,
            reply_markup=reply_markup,
        )
    elif message.poll:
        await message.copy(chat_id=target_id, reply_markup=reply_markup)
    elif message.text:
        await client.send_message(
            chat_id=target_id,
            text=final_caption or message.text,
            entities=ents,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    else:
        await message.copy(
            chat_id=target_id,
            caption=final_caption,
            caption_entities=ents,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    db.record_forward_success(source_id, target_id, owner_id=owner_id)
    logger.info(
        f"Forwarded {get_message_type(message)} from {source_id} → {target_id}"
    )


# ---------------------------------------------------------------------------
# Album send
# ---------------------------------------------------------------------------

async def _send_album(
    receive_client: Client,
    messages: List[Message],
    rule: dict,
) -> None:
    if not messages:
        return

    messages = sorted(messages, key=lambda m: m.id)
    target_id = rule["target_chat_id"]
    source_id = rule["source_chat_id"]
    owner_id = rule.get("owner_id")

    if not rule.get("enabled", True):
        return

    first_type = get_message_type(messages[0])
    if not is_type_allowed(first_type, rule.get("allowed_types", ["all"])):
        return

    ct_setting = normalize_content_type(rule.get("content_type", "all"))
    if ct_setting != "all":
        ok, reason = apply_content_type_filter_album(messages, ct_setting)
        if not ok:
            logger.info(
                content_filter_log_line(reason.split(":")[-1], ct_setting)
                + f" album {source_id} → {target_id}"
            )
            return

    if not album_passes_size_filter(messages, rule):
        logger.info(
            f"Album skipped (media size) {source_id} → {target_id}"
        )
        return

    check_text = None
    for m in messages:
        if m.caption or m.text:
            check_text = m.caption or m.text
            break

    if is_blocked(check_text, rule.get("block_words", [])):
        db.record_blocked(source_id, target_id)
        return
    if not is_whitelisted(check_text, rule.get("whitelist_words", [])):
        db.record_blocked(source_id, target_id)
        return

    if rule.get("anti_dupe"):
        album_hash = get_album_hash(messages)
        if album_hash and not db.try_claim_hash(
            album_hash,
            target_id,
            source_chat_id=source_id,
            message_id=messages[0].id,
        ):
            logger.info(f"Album duplicate skipped: {source_id} → {target_id}")
            db.record_duplicate_skipped(source_id, target_id)
            return

    if owner_id is not None and not db.try_consume_quota(owner_id):
        logger.info(
            f"Quota exceeded for owner {owner_id} | "
            f"album skipped {source_id} → {target_id}"
        )
        return

    try:
        send_client = await _get_forward_client(owner_id, rule)
    except ForwardClientUnavailable as e:
        logger.warning(str(e))
        db.record_failed(source_id, target_id)
        return

    async def _do_album_send() -> None:
        if rule.get("forward_tag"):
            delay = float(rule.get("delay", 0) or 0)
            if delay > 0:
                await asyncio.sleep(delay)
            # forward() must run on the client that owns the messages
            for m in messages:
                await m.forward(target_id)
            db.record_forward_success(source_id, target_id, owner_id=owner_id)
            logger.info(
                f"Album forwarded (tag ON) {len(messages)} items "
                f"{source_id} → {target_id}"
            )
            return

        media_list = []
        for m in messages:
            cap, ents, use_html = build_final_caption_and_entities(m, rule)
            pm = ParseMode.HTML if use_html else None
            im = _to_input_media(m, cap, ents, parse_mode=pm)
            if im is not None:
                media_list.append(im)

        if not media_list:
            return

        delay = float(rule.get("delay", 0) or 0)
        if delay > 0:
            await asyncio.sleep(delay)

        await send_client.send_media_group(chat_id=target_id, media=media_list)

        reply_markup = build_keyboard(rule.get("buttons", []))
        if reply_markup:
            await send_client.send_message(
                chat_id=target_id,
                text="‎",
                reply_markup=reply_markup,
            )

        db.record_forward_success(source_id, target_id, owner_id=owner_id)
        logger.info(
            f"Album sent ({len(media_list)} items) {source_id} → {target_id}"
        )

    max_flood_retries = 2
    for attempt in range(max_flood_retries + 1):
        try:
            try:
                await prepare_before_forward(send_client, rule, messages[0])
            except Exception:
                logger.exception("completion sticker prepare (album) failed")
            await _do_album_send()
            try:
                await on_successful_forward(send_client, rule, messages[0])
            except Exception:
                logger.exception("completion sticker track (album) failed")
            return
        except (AuthKeyUnregistered, SessionRevoked, UserDeactivated,
                AccessTokenInvalid, AccessTokenExpired) as e:
            logger.warning(
                f"Auth dead for owner {owner_id} ({rule.get('forward_via')}): "
                f"{type(e).__name__}"
            )
            if owner_id:
                await _mark_client_dead(owner_id, rule)
            db.record_failed(source_id, target_id)
            return
        except FloodWait as e:
            if attempt < max_flood_retries:
                logger.warning(
                    f"FloodWait {e.value}s album → {target_id}, "
                    f"retry {attempt + 1}/{max_flood_retries}"
                )
                await asyncio.sleep(e.value + 1)
                continue
            logger.warning(f"FloodWait exhausted album → {target_id}")
            db.record_failed(source_id, target_id)
            return
        except (ChatWriteForbidden, PeerIdInvalid, MessageIdInvalid) as e:
            logger.error(f"Cannot write album to {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return
        except RPCError as e:
            logger.error(f"RPCError album → {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return
        except Exception as e:
            logger.exception(f"Unexpected album error → {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return


# ---------------------------------------------------------------------------
# Core: one message + one rule
# ---------------------------------------------------------------------------

async def process_and_forward(
    client: Client,
    message: Message,
    rule: dict,
) -> None:
    """
    Called by UserBotManager / UserClientManager handlers.
    `client` is the client that received the message (user bot or user account).
    Send client is resolved again from rule.forward_via for safety.
    """
    target_id = rule["target_chat_id"]
    source_id = rule["source_chat_id"]
    owner_id = rule.get("owner_id")

    if not rule.get("enabled", True):
        return

    msg_type = get_message_type(message)
    if not is_type_allowed(msg_type, rule.get("allowed_types", ["all"])):
        return

    ct_setting = normalize_content_type(rule.get("content_type", "all"))
    if ct_setting != "all":
        ok, reason = apply_content_type_filter(message, ct_setting)
        if not ok:
            logger.info(
                content_filter_log_line(reason.split(":")[-1], ct_setting)
                + f" {source_id} → {target_id}"
            )
            return

    ok_sz, reason_sz = passes_size_filter(message, _size_settings(rule))
    if not ok_sz:
        logger.info(
            f"Skipped {source_id} → {target_id} ({reason_sz})"
        )
        return

    original_text = message.text or message.caption

    if is_blocked(original_text, rule.get("block_words", [])):
        logger.info(f"Blocked {source_id} → {target_id} (block word)")
        db.record_blocked(source_id, target_id)
        return

    if not is_whitelisted(original_text, rule.get("whitelist_words", [])):
        logger.info(f"Skipped {source_id} → {target_id} (whitelist)")
        db.record_blocked(source_id, target_id)
        return

    if rule.get("anti_dupe"):
        content_hash = get_content_hash(message)
        if content_hash and not db.try_claim_hash(
            content_hash,
            target_id,
            source_chat_id=source_id,
            message_id=message.id,
        ):
            logger.info(f"Duplicate skipped: {source_id} → {target_id}")
            db.record_duplicate_skipped(source_id, target_id)
            return

    if owner_id is not None and not db.try_consume_quota(owner_id):
        logger.info(
            f"Quota exceeded for owner {owner_id} | "
            f"skipped {source_id} → {target_id}"
        )
        return

    try:
        send_client = await _get_forward_client(owner_id, rule)
    except ForwardClientUnavailable as e:
        logger.warning(str(e))
        db.record_failed(source_id, target_id)
        return

    async def _do_forward() -> None:
        if rule.get("forward_tag"):
            delay = float(rule.get("delay", 0) or 0)
            if delay > 0:
                await asyncio.sleep(delay)
            # Must use the receiving client for forward()
            await message.forward(target_id)
            db.record_forward_success(source_id, target_id, owner_id=owner_id)
            logger.info(
                f"Forwarded (tag ON) {msg_type} {source_id} → {target_id}"
            )
        else:
            await _send_single(
                send_client, message, rule, target_id, source_id, owner_id
            )

    max_flood_retries = 2
    for attempt in range(max_flood_retries + 1):
        try:
            try:
                await prepare_before_forward(send_client, rule, message)
            except Exception:
                logger.exception("completion sticker prepare failed")
            await _do_forward()
            try:
                await on_successful_forward(send_client, rule, message)
            except Exception:
                logger.exception("completion sticker track failed")
            return
        except (AuthKeyUnregistered, SessionRevoked, UserDeactivated,
                AccessTokenInvalid, AccessTokenExpired) as e:
            logger.warning(
                f"Auth dead for owner {owner_id} ({rule.get('forward_via')}): "
                f"{type(e).__name__}"
            )
            if owner_id:
                await _mark_client_dead(owner_id, rule)
            db.record_failed(source_id, target_id)
            return
        except FloodWait as e:
            if attempt < max_flood_retries:
                logger.warning(
                    f"FloodWait {e.value}s → {target_id}, "
                    f"retry {attempt + 1}/{max_flood_retries}"
                )
                await asyncio.sleep(e.value + 1)
                continue
            logger.warning(f"FloodWait exhausted → {target_id}")
            db.record_failed(source_id, target_id)
            return
        except (ChatWriteForbidden, PeerIdInvalid, MessageIdInvalid) as e:
            logger.error(f"Cannot write to {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return
        except RPCError as e:
            logger.error(f"RPCError → {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return
        except Exception as e:
            logger.exception(f"Unexpected error → {target_id}: {e}")
            db.record_failed(source_id, target_id)
            return


# ---------------------------------------------------------------------------
# Album buffer (shared by user bots / user clients)
# ---------------------------------------------------------------------------

async def _flush_album(client: Client, key: Tuple[int, str]) -> None:
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    await asyncio.sleep(0.4)

    async with _album_lock:
        messages = _album_buffers.pop(key, [])
        _album_tasks.pop(key, None)

    if not messages:
        return

    source_id = key[0]
    rules = db.get_rules_by_source(source_id, only_enabled=True)
    if not rules:
        return

    async def _run_album_rule(rule: dict) -> None:
        async with _forward_sem:
            try:
                await _send_album(client, messages, rule)
            except FloodWait:
                return
            except Exception as e:
                logger.exception(
                    f"Album rule failed {rule.get('_id')} | "
                    f"{source_id} → {rule['target_chat_id']}: {e}"
                )

    await asyncio.gather(
        *(_run_album_rule(rule) for rule in rules),
        return_exceptions=True,
    )


async def _handle_album_message(client: Client, message: Message) -> None:
    source_id = message.chat.id
    group_id = message.media_group_id
    key = (source_id, str(group_id))

    async with _album_lock:
        _album_buffers[key].append(message)
        old = _album_tasks.pop(key, None)
        if old and not old.done():
            old.cancel()
        _album_tasks[key] = asyncio.create_task(_flush_album(client, key))


# ---------------------------------------------------------------------------
# NOTE: No @Client.on_message on the main management bot.
# Handlers live in UserBotManager and UserClientManager only.
# ---------------------------------------------------------------------------
