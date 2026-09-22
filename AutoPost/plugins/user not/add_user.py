"""
connect.py
Conversation handler for connecting a user's personal Telegram account.

Flow:
1. /connect  → ask phone number
2. User sends phone → send OTP
3. User sends OTP  → try sign_in
4. If 2FA needed  → ask password
5. Success → export session → encrypt & save → start permanent client
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.errors import (
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    PhoneNumberBanned,
    PhoneNumberFlood,
    SessionPasswordNeeded,
    FloodWait,
    RPCError,
)
from pyrogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from AutoPost import API_ID, API_HASH
from AutoPost.Database.database import get_db
from AutoPost.helper.user_clients import get_user_client_manager
from AutoPost.bot import app

LOGGER = logging.getLogger("ConnectPlugin")

# ---------------------------------------------------------------------------
# Simple in-memory state store (user_id → state dict)
# In production you can move this to Redis if you run multiple instances.
# ---------------------------------------------------------------------------

_states: Dict[int, dict] = {}
STATE_TTL = 10 * 60  # 10 minutes
MAX_PHONE_ATTEMPTS = 2


def _get_state(user_id: int) -> Optional[dict]:
    state = _states.get(user_id)
    if not state:
        return None
    if time.time() - state.get("_ts", 0) > STATE_TTL:
        _states.pop(user_id, None)
        return None
    return state


def _set_state(user_id: int, **kwargs) -> None:
    current = _states.get(user_id, {})
    current.update(kwargs)
    current["_ts"] = time.time()
    _states[user_id] = current


def _clear_state(user_id: int) -> None:
    _states.pop(user_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHONE_REGEX = re.compile(r"^\+?[1-9]\d{6,14}$")


def _normalize_phone(text: str) -> Optional[str]:
    text = text.strip().replace(" ", "").replace("-", "")
    if not text.startswith("+"):
        # Assume user forgot +, but still require country code
        if text.isdigit() and len(text) >= 8:
            text = "+" + text
        else:
            return None
    if PHONE_REGEX.match(text):
        return text
    return None


CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Cancel")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@app.on_message(filters.command("connect") & filters.private)
async def connect_command(client: Client, message: Message):
    """Entry point – /connect"""
    user_id = message.from_user.id
    db = get_db()

    # Already has an active session?
    if db.has_active_session(user_id):
        info = db.get_session_info(user_id)
        uname = f"@{info['tg_username']}" if info and info.get("tg_username") else "N/A"
        await message.reply_text(
            f"You already have a connected account:\n\n"
            f"• Phone: `{info.get('phone_number', 'N/A')}`\n"
            f"• Telegram: {uname} (`{info.get('tg_user_id')}`)\n\n"
            f"Use /account to manage it or /disconnect to remove it.",
            quote=True,
        )
        return

    _clear_state(user_id)
    _set_state(user_id, step="phone", phone_attempts=0)

    await message.reply_text(
        "**Connect your Telegram Account**\n\n"
        "Please send your phone number in international format.\n"
        "Example: `+919876543210`\n\n"
        "This number will receive a login code from Telegram.\n"
        f"You have **{MAX_PHONE_ATTEMPTS}** attempts.",
        reply_markup=CANCEL_KEYBOARD,
        quote=True,
    )


async def _in_connect_flow_filter(_, __, message: Message) -> bool:
    """Only match while user is inside /connect conversation."""
    return _get_state(message.from_user.id) is not None


in_connect_flow = filters.create(_in_connect_flow_filter)


@app.on_message(
    filters.private & filters.text & in_connect_flow,
    group=20,
)
async def connect_conversation(client: Client, message: Message):
    """Main conversation handler for the login flow."""
    user_id = message.from_user.id
    state = _get_state(user_id)

    if not state:
        return  # not in a conversation

    text = message.text.strip()

    # Global cancel
    if text.lower() in {"❌ cancel", "cancel", "/cancel"}:
        await _cancel(client, message)
        return

    # Ignore other bot commands (e.g. the /connect message itself that started the flow)
    # so they are not treated as invalid phone/code/password input.
    if text.startswith("/"):
        return

    step = state.get("step")

    # ---------------------------------------------------------------
    # Step 1: Phone number (max MAX_PHONE_ATTEMPTS tries)
    # ---------------------------------------------------------------
    if step == "phone":
        attempts = int(state.get("phone_attempts") or 0) + 1
        _set_state(user_id, phone_attempts=attempts)

        phone = _normalize_phone(text)
        if not phone:
            if attempts >= MAX_PHONE_ATTEMPTS:
                _clear_state(user_id)
                await message.reply_text(
                    f"❌ Invalid phone number again ({attempts}/{MAX_PHONE_ATTEMPTS}).\n\n"
                    "Too many failed attempts. Please start again with /connect",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return

            await message.reply_text(
                f"❌ Invalid phone number ({attempts}/{MAX_PHONE_ATTEMPTS}).\n"
                "Please send it in international format, e.g. `+919876543210`",
                reply_markup=CANCEL_KEYBOARD,
            )
            return

        # Create a temporary in-memory client just for login
        temp_client = Client(
            name=f"temp_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            no_updates=True,
        )

        try:
            await temp_client.connect()
            sent = await temp_client.send_code(phone)
        except PhoneNumberInvalid:
            await temp_client.disconnect()
            if attempts >= MAX_PHONE_ATTEMPTS:
                _clear_state(user_id)
                await message.reply_text(
                    f"❌ This phone number is invalid ({attempts}/{MAX_PHONE_ATTEMPTS}).\n\n"
                    "Too many failed attempts. Please start again with /connect",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return
            await message.reply_text(
                f"❌ This phone number is invalid ({attempts}/{MAX_PHONE_ATTEMPTS}).\n"
                "Please try again.",
                reply_markup=CANCEL_KEYBOARD,
            )
            return
        except PhoneNumberBanned:
            await temp_client.disconnect()
            _clear_state(user_id)
            await message.reply_text(
                "❌ This phone number is banned from Telegram.\n"
                "Please start again with /connect using a different number.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        except PhoneNumberFlood:
            await temp_client.disconnect()
            _clear_state(user_id)
            await message.reply_text(
                "❌ Too many attempts for this number. Please wait a while and try again later with /connect.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        except FloodWait as e:
            await temp_client.disconnect()
            _clear_state(user_id)
            await message.reply_text(
                f"❌ Telegram rate limit. Please wait {e.value} seconds and try /connect again.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        except Exception as e:
            await temp_client.disconnect()
            _clear_state(user_id)
            LOGGER.exception(f"send_code failed for {user_id}")
            await message.reply_text(
                f"❌ Failed to send code: `{type(e).__name__}`\nPlease try again later with /connect.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        # Store temporary client + hash in state (never store the code itself)
        _set_state(
            user_id,
            step="code",
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            temp_client=temp_client,
            phone_attempts=0,
        )

        await message.reply_text(
            f"A login code has been sent to **{phone}**.\n\n"
            "Please enter the code you received.\n"
            "(You can also get it from any already logged-in device)",
            reply_markup=CANCEL_KEYBOARD,
        )
        return

    # ---------------------------------------------------------------
    # Step 2: OTP / Login code
    # ---------------------------------------------------------------
    if step == "code":
        code = text.replace(" ", "").replace("-", "")
        if not code.isdigit() or not (4 <= len(code) <= 6):
            await message.reply_text("Please enter a valid numeric code (4-6 digits).", reply_markup=CANCEL_KEYBOARD)
            return

        temp_client: Client = state["temp_client"]
        phone = state["phone"]
        phone_code_hash = state["phone_code_hash"]

        try:
            signed_in = await temp_client.sign_in(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                phone_code=code,
            )
            # Success without 2FA
            await _finish_login(client, message, user_id, temp_client, phone)
            return

        except SessionPasswordNeeded:
            # 2FA required
            _set_state(user_id, step="password")
            await message.reply_text(
                "Two-Step Verification is enabled on this account.\n\n"
                "Please enter your Telegram password:",
                reply_markup=CANCEL_KEYBOARD,
            )
            return

        except PhoneCodeInvalid:
            await message.reply_text("Invalid code. Please try again.", reply_markup=CANCEL_KEYBOARD)
            return

        except PhoneCodeExpired:
            await temp_client.disconnect()
            _clear_state(user_id)
            await message.reply_text(
                "The code has expired. Please start again with /connect",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        except FloodWait as e:
            await temp_client.disconnect()
            _clear_state(user_id)
            await message.reply_text(
                f"Rate limited by Telegram. Wait {e.value} seconds and try /connect again.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        except Exception as e:
            await temp_client.disconnect()
            _clear_state(user_id)
            LOGGER.exception(f"sign_in failed for {user_id}")
            await message.reply_text(
                f"Login failed: `{type(e).__name__}`\nPlease try again with /connect",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

    # ---------------------------------------------------------------
    # Step 3: 2FA password
    # ---------------------------------------------------------------
    if step == "password":
        password = text  # do NOT log or store this

        temp_client: Client = state["temp_client"]
        phone = state["phone"]

        try:
            await temp_client.check_password(password)
            # Success
            await _finish_login(client, message, user_id, temp_client, phone)
            return

        except Exception as e:
            # Wrong password or other error
            # We keep the state so user can try again a few times
            err_name = type(e).__name__
            if "PASSWORD" in err_name.upper() or "INVALID" in err_name.upper():
                await message.reply_text(
                    "Incorrect password. Please try again:",
                    reply_markup=CANCEL_KEYBOARD,
                )
                return

            await temp_client.disconnect()
            _clear_state(user_id)
            LOGGER.exception(f"check_password failed for {user_id}")
            await message.reply_text(
                f"Failed to verify password: `{err_name}`\nPlease start again with /connect",
                reply_markup=ReplyKeyboardRemove(),
            )
            return


async def _finish_login(
    bot: Client,
    message: Message,
    user_id: int,
    temp_client: Client,
    phone: str,
) -> None:
    """Export session, save to DB, start permanent client, clean up."""
    db = get_db()
    manager = get_user_client_manager()

    try:
        me = await temp_client.get_me()
        session_string = await temp_client.export_session_string()
    except Exception as e:
        await temp_client.disconnect()
        _clear_state(user_id)
        LOGGER.exception("Failed to export session")
        await message.reply_text(
            f"Failed to finalize login: `{type(e).__name__}`\nPlease try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Disconnect temporary client immediately
    try:
        await temp_client.disconnect()
    except Exception:
        pass

    # Save encrypted session
    try:
        db.save_user_session(
            user_id=user_id,
            session_string=session_string,
            phone_number=phone,
            tg_user_id=me.id,
            tg_username=me.username,
            tg_first_name=me.first_name,
            tg_last_name=me.last_name,
            dc_id=getattr(me, "dc_id", None),
        )
    except Exception as e:
        _clear_state(user_id)
        LOGGER.exception("Failed to save session to DB")
        await message.reply_text(
            "Login succeeded but failed to save session securely. Please contact the bot owner.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Start the permanent client via manager
    permanent = await manager.start_user_client(user_id, session_string=session_string)

    _clear_state(user_id)

    if permanent is None:
        await message.reply_text(
            "Account linked, but failed to start the session.\n"
            "Try /account or contact support.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    uname = f"@{me.username}" if me.username else "No username"
    await message.reply_text(
        "**Account connected successfully!**\n\n"
        f"• Name: {me.first_name or ''} {me.last_name or ''}\n"
        f"• Username: {uname}\n"
        f"• Telegram ID: `{me.id}`\n"
        f"• Phone: `{phone}`\n\n"
        "You can now create forwarding rules that will use **your** account.\n"
        "Use /account to manage the connection.",
        reply_markup=ReplyKeyboardRemove(),
        quote=True,
    )
    LOGGER.info(f"User {user_id} successfully connected TG account {me.id}")


async def _cancel(client: Client, message: Message) -> None:
    user_id = message.from_user.id
    state = _get_state(user_id)

    if state and "temp_client" in state:
        try:
            await state["temp_client"].disconnect()
        except Exception:
            pass

    _clear_state(user_id)
    await message.reply_text(
        "Connection cancelled.",
        reply_markup=ReplyKeyboardRemove(),
    )


@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, message: Message):
    await _cancel(client, message)
