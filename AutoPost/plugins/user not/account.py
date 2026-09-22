"""
account.py
Commands for managing the connected Telegram user account.

Commands:
- /account     → Show current connection status + details
- /disconnect  → Stop client + delete session
- /reconnect   → Force restart the user client (useful after network issues)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from AutoPost.Database.database import get_db
from AutoPost.helper.user_clients import get_user_client_manager
from AutoPost.bot import app
from pyrogram.enums import ChatType

LOGGER = logging.getLogger("AccountPlugin")


def _format_dt(dt) -> str:
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------
# /account
# ------------------------------------------------------------------

@app.on_message(filters.command(["account", "myaccount", "session"]) & filters.private)
async def account_command(client: Client, message: Message):
    """Show detailed information about the connected Telegram account."""
    user_id = message.from_user.id
    db = get_db()
    manager = get_user_client_manager()

    info = db.get_session_info(user_id)

    if not info:
        await message.reply_text(
            "**No Telegram account connected.**\n\n"
            "Use /connect to link your personal Telegram account.\n"
            "After connecting, all your forwarding rules will use your own account.",
            quote=True,
        )
        return

    is_running = manager.is_running(user_id)
    status_emoji = "🟢" if (info.get("is_active") and is_running) else "🔴"
    status_text = "Online & Ready" if (info.get("is_active") and is_running) else "Offline / Inactive"

    phone = info.get("phone_number") or "N/A"
    # Mask middle digits for privacy
    if phone.startswith("+") and len(phone) > 8:
        phone_display = phone[:4] + "****" + phone[-3:]
    else:
        phone_display = phone

    uname = f"@{info['tg_username']}" if info.get("tg_username") else "No username"
    full_name = " ".join(filter(None, [info.get("tg_first_name"), info.get("tg_last_name")])) or "N/A"

    text = (
        f"**Connected Telegram Account** {status_emoji}\n\n"
        f"**Status:** {status_text}\n"
        f"**Name:** {full_name}\n"
        f"**Username:** {uname}\n"
        f"**Telegram ID:** `{info.get('tg_user_id')}`\n"
        f"**Phone:** `{phone_display}`\n"
        f"**DC:** {info.get('dc_id') or 'N/A'}\n\n"
        f"**Connected:** {_format_dt(info.get('created_at'))}\n"
        f"**Last used:** {_format_dt(info.get('last_used'))}\n"
    )

    buttons = []

    if info.get("is_active") and is_running:
        buttons.append([
            InlineKeyboardButton("🔄 Reconnect", callback_data="account:reconnect"),
            InlineKeyboardButton("🚪 Disconnect", callback_data="account:disconnect"),
        ])
    elif info.get("is_active"):
        buttons.append([
            InlineKeyboardButton("🔄 Reconnect / Start", callback_data="account:reconnect"),
            InlineKeyboardButton("🗑️ Delete Session", callback_data="account:disconnect"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("🔄 Try Reconnect", callback_data="account:reconnect"),
            InlineKeyboardButton("🗑️ Delete Session", callback_data="account:disconnect"),
        ])

    buttons.append([InlineKeyboardButton("🔗 Connect New Account", callback_data="account:connect")])

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
    )


# ------------------------------------------------------------------
# /disconnect
# ------------------------------------------------------------------

@app.on_message(filters.command(["disconnect", "logout", "unlink"]) & filters.private)
async def disconnect_command(client: Client, message: Message):
    """Disconnect and permanently delete the stored session."""
    user_id = message.from_user.id
    db = get_db()
    manager = get_user_client_manager()

    if not db.get_session_info(user_id):
        await message.reply_text("You don't have any connected account.", quote=True)
        return

    # Ask for confirmation via inline buttons (safer)
    await message.reply_text(
        "**Are you sure you want to disconnect?**\n\n"
        "This will:\n"
        "• Stop your Telegram session\n"
        "• Permanently delete the stored session\n"
        "• Your forwarding rules will stop working until you connect again\n\n"
        "This action cannot be undone.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Disconnect", callback_data="account:disconnect_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="account:cancel"),
            ]
        ]),
        quote=True,
    )


# ------------------------------------------------------------------
# /reconnect
# ------------------------------------------------------------------

@app.on_message(filters.command(["reconnect", "restartsession"]) & filters.private)
async def reconnect_command(client: Client, message: Message):
    """Force restart the user client."""
    user_id = message.from_user.id
    db = get_db()
    manager = get_user_client_manager()

    info = db.get_session_info(user_id)
    if not info:
        await message.reply_text(
            "No session found. Use /connect first.",
            quote=True,
        )
        return

    status_msg = await message.reply_text("🔄 Reconnecting your account...", quote=True)

    # Stop existing client first (if any)
    await manager.stop_user_client(user_id, delete_session=False)

    # Try to start again
    client_obj = await manager.start_user_client(user_id)

    if client_obj and client_obj.is_connected:
        me = await client_obj.get_me()
        uname = f"@{me.username}" if me.username else "N/A"
        await status_msg.edit_text(
            f"**Reconnected successfully!**\n\n"
            f"• {me.first_name or ''} {me.last_name or ''}\n"
            f"• {uname} (`{me.id}`)\n"
            f"Status: 🟢 Online"
        )
    else:
        # Session is probably dead
        db.mark_session_inactive(user_id)
        await status_msg.edit_text(
            "**Failed to reconnect.**\n\n"
            "The session appears to be invalid or revoked.\n"
            "Please use /disconnect and then /connect again."
        )


# ------------------------------------------------------------------
# Callback handlers for the inline buttons
# ------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^account:"))
async def account_callbacks(client: Client, callback):
    data = callback.data
    user_id = callback.from_user.id
    db = get_db()
    manager = get_user_client_manager()

    # Security: only the owner can press these buttons
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("This can only be used in private chat.", show_alert=True)
        return

    if data == "account:cancel":
        await callback.message.edit_text("Cancelled.")
        await callback.answer()
        return

    if data == "account:connect":
        await callback.answer()
        # Redirect to /connect by simulating the command
        await callback.message.reply_text("Starting connection process...")
        # We call the connect command handler manually
        from AutoPost.plugins.connect import connect_command
        # Create a fake message-like object is complicated; just tell the user
        await callback.message.reply_text("Please send /connect to start linking a new account.")
        return

    if data == "account:disconnect":
        # Show confirmation
        await callback.message.edit_text(
            "**Are you sure you want to disconnect?**\n\n"
            "This will permanently delete your stored session.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Disconnect", callback_data="account:disconnect_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="account:cancel"),
                ]
            ]),
        )
        await callback.answer()
        return

    if data == "account:disconnect_confirm":
        await callback.answer("Disconnecting...")

        success = await manager.disconnect_and_delete(user_id)

        if success or not db.get_session_info(user_id):
            await callback.message.edit_text(
                "**Account disconnected successfully.**\n\n"
                "Your session has been removed.\n"
                "Use /connect whenever you want to link an account again."
            )
        else:
            await callback.message.edit_text(
                "Something went wrong while disconnecting.\n"
                "Please try /disconnect again or contact support."
            )
        return

    if data == "account:reconnect":
        await callback.answer("Reconnecting...")

        await callback.message.edit_text("🔄 Reconnecting your account...")

        await manager.stop_user_client(user_id, delete_session=False)
        client_obj = await manager.start_user_client(user_id)

        if client_obj and client_obj.is_connected:
            me = await client_obj.get_me()
            uname = f"@{me.username}" if me.username else "N/A"
            await callback.message.edit_text(
                f"**Reconnected successfully!**\n\n"
                f"• {me.first_name or ''} {me.last_name or ''}\n"
                f"• {uname} (`{me.id}`)\n"
                f"Status: 🟢 Online"
            )
        else:
            db.mark_session_inactive(user_id)
            await callback.message.edit_text(
                "**Failed to reconnect.**\n\n"
                "The session is invalid or has been revoked by Telegram.\n"
                "Please /disconnect and then /connect again with a fresh login."
            )
        return
