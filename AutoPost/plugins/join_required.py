from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from pyrogram.enums import ParseMode
import logging
from AutoPost.Database.database import get_db
from AutoPost.bot import app

logger = logging.getLogger(__name__)
db = get_db()

FORCE_SUB_CHANNEL = -1001633563818   # ← apna sahi channel ID daalo


async def check_force_sub(client: Client, message: Message) -> bool:
    """
    Returns True  → user can use the bot
    Returns False → user has not joined, join message already sent
    """
    user_id = message.from_user.id

    # Bot Owner / Admins ko bypass
    if db.is_bot_admin(user_id):
        return True

    try:
        member = await client.get_chat_member(FORCE_SUB_CHANNEL, user_id)

        # Pyrogram new versions use Enum
        if member.status in (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,          # old name was KICKED
            ChatMemberStatus.RESTRICTED,
        ):
            raise UserNotParticipant

        return True

    except UserNotParticipant:
        # User ne join nahi kiya → Join message bhejo
        invite_link = None
        try:
            chat = await client.get_chat(FORCE_SUB_CHANNEL)
            invite_link = chat.invite_link
            if not invite_link and chat.username:
                invite_link = f"https://t.me/{chat.username}"
        except (ChatAdminRequired, ChannelPrivate, Exception) as e:
            logger.warning(f"Could not get invite link for force-sub channel: {e}")

        buttons = []
        if invite_link:
            buttons.append([
                InlineKeyboardButton("🔔 Join Channel", url=invite_link)
            ])
        buttons.append([
            InlineKeyboardButton("✅ I have Joined", callback_data="check_fsub")
        ])

        await message.reply_text(
            "📢 <b>Channel Subscription Required</b>\n\n"
            "You must join our channel to use this bot.\n\n"
            "Please join the channel and then click the button below.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return False

    except Exception as e:
        # Bot channel mein admin nahi hai ya koi unexpected error
        logger.error(f"ForceSub check error for user {user_id}: {e}")
        # Fail-open (bot chalna chahiye) – chahein to False bhi rakh sakte ho
        return True


@app.on_callback_query(filters.regex("^check_fsub$"))
async def fsub_check_callback(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id

    try:
        member = await client.get_chat_member(FORCE_SUB_CHANNEL, user_id)

        if member.status in (
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,
            ChatMemberStatus.RESTRICTED,
        ):
            await callback.answer(
                "❌ You still haven't joined the channel!",
                show_alert=True
            )
            return

    except UserNotParticipant:
        await callback.answer(
            "❌ You still haven't joined the channel!",
            show_alert=True
        )
        return
    except Exception as e:
        logger.error(f"FSub callback error: {e}")
        await callback.answer("Error while checking. Please try again.", show_alert=True)
        return

    # Success
    await callback.answer("✅ Thanks for joining!", show_alert=True)

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.reply_text(
        "✅ <b>Verification successful!</b>\n\nYou can now use the bot.",
        parse_mode=ParseMode.HTML,
    )
