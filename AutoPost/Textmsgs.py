class textmsgs(object):
    START_MSG = """
👋 <b>Hello {}! Welcome to Auto Forward Bot</b>

I am a <b>Telegram Auto Forward Bot</b> built to Automate forwarding between Telegram channels and groups with powerful filters, message customization, and advanced routing options.

<blockquote><b><i>I created this bot for personal use, but you all are welcome to use it as well!</i></b></blockquote>

<blockquote expandable>🚀 <b>First-time setup (required before creating rules):</b>
1️⃣ Connect a forwarder — pick <b>one</b>:
   • 🤖 <code>/addbot</code> — connect your own bot (recommended)
   • 👤 <code>/connect</code> — connect your Telegram account
2️⃣ Add that bot/account as <b>Admin</b> in both <b>Source</b> and <b>Target</b> chats (post permission on target).
3️⃣ Create a rule: <code>/addrule &lt;source&gt; &lt;target&gt;</code>

💡 <b>Quick Navigation:</b>
▫️ Use /feature to see what I can do.
▫️ Use /help to view the full command list.
▫️ Use /format to check how to use each command.
▫️ Daily forward limit for normal users: <b>{} msgs</b></blockquote>

<b>@KynozeFun</b>
"""


    FORMAT_MSG = """
📖 <b>Command Usage Guide</b>

<blockquote expandable><b>Setup first:</b>
1. <code>/addbot</code> or <code>/connect</code>
2. Add that bot/account as Admin in Source + Target
3. You must be Admin of Target
Tip: forward a channel msg here to get Chat ID.</blockquote>

<b>General</b>
<code>/quota</code> · <code>/id</code>

<b>Rules</b>
<code>/addrule src tgt</code>
<code>/delrule src tgt</code> · <code>/deletemyrules</code>
<code>/listrules</code> · <code>/myrules</code>
<code>/rule src tgt</code>
<code>/enable src tgt</code> · <code>/disable src tgt</code>

<b>Caption</b>
<code>/setcaption src tgt start|end|end_with_gap text</code>
<code>/removecaption src tgt</code>
<code>/removeoldcaption src tgt on|off</code>
<code>/set_caption src tgt</code> (then send HTML template with {caption})
<code>/clear_caption src tgt</code>
⚠️ /setcaption and /set_caption cannot be used together.

<b>Filters</b>
<code>/removelinks src tgt on|off</code>
<code>/addreplace src tgt old | new</code> [| regex]
<code>/delreplace</code> · <code>/listreplace</code> · <code>/clearreplace</code>
<code>/addblock src tgt w1,w2</code> · <code>/delblock</code> · <code>/listblock</code> · <code>/clearblock</code>
<code>/addwhitelist src tgt w1,w2</code> · <code>/delwhitelist</code> · <code>/listwhitelist</code> · <code>/clearwhitelist</code>

<b>Other</b>
<code>/setbuttons src tgt</code> · <code>/clearbuttons src tgt</code>
<code>/forwardtag src tgt on|off</code>
<code>/settypes src tgt all|photo,video,...</code>
<code>/setdelay src tgt seconds</code>
<code>/antidupe src tgt on|off</code>
<code>/contenttype src tgt all|movies|series|both</code>
<code>/sizefilter src tgt</code> · <code>/minsize src tgt 100 MB</code>
<code>/sticker src tgt</code> · <code>/addsticker src tgt</code>

<b>Owner / Admin</b>
<code>/broadcast</code> · <code>/dbstats</code> · <code>/stats</code>
<code>/addadmin id</code> · <code>/removeadmin id</code> · <code>/admins</code>
<code>/adminonly on|off</code>
<code>/deleteallrules confirm</code> · <code>/wipedb confirm</code>
"""

    HELP_MSG = """
📖 <b>Bot Commands Directory</b>
<i>Use /format for full syntax. Setup: /addbot or /connect first.</i>

<blockquote expandable><b>Setup</b>
/addbot — connect your forwarding bot
/connect — connect your Telegram account
/mybot · /account — status</blockquote>

<blockquote expandable><b>General</b>
/help · /quota · /id</blockquote>

<blockquote expandable><b>Rules</b>
/addrule — create rule (needs /addbot or /connect)
/delrule · /deletemyrules
/listrules · /myrules · /rule
/enable · /disable</blockquote>

<blockquote expandable><b>Caption</b>
/setcaption — add text at start/end (HTML ok)
/removecaption · /removeoldcaption
/set_caption — full HTML template with {caption}
/clear_caption
⚠️ /setcaption and /set_caption cannot both be active</blockquote>

<blockquote expandable><b>Filters</b>
/removelinks — strip links
/addreplace · /delreplace · /listreplace · /clearreplace
/addblock · /delblock · /listblock · /clearblock
/addwhitelist · /delwhitelist · /listwhitelist · /clearwhitelist</blockquote>

<blockquote expandable><b>Other</b>
/setbuttons · /clearbuttons
/forwardtag · /settypes · /setdelay · /antidupe
/contenttype — all / movies / series / movies+series
/sizefilter · /minsize — skip files below min size
/sticker · /addsticker — send sticker when a movie/series group completes</blockquote>

<blockquote expandable><b>Owner / Admin</b>
/broadcast · /dbstats · /stats
/addadmin · /removeadmin · /admins
/adminonly on|off — restrict bot to Owner/Admins only
/deleteallrules · /wipedb</blockquote>
"""

    FEATURE_MSG = """
⚡ <b>Core Routing Matrix:</b>
• <b>One-to-Many:</b> Forward from 1 Source to multiple Target channels.
• <b>Many-to-One:</b> Merge updates from multiple Sources into 1 Target.
• <b>Multi-to-Multi:</b> Configure completely independent rules for every single routing path.

⚡ <b>Features Built-In:</b>
<blockquote expandable>⚡ <i>Simple Caption</i> (start / end / gap + HTML support)
⚡ <i>Custom Caption Template</i> (full HTML layout with {caption} placeholder)
⚡ <i>Text & Regex Replacement</i> (modify content on the fly)
⚡ <i>Block Words Filter</i> (skip messages with unwanted text)
⚡ <i>Whitelist Message Filter</i> (forward only specific matches)
⚡ <i>Automatic Link Removal</i> (clean URLs instantly)
⚡ <i>Custom Inline URL Buttons</i> (append your own branding/links)
⚡ <i>Media Type Filtering</i> (allow only photos, videos, docs, etc.)
⚡ <i>Forward Tag ON/OFF</i> (anonymize or credit the source)
⚡ <i>Forward Delay Control</i> (set smart delays to prevent FloodWait)
⚡ <i>Duplicate Message Protection</i> (avoid spamming target chats)
⚡ <i>Pause & Resume Rules</i> (turn rules ON/OFF with 1 click)
⚡ <i>Detailed Rule Statistics</i> (monitor full channel analytics)
⚡ <i>Album / Media Group Support</i> (keep photo groups intact)
⚡ <i>Movie / Series Filter</i> (all · movies only · series only · movies + series)
⚡ <i>Media Size Filter</i> (skip files smaller than a minimum MB/GB)
⚡ <i>Completion Sticker</i> (send a sticker when a movie/series group finishes)</blockquote>

⚠️ <b>Quick Note:</b> Make sure to add this bot as an <b>Admin</b> in both your source and target chats with message posting rights to allow smooth forwarding.
"""

    # ------------------------------------------------------------------
    # DETAILED GUIDES
    # ------------------------------------------------------------------

    GUIDE_RULES = """
📋 <b>Add / Manage Rules</b>

<i>A rule connects one Source chat to one Target chat.</i>

<code>/addrule -100(SourceID) -100(TargetID)</code>
<i>Create a new rule. You and the bot must both be Admin in source and target.</i>

<code>/delrule -100(SourceID) -100(TargetID)</code>
<i>Delete the rule.</i>

<code>/deletemyrules</code>
<i>Delete all your forwarding rules at once.</i>

<code>/listrules</code>
<code>/myrules</code>
<i>Show all your rules.</i>

<code>/rule -100(SourceID) -100(TargetID)</code>
<i>Show full settings of one rule.</i>

<code>/enable -100(SourceID) -100(TargetID)</code>
<i>Turn forwarding ON.</i>

<code>/disable -100(SourceID) -100(TargetID)</code>
<i>Turn forwarding OFF (settings kept).</i>
"""

    GUIDE_SIMPLE_CAPTION = """
✏️ <b>Simple Caption</b>

<i>Adds extra text before or after the original caption. Original formatting is kept. Extra text supports HTML.</i>

<code>/setcaption -100(SourceID) -100(TargetID) end Join Channel</code>
<i>Add text at the end.</i>

<code>/setcaption -100(SourceID) -100(TargetID) start New Upload</code>
<i>Add text at the start.</i>

<code>/setcaption -100(SourceID) -100(TargetID) end_with_gap Thanks</code>
<i>Add text at the end with extra gap.</i>

<i>Position: start | end | end_with_gap</i>

<code>/setcaption -100(SourceID) -100(TargetID) end &lt;b&gt;Join Channel&lt;/b&gt;</code>
<i>HTML example.</i>

<code>/removecaption -100(SourceID) -100(TargetID)</code>
<i>Remove the extra caption.</i>

<code>/removeoldcaption -100(SourceID) -100(TargetID) on</code>
<i>Delete original caption (keep only yours).</i>

<code>/removeoldcaption -100(SourceID) -100(TargetID) off</code>
<i>Keep original caption.</i>

<i>Supported HTML: &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;s&gt; &lt;spoiler&gt; &lt;code&gt; &lt;pre&gt; &lt;a href&gt; &lt;blockquote&gt;</i>

⚠️ <i>This clears any custom template if set.</i>
"""

    GUIDE_CUSTOM_CAPTION = """
🎨 <b>Custom Caption Template</b>

<i>Full control over the final layout. Use {caption} where original text should appear.</i>

<code>/set_caption -100(SourceID) -100(TargetID)</code>
<i>Bot will ask you to send an HTML template.</i>

<code>&lt;b&gt;🎬 New Upload&lt;/b&gt;

📦 &lt;code&gt;{caption}&lt;/code&gt;

&lt;a href="https://t.me/channel"&gt;Join&lt;/a&gt;</code>
<i>Reply with a template like this.</i>

<code>/clear_caption -100(SourceID) -100(TargetID)</code>
<i>Remove the template.</i>

<i>Also works: /setcustomcaption · /clearcustomcaption</i>

<i>Supported HTML: &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;s&gt; &lt;spoiler&gt; &lt;code&gt; &lt;pre&gt; &lt;a href&gt; &lt;blockquote&gt;</i>

⚠️ <i>This clears simple /setcaption if set.</i>
"""

    GUIDE_REPLACE = """
🔄 <b>Text &amp; Regex Replacement</b>

<i>Automatically replace words or links in forwarded messages.</i>

<code>/addreplace -100(SourceID) -100(TargetID) t.me/old | t.me/new</code>
<i>Plain whole-word replace.</i>

<code>/addreplace -100(SourceID) -100(TargetID) @old | @new</code>
<i>Replace a username.</i>

<code>/addreplace -100(SourceID) -100(TargetID) https?://t\\.me/\\S+ | https://t.me/ch | regex</code>
<i>Regex replace (any t.me link).</i>

<code>/delreplace -100(SourceID) -100(TargetID) t.me/old</code>
<i>Remove one replacement.</i>

<code>/listreplace -100(SourceID) -100(TargetID)</code>
<i>List all replacements.</i>

<code>/clearreplace -100(SourceID) -100(TargetID)</code>
<i>Clear all replacements.</i>
"""

    GUIDE_BLOCK = """
🚫 <b>Block Words</b>

<i>If a message contains any blocked word, it is not forwarded.</i>

<code>/addblock -100(SourceID) -100(TargetID) spam,scam,@baduser</code>
<i>Add multiple words (comma separated).</i>

<code>/delblock -100(SourceID) -100(TargetID) spam</code>
<i>Remove one word.</i>

<code>/listblock -100(SourceID) -100(TargetID)</code>
<i>Show blocked words.</i>

<code>/clearblock -100(SourceID) -100(TargetID)</code>
<i>Clear all blocked words.</i>
"""

    GUIDE_WHITELIST = """
✅ <b>Whitelist Filters</b>

<i>Empty whitelist → all messages allowed. With words → only messages containing at least one word are forwarded.</i>

<code>/addwhitelist -100(SourceID) -100(TargetID) movie,series,episode</code>
<i>Add whitelist words.</i>

<code>/delwhitelist -100(SourceID) -100(TargetID) movie</code>
<i>Remove one word.</i>

<code>/listwhitelist -100(SourceID) -100(TargetID)</code>
<i>Show whitelist.</i>

<code>/clearwhitelist -100(SourceID) -100(TargetID)</code>
<i>Clear whitelist (allow all again).</i>
"""

    GUIDE_LINKS = """
🔗 <b>Automatic Link Removal</b>

<code>/removelinks -100(SourceID) -100(TargetID) on</code>
<i>Remove links before forwarding.</i>

<code>/removelinks -100(SourceID) -100(TargetID) off</code>
<i>Keep links.</i>
"""

    GUIDE_BUTTONS = """
🔘 <b>Custom Inline URL Buttons</b>

<code>/setbuttons -100(SourceID) -100(TargetID)</code>
<i>Bot will ask you to send the button layout.</i>

<code>Join - https://t.me/channel | Web - https://example.com
Support - https://t.me/support</code>
<i>Reply format. | = same row · New line = new row.</i>

<code>/clearbuttons -100(SourceID) -100(TargetID)</code>
<i>Remove all buttons.</i>
"""

    GUIDE_TYPES = """
📂 <b>Media Type Filtering</b>

<code>/settypes -100(SourceID) -100(TargetID) all</code>
<i>Allow everything.</i>

<code>/settypes -100(SourceID) -100(TargetID) photo,video</code>
<i>Only photos and videos.</i>

<code>/settypes -100(SourceID) -100(TargetID) document,text</code>
<i>Only documents and text.</i>

<i>Allowed: all photo video document sticker animation audio voice text poll contact location venue</i>
"""

    GUIDE_CONTENT = """
🎬 <b>Movie / Series Filter</b>

<code>/contenttype -100(SourceID) -100(TargetID)</code>
<i>Open the picker.</i>

<code>/contenttype src tgt all</code> — everything
<code>/contenttype src tgt movies</code> — movies only
<code>/contenttype src tgt series</code> — series only
<code>/contenttype src tgt both</code> — movies + series (skip unknown)

<i>Uses caption + filename. S01E02 / Season 2 → series. Title 2024 1080p → movie. Unknown is skipped unless All is selected.</i>
"""

    GUIDE_SIZE = """
📏 <b>Media Size Filter</b>

<code>/sizefilter -100(SourceID) -100(TargetID)</code>
<i>Open presets (10 MB … 10 GB) and ON/OFF.</i>

<code>/minsize src tgt 500 MB</code>
<code>/minsize src tgt 1.5 GB</code>
<code>/sizefilter src tgt on|off</code>

<i>Files smaller than the minimum are skipped. Exact match is allowed. Text / unknown size is not bulk-skipped. OFF keeps the saved minimum.</i>
"""

    GUIDE_STICKER = """
🎟️ <b>Completion Sticker</b>

<code>/sticker -100(SourceID) -100(TargetID)</code>
<i>Open the menu.</i>

<code>/sticker src tgt on</code>
<code>/addsticker src tgt</code> — then send a sticker
<code>/liststickers src tgt</code>
<code>/clearstickers src tgt</code>

<i>When a movie/series title group finishes forwarding, one random saved sticker is sent to the target. Quality variants of the same title count as one group. A later run of the same title gets a new sticker.</i>
"""

    GUIDE_FORWARD_TAG = """
🏷 <b>Forward Tag ON/OFF</b>

<code>/forwardtag -100(SourceID) -100(TargetID) on</code>
<i>Pure Telegram forward (shows “Forwarded from”). No caption editing or buttons.</i>

<code>/forwardtag -100(SourceID) -100(TargetID) off</code>
<i>Full control — caption, buttons, filters all work.</i>
"""

    GUIDE_DELAY = """
⏱ <b>Delay Control</b>

<i>Helps avoid FloodWait errors.</i>

<code>/setdelay -100(SourceID) -100(TargetID) 3</code>
<i>Wait 3 seconds before each forward.</i>

<code>/setdelay -100(SourceID) -100(TargetID) 0</code>
<i>No delay.</i>
"""

    GUIDE_ANTIDUPE = """
🛡 <b>Duplicate Message Protection</b>

<i>Skips messages already sent to the same target.</i>

<code>/antidupe -100(SourceID) -100(TargetID) on</code>
<i>Enable protection.</i>

<code>/antidupe -100(SourceID) -100(TargetID) off</code>
<i>Disable protection.</i>
"""

    GUIDE_PAUSE = """
⏸ <b>Pause &amp; Resume Rules</b>

<code>/enable -100(SourceID) -100(TargetID)</code>
<i>Resume forwarding.</i>

<code>/disable -100(SourceID) -100(TargetID)</code>
<i>Pause forwarding (settings kept).</i>

<code>/rule -100(SourceID) -100(TargetID)</code>
<i>View status and all settings.</i>
"""

    GUIDE_QUOTA = """
📊 <b>Daily Quota</b>

<code>/quota</code>
<i>Shows today’s successful forwards and remaining limit. Normal users have a daily limit. Bot Owner / Admins have unlimited forwards.</i>
"""

    GUIDE_STATS = """
📈 <b>Statistics</b>

<code>/rule -100(SourceID) -100(TargetID)</code>
<i>Per-rule counters (forwarded, blocked, failed, duplicates).</i>

<code>/quota</code>
<i>Your daily limit.</i>
"""

    GUIDE_GENERAL = """
ℹ️ <b>General Commands</b>

<code>/start</code>
<i>Main menu.</i>

<code>/help</code>
<i>Command directory.</i>

<code>/format</code>
<i>Quick usage guide.</i>

<code>/feature</code>
<i>Feature list.</i>

<code>/quota</code>
<i>Your daily limit.</i>

<code>/id</code>
<i>Use this command in Channel or Group to get chat id</i>

<b>How to get Chat ID</b>
<i>Forward a message with the forward tag from your channel to this bot to get the Channel ID.</i>
"""

    GUIDE_ADMIN = """
🛡 <b>Bot Administration</b>
<i>Owner / Admin only.</i>

<code>/addadmin 123456789</code>
<i>Promote a user to Bot Admin.</i>

<code>/removeadmin 123456789</code>
<i>Remove a Bot Admin.</i>

<code>/admins</code>
<i>List all Owners and Admins.</i>

<code>/adminonly on</code>
<code>/adminonly off</code>
<i>Restrict the bot to Owner &amp; Admins only.
When ON, normal users get <b>no reply</b> to any command.
When OFF, everyone can use the bot.
Only the Bot Owner can toggle this.</i>

<code>/broadcast</code>
<i>Message all registered users.</i>

<code>/deleteallrules confirm</code>
<i>Delete every rule of every user.</i>

<code>/wipedb confirm</code>
<i>Wipe the entire database.</i>

<code>/stats</code>
<i>Overall bot stats (Admin only).</i>

<code>/dbstats</code>
<i>Database size (Admin only).</i>
"""
