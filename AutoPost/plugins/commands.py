"""
commands.py
Telegram Auto-Forward Bot - Management command handlers only.
Helpers live in AutoPost.helper.command_helpers

Kurigram (Pyrogram fork) | Python 3.14 | PyMongo
"""

from __future__ import annotations

import re

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ParseMode, ChatType
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from AutoPost import DAILY_FORWARD_LIMIT as LIMIT
from AutoPost.Database.database import (
    get_db,
    ALLOWED_CAPTION_POSITIONS,
    ALLOWED_MEDIA_TYPES,
    DAILY_FORWARD_LIMIT,
)
from AutoPost.Textmsgs import textmsgs
from AutoPost.helper.command_helpers import (
    can_manage_rule,
    check_user_account_permissions,
    check_user_bot_permissions,
    create_rule_with_via,
    format_rule,
    is_user_admin_of_chat,
    parse_buttons,
    register_user,
    resolve_and_authorize,
    resolve_chat_id,
    _format_word_list,
)

from AutoPost.bot import app
from .join_required import check_force_sub

db = get_db()

# user_id -> {source, target} while waiting for /setbuttons text
_waiting_buttons: dict[int, dict] = {}

keyboard = InlineKeyboardMarkup([[
    InlineKeyboardButton("👨‍💻 Creator", url="https://t.me/KynozeFun"),
]])


# ---------------------------------------------------------------------------
# Admin-only mode gate
# When /adminonly is ON, only bot owners & bot admins may use the bot.
# Normal users get a clear message + source/creator links.
# Runs first (group=-1). stop_propagation must NOT be swallowed by except.
# ---------------------------------------------------------------------------

ADMIN_ONLY_MSG = (
    "🔒 <b>This bot is restricted</b>\n\n"
    "Only the <b>Bot Owner</b> and <b>Bot Admins</b> can use this bot right now.\n\n"
    "Want your own Auto Forward Bot? Use the source below and host it yourself."
)

ADMIN_ONLY_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "📦 Source Code",
            url="https://github.com/kynoze/CNL-Auto-Post-Bot",
        ),
    ],
    [
        InlineKeyboardButton(
            "👨‍💻 Creator",
            url="https://t.me/KynozeFun",
        ),
    ],
])


@app.on_message(filters.private, group=-1)
async def admin_only_message_gate(client: Client, message: Message):
    if not db.is_admin_only():
        return
    user = message.from_user
    if user and db.is_bot_admin(user.id):
        return
    try:
        await message.reply_text(
            ADMIN_ONLY_MSG,
            parse_mode=ParseMode.HTML,
            reply_markup=ADMIN_ONLY_KEYBOARD,
            quote=True,
        )
    except Exception:
        pass
    # Must raise / stop — do not catch this, or other handlers still run
    message.stop_propagation()


@app.on_callback_query(group=-1)
async def admin_only_callback_gate(client: Client, callback: CallbackQuery):
    if not db.is_admin_only():
        return
    user = callback.from_user
    if user and db.is_bot_admin(user.id):
        return
    try:
        await callback.answer(
            "This bot is restricted to Owner / Admins only.",
            show_alert=True,
        )
    except Exception:
        pass
    try:
        await callback.message.reply_text(
            ADMIN_ONLY_MSG,
            parse_mode=ParseMode.HTML,
            reply_markup=ADMIN_ONLY_KEYBOARD,
        )
    except Exception:
        pass
    callback.stop_propagation()


# ---------------------------------------------------------------------------
# /start & /help
# ---------------------------------------------------------------------------

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await register_user(client, message)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✨ Features", callback_data="menu_features"),
        InlineKeyboardButton("📖 All Commands", callback_data="menu_help"),
    ], [
        InlineKeyboardButton("🛠 Commands Format", callback_data="menu_howto"),
        InlineKeyboardButton("📚 Guides", callback_data="menu_guides"),
    ], [
        InlineKeyboardButton("👨‍💻 Creator", url="https://t.me/KynozeFun"),
    ]])
    user_mention = message.from_user.mention
    await message.reply_text(
        textmsgs.START_MSG.format(user_mention, LIMIT),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@app.on_message(filters.command("format") & filters.private)
async def format_command_handler(client: Client, message: Message):
    await message.reply_text(
        textmsgs.FORMAT_MSG,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@app.on_message(filters.command(["help", "commands"]) & filters.private)
async def help_command_handler(client: Client, message: Message):
    await message.reply_text(
        textmsgs.HELP_MSG,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@app.on_message(filters.command("feature") & filters.private)
async def feature_command_handler(client: Client, message: Message):
    await message.reply_text(
        textmsgs.FEATURE_MSG,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


# ---------------------------------------------------------------------------
# Add / Delete Rule
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addrule") & filters.private)
async def add_rule_cmd(client: Client, message: Message):
    if not await check_force_sub(client, message):
        return

    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/addrule &lt;source_chat&gt; &lt;target_chat&gt;</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/addrule @mychannel -1001234567890</code>\n\n"
            "<b>Before creating a rule you must:</b>\n"
            "1. Connect a forwarder (pick one):\n"
            "   • 🤖 <code>/addbot</code> — your own bot\n"
            "   • 👤 <code>/connect</code> — your Telegram account\n"
            "2. Add that bot/account as <b>Admin</b> in both Source and Target "
            "(post permission on Target).\n"
            "3. You must be Admin/Owner of the Target chat.",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])

    if not source or not target:
        return await message.reply_text(
            "❌ Could not resolve source or target chat.",
            parse_mode=ParseMode.HTML,
        )

    if source == target:
        return await message.reply_text(
            "❌ Source and target cannot be the same chat.",
            parse_mode=ParseMode.HTML,
        )

    user_id = message.from_user.id
    has_bot = db.has_active_bot(user_id)
    has_session = db.has_active_session(user_id)

    if not has_bot and not has_session:
        return await message.reply_text(
            "❌ <b>Nothing connected.</b>\n\n"
            "Add at least one:\n"
            "• <code>/addbot</code> — your forwarding bot\n"
            "• <code>/connect</code> — your Telegram account",
            parse_mode=ParseMode.HTML,
        )

    if not await is_user_admin_of_chat(client, target, user_id):
        return await message.reply_text(
            "❌ You must be an <b>Admin/Owner</b> of the <b>Target</b> chat.",
            parse_mode=ParseMode.HTML,
        )

    # Both available → choose
    if has_bot and has_session:
        return await message.reply_text(
            "<b>Choose forward method</b>\n\n"
            f"Source: <code>{source}</code>\n"
            f"Target: <code>{target}</code>\n\n"
            "🤖 <b>User Bot</b> — your connected bot forwards\n"
            "   (bot must be Admin in source + target)\n\n"
            "👤 <b>User Account</b> — your personal account forwards\n"
            "   (account member in source, can post in target)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🤖 User Bot",
                        callback_data=f"addrule:user_bot:{source}:{target}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "👤 User Account",
                        callback_data=f"addrule:user_account:{source}:{target}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="addrule:cancel",
                    ),
                ],
            ]),
        )

    if has_bot:
        err = await check_user_bot_permissions(user_id, source, target)
        if err:
            return await message.reply_text(err, parse_mode=ParseMode.HTML)
        return await create_rule_with_via(
            message, source, target, user_id, "user_bot"
        )

    err = await check_user_account_permissions(user_id, source, target)
    if err:
        return await message.reply_text(err, parse_mode=ParseMode.HTML)
    await create_rule_with_via(
        message, source, target, user_id, "user_account"
    )


@app.on_callback_query(filters.regex(r"^addrule:"))
async def addrule_callback(client: Client, callback: CallbackQuery):
    data = callback.data or ""
    user_id = callback.from_user.id

    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Private only.", show_alert=True)
        return

    if data == "addrule:cancel":
        await callback.message.edit_text("❌ Cancelled.")
        await callback.answer()
        return

    parts = data.split(":")
    if len(parts) != 4:
        await callback.answer("Invalid data.", show_alert=True)
        return

    _, method, src_s, tgt_s = parts
    if method not in ("user_bot", "user_account"):
        await callback.answer("Unknown method.", show_alert=True)
        return

    try:
        source = int(src_s)
        target = int(tgt_s)
    except ValueError:
        await callback.answer("Invalid chat id.", show_alert=True)
        return

    await callback.answer("Checking permissions...")

    if method == "user_bot":
        err = await check_user_bot_permissions(user_id, source, target)
    else:
        err = await check_user_account_permissions(user_id, source, target)

    if err:
        await callback.message.edit_text(err, parse_mode=ParseMode.HTML)
        return

    try:
        rule = db.create_forward_rule(
            source_chat_id=source,
            target_chat_id=target,
            owner_id=user_id,
            enabled=True,
            forward_tag=False,
            allowed_types=["all"],
            remove_old_caption=False,
            remove_links=False,
            delay=0,
            anti_dupe=False,
            forward_via=method,
        )
        via_label = (
            "🤖 Your Bot" if method == "user_bot" else "👤 Your Account"
        )
        await callback.message.edit_text(
            f"✅ <b>Rule created</b> — via <b>{via_label}</b>\n\n"
            f"{format_rule(rule)}",
            parse_mode=ParseMode.HTML,
        )
    except ValueError as e:
        await callback.message.edit_text(f"❌ {e}", parse_mode=ParseMode.HTML)


@app.on_message(filters.command("delrule") & filters.private)
async def del_rule_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, rule = result

    db.delete_forward_rule(source, target)
    db.clear_hashes_for_target(target)
    await message.reply_text("✅ Rule has been deleted.", parse_mode=ParseMode.HTML)


@app.on_message(filters.command(["delallrules", "deletemyrules"]) & filters.private)
async def del_all_my_rules_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    if len(message.command) < 2 or message.command[1].lower() != "confirm":
        rules = db.get_rules_by_owner(user_id)
        count = len(rules)
        return await message.reply_text(
            "⚠️ <b>Delete all your rules?</b>\n\n"
            f"You currently have <b>{count}</b> rule(s).\n"
            "This will permanently delete <b>only your</b> rules.\n\n"
            "To confirm, send:\n"
            "<code>/delallrules confirm</code>",
            parse_mode=ParseMode.HTML,
        )

    deleted = db.delete_all_rules_of_user(user_id)
    await message.reply_text(
        f"✅ Deleted <b>{deleted}</b> of your rule(s).",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# List & View Rules
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["listrules", "myrules"]) & filters.private)
async def list_rules_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    if db.is_bot_owner(user_id):
        rules = list(db.forward_rules.find({}))
        title = "All rules on this bot"
    else:
        rules = db.get_rules_by_owner(user_id)
        title = "Your rules"

    if not rules:
        return await message.reply_text("ℹ️ No rules found.", parse_mode=ParseMode.HTML)

    text = f"<b>{title}: {len(rules)}</b>\n\n"
    for i, rule in enumerate(rules, 1):
        status = "✅" if rule.get("enabled") else "❌"
        via = rule.get("forward_via") or "user_bot"
        via_icon = "🤖" if via == "user_bot" else "👤"
        text += (
            f"{i}. {status} {via_icon} "
            f"<code>{rule['source_chat_id']}</code> → "
            f"<code>{rule['target_chat_id']}</code>\n"
        )

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.command(["rule", "settings"]) & filters.private)
async def view_rule_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, rule = result

    text = format_rule(rule)

    if rule.get("replacements"):
        text += "\n<b>Replacements:</b>\n"
        for r in rule["replacements"]:
            flag = " (regex)" if r.get("is_regex") else ""
            text += (
                f"• <code>{r.get('from')}</code> → "
                f"<code>{r.get('to')}</code>{flag}\n"
            )

    if rule.get("block_words"):
        text += (
            f"\n<b>Block Words:</b>\n"
            f"<code>{_format_word_list(rule['block_words'])}</code>\n"
        )

    if rule.get("whitelist_words"):
        text += (
            f"\n<b>Whitelist Words:</b>\n"
            f"<code>{_format_word_list(rule['whitelist_words'])}</code>\n"
        )

    if rule.get("buttons"):
        text += "\n<b>Buttons:</b>\n"
        for row in rule["buttons"]:
            text += (
                " | ".join(
                    f"<a href=\"{b['url']}\">{b['text']}</a>" for b in row
                )
                + "\n"
            )

    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


# ---------------------------------------------------------------------------
# Enable / Disable
# ---------------------------------------------------------------------------

@app.on_message(filters.command("enable") & filters.private)
async def enable_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.set_rule_enabled(source, target, True):
        await message.reply_text(
            "✅ Forwarding <b>ENABLED</b>.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("disable") & filters.private)
async def disable_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.set_rule_enabled(source, target, False):
        await message.reply_text(
            "✅ Forwarding <b>DISABLED</b>.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------

@app.on_message(filters.command("setcaption") & filters.private)
async def set_caption_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/setcaption &lt;source&gt; &lt;target&gt; "
            "&lt;position&gt; &lt;caption text&gt;</code>\n\n"
            "position = <code>start</code> | <code>end</code> | "
            "<code>end_with_gap</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    position = message.command[3].lower()

    try:
        caption = message.text.split(None, 4)[4]
    except IndexError:
        caption = ""

    if position not in ALLOWED_CAPTION_POSITIONS:
        return await message.reply_text(
            f"❌ Position must be one of: "
            f"<code>{', '.join(ALLOWED_CAPTION_POSITIONS)}</code>",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )

    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.set_add_caption(source, target, caption, position):
        await message.reply_text(
            f"✅ Caption set successfully.\n"
            f"<b>Position:</b> <code>{position}</code>\n"
            f"<b>Text:</b> <code>{caption}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("removecaption") & filters.private)
async def remove_caption_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, rule = result

    current_pos = rule.get("caption_position", "end")
    if db.set_add_caption(source, target, None, current_pos):
        await message.reply_text(
            "✅ Add caption removed.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(
    filters.command(["set_caption", "setcustomcaption"]) & filters.private
)
async def set_custom_caption_cmd(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/set_caption &lt;source&gt; &lt;target&gt;</code>\n\n"
            "Then send your HTML template in the next message.\n"
            "Use <code>{caption}</code> where the original caption should appear.\n\n"
            "<b>Example template:</b>\n"
            "<code>&lt;b&gt;🎬 New Upload&lt;/b&gt;\n\n"
            "📦 &lt;code&gt;{caption}&lt;/code&gt;\n\n"
            "&lt;a href=\"https://t.me/mychannel\"&gt;Join Channel&lt;/a&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )

    if not await can_manage_rule(client, message, source, target, rule):
        return

    await message.reply_text(
        f"✅ <b>Send your custom caption template</b>\n"
        f"(source=<code>{source}</code> target=<code>{target}</code>)\n\n"
        "Use <code>{caption}</code> where you want the original caption/text.\n\n"
        "<b>Supported HTML:</b>\n"
        "<code>&lt;b&gt;bold&lt;/b&gt;  &lt;i&gt;italic&lt;/i&gt;  "
        "&lt;u&gt;underline&lt;/u&gt;\n"
        "&lt;s&gt;strike&lt;/s&gt;  &lt;spoiler&gt;...&lt;/spoiler&gt;\n"
        "&lt;code&gt;code&lt;/code&gt;  "
        "&lt;a href=\"url\"&gt;link&lt;/a&gt;</code>\n\n"
        "<b>Note:</b> This will clear any simple /setcaption text.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.private
    & filters.reply
    & filters.text
    & ~filters.regex(r"^/")
)
async def custom_caption_reply_handler(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.text:
        return
    if "Send your custom caption template" not in message.reply_to_message.text:
        return

    match = re.search(
        r"source=(-?\d+)\s+target=(-?\d+)",
        message.reply_to_message.text,
    )
    if not match:
        return

    source = int(match.group(1))
    target = int(match.group(2))

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )

    if not await can_manage_rule(client, message, source, target, rule):
        return

    template = message.text.strip()
    if not template:
        return await message.reply_text(
            "❌ Template cannot be empty.",
            parse_mode=ParseMode.HTML,
        )

    if db.set_custom_caption(source, target, template):
        has_placeholder = "{caption}" in template
        note = (
            "✅ <code>{caption}</code> placeholder found."
            if has_placeholder
            else "⚠️ No <code>{caption}</code> in template — "
            "original caption will be ignored."
        )
        preview = template[:300] + ("..." if len(template) > 300 else "")
        await message.reply_text(
            f"✅ <b>Custom caption template saved!</b>\n\n{note}\n\n"
            f"<b>Template:</b>\n<code>{preview}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(
    filters.command(
        ["clear_caption", "clearcustomcaption", "removecustomcaption"]
    )
    & filters.private
)
async def clear_custom_caption_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.clear_custom_caption(source, target):
        await message.reply_text(
            "✅ Custom caption template removed.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("removeoldcaption") & filters.private)
async def remove_old_caption_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/removeoldcaption &lt;source&gt; &lt;target&gt; on/off</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    state = message.command[3].lower()

    if state not in ("on", "off"):
        return await message.reply_text(
            "❌ Please use <code>on</code> or <code>off</code>.",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    enabled = state == "on"
    if db.set_remove_old_caption(source, target, enabled):
        await message.reply_text(
            f"✅ Remove Old Caption is now <b>{state.upper()}</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Remove Links
# ---------------------------------------------------------------------------

@app.on_message(filters.command("removelinks") & filters.private)
async def remove_links_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/removelinks &lt;source&gt; &lt;target&gt; on/off</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    state = message.command[3].lower()

    if state not in ("on", "off"):
        return await message.reply_text(
            "❌ Please use <code>on</code> or <code>off</code>.",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    enabled = state == "on"
    if db.set_remove_links(source, target, enabled):
        await message.reply_text(
            f"✅ Remove Links is now <b>{state.upper()}</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Replacements
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addreplace") & filters.private)
async def add_replace_cmd(client: Client, message: Message):
    if len(message.command) < 3 or "|" not in message.text:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/addreplace &lt;source&gt; &lt;target&gt; "
            "&lt;old text&gt; | &lt;new text&gt;</code>\n"
            "<code>/addreplace &lt;source&gt; &lt;target&gt; "
            "&lt;old text&gt; | &lt;new text&gt; | regex</code>",
            parse_mode=ParseMode.HTML,
        )

    parts = message.text.split(None, 3)
    if len(parts) < 4:
        return await message.reply_text(
            "❌ Invalid format.", parse_mode=ParseMode.HTML
        )

    source = await resolve_chat_id(client, parts[1])
    target = await resolve_chat_id(client, parts[2])
    rest = parts[3]

    segments = [s.strip() for s in rest.split("|")]
    if len(segments) < 2:
        return await message.reply_text(
            "❌ <code>|</code> separator missing.",
            parse_mode=ParseMode.HTML,
        )

    old = segments[0]
    new = segments[1]
    is_regex = False
    if len(segments) >= 3 and segments[2].lower() in ("regex", "re", "true", "1"):
        is_regex = True

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.add_replacement(source, target, old, new, is_regex=is_regex):
        flag = " (regex)" if is_regex else ""
        await message.reply_text(
            f"✅ Replacement added{flag}:\n"
            f"<code>{old}</code> → <code>{new}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("delreplace") & filters.private)
async def del_replace_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/delreplace &lt;source&gt; &lt;target&gt; &lt;old text&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    old = " ".join(message.command[3:])

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.remove_replacement(source, target, old):
        await message.reply_text(
            f"✅ Replacement removed: <code>{old}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Replacement not found.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("listreplace") & filters.private)
async def list_replace_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    _, _, rule = result

    reps = rule.get("replacements", [])
    if not reps:
        return await message.reply_text(
            "ℹ️ No replacements set.", parse_mode=ParseMode.HTML
        )

    text = "<b>Current Replacements:</b>\n\n"
    for r in reps:
        flag = " (regex)" if r.get("is_regex") else ""
        text += (
            f"• <code>{r.get('from')}</code> → "
            f"<code>{r.get('to')}</code>{flag}\n"
        )
    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.command("clearreplace") & filters.private)
async def clear_replace_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.clear_replacements(source, target):
        await message.reply_text(
            "✅ All replacements have been cleared.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Block Words
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addblock") & filters.private)
async def add_block_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/addblock &lt;source&gt; &lt;target&gt; word1,word2,word3</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    words_raw = " ".join(message.command[3:])
    words = [w.strip() for w in words_raw.split(",") if w.strip()]

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    current = list(rule.get("block_words", []))
    existing = {
        item if isinstance(item, str) else item.get("pattern", "")
        for item in current
    }
    for w in words:
        if w not in existing:
            current.append(w)

    db.set_block_words(source, target, current)
    await message.reply_text(
        f"✅ Block words updated.\n<b>Total:</b> {len(current)}",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("delblock") & filters.private)
async def del_block_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/delblock &lt;source&gt; &lt;target&gt; &lt;word&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    word = " ".join(message.command[3:])

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.remove_block_word(source, target, word):
        await message.reply_text(
            f"✅ Block word removed: <code>{word}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("listblock") & filters.private)
async def list_block_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    _, _, rule = result

    words = rule.get("block_words", [])
    if not words:
        return await message.reply_text(
            "ℹ️ No block words set.", parse_mode=ParseMode.HTML
        )
    await message.reply_text(
        f"<b>Block Words:</b>\n<code>{_format_word_list(words)}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("clearblock") & filters.private)
async def clear_block_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.set_block_words(source, target, []):
        await message.reply_text(
            "✅ All block words have been cleared.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Whitelist Words
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addwhitelist") & filters.private)
async def add_whitelist_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/addwhitelist &lt;source&gt; &lt;target&gt; "
            "word1,word2,word3</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    words_raw = " ".join(message.command[3:])
    words = [w.strip() for w in words_raw.split(",") if w.strip()]

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    current = list(rule.get("whitelist_words", []))
    existing = {
        item if isinstance(item, str) else item.get("pattern", "")
        for item in current
    }
    for w in words:
        if w not in existing:
            current.append(w)

    db.set_whitelist_words(source, target, current)
    await message.reply_text(
        f"✅ Whitelist words updated.\n<b>Total:</b> {len(current)}\n\n"
        "Only messages containing at least one of these words will be forwarded.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("delwhitelist") & filters.private)
async def del_whitelist_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/delwhitelist &lt;source&gt; &lt;target&gt; &lt;word&gt;</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    word = " ".join(message.command[3:])

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.remove_whitelist_word(source, target, word):
        await message.reply_text(
            f"✅ Whitelist word removed: <code>{word}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(filters.command("listwhitelist") & filters.private)
async def list_whitelist_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    _, _, rule = result

    words = rule.get("whitelist_words", [])
    if not words:
        return await message.reply_text(
            "ℹ️ No whitelist words set (all messages allowed).",
            parse_mode=ParseMode.HTML,
        )
    await message.reply_text(
        f"<b>Whitelist Words:</b>\n<code>{_format_word_list(words)}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("clearwhitelist") & filters.private)
async def clear_whitelist_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.set_whitelist_words(source, target, []):
        await message.reply_text(
            "✅ Whitelist cleared. All messages are now allowed.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

@app.on_message(filters.command("setbuttons") & filters.private)
async def set_buttons_cmd(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/setbuttons &lt;source&gt; &lt;target&gt;</code>\n\n"
            "Then <b>send the next message</b> with buttons in this format:\n\n"
            "<code>Button1 - https://t.me/xxx | Button2 - https://t.me/yyy</code>\n"
            "<code>Button3 - https://example.com</code>\n\n"
            "Each line = one row\n"
            "Use <code>|</code> for multiple buttons in the same row.\n"
            "Send /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    # If button text was passed on the same line after source/target, apply now
    # e.g. /setbuttons src tgt Want HQ? - https://t.me/xxx
    extra = message.text.split(None, 3)
    if len(extra) >= 4:
        buttons = parse_buttons(extra[3])
        if buttons:
            if db.set_buttons(source, target, buttons):
                return await message.reply_text(
                    f"✅ <b>{len(buttons)} row(s)</b> of buttons have been set!",
                    parse_mode=ParseMode.HTML,
                )
            return await message.reply_text(
                "❌ Failed to update rule.", parse_mode=ParseMode.HTML
            )

    uid = message.from_user.id if message.from_user else None
    if uid:
        _waiting_buttons[uid] = {"source": int(source), "target": int(target)}

    await message.reply_text(
        f"✅ <b>Send the button text now</b>\n"
        f"(source=<code>{source}</code> target=<code>{target}</code>)\n\n"
        "You can just type the next message — reply is optional.\n\n"
        "<b>Format:</b>\n"
        "<code>Want high quality? - https://t.me/webxzonepremium/10</code>\n"
        "<code>Join Channel - https://t.me/mychannel | "
        "Website - https://example.com</code>\n\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )


async def _apply_buttons_from_text(
    client: Client, message: Message, source: int, target: int, text: str
) -> None:
    rule = db.get_forward_rule(source, target)
    if not rule:
        await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
        return
    if not await can_manage_rule(client, message, source, target, rule):
        return

    buttons = parse_buttons(text)
    if not buttons:
        await message.reply_text(
            "❌ No valid buttons found.\n\n"
            "<b>Format:</b> <code>Label - https://url</code>\n"
            "Example:\n"
            "<code>Want high quality? - https://t.me/webxzonepremium/10</code>\n\n"
            "Send again, or /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return

    if db.set_buttons(source, target, buttons):
        uid = message.from_user.id if message.from_user else None
        if uid:
            _waiting_buttons.pop(uid, None)
        await message.reply_text(
            f"✅ <b>{len(buttons)} row(s)</b> of buttons have been set!",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


@app.on_message(
    filters.private
    & filters.text
    & ~filters.regex(r"^/")
    & filters.incoming,
    group=5,
)
async def buttons_text_handler(client: Client, message: Message):
    """Accept button text after /setbuttons — reply optional."""
    uid = message.from_user.id if message.from_user else None
    if not uid:
        return

    # 1) Pending wait from /setbuttons (next message, no reply needed)
    pending = _waiting_buttons.get(uid)
    if pending:
        await _apply_buttons_from_text(
            client,
            message,
            int(pending["source"]),
            int(pending["target"]),
            message.text or "",
        )
        return

    # 2) Explicit reply to the bot prompt (legacy)
    rtm = message.reply_to_message
    if not rtm or not (rtm.text or ""):
        return
    prompt = rtm.text or ""
    if "Send the button text now" not in prompt and "Now reply the buttons" not in prompt:
        return
    match = re.search(r"source=(-?\d+)\s+target=(-?\d+)", prompt)
    if not match:
        return
    await _apply_buttons_from_text(
        client,
        message,
        int(match.group(1)),
        int(match.group(2)),
        message.text or "",
    )


@app.on_message(filters.command("cancel") & filters.private, group=-2)
async def cancel_pending_cmd(client: Client, message: Message):
    """Clear pending /setbuttons wait; stop other /cancel handlers if we handled it."""
    uid = message.from_user.id if message.from_user else None
    if uid and uid in _waiting_buttons:
        _waiting_buttons.pop(uid, None)
        await message.reply_text("✅ Button setup cancelled.", parse_mode=ParseMode.HTML)
        message.stop_propagation()


@app.on_message(filters.command("clearbuttons") & filters.private)
async def clear_buttons_cmd(client: Client, message: Message):
    result = await resolve_and_authorize(client, message, need_manage=True)
    if not result:
        return
    source, target, _ = result

    if db.set_buttons(source, target, []):
        await message.reply_text(
            "✅ Buttons have been cleared.", parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Forward Tag
# ---------------------------------------------------------------------------

@app.on_message(filters.command("forwardtag") & filters.private)
async def forward_tag_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/forwardtag &lt;source&gt; &lt;target&gt; on/off</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    state = message.command[3].lower()

    if state not in ("on", "off"):
        return await message.reply_text(
            "❌ Please use <code>on</code> or <code>off</code>.",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    enabled = state == "on"
    if db.set_forward_tag(source, target, enabled):
        await message.reply_text(
            f"✅ Forward Tag is now <b>{state.upper()}</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Allowed Types
# ---------------------------------------------------------------------------

@app.on_message(filters.command("settypes") & filters.private)
async def set_types_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b>\n"
            "<code>/settypes &lt;source&gt; &lt;target&gt; all</code>\n"
            "<code>/settypes &lt;source&gt; &lt;target&gt; "
            "photo,video,document,text,poll</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    types_raw = " ".join(message.command[3:]).lower()
    types = [t.strip() for t in types_raw.split(",") if t.strip()]

    if "all" in types:
        types = ["all"]
    else:
        for t in types:
            if t not in ALLOWED_MEDIA_TYPES:
                return await message.reply_text(
                    f"❌ Invalid type: <code>{t}</code>\n"
                    f"<b>Allowed:</b> {', '.join(sorted(ALLOWED_MEDIA_TYPES))}",
                    parse_mode=ParseMode.HTML,
                )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.set_allowed_types(source, target, types):
        await message.reply_text(
            f"✅ Allowed types set to: <code>{', '.join(types)}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Delay
# ---------------------------------------------------------------------------

@app.on_message(filters.command("setdelay") & filters.private)
async def set_delay_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/setdelay &lt;source&gt; &lt;target&gt; &lt;seconds&gt;</code>\n\n"
            "Example: <code>/setdelay @source @target 3</code>\n"
            "Set to <code>0</code> to disable delay.",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])

    try:
        delay = float(message.command[3])
        if delay < 0:
            raise ValueError
    except ValueError:
        return await message.reply_text(
            "❌ Delay must be a non-negative number (seconds).",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    if db.set_delay(source, target, delay):
        await message.reply_text(
            f"✅ Delay set to <b>{delay}</b> seconds.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Anti-Duplication
# ---------------------------------------------------------------------------

@app.on_message(
    filters.command(["antidupe", "antiduape", "antidu"]) & filters.private
)
async def anti_dupe_cmd(client: Client, message: Message):
    if len(message.command) < 4:
        return await message.reply_text(
            "❌ <b>Usage:</b> "
            "<code>/antidupe &lt;source&gt; &lt;target&gt; on/off</code>",
            parse_mode=ParseMode.HTML,
        )

    source = await resolve_chat_id(client, message.command[1])
    target = await resolve_chat_id(client, message.command[2])
    state = message.command[3].lower()

    if state not in ("on", "off"):
        return await message.reply_text(
            "❌ Please use <code>on</code> or <code>off</code>.",
            parse_mode=ParseMode.HTML,
        )

    if not source or not target:
        return await message.reply_text(
            "❌ Invalid chat.", parse_mode=ParseMode.HTML
        )

    rule = db.get_forward_rule(source, target)
    if not rule:
        return await message.reply_text(
            "❌ Rule not found.", parse_mode=ParseMode.HTML
        )
    if not await can_manage_rule(client, message, source, target, rule):
        return

    enabled = state == "on"
    if db.set_anti_dupe(source, target, enabled):
        await message.reply_text(
            f"✅ Anti-Duplication is now <b>{state.upper()}</b>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            "❌ Failed to update rule.", parse_mode=ParseMode.HTML
        )


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

@app.on_message(filters.command("quota") & filters.private)
async def quota_cmd(client: Client, message: Message):
    info = db.get_user_quota_info(message.from_user.id)

    if info["is_unlimited"]:
        text = (
            "♾️ <b>Your Quota</b>\n\n"
            "You are a <b>Bot Owner / Admin</b>.\n"
            "You have <b>unlimited</b> forwards."
        )
    else:
        text = (
            "📊 <b>Your Daily Quota</b>\n\n"
            f"<b>Used today:</b> <code>{info['used']}</code>\n"
            f"<b>Limit:</b> <code>{info['limit']}</code>\n"
            f"<b>Remaining:</b> <code>{info['remaining']}</code>\n"
            f"<b>Date (UTC):</b> <code>{info['quota_date']}</code>\n\n"
        )
        if info["limit_reached"]:
            text += (
                "🚫 <b>Limit reached!</b> Your rules will be skipped "
                "until next day (UTC)."
            )
        else:
            text += "✅ You can still forward more messages today."

    await message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client: Client, message: Message):
    if not db.is_bot_admin(message.from_user.id):
        return await message.reply_text(
            "❌ This command is only for <b>Bot Owner / Admins</b>.",
            parse_mode=ParseMode.HTML,
        )

    stats = db.get_stats()
    last30 = stats.get("last_30_days_forwarded", 0)

    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"<b>Users:</b> <code>{stats.get('total_users', 0)}</code>\n"
        f"<b>Total Rules:</b> <code>{stats.get('total_rules', 0)}</code>\n"
        f"<b>Enabled Rules:</b> <code>{stats.get('enabled_rules', 0)}</code>\n"
        f"<b>Disabled Rules:</b> <code>{stats.get('disabled_rules', 0)}</code>\n"
        f"<b>Bot Admins:</b> <code>{stats.get('total_bot_admins', 0)}</code>\n\n"
        f"<b>Messages Forwarded:</b> "
        f"<code>{stats.get('total_forwarded', 0)}</code>\n"
        f"📦 <b>Last 30 days forwarded:</b> <code>{last30:,}</code>\n"
        f"<b>Messages Blocked:</b> "
        f"<code>{stats.get('total_blocked', 0)}</code>\n"
        f"<b>Messages Failed:</b> "
        f"<code>{stats.get('total_failed', 0)}</code>\n"
        f"<b>Duplicates Skipped:</b> "
        f"<code>{stats.get('total_duplicates_skipped', 0)}</code>\n"
        f"<b>Daily Limit (normal users):</b> "
        f"<code>{stats.get('daily_forward_limit', DAILY_FORWARD_LIMIT)}</code>\n"
        f"<b>Active user sessions:</b> "
        f"<code>{stats.get('active_user_sessions', 0)}</code>\n"
        f"<b>Active user bots:</b> "
        f"<code>{stats.get('active_user_bots', 0)}</code>"
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Bot Owner Commands
# ---------------------------------------------------------------------------

@app.on_message(filters.command("dbstats") & filters.private)
async def dbstats_cmd(client: Client, message: Message):
    if not db.is_bot_admin(message.from_user.id):
        return await message.reply_text(
            "❌ This command is only for <b>Bot Owner / Admins</b>.",
            parse_mode=ParseMode.HTML,
        )

    stats = db.get_db_stats()
    if "error" in stats:
        return await message.reply_text(
            f"❌ Error: {stats['error']}", parse_mode=ParseMode.HTML
        )

    text = (
        f"🗄 <b>Database Statistics</b>\n\n"
        f"<b>Database:</b> <code>{stats['db_name']}</code>\n"
        f"<b>Collections:</b> <code>{stats['collections_count']}</code>\n"
        f"<b>Total Documents:</b> <code>{stats['total_documents']}</code>\n\n"
        f"<b>Data Size:</b> <code>{stats['data_size_mb']} MB</code>\n"
        f"<b>Storage Size:</b> <code>{stats['storage_size_mb']} MB</code>\n"
        f"<b>Index Size:</b> <code>{stats['index_size_mb']} MB</code>\n\n"
        f"<b>Collections Detail:</b>\n"
    )

    for coll in stats["collections"]:
        text += (
            f"• <code>{coll['name']}</code>: "
            f"{coll['documents']} docs | "
            f"{coll['size_mb']} MB\n"
        )

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.command("deleteallrules") & filters.private)
async def delete_all_rules_cmd(client: Client, message: Message):
    if not db.is_bot_owner(message.from_user.id):
        return await message.reply_text(
            "❌ Only <b>Bot Owner</b> can use this command.",
            parse_mode=ParseMode.HTML,
        )

    if len(message.command) < 2 or message.command[1].lower() != "confirm":
        return await message.reply_text(
            "⚠️ <b>Dangerous Action</b>\n\n"
            "This will delete <b>ALL</b> forward rules of <b>ALL users</b>.\n\n"
            "To confirm, send:\n"
            "<code>/deleteallrules confirm</code>",
            parse_mode=ParseMode.HTML,
        )

    deleted = db.delete_all_rules()
    await message.reply_text(
        f"✅ Deleted <b>{deleted}</b> rules successfully.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("wipedb") & filters.private)
async def wipe_db_cmd(client: Client, message: Message):
    if not db.is_bot_owner(message.from_user.id):
        return await message.reply_text(
            "❌ Only <b>Bot Owner</b> can use this command.",
            parse_mode=ParseMode.HTML,
        )

    if len(message.command) < 2 or message.command[1].lower() != "confirm":
        return await message.reply_text(
            "☢️ <b>EXTREMELY DANGEROUS</b>\n\n"
            "This will <b>DROP ALL COLLECTIONS</b> and wipe the entire database.\n"
            "All users, rules, stats, hashes will be permanently deleted.\n\n"
            "To confirm, send:\n"
            "<code>/wipedb confirm</code>",
            parse_mode=ParseMode.HTML,
        )

    result = db.wipe_database()
    text = "☢️ <b>Database wiped successfully!</b>\n\n"
    for coll, count in result.items():
        text += f"• <code>{coll}</code>: {count} documents removed\n"
    text += "\nEssential structure has been recreated."

    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.command("addadmin") & filters.private)
async def add_admin_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Usage: `/addadmin <user_id>`")

    user_id = message.from_user.id
    if not db.is_bot_owner(user_id):
        return await message.reply_text(
            "❌ Only the Bot Owner can add a new admin."
        )

    try:
        new_admin_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Please provide a valid user_id.")

    if db.add_bot_admin(new_admin_id, added_by=user_id):
        await message.reply_text(
            f"✅ User `{new_admin_id}` has been successfully promoted to Bot Admin."
        )
    else:
        await message.reply_text(
            "❌ This user is already an admin or an error occurred."
        )


@app.on_message(filters.command("removeadmin") & filters.private)
async def remove_admin_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("❌ Usage: `/removeadmin <user_id>`")

    user_id = message.from_user.id
    if not db.is_bot_owner(user_id):
        return await message.reply_text(
            "❌ Only the Bot Owner can remove an admin."
        )

    try:
        target_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Please provide a valid user_id.")

    if db.remove_bot_admin(target_id, removed_by=user_id):
        await message.reply_text(
            f"✅ User `{target_id}` has been removed from Bot Admins."
        )
    else:
        await message.reply_text(
            "❌ User is not an admin, or is the Owner (Owner cannot be removed)."
        )


@app.on_message(filters.command("admins") & filters.private)
async def list_admins_cmd(client: Client, message: Message):
    if not db.is_bot_admin(message.from_user.id):
        return await message.reply_text(
            "❌ This command is restricted to the Bot Owner / Admins only."
        )

    admins = db.get_bot_admins()
    if not admins:
        return await message.reply_text("ℹ️ No Bot Admins found.")

    text = "**🛡 Bot Admins / Owners:**\n\n"
    for admin in admins:
        role = "👑 Owner" if admin.get("is_owner") else "🛡 Admin"
        text += f"• `{admin['user_id']}` — {role}\n"

    await message.reply_text(text)


@app.on_message(filters.command("adminonly") & filters.private)
async def admin_only_cmd(client: Client, message: Message):
    """
    /adminonly on|off
    When ON: only Bot Owner & Bot Admins can use the bot.
    Normal users get a restriction message + source/creator links.
    When OFF: everyone can use the bot (subject to other checks).
    """
    if not db.is_bot_owner(message.from_user.id):
        return await message.reply_text(
            "❌ Only the <b>Bot Owner</b> can toggle Admin-Only mode.",
            parse_mode=ParseMode.HTML,
        )

    args = message.command[1:] if len(message.command) > 1 else []
    if not args:
        status = "ON ✅" if db.is_admin_only() else "OFF ❌"
        return await message.reply_text(
            f"<b>Admin-Only mode:</b> <code>{status}</code>\n\n"
            "<b>Usage:</b>\n"
            "<code>/adminonly on</code> — only Owner/Admins can use the bot\n"
            "<code>/adminonly off</code> — normal users can use the bot too\n\n"
            "When ON, normal users are told the bot is restricted and get "
            "Source Code + Creator links.",
            parse_mode=ParseMode.HTML,
        )

    value = args[0].lower()
    if value in {"on", "1", "true", "yes", "enable", "enabled"}:
        db.set_admin_only(True)
        return await message.reply_text(
            "✅ <b>Admin-Only mode is now ON.</b>\n\n"
            "Only Bot Owner and Bot Admins can use this bot.\n"
            "Normal users will see a restriction message with Source Code "
            "and Creator links.",
            parse_mode=ParseMode.HTML,
        )
    if value in {"off", "0", "false", "no", "disable", "disabled"}:
        db.set_admin_only(False)
        return await message.reply_text(
            "✅ <b>Admin-Only mode is now OFF.</b>\n\n"
            "Normal users can use the bot again.",
            parse_mode=ParseMode.HTML,
        )

    return await message.reply_text(
        "❌ Invalid option.\n\n"
        "Use:\n"
        "<code>/adminonly on</code>\n"
        "<code>/adminonly off</code>",
        parse_mode=ParseMode.HTML,
    )
