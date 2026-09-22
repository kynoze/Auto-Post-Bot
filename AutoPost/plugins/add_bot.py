"""
user_bot_cmds.py
Commands to connect / view / remove a user's own forwarding bot.

/addbot     → ask for bot token → verify → save → start
/mybot      → show connected bot status
/removebot  → stop + delete token
"""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import (
    AccessTokenInvalid,
    AccessTokenExpired,
    FloodWait,
    MessageNotModified, 
    BadRequest,
)
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
)


from AutoPost import API_ID, API_HASH
from AutoPost.Database.database import get_db
from AutoPost.helper.user_bots import get_user_bot_manager
from AutoPost.bot import app

LOGGER = logging.getLogger("UserBotCmds")
db = get_db()

# Simple in-memory state for token input
# user_id → {"ts": float, "attempts": int}
_waiting_token: dict[int, dict] = {}
TOKEN_TTL = 5 * 60  # 5 minutes
MAX_TOKEN_ATTEMPTS = 2


def _is_waiting(user_id: int) -> bool:
    import time
    data = _waiting_token.get(user_id)
    if not data:
        return False
    if time.time() - data.get("ts", 0) > TOKEN_TTL:
        _waiting_token.pop(user_id, None)
        return False
    return True


def _set_waiting(user_id: int) -> None:
    import time
    _waiting_token[user_id] = {"ts": time.time(), "attempts": 0}


def _clear_waiting(user_id: int) -> None:
    _waiting_token.pop(user_id, None)


def _bump_attempts(user_id: int) -> int:
    import time
    data = _waiting_token.get(user_id) or {"ts": time.time(), "attempts": 0}
    data["attempts"] = int(data.get("attempts") or 0) + 1
    data["ts"] = time.time()
    _waiting_token[user_id] = data
    return data["attempts"]


async def _waiting_token_filter(_, __, message: Message) -> bool:
    return _is_waiting(message.from_user.id)

waiting_token_filter = filters.create(_waiting_token_filter)


BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


# ---------------------------------------------------------------------------
# /addbot
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addbot") & filters.private)
async def addbot_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    # Already has a bot?
    info = db.get_user_bot_info(user_id)
    if info and info.get("is_active"):
        uname = f"@{info['bot_username']}" if info.get("bot_username") else "N/A"
        return await message.reply_text(
            "<b>You already have a connected bot.</b>\n\n"
            f"• Name: <code>{info.get('bot_name') or 'N/A'}</code>\n"
            f"• Username: {uname}\n"
            f"• ID: <code>{info.get('bot_id')}</code>\n\n"
            "Use /mybot to manage it or /removebot to disconnect first.",
            parse_mode=ParseMode.HTML,
        )

    _set_waiting(user_id)

    await message.reply_text(
        "<b>Connect your Forwarding Bot</b>\n\n"
        "1. Open @BotFather\n"
        "2. Create a new bot or use an existing one\n"
        "3. Copy the <b>bot token</b>\n"
        "4. Send it here\n\n"
        "<b>Example:</b>\n"
        "<code>123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n\n"
        "⚠️ Never share this token with anyone else.\n"
        f"You have <b>{MAX_TOKEN_ATTEMPTS}</b> attempts.\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(
    filters.private
    & filters.text
    & waiting_token_filter,   # ← sirf jab token wait ho
    group=20,
)
async def addbot_token_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if not _is_waiting(user_id):
        return

    text = message.text.strip()

    # Ignore other bot commands (e.g. the /addbot message itself that set waiting state)
    # so they don't count as invalid token attempts.
    if text.startswith("/") and text.lower() not in {"/cancel", "cancel"}:
        return

    if text.lower() in {"/cancel", "cancel"}:
        _clear_waiting(user_id)
        return await message.reply_text(
            "Cancelled.",
            reply_markup=ReplyKeyboardRemove(),
        )

    if not BOT_TOKEN_RE.match(text):
        attempts = _bump_attempts(user_id)
        if attempts >= MAX_TOKEN_ATTEMPTS:
            _clear_waiting(user_id)
            return await message.reply_text(
                f"❌ Invalid token format ({attempts}/{MAX_TOKEN_ATTEMPTS}).\n\n"
                "Too many failed attempts. Please start again with /addbot",
                parse_mode=ParseMode.HTML,
            )
        return await message.reply_text(
            f"❌ Invalid token format ({attempts}/{MAX_TOKEN_ATTEMPTS}).\n\n"
            "Token should look like:\n"
            "<code>123456789:AAHxxxx...</code>\n\n"
            "Try again or send /cancel.",
            parse_mode=ParseMode.HTML,
        )

    # Delete the message that contains the token (privacy)
    try:
        await message.delete()
    except Exception:
        pass

    status = await message.reply_text("🔄 Verifying token...")

    # Temporary client to verify token
    temp = Client(
        name=f"verify_{user_id}",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=text,
        in_memory=True,
    )

    try:
        await temp.start()
        me = await temp.get_me()
        await temp.stop()
    except (AccessTokenInvalid, AccessTokenExpired):
        attempts = _bump_attempts(user_id)
        if attempts >= MAX_TOKEN_ATTEMPTS:
            _clear_waiting(user_id)
            return await status.edit_text(
                f"❌ Invalid or expired bot token ({attempts}/{MAX_TOKEN_ATTEMPTS}).\n\n"
                "Too many failed attempts. Please generate a new token from "
                "@BotFather and try /addbot again."
            )
        return await status.edit_text(
            f"❌ Invalid or expired bot token ({attempts}/{MAX_TOKEN_ATTEMPTS}).\n"
            "Please check the token and try again, or send /cancel."
        )
    except FloodWait as e:
        _clear_waiting(user_id)
        return await status.edit_text(
            f"❌ Rate limited by Telegram. Wait {e.value}s and try /addbot again."
        )
    except Exception as e:
        _clear_waiting(user_id)
        LOGGER.exception("Token verify failed")
        return await status.edit_text(
            f"❌ Verification failed: <code>{type(e).__name__}</code>\n"
            "Please try /addbot again.",
            parse_mode=ParseMode.HTML,
        )

    # Save encrypted token
    try:
        db.save_user_bot(
            user_id=user_id,
            bot_token=text,
            bot_id=me.id,
            bot_username=me.username,
            bot_name=me.first_name,
        )
    except Exception as e:
        _clear_waiting(user_id)
        LOGGER.exception("Failed to save user bot")
        return await status.edit_text(
            "❌ Failed to save bot securely. Contact support."
        )

    # Start permanent client via manager
    manager = get_user_bot_manager()
    bot_client = await manager.start_user_bot(user_id, bot_token=text)

    _clear_waiting(user_id)

    uname = f"@{me.username}" if me.username else "N/A"

    if bot_client and bot_client.is_connected:
        await status.edit_text(
            "<b>✅ Bot connected successfully!</b>\n\n"
            f"• Name: <code>{me.first_name}</code>\n"
            f"• Username: {uname}\n"
            f"• ID: <code>{me.id}</code>\n\n"
            "<b>Next steps:</b>\n"
            "1. Add this bot as <b>Admin</b> in your <b>Source</b> channel/group\n"
            "2. Add this bot as <b>Admin</b> in your <b>Target</b> channel/group "
            "(with permission to post messages)\n"
            "3. Create a rule with /addrule\n\n"
            "Use /mybot anytime to check status.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await status.edit_text(
            "<b>⚠️ Token saved but bot failed to start.</b>\n\n"
            f"• Username: {uname}\n"
            f"• ID: <code>{me.id}</code>\n\n"
            "Try /mybot or /removebot and /addbot again.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# /mybot
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["mybot", "botinfo"]) & filters.private)
async def mybot_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    info = db.get_user_bot_info(user_id)
    manager = get_user_bot_manager()

    if not info:
        return await message.reply_text(
            "<b>No forwarding bot connected.</b>\n\n"
            "Use /addbot to connect your own bot.\n"
            "That bot will do the actual forwarding.",
            parse_mode=ParseMode.HTML,
        )

    is_running = manager.is_running(user_id)
    status_emoji = "🟢" if (info.get("is_active") and is_running) else "🔴"
    status_text = (
        "Online & Ready"
        if (info.get("is_active") and is_running)
        else "Offline / Inactive"
    )

    uname = f"@{info['bot_username']}" if info.get("bot_username") else "N/A"

    text = (
        f"<b>Your Forwarding Bot</b> {status_emoji}\n\n"
        f"<b>Status:</b> {status_text}\n"
        f"<b>Name:</b> <code>{info.get('bot_name') or 'N/A'}</code>\n"
        f"<b>Username:</b> {uname}\n"
        f"<b>Bot ID:</b> <code>{info.get('bot_id')}</code>\n\n"
        f"<b>Connected:</b> <code>{info.get('created_at')}</code>\n"
        f"<b>Last used:</b> <code>{info.get('last_used')}</code>\n"
    )

    buttons = [
        [
            InlineKeyboardButton("🔄 Restart", callback_data="userbot:restart"),
            InlineKeyboardButton("🗑 Remove", callback_data="userbot:remove"),
        ]
    ]

    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ---------------------------------------------------------------------------
# /removebot
# ---------------------------------------------------------------------------

@app.on_message(filters.command(["removebot", "delbot", "unlinkbot"]) & filters.private)
async def removebot_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    info = db.get_user_bot_info(user_id)

    if not info:
        return await message.reply_text(
            "You don't have any connected bot.",
            parse_mode=ParseMode.HTML,
        )

    await message.reply_text(
        "<b>Remove your forwarding bot?</b>\n\n"
        "This will:\n"
        "• Stop the bot\n"
        "• Delete the stored token\n"
        "• Your rules using this bot will stop working\n\n"
        "Confirm?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Yes, Remove", callback_data="userbot:remove_confirm"
                ),
                InlineKeyboardButton("❌ Cancel", callback_data="userbot:cancel"),
            ]
        ]),
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^userbot:"))
async def userbot_callbacks(client: Client, callback):
    data = callback.data
    user_id = callback.from_user.id
    manager = get_user_bot_manager()

    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Only in private chat.", show_alert=True)
        return

    if data == "userbot:cancel":
        try:
            await callback.message.edit_text("Cancelled.")
        except (BadRequest, MessageNotModified):
            pass
        await callback.answer()
        return

    if data == "userbot:remove":
        try:
            await callback.message.edit_text(
                "<b>Are you sure?</b>\n\nToken will be permanently deleted.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Yes, Remove", callback_data="userbot:remove_confirm"
                        ),
                            InlineKeyboardButton("❌ Cancel", callback_data="userbot:cancel"),
                ]
                ]),
            )
        except MessageNotModified:
            pass   
        await callback.answer()
        return

    if data == "userbot:remove_confirm":
        await callback.answer("Removing...")
        await manager.disconnect_and_delete(user_id)
        await callback.message.edit_text(
            "<b>✅ Bot removed.</b>\n\n"
            "Use /addbot whenever you want to connect a new one.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "userbot:restart":
        await callback.answer("Restarting...")
        await callback.message.edit_text("🔄 Restarting your bot...")

        await manager.stop_user_bot(user_id, delete_from_db=False)
        bot_client = await manager.start_user_bot(user_id)

        if bot_client and bot_client.is_connected:
            me = await bot_client.get_me()
            uname = f"@{me.username}" if me.username else "N/A"
            await callback.message.edit_text(
                f"<b>✅ Restarted successfully!</b>\n\n"
                f"• {me.first_name}\n"
                f"• {uname} (<code>{me.id}</code>)\n"
                f"Status: 🟢 Online",
                parse_mode=ParseMode.HTML,
            )
        else:
            db.mark_bot_inactive(user_id)
            await callback.message.edit_text(
                "<b>❌ Failed to restart.</b>\n\n"
                "Token may be invalid. Use /removebot and /addbot again.",
                parse_mode=ParseMode.HTML,
            )
        return
