"""
job_filters.py
Media size, movie/series content type, and completion sticker — same
behaviour as Forward Manager jobs, applied per AutoPost rule.
"""
from __future__ import annotations

from typing import Optional, Tuple

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from AutoPost.Database.database import get_db
from AutoPost.bot import app
from AutoPost.helper.command_helpers import can_manage_rule, resolve_chat_id
from AutoPost.helper.content_type import (
    CONTENT_TYPE_LABELS,
    content_type_button_rows,
    content_type_label,
    normalize_content_type,
)
from AutoPost.helper.media_size import SIZE_PRESETS, format_bytes, parse_size_input

db = get_db()

# user_id → {source, target}
_waiting_sticker: dict[int, dict] = {}
_waiting_custom_size: dict[int, dict] = {}


def _cb(prefix: str, source: int, target: int, action: str) -> str:
    return f"{prefix}:{source}:{target}:{action}"


def _parse_st_tgt(data: str) -> Optional[Tuple[str, int, int, str]]:
    parts = data.split(":")
    if len(parts) < 4:
        return None
    prefix, src_s, tgt_s, action = parts[0], parts[1], parts[2], ":".join(parts[3:])
    try:
        return prefix, int(src_s), int(tgt_s), action
    except ValueError:
        return None


async def _load_managed(
    client: Client, user_id: int, source: int, target: int, query: Optional[CallbackQuery] = None
):
    rule = db.get_forward_rule(source, target)
    if not rule:
        if query:
            await query.answer("Rule not found.", show_alert=True)
        return None
    if db.is_bot_owner(user_id):
        return rule
    if rule.get("owner_id") == user_id:
        return rule
    if query:
        await query.answer("Not allowed.", show_alert=True)
    return None


# ---------------------------------------------------------------------------
# Content type
# ---------------------------------------------------------------------------

def _content_kb(source: int, target: int, current) -> InlineKeyboardMarkup:
    rows = []
    for spec_row in content_type_button_rows(current, long=True):
        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=_cb("ct", source, target, mode),
            )
            for mode, label in spec_row
        ])
    rows.append([
        InlineKeyboardButton("❌ Close", callback_data="ui:close"),
    ])
    return InlineKeyboardMarkup(rows)


def _content_text(rule: dict) -> str:
    cur = normalize_content_type(rule.get("content_type", "all"))
    return (
        "<b>🎬 Content Type Filter</b>\n\n"
        f"Current: <b>{content_type_label(cur)}</b>\n\n"
        "📦 <b>All</b> — forward everything\n"
        "🎬 <b>Movies Only</b> — skip series & unknown\n"
        "📺 <b>Series Only</b> — skip movies & unknown\n"
        "🎬📺 <b>Movies + Series</b> — skip unknown / other\n\n"
        "Detection uses filename + caption (S01E02 → series, Title 2024 1080p → movie)."
    )


@app.on_message(filters.command(["contenttype", "content", "ctype"]) & filters.private)
async def content_type_cmd(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/contenttype <source> <target> "
            "all|movies|series|both</code>\n\n"
            "Examples:\n"
            "<code>/contenttype @src @tgt movies</code>\n"
            "<code>/contenttype @src @tgt both</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    if not source or not target:
        return await message.reply_text("❌ Invalid chat.", parse_mode=ParseMode.HTML)

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text("❌ Rule not found.", parse_mode=ParseMode.HTML)
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if len(message.command) >= 4:
        mode = normalize_content_type(" ".join(message.command[3:]))
        db.set_content_type(source, target, mode)
        rule = db.get_forward_rule(source, target) or rule
        return await message.reply_text(
            f"✅ Content type set to <b>{content_type_label(mode)}</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_content_kb(source, target, mode),
        )

    await message.reply_text(
        _content_text(rule),
        parse_mode=ParseMode.HTML,
        reply_markup=_content_kb(source, target, rule.get("content_type")),
    )


@app.on_callback_query(filters.regex(r"^ct:"))
async def content_type_cb(client: Client, query: CallbackQuery):
    parsed = _parse_st_tgt(query.data or "")
    if not parsed:
        return await query.answer("Invalid.", show_alert=True)
    _, source, target, mode = parsed
    user_id = query.from_user.id
    rule = await _load_managed(client, user_id, source, target, query)
    if not rule:
        return
    mode = normalize_content_type(mode)
    db.set_content_type(source, target, mode)
    rule = db.get_forward_rule(source, target) or rule
    try:
        await query.message.edit_text(
            _content_text(rule),
            parse_mode=ParseMode.HTML,
            reply_markup=_content_kb(source, target, mode),
        )
    except Exception:
        pass
    await query.answer(CONTENT_TYPE_LABELS.get(mode, mode))


# ---------------------------------------------------------------------------
# Media size filter
# ---------------------------------------------------------------------------

def _size_kb(source: int, target: int, rule: dict) -> InlineKeyboardMarkup:
    on = bool(rule.get("size_filter_enabled"))
    min_b = int(rule.get("min_media_size") or 0)
    rows = [[
        InlineKeyboardButton(
            ("🟢 ON" if on else "⚪ OFF") + "  (tap to toggle)",
            callback_data=_cb("sz", source, target, "tog"),
        )
    ]]
    row = []
    for label, nbytes in SIZE_PRESETS:
        mark = "● " if min_b == nbytes else ""
        row.append(InlineKeyboardButton(
            f"{mark}{label}",
            callback_data=_cb("sz", source, target, f"set:{nbytes}"),
        ))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✏️ Custom Size", callback_data=_cb("sz", source, target, "custom")),
        InlineKeyboardButton("🗑️ Clear Min", callback_data=_cb("sz", source, target, "clr")),
    ])
    rows.append([
        InlineKeyboardButton("❌ Close", callback_data="ui:close"),
    ])
    return InlineKeyboardMarkup(rows)


def _size_text(rule: dict) -> str:
    on = bool(rule.get("size_filter_enabled"))
    min_b = int(rule.get("min_media_size") or 0)
    return (
        "<b>📏 Media Size Filter</b>\n\n"
        f"Status: <b>{'ON' if on else 'OFF'}</b>\n"
        f"Minimum Size: <b>{format_bytes(min_b) if min_b else 'Not Set'}</b>\n\n"
        "Files smaller than the minimum are skipped (not forwarded).\n"
        "Exact size match is allowed.\n"
        "Text / unknown-size messages are not bulk-skipped.\n\n"
        "OFF keeps the saved minimum for later."
    )


@app.on_message(filters.command(["sizefilter", "minsize", "mediasize"]) & filters.private)
async def size_filter_cmd(client: Client, message: Message):
    cmd = message.command[0].lower()
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/sizefilter <source> <target></code>\n"
            "<code>/sizefilter <source> <target> on|off</code>\n"
            "<code>/minsize <source> <target> 100 MB</code>\n\n"
            "Examples:\n"
            "<code>/minsize @src @tgt 500 MB</code>\n"
            "<code>/minsize @src @tgt 1.5 GB</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    if not source or not target:
        return await message.reply_text("❌ Invalid chat.", parse_mode=ParseMode.HTML)

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text("❌ Rule not found.", parse_mode=ParseMode.HTML)
    if not await can_manage_rule(client, message, source, target, rule):
        return

    extra = message.command[3:] if len(message.command) > 3 else []
    if extra:
        raw = " ".join(extra).strip().lower()
        if raw in {"on", "off"}:
            db.set_size_filter(source, target, enabled=(raw == "on"))
            rule = db.get_forward_rule(source, target) or rule
            return await message.reply_text(
                _size_text(rule),
                parse_mode=ParseMode.HTML,
                reply_markup=_size_kb(source, target, rule),
            )
        nbytes, err = parse_size_input(" ".join(extra))
        if err:
            return await message.reply_text(f"❌ {err}")
        db.set_size_filter(source, target, enabled=True, min_bytes=nbytes)
        rule = db.get_forward_rule(source, target) or rule
        return await message.reply_text(
            _size_text(rule),
            parse_mode=ParseMode.HTML,
            reply_markup=_size_kb(source, target, rule),
        )

    await message.reply_text(
        _size_text(rule),
        parse_mode=ParseMode.HTML,
        reply_markup=_size_kb(source, target, rule),
    )


@app.on_callback_query(filters.regex(r"^sz:"))
async def size_filter_cb(client: Client, query: CallbackQuery):
    parsed = _parse_st_tgt(query.data or "")
    if not parsed:
        return await query.answer("Invalid.", show_alert=True)
    _, source, target, action = parsed
    user_id = query.from_user.id
    rule = await _load_managed(client, user_id, source, target, query)
    if not rule:
        return

    if action == "tog":
        db.set_size_filter(
            source, target, enabled=not bool(rule.get("size_filter_enabled"))
        )
        await query.answer("Toggled")
    elif action.startswith("set:"):
        try:
            nbytes = int(action.split(":", 1)[1])
        except ValueError:
            return await query.answer("Invalid size", show_alert=True)
        db.set_size_filter(source, target, enabled=True, min_bytes=nbytes)
        await query.answer(format_bytes(nbytes))
    elif action == "clr":
        db.set_size_filter(source, target, enabled=False, min_bytes=0)
        await query.answer("Cleared")
    elif action == "custom":
        _waiting_custom_size[user_id] = {"source": source, "target": target}
        try:
            await query.message.edit_text(
                "<b>✏️ Custom Minimum Size</b>\n\n"
                "Send a size like:\n"
                "<code>100 MB</code>\n"
                "<code>500 MB</code>\n"
                "<code>1.5 GB</code>\n\n"
                "/cancel to abort.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return await query.answer()

    rule = db.get_forward_rule(source, target) or rule
    try:
        await query.message.edit_text(
            _size_text(rule),
            parse_mode=ParseMode.HTML,
            reply_markup=_size_kb(source, target, rule),
        )
    except Exception:
        pass


@app.on_message(filters.private & filters.text, group=21)
async def custom_size_input(client: Client, message: Message):
    user_id = message.from_user.id
    st = _waiting_custom_size.get(user_id)
    if not st:
        return
    text = (message.text or "").strip()
    if text.lower() in {"/cancel", "cancel"}:
        _waiting_custom_size.pop(user_id, None)
        return await message.reply_text("Cancelled.")
    if text.startswith("/"):
        return
    nbytes, err = parse_size_input(text)
    if err:
        return await message.reply_text(f"❌ {err}\n\nTry again or /cancel.")
    source, target = st["source"], st["target"]
    _waiting_custom_size.pop(user_id, None)
    db.set_size_filter(source, target, enabled=True, min_bytes=nbytes)
    rule = db.get_forward_rule(source, target) or {}
    await message.reply_text(
        _size_text(rule),
        parse_mode=ParseMode.HTML,
        reply_markup=_size_kb(source, target, rule),
    )


# ---------------------------------------------------------------------------
# Completion sticker
# ---------------------------------------------------------------------------

def _cs_kb(source: int, target: int, rule: dict) -> InlineKeyboardMarkup:
    on = bool(rule.get("completion_sticker_enabled"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🎟️ Completion Sticker: {'ON' if on else 'OFF'}",
            callback_data=_cb("cs", source, target, "tog"),
        )],
        [
            InlineKeyboardButton("➕ Add Sticker", callback_data=_cb("cs", source, target, "add")),
            InlineKeyboardButton("🗂️ Manage", callback_data=_cb("cs", source, target, "mgr")),
        ],
        [InlineKeyboardButton("🗑️ Clear All", callback_data=_cb("cs", source, target, "clr"))],
        [InlineKeyboardButton("❌ Close", callback_data="ui:close")],
    ])


def _cs_manage_kb(source: int, target: int, rule: dict) -> InlineKeyboardMarkup:
    stickers = rule.get("completion_stickers") or []
    rows = []
    for i, _fid in enumerate(stickers):
        rows.append([
            InlineKeyboardButton(f"{i + 1}. Sticker", callback_data=_cb("cs", source, target, "noop")),
            InlineKeyboardButton("🗑️ Remove", callback_data=_cb("cs", source, target, f"rm:{i}")),
        ])
    if not rows:
        rows.append([InlineKeyboardButton("No stickers", callback_data=_cb("cs", source, target, "noop"))])
    rows.append([
        InlineKeyboardButton("« Back", callback_data=_cb("cs", source, target, "home")),
        InlineKeyboardButton("❌ Close", callback_data="ui:close"),
    ])
    return InlineKeyboardMarkup(rows)


def _cs_text(rule: dict) -> str:
    on = bool(rule.get("completion_sticker_enabled"))
    n = len(rule.get("completion_stickers") or [])
    return (
        "<b>🎟️ Completion Sticker</b>\n\n"
        f"Status: <b>{'ON' if on else 'OFF'}</b>\n"
        f"Stickers: <b>{n}</b>\n\n"
        "When a Movie / Series group is fully forwarded, "
        "<b>one</b> random configured sticker is sent to the target.\n\n"
        "Quality variants of the same title count as <b>one</b> group.\n"
        "A new run of the same title later gets its own sticker.\n"
        "Turning OFF keeps saved stickers."
    )


@app.on_message(
    filters.command(["sticker", "addsticker", "liststickers", "clearstickers"])
    & filters.private
)
async def sticker_cmd(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/sticker <source> <target></code> — open menu\n"
            "<code>/sticker <source> <target> on|off</code>\n"
            "<code>/addsticker <source> <target></code> — then send a sticker\n"
            "<code>/liststickers <source> <target></code>\n"
            "<code>/clearstickers <source> <target></code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    if not source or not target:
        return await message.reply_text("❌ Invalid chat.", parse_mode=ParseMode.HTML)

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text("❌ Rule not found.", parse_mode=ParseMode.HTML)
    if not await can_manage_rule(client, message, source, target, rule):
        return

    cmd = message.command[0].lower()
    extra = message.command[3].lower() if len(message.command) > 3 else ""

    if cmd == "addsticker" or extra == "add":
        _waiting_sticker[message.from_user.id] = {"source": source, "target": target}
        return await message.reply_text(
            "<b>➕ Add Completion Sticker</b>\n\n"
            "Send or <b>forward</b> the Telegram sticker you want to use.\n\n"
            "You can add multiple stickers.\n"
            "Only <b>ONE</b> random sticker is sent when a Movie/Series group completes.\n\n"
            "/cancel to abort.",
            parse_mode=ParseMode.HTML,
        )

    if cmd == "clearstickers" or extra == "clear":
        db.set_completion_sticker(source, target, stickers=[])
        rule = db.get_forward_rule(source, target) or rule
        return await message.reply_text(
            _cs_text(rule),
            parse_mode=ParseMode.HTML,
            reply_markup=_cs_kb(source, target, rule),
        )

    if cmd == "liststickers":
        return await message.reply_text(
            f"<b>🎟️ Completion Stickers</b>\n\nSaved: <b>{len(rule.get('completion_stickers') or [])}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=_cs_manage_kb(source, target, rule),
        )

    if extra in {"on", "off"}:
        db.set_completion_sticker(source, target, enabled=(extra == "on"))
        rule = db.get_forward_rule(source, target) or rule

    await message.reply_text(
        _cs_text(rule),
        parse_mode=ParseMode.HTML,
        reply_markup=_cs_kb(source, target, rule),
    )


@app.on_callback_query(filters.regex(r"^cs:"))
async def sticker_cb(client: Client, query: CallbackQuery):
    parsed = _parse_st_tgt(query.data or "")
    if not parsed:
        return await query.answer("Invalid.", show_alert=True)
    _, source, target, action = parsed
    user_id = query.from_user.id
    rule = await _load_managed(client, user_id, source, target, query)
    if not rule:
        return

    if action == "noop":
        return await query.answer()

    if action == "tog":
        db.set_completion_sticker(
            source, target, enabled=not bool(rule.get("completion_sticker_enabled"))
        )
        await query.answer("Toggled")
    elif action == "add":
        _waiting_sticker[user_id] = {"source": source, "target": target}
        try:
            await query.message.edit_text(
                "<b>➕ Add Completion Sticker</b>\n\n"
                "Send or <b>forward</b> the Telegram sticker you want to use.\n\n"
                "You can add multiple stickers.\n"
                "Only <b>ONE</b> random sticker is sent when a Movie/Series group completes.\n\n"
                "/cancel to abort.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return await query.answer()
    elif action == "mgr":
        try:
            await query.message.edit_text(
                f"<b>🎟️ Completion Stickers</b>\n\nSaved: <b>{len(rule.get('completion_stickers') or [])}</b>\n"
                "Tap Remove to delete one. Stickers stay if you turn the feature OFF.",
                parse_mode=ParseMode.HTML,
                reply_markup=_cs_manage_kb(source, target, rule),
            )
        except Exception:
            pass
        return await query.answer()
    elif action.startswith("rm:"):
        try:
            idx = int(action.split(":", 1)[1])
        except ValueError:
            return await query.answer("Invalid", show_alert=True)
        stickers = list(rule.get("completion_stickers") or [])
        if 0 <= idx < len(stickers):
            stickers.pop(idx)
            db.set_completion_sticker(source, target, stickers=stickers)
            await query.answer("Removed")
        else:
            await query.answer("Not found", show_alert=True)
        rule = db.get_forward_rule(source, target) or rule
        try:
            await query.message.edit_text(
                f"<b>🎟️ Completion Stickers</b>\n\nSaved: <b>{len(rule.get('completion_stickers') or [])}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=_cs_manage_kb(source, target, rule),
            )
        except Exception:
            pass
        return
    elif action == "clr":
        db.set_completion_sticker(source, target, stickers=[])
        await query.answer("Cleared")
    elif action == "home":
        pass

    rule = db.get_forward_rule(source, target) or rule
    try:
        await query.message.edit_text(
            _cs_text(rule),
            parse_mode=ParseMode.HTML,
            reply_markup=_cs_kb(source, target, rule),
        )
    except Exception:
        pass


@app.on_message(filters.private & filters.incoming & filters.sticker)
async def sticker_input(client: Client, message: Message):
    user_id = message.from_user.id
    st = _waiting_sticker.get(user_id)
    if not st:
        return
    source, target = st["source"], st["target"]
    _waiting_sticker.pop(user_id, None)
    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text("Rule not found. Sticker not saved.")
    file_id = getattr(message.sticker, "file_id", None)
    if not file_id:
        return await message.reply_text("That is not a valid Telegram sticker.")
    stickers = list(rule.get("completion_stickers") or [])
    if file_id in stickers:
        await message.reply_text("This sticker is already saved for this rule.")
    else:
        stickers.append(str(file_id))
        db.set_completion_sticker(source, target, stickers=stickers)
        await message.reply_text("✅ Sticker added successfully.")
    rule = db.get_forward_rule(source, target) or rule
    await message.reply_text(
        _cs_text(rule),
        parse_mode=ParseMode.HTML,
        reply_markup=_cs_kb(source, target, rule),
    )


@app.on_message(filters.private & filters.incoming & ~filters.sticker, group=8)
async def sticker_wrong_media(client: Client, message: Message):
    user_id = getattr(message.from_user, "id", None)
    if not user_id:
        return
    st = _waiting_sticker.get(user_id)
    if not st:
        return
    text = (message.text or "").strip().lower()
    if text in {"/cancel", "cancel"}:
        _waiting_sticker.pop(user_id, None)
        return await message.reply_text("Cancelled.")
    if text.startswith("/"):
        return
    await message.reply_text(
        "Please send a <b>sticker</b> (or /cancel).",
        parse_mode=ParseMode.HTML,
    )


@app.on_callback_query(filters.regex(r"^ui:close$"))
async def ui_close_cb(client: Client, query: CallbackQuery):
    """Delete the settings menu message."""
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.message.edit_text("Closed.")
        except Exception:
            pass
    try:
        await query.answer()
    except Exception:
        pass
