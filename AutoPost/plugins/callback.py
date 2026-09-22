"""
callbacks.py
Inline menu callbacks for Auto Forward Bot
"""

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)

from AutoPost.Textmsgs import textmsgs
from AutoPost import DAILY_FORWARD_LIMIT as LIMIT
from AutoPost.bot import app
 
def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Features", callback_data="menu_features"),
            InlineKeyboardButton("📖 All Commands", callback_data="menu_help"),
        ],
        [
            InlineKeyboardButton("🛠 Commands Format", callback_data="menu_howto"),
            InlineKeyboardButton("📚 Guides", callback_data="menu_guides"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Creator", url="https://t.me/KynozeFun"),
        ],
    ])


def guides_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Add Rule", callback_data="guide_rules"),
            InlineKeyboardButton("✏️ Simple Caption", callback_data="guide_simple_caption"),
        ],
        [
            InlineKeyboardButton("🎨 Custom Caption", callback_data="guide_custom_caption"),
            InlineKeyboardButton("🔄 Replacement", callback_data="guide_replace"),
        ],
        [
            InlineKeyboardButton("🚫 Block Words", callback_data="guide_block"),
            InlineKeyboardButton("✅ Whitelist", callback_data="guide_whitelist"),
        ],
        [
            InlineKeyboardButton("🔗 Remove Links", callback_data="guide_links"),
            InlineKeyboardButton("🔘 Inline Buttons", callback_data="guide_buttons"),
        ],
        [
            InlineKeyboardButton("📂 Media Types", callback_data="guide_types"),
            InlineKeyboardButton("🎬 Movies / Series", callback_data="guide_content"),
        ],
        [
            InlineKeyboardButton("📏 Media Size", callback_data="guide_size"),
            InlineKeyboardButton("🎟️ Sticker", callback_data="guide_sticker"),
        ],
        [
            InlineKeyboardButton("🏷 Forward Tag", callback_data="guide_forward_tag"),
            InlineKeyboardButton("⏱ Delay", callback_data="guide_delay"),
        ],
        [
            InlineKeyboardButton("🛡 Anti-Dupe", callback_data="guide_antidupe"),
            InlineKeyboardButton("⏸ Pause / Resume", callback_data="guide_pause"),
        ],
        [
            InlineKeyboardButton("📊 Quota", callback_data="guide_quota"),
            InlineKeyboardButton("📈 Stats", callback_data="guide_stats"),
        ],
        [
            InlineKeyboardButton("ℹ️ General", callback_data="guide_general"),
            InlineKeyboardButton("🛡 Admin", callback_data="guide_admin"),
        ],
        [
            InlineKeyboardButton("🏠 Home", callback_data="menu_home"),
        ],
    ])



def back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Home", callback_data="menu_home"),
        ]])
    
def back_to_guides() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
            InlineKeyboardButton("Back", callback_data="menu_guides"),
        ]])        

@app.on_callback_query(filters.regex(r"^(menu_|guide_)"))
async def menu_callbacks(client: Client, callback: CallbackQuery):
    data = callback.data

    if data == "menu_home":
        text = textmsgs.START_MSG.format(callback.from_user.mention, LIMIT)
        keyboard = home_keyboard()

    elif data == "menu_features":
        text = textmsgs.FEATURE_MSG
        keyboard = back_to_home()
    elif data == "menu_help":
        text = textmsgs.HELP_MSG
        keyboard = back_to_home()
    elif data == "menu_howto":
        text = textmsgs.FORMAT_MSG
        keyboard = back_to_home()

    elif data == "menu_guides":
        text = (
            "📚 <b>Feature Guides</b>\n\n"
            "Tap a button for detailed usage, Chat ID examples, "
            "and supported HTML formats.\n\n"
            "<i>Forward a message with the forward tag from your</i> "
            "<i>channel to this bot to get the Channel ID.</i>"
        )
        keyboard = guides_keyboard()

    elif data == "guide_rules":
        text = textmsgs.GUIDE_RULES
        keyboard = back_to_guides()

    elif data == "guide_simple_caption":
        text = textmsgs.GUIDE_SIMPLE_CAPTION
        keyboard = back_to_guides()

    elif data == "guide_custom_caption":
        text = textmsgs.GUIDE_CUSTOM_CAPTION
        keyboard = back_to_guides()

    elif data == "guide_replace":
        text = textmsgs.GUIDE_REPLACE
        keyboard = back_to_guides()

    elif data == "guide_block":
        text = textmsgs.GUIDE_BLOCK
        keyboard = back_to_guides()

    elif data == "guide_whitelist":
        text = textmsgs.GUIDE_WHITELIST
        keyboard = back_to_guides()

    elif data == "guide_links":
        text = textmsgs.GUIDE_LINKS
        keyboard = back_to_guides()

    elif data == "guide_buttons":
        text = textmsgs.GUIDE_BUTTONS
        keyboard = back_to_guides()

    elif data == "guide_types":
        text = textmsgs.GUIDE_TYPES
        keyboard = back_to_guides()

    elif data == "guide_content":
        text = textmsgs.GUIDE_CONTENT
        keyboard = back_to_guides()

    elif data == "guide_size":
        text = textmsgs.GUIDE_SIZE
        keyboard = back_to_guides()

    elif data == "guide_sticker":
        text = textmsgs.GUIDE_STICKER
        keyboard = back_to_guides()

    elif data == "guide_forward_tag":
        text = textmsgs.GUIDE_FORWARD_TAG
        keyboard = back_to_guides()

    elif data == "guide_delay":
        text = textmsgs.GUIDE_DELAY
        keyboard = back_to_guides()

    elif data == "guide_antidupe":
        text = textmsgs.GUIDE_ANTIDUPE
        keyboard = back_to_guides()

    elif data == "guide_pause":
        text = textmsgs.GUIDE_PAUSE
        keyboard = back_to_guides()

    elif data == "guide_quota":
        text = textmsgs.GUIDE_QUOTA
        keyboard = back_to_guides()

    elif data == "guide_stats":
        text = textmsgs.GUIDE_STATS
        keyboard = back_to_guides()

    elif data == "guide_general":
        text = textmsgs.GUIDE_GENERAL
        keyboard = back_to_guides()

    elif data == "guide_admin":
        text = textmsgs.GUIDE_ADMIN
        keyboard = back_to_guides()

    else:
        return await callback.answer()

    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        # MessageNotModified, message too long, etc. — fall back to a new reply
        try:
            await callback.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            try:
                await callback.answer(
                    f"Could not open menu: {type(e).__name__}",
                    show_alert=True,
                )
                return
            except Exception:
                return

    await callback.answer()
