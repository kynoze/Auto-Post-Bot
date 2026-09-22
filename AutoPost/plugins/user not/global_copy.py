"""
global_copy.py
Global Copy commands + external anti-dupe DB + filter commands.

/globalcopy [target|off]
/setdupedb /dupedb /cleardupe /removedupedb
/gstatus /gblock /gunblock /gwhite /gunwhite /greplace /gunreplace
/gantidupe /gdelay
"""

from __future__ import annotations

import logging
from typing import List, Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatMemberStatus, ChatType
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from AutoPost.Database.database import get_db
from AutoPost.helper.user_clients import get_user_client_manager
from AutoPost.bot import app

LOGGER = logging.getLogger("GlobalCopy")
db = get_db()

_MEDIA_CHOICES = {
    "all": {
        "label": "📦 All",
        "types": ["all"],
        "desc": "Every message type",
    },
    "document": {
        "label": "📄 Document",
        "types": ["document"],
        "desc": "Documents / files only",
    },
    "video": {
        "label": "🎬 Videos",
        "types": ["video"],
        "desc": "Videos only",
    },
    "audio": {
        "label": "🎵 Audio",
        "types": ["audio", "voice"],
        "desc": "Audio & voice notes",
    },
    "media": {
        "label": "🎞 Media",
        "types": ["video", "document"],
        "desc": "Videos + Documents",
    },
}


def _require_session(user_id: int) -> Optional[str]:
    if not db.has_active_session(user_id):
        return (
            "❌ <b>User account required.</b>\n"
            "Link your account with /connect first."
        )
    return None


def _require_gcopy(user_id: int) -> Optional[str]:
    gc = db.get_global_copy(user_id)
    if not gc or not gc.get("enabled"):
        return (
            "❌ <b>Global Copy is OFF.</b>\n"
            "Enable it first:\n"
            "<code>/globalcopy &lt;target&gt;</code>"
        )
    return None


async def resolve_chat_id(client: Client, text: str) -> Optional[int]:
    text = text.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        chat = await client.get_chat(text)
        return chat.id
    except Exception:
        return None


def _media_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                _MEDIA_CHOICES["all"]["label"],
                callback_data=f"gcopy:all:{target_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                _MEDIA_CHOICES["document"]["label"],
                callback_data=f"gcopy:document:{target_id}",
            ),
            InlineKeyboardButton(
                _MEDIA_CHOICES["video"]["label"],
                callback_data=f"gcopy:video:{target_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                _MEDIA_CHOICES["audio"]["label"],
                callback_data=f"gcopy:audio:{target_id}",
            ),
            InlineKeyboardButton(
                _MEDIA_CHOICES["media"]["label"],
                callback_data=f"gcopy:media:{target_id}",
            ),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="gcopy:cancel"),
        ],
    ])


# ---------------------------------------------------------------------------
# Debug — confirms this plugin's handlers are alive
# ---------------------------------------------------------------------------

@app.on_message(filters.command("gping") & filters.private)
async def gping_cmd(client: Client, message: Message):
    await message.reply_text(
        "✅ <b>global_copy plugin is loaded and receiving commands.</b>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /globalcopy
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["globalcopy", "gcopy"]) & filters.private)
async def globalcopy_cmd(client: Client, message: Message):
    try:
        await _globalcopy_cmd_impl(client, message)
    except Exception as e:
        LOGGER.exception("globalcopy_cmd failed")
        try:
            await message.reply_text(
                f"❌ <b>Error in /globalcopy</b>\n"
                f"<code>{type(e).__name__}: {e}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def _globalcopy_cmd_impl(client: Client, message: Message):
    user_id = message.from_user.id

    if not db.has_active_session(user_id):
        return await message.reply_text(
            "❌ <b>User Account required</b>\n\n"
            "Global Copy uses your personal Telegram account to receive "
            "messages from groups, channels, private chats, and bots, "
            "then copies them into one target chat.\n\n"
            "Please link your account first with /connect.",
            parse_mode=ParseMode.HTML,
        )

    args = message.command[1:] if len(message.command) > 1 else []

    if not args:
        gc = db.get_global_copy(user_id)
        if not gc or not gc.get("enabled"):
            return await message.reply_text(
                "<b>Global Copy — OFF</b>\n\n"
                "Forward <b>all incoming messages</b> from your account "
                "(groups, channels, private chats, bots) into a single target chat.\n\n"
                "<b>How to enable</b>\n"
                "<code>/globalcopy &lt;target_chat&gt;</code>\n\n"
                "Examples:\n"
                "<code>/globalcopy @mybackup</code>\n"
                "<code>/globalcopy -1001234567890</code>\n\n"
                "You will then choose which media types to copy "
                "(All / Document / Videos / Audio / Media).\n\n"
                "<b>Disable</b>\n"
                "<code>/globalcopy off</code>\n\n"
                "Filters: /gstatus · /gblock · /gwhite · /greplace",
                parse_mode=ParseMode.HTML,
            )

        types = ", ".join(gc.get("allowed_types") or ["all"])
        return await message.reply_text(
            "<b>Global Copy — ON</b> 🟢\n\n"
            f"<b>Target:</b> <code>{gc.get('target_chat_id')}</code>\n"
            f"<b>Types:</b> <code>{types}</code>\n\n"
            "Disable: <code>/globalcopy off</code>\n"
            "Details: /gstatus\n"
            "Change target/types: /globalcopy &lt;new_target&gt;",
            parse_mode=ParseMode.HTML,
        )

    if args[0].lower() in ("off", "disable", "stop"):
        db.disable_global_copy(user_id)
        return await message.reply_text(
            "✅ Global Copy has been <b>disabled</b>.",
            parse_mode=ParseMode.HTML,
        )

    target = await resolve_chat_id(client, args[0])
    if not target:
        return await message.reply_text(
            "❌ Invalid target chat.\n"
            "Use a username, invite link, or numeric chat ID.",
            parse_mode=ParseMode.HTML,
        )

    manager = get_user_client_manager()
    user_client = await manager.get_client(user_id)

    if not user_client or not user_client.is_connected:
        return await message.reply_text(
            "❌ Your user account session is offline.\n"
            "Use /account → Reconnect, or /connect again.",
            parse_mode=ParseMode.HTML,
        )

    try:
        member = await user_client.get_chat_member(target, "me")
        status = getattr(member.status, "value", str(member.status)).lower()
        if status in ("left", "kicked", "banned"):
            return await message.reply_text(
                "❌ Your account is not a member of the target chat.",
                parse_mode=ParseMode.HTML,
            )

        chat = await user_client.get_chat(target)
        chat_type = getattr(chat.type, "name", str(chat.type)).upper()
        if chat_type == "CHANNEL" and member.status not in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            return await message.reply_text(
                "❌ For a <b>channel</b> target you must be an "
                "<b>Admin</b> (with permission to post messages).",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        return await message.reply_text(
            f"❌ Could not verify the target chat: "
            f"<code>{type(e).__name__}</code>\n"
            "Make sure your account is a member/admin there.",
            parse_mode=ParseMode.HTML,
        )

    await message.reply_text(
        "<b>Choose what to copy</b>\n\n"
        f"Target: <code>{target}</code>\n\n"
        "📦 <b>All</b> — every message type\n"
        "📄 <b>Document</b> — files / documents only\n"
        "🎬 <b>Videos</b> — videos only\n"
        "🎵 <b>Audio</b> — audio & voice notes\n"
        "🎞 <b>Media</b> — videos + documents\n\n"
        "Tap a button below:",
        parse_mode=ParseMode.HTML,
        reply_markup=_media_keyboard(target),
    )


@app.on_callback_query(filters.regex(r"^gcopy:"))
async def globalcopy_callback(client: Client, callback: CallbackQuery):
    data = callback.data or ""
    user_id = callback.from_user.id

    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Only available in private chat.", show_alert=True)
        return

    if data == "gcopy:cancel":
        await callback.message.edit_text("❌ Cancelled.")
        await callback.answer()
        return

    parts = data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid data.", show_alert=True)
        return

    _, kind, target_str = parts
    if kind not in _MEDIA_CHOICES:
        await callback.answer("Unknown option.", show_alert=True)
        return

    try:
        target_id = int(target_str)
    except ValueError:
        await callback.answer("Invalid target.", show_alert=True)
        return

    if not db.has_active_session(user_id):
        await callback.message.edit_text(
            "❌ User account session is missing. Use /connect first."
        )
        await callback.answer()
        return

    choice = _MEDIA_CHOICES[kind]
    allowed_types: List[str] = choice["types"]

    try:
        db.set_global_copy(
            user_id=user_id,
            enabled=True,
            target_chat_id=target_id,
            allowed_types=allowed_types,
        )
    except ValueError as e:
        await callback.message.edit_text(f"❌ {e}")
        await callback.answer()
        return

    types_str = ", ".join(allowed_types)
    await callback.message.edit_text(
        "<b>✅ Global Copy enabled</b>\n\n"
        f"<b>Target:</b> <code>{target_id}</code>\n"
        f"<b>Filter:</b> {choice['label']} — {choice['desc']}\n"
        f"<b>Types:</b> <code>{types_str}</code>\n\n"
        "Incoming messages on your account that match this filter "
        "will be copied to the target chat.\n\n"
        "Filters: /gstatus · /gblock · /gwhite · /greplace\n"
        "Disable: <code>/globalcopy off</code>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Saved")


# ===========================================================================
# External Duplicate DB
# ===========================================================================

@app.on_message(filters.command("setdupedb") & filters.private)
async def setdupedb_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Set your own anti-dupe MongoDB</b>\n\n"
            "Duplicate hashes will be stored in <b>your</b> Atlas DB "
            "(permanent until you clear them).\n\n"
            "<b>Usage:</b>\n"
            "<code>/setdupedb mongodb+srv://user:pass@cluster/dbname</code>\n\n"
            "⚠️ Never share this URI. The message with the URI will be deleted.",
            parse_mode=ParseMode.HTML,
        )

    uri = message.text.split(None, 1)[1].strip()

    try:
        await message.delete()
    except Exception:
        pass

    status = await client.send_message(
        message.chat.id,
        "🔄 Validating connection...",
    )

    try:
        db.set_dupe_db(user_id, uri)
    except ValueError as e:
        return await status.edit_text(f"❌ {e}")
    except Exception as e:
        LOGGER.exception("set_dupe_db failed")
        return await status.edit_text(
            f"❌ Failed: <code>{type(e).__name__}</code>",
            parse_mode=ParseMode.HTML,
        )

    info = db.get_dupe_db_info(user_id)
    await status.edit_text(
        "<b>✅ External dupe DB connected</b>\n\n"
        f"<b>Database:</b> <code>{info.get('db_name') if info else 'dupedb'}</code>\n"
        f"<b>Status:</b> 🟢 Enabled\n\n"
        "Use /dupedb for status · /cleardupe to wipe · /removedupedb to unlink.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command(["dupedb", "dupeinfo"]) & filters.private)
async def dupedb_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    info = db.get_dupe_db_info(user_id)

    if not info:
        return await message.reply_text(
            "<b>No external dupe DB</b>\n\n"
            "Using main bot DB (7-day TTL) for anti-dupe.\n\n"
            "Set your own permanent DB:\n"
            "<code>/setdupedb mongodb+srv://...</code>",
            parse_mode=ParseMode.HTML,
        )

    docs = size_mb = storage_mb = "N/A"
    try:
        coll = db._get_dupe_collection(user_id)
        if coll is not None:
            docs = coll.count_documents({})
            try:
                stats = coll.database.command("collStats", coll.name)
                size_mb = round(stats.get("size", 0) / (1024 * 1024), 3)
                storage_mb = round(stats.get("storageSize", 0) / (1024 * 1024), 3)
            except Exception:
                pass
    except Exception:
        pass

    emoji = "🟢" if info.get("enabled") else "🔴"
    await message.reply_text(
        f"<b>Your External Dupe DB</b> {emoji}\n\n"
        f"<b>Status:</b> {'Enabled' if info.get('enabled') else 'Disabled'}\n"
        f"<b>Database:</b> <code>{info.get('db_name')}</code>\n"
        f"<b>Hash documents:</b> <code>{docs}</code>\n"
        f"<b>Data size:</b> <code>{size_mb}</code> MB\n"
        f"<b>Storage:</b> <code>{storage_mb}</code> MB\n"
        f"<b>Updated:</b> <code>{info.get('updated_at')}</code>\n\n"
        "URI is stored encrypted and never shown.\n\n"
        "<b>Commands</b>\n"
        "• /cleardupe — delete all hashes\n"
        "• /cleardupe &lt;target_id&gt; — one target\n"
        "• /removedupedb — unlink this DB",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗑 Clear all hashes",
                    callback_data="dupedb:clear_confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔓 Remove DB config",
                    callback_data="dupedb:remove_confirm",
                ),
            ],
        ]),
    )


@app.on_message(filters.command("cleardupe") & filters.private)
async def cleardupe_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    info = db.get_dupe_db_info(user_id)

    target = None
    if len(message.command) >= 2:
        raw = message.command[1].strip()
        if raw.lstrip("-").isdigit():
            target = int(raw)
        else:
            return await message.reply_text(
                "❌ Invalid target id.\n"
                "<code>/cleardupe</code> or <code>/cleardupe -100123...</code>",
                parse_mode=ParseMode.HTML,
            )

    if not info and target is None:
        return await message.reply_text(
            "No external dupe DB configured.\n"
            "To clear main-DB hashes for one target:\n"
            "<code>/cleardupe &lt;target_chat_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    if target is None:
        return await message.reply_text(
            "<b>Clear ALL anti-dupe hashes?</b>\n\n"
            "This cannot be undone. Duplicates may be forwarded again.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Yes, clear all",
                        callback_data="dupedb:clear_yes",
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="dupedb:cancel",
                    ),
                ]
            ]),
        )

    n = db.clear_dupe_for_owner(user_id, target_chat_id=target)
    await message.reply_text(
        f"✅ Cleared <code>{n}</code> hash(es) for target <code>{target}</code>.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.command(["removedupedb", "unlinkdupedb"]) & filters.private
)
async def removedupedb_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not db.get_dupe_db_info(user_id):
        return await message.reply_text("No external dupe DB to remove.")

    await message.reply_text(
        "<b>Remove external dupe DB config?</b>\n\n"
        "Hashes already stored in that Atlas stay there.\n"
        "Bot will fall back to main DB (7-day TTL).",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Remove",
                    callback_data="dupedb:remove_yes",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="dupedb:cancel",
                ),
            ]
        ]),
    )


@app.on_callback_query(filters.regex(r"^dupedb:"))
async def dupedb_callbacks(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Private only.", show_alert=True)
        return

    if data == "dupedb:cancel":
        await callback.message.edit_text("Cancelled.")
        await callback.answer()
        return

    if data == "dupedb:clear_confirm":
        await callback.message.edit_text(
            "<b>Clear ALL hashes in your dupe DB?</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Yes", callback_data="dupedb:clear_yes"
                    ),
                    InlineKeyboardButton(
                        "❌ No", callback_data="dupedb:cancel"
                    ),
                ]
            ]),
        )
        await callback.answer()
        return

    if data == "dupedb:clear_yes":
        await callback.answer("Clearing...")
        n = db.clear_dupe_for_owner(user_id, target_chat_id=None)
        await callback.message.edit_text(
            f"✅ Cleared <b>{n}</b> hash document(s).",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "dupedb:remove_confirm":
        await callback.message.edit_text(
            "<b>Unlink external dupe DB?</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Yes", callback_data="dupedb:remove_yes"
                    ),
                    InlineKeyboardButton(
                        "❌ No", callback_data="dupedb:cancel"
                    ),
                ]
            ]),
        )
        await callback.answer()
        return

    if data == "dupedb:remove_yes":
        await callback.answer("Removing...")
        db.remove_dupe_db(user_id)
        await callback.message.edit_text(
            "✅ External dupe DB unlinked.\n"
            "Anti-dupe now uses main bot DB (7-day TTL).",
            parse_mode=ParseMode.HTML,
        )
        return


# ===========================================================================
# Global Copy filters
# ===========================================================================

@app.on_message(filters.command(["gstatus", "gcopyinfo"]) & filters.private)
async def gstatus_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    err = _require_session(user_id)
    if err:
        return await message.reply_text(err, parse_mode=ParseMode.HTML)

    gc = db.get_global_copy(user_id)
    if not gc or not gc.get("enabled"):
        return await message.reply_text(
            "<b>Global Copy — OFF</b>\n\n"
            "<code>/globalcopy &lt;target&gt;</code> to enable.",
            parse_mode=ParseMode.HTML,
        )

    def _n(lst):
        return len(lst) if isinstance(lst, list) else 0

    await message.reply_text(
        "<b>Global Copy — ON</b> 🟢\n\n"
        f"<b>Target:</b> <code>{gc.get('target_chat_id')}</code>\n"
        f"<b>Types:</b> <code>{', '.join(gc.get('allowed_types') or ['all'])}</code>\n"
        f"<b>Anti-dupe:</b> {'ON' if gc.get('anti_dupe') else 'OFF'}\n"
        f"<b>Delay:</b> <code>{gc.get('delay') or 0}</code>s\n"
        f"<b>Forward tag:</b> {'ON' if gc.get('forward_tag') else 'OFF'}\n\n"
        f"<b>Block words:</b> {_n(gc.get('block_words'))}\n"
        f"<b>Whitelist:</b> {_n(gc.get('whitelist_words'))}\n"
        f"<b>Replacements:</b> {_n(gc.get('replacements'))}\n"
        f"<b>Buttons:</b> {_n(gc.get('buttons'))}\n\n"
        "<b>Filter commands</b>\n"
        "/gblock · /gwhite · /greplace · /gantidupe · /gdelay",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command(["gblock", "gblocks"]) & filters.private)
async def gblock_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    args = message.command[1:]
    gc = db.get_global_copy(user_id)
    words = list(gc.get("block_words") or [])

    if message.command[0].lower() == "gblocks" or not args:
        if not words:
            return await message.reply_text(
                "No block words.\n"
                "Add: <code>/gblock spam word</code>",
                parse_mode=ParseMode.HTML,
            )
        lines = []
        for w in words:
            if isinstance(w, str):
                lines.append(f"• <code>{w}</code>")
            else:
                flag = "regex" if w.get("is_regex") else "plain"
                lines.append(
                    f"• <code>{w.get('pattern')}</code> ({flag})"
                )
        return await message.reply_text(
            "<b>Global Copy — Block list</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    text = " ".join(args).strip()
    if not text:
        return await message.reply_text(
            "Usage: <code>/gblock word</code>",
            parse_mode=ParseMode.HTML,
        )

    existing = [
        w if isinstance(w, str) else w.get("pattern") for w in words
    ]
    if text not in existing:
        words.append(text)
        db.update_global_copy_filters(user_id, {"block_words": words})

    await message.reply_text(
        f"✅ Blocked for Global Copy: <code>{text}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command(["gunblock", "gdelblock"]) & filters.private)
async def gunblock_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: <code>/gunblock word</code>",
            parse_mode=ParseMode.HTML,
        )

    text = " ".join(message.command[1:]).strip()
    gc = db.get_global_copy(user_id)
    words = []
    for w in gc.get("block_words") or []:
        pattern = w if isinstance(w, str) else w.get("pattern", "")
        if pattern != text:
            words.append(w)
    db.update_global_copy_filters(user_id, {"block_words": words})
    await message.reply_text(
        f"✅ Removed block: <code>{text}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.command(["gwhite", "gwhites", "gwhitelist"]) & filters.private
)
async def gwhite_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    args = message.command[1:]
    gc = db.get_global_copy(user_id)
    words = list(gc.get("whitelist_words") or [])

    if message.command[0].lower() == "gwhites" or not args:
        if not words:
            return await message.reply_text(
                "No whitelist words (empty = allow all text).\n"
                "Add: <code>/gwhite keyword</code>",
                parse_mode=ParseMode.HTML,
            )
        lines = [
            f"• <code>{w if isinstance(w, str) else w.get('pattern')}</code>"
            for w in words
        ]
        return await message.reply_text(
            "<b>Global Copy — Whitelist</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    text = " ".join(args).strip()
    existing = [
        w if isinstance(w, str) else w.get("pattern") for w in words
    ]
    if text not in existing:
        words.append(text)
        db.update_global_copy_filters(user_id, {"whitelist_words": words})

    await message.reply_text(
        f"✅ Whitelisted: <code>{text}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.command(["gunwhite", "gdelwhite"]) & filters.private
)
async def gunwhite_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: <code>/gunwhite word</code>",
            parse_mode=ParseMode.HTML,
        )

    text = " ".join(message.command[1:]).strip()
    gc = db.get_global_copy(user_id)
    words = []
    for w in gc.get("whitelist_words") or []:
        pattern = w if isinstance(w, str) else w.get("pattern", "")
        if pattern != text:
            words.append(w)
    db.update_global_copy_filters(user_id, {"whitelist_words": words})
    await message.reply_text(
        f"✅ Removed whitelist: <code>{text}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.command(["greplace", "greplaces"]) & filters.private
)
async def greplace_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    gc = db.get_global_copy(user_id)
    reps = list(gc.get("replacements") or [])

    if message.command[0].lower() == "greplaces" or len(message.command) < 2:
        if not reps:
            return await message.reply_text(
                "No replacements.\n"
                "Add: <code>/greplace old_text ||| new_text</code>",
                parse_mode=ParseMode.HTML,
            )
        lines = [
            f"• <code>{r.get('from')}</code> → <code>{r.get('to')}</code>"
            for r in reps
        ]
        return await message.reply_text(
            "<b>Global Copy — Replacements</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    body = message.text.split(None, 1)[1]
    if "|||" not in body:
        return await message.reply_text(
            "Usage:\n<code>/greplace old text ||| new text</code>",
            parse_mode=ParseMode.HTML,
        )

    old, new = body.split("|||", 1)
    old, new = old.strip(), new.strip()
    if not old:
        return await message.reply_text("❌ Old text cannot be empty.")

    if not any(
        r.get("from") == old and r.get("to") == new for r in reps
    ):
        reps.append({"from": old, "to": new, "is_regex": False})
        db.update_global_copy_filters(user_id, {"replacements": reps})

    await message.reply_text(
        f"✅ Replace: <code>{old}</code> → <code>{new}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.command(["gunreplace", "gdelreplace"]) & filters.private
)
async def gunreplace_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: <code>/gunreplace old_text</code>",
            parse_mode=ParseMode.HTML,
        )

    old = " ".join(message.command[1:]).strip()
    gc = db.get_global_copy(user_id)
    reps = [
        r for r in (gc.get("replacements") or []) if r.get("from") != old
    ]
    db.update_global_copy_filters(user_id, {"replacements": reps})
    await message.reply_text(
        f"✅ Removed replace for: <code>{old}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("gantidupe") & filters.private)
async def gantidupe_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        gc = db.get_global_copy(user_id)
        state = "ON" if gc.get("anti_dupe") else "OFF"
        return await message.reply_text(
            f"Global Copy anti-dupe: <b>{state}</b>\n"
            "Usage: <code>/gantidupe on</code> | <code>/gantidupe off</code>",
            parse_mode=ParseMode.HTML,
        )

    val = message.command[1].lower() in (
        "on", "1", "true", "yes", "enable",
    )
    db.update_global_copy_filters(user_id, {"anti_dupe": val})
    await message.reply_text(
        f"✅ Global Copy anti-dupe: <b>{'ON' if val else 'OFF'}</b>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("gdelay") & filters.private)
async def gdelay_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    for err in (_require_session(user_id), _require_gcopy(user_id)):
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)

    if len(message.command) < 2:
        gc = db.get_global_copy(user_id)
        return await message.reply_text(
            f"Current delay: <code>{gc.get('delay') or 0}</code>s\n"
            "Usage: <code>/gdelay 2.5</code>",
            parse_mode=ParseMode.HTML,
        )

    try:
        delay = float(message.command[1])
        if delay < 0:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ Delay must be a non-negative number."
        )

    db.update_global_copy_filters(user_id, {"delay": delay})
    await message.reply_text(
        f"✅ Global Copy delay set to <code>{delay}</code>s",
        parse_mode=ParseMode.HTML,
    )
