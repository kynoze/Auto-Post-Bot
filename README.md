# Auto Post Bot

A feature-rich Telegram automation bot for **channel-to-channel / group-to-channel forwarding**, with per-route rules, media filters, caption customization, duplicate protection, and optional forwarding through either a dedicated user bot or a connected Telegram account.

> Built with **Kurigram**, **PyMongo**, **MongoDB**, and Telegram's Bot API / MTProto clients.

## ✨ Features

### 🔀 Flexible Routing

- **One-to-Many:** one source → multiple targets
- **Many-to-One:** multiple sources → one target
- **Multi-to-Multi:** independent rules for every source/target pair
- Enable or disable any rule without deleting its settings
- Forward through either:
  - 🤖 **User Bot** — your own connected Telegram bot
  - 👤 **User Account** — your connected Telegram account

### 📝 Caption & Message Customization

- Add a caption at the **start**, **end**, or **end with an extra gap**
- Replace the original caption with a **custom HTML template**
- Use `{caption}` to insert the original caption/text into a template
- Remove the original caption
- Automatically remove links from forwarded text
- Preserve supported Telegram formatting when no transformation is required

### 🔎 Advanced Filters

- **Block words** — skip messages containing unwanted words
- **Whitelist words** — forward only matching messages
- **Plain-text replacements**
- **Regex replacements**
- **Media type filtering**
- **Movie / Series filtering**
- **Minimum media-size filtering**
- **Anti-duplicate protection**

### 🎬 Movie / Series Detection

The bot uses `parsett==1.8.5` (`PTT.parse_title`) as the primary classifier, with regex fallback patterns when needed.

Available modes:

- `all`
- `movies`
- `series`
- `movies + series`

Unrecognized content is **not** automatically treated as a movie or series when a specific movie/series filter is enabled.

### 📦 Media Size Filter

Skip media below a configured minimum size.

Built-in size presets include:

`10 MB`, `30 MB`, `50 MB`, `100 MB`, `200 MB`, `500 MB`, `1 GB`, `1.5 GB`, `2 GB`, `4 GB`, `5 GB`, `10 GB`

Custom sizes are supported as well.

### 🎟️ Completion Sticker

For movie/series batches, the bot can send a randomly selected configured sticker after the whole group completes.

- Add multiple stickers per rule
- Enable/disable the feature without deleting saved stickers
- Quality variants of the same title are treated as one logical group

### 🔘 Inline Buttons

Attach custom URL buttons to forwarded messages.

Example:

```text
Join Channel - https://t.me/example
Website - https://example.com
```

Use `|` to place multiple buttons in the same row:

```text
Join Channel - https://t.me/example | Website - https://example.com
```

### 🌐 Global Copy

An optional **Global Copy** mode uses a connected Telegram account to copy incoming messages from the account's chats into **one target chat**.

Supported source types include messages arriving through groups, channels, private chats, and bots.

Global Copy supports filters such as:

- Media-type selection
- Block words
- Whitelist words
- Text replacement
- Anti-duplicate mode
- Delay control
- Caption/link/button settings shared by the global-copy filter system

### 📊 Statistics & Quotas

- Per-rule forwarded / blocked / failed / duplicate-skipped counters
- Bot-wide database statistics for owners
- Daily forwarding quota for normal users
- `/quota` command to view the current quota

### 🛡️ Access & Security

- Owner/admin management
- Optional **admin-only mode**
- Rule-management authorization checks
- Telegram user sessions and bot tokens are stored encrypted using **Fernet**
- Optional per-user external MongoDB database for persistent anti-duplicate history

---

## 🧰 Tech Stack

| Component | Version / Technology |
|---|---|
| Python | **3.13.9** (project `.python-version`) |
| Telegram framework | **Kurigram 2.2.24** |
| Telegram crypto | `tgcrypto-pyrofork` |
| Database | **MongoDB** via PyMongo |
| PyMongo | **4.17.0** |
| Title parser | **parsett 1.8.5** |
| Web server | Flask + Gunicorn |
| HTTP client | aiohttp |
| Secret encryption | cryptography / Fernet |

> Keep the dependency versions aligned with `requirements.txt` unless you have tested newer versions with this codebase.

---

## 📁 Project Structure

```text
.
├── AutoPost/
│   ├── Database/
│   │   └── database.py          # MongoDB layer, rules, stats, sessions, tokens
│   ├── helper/
│   │   ├── command_helpers.py   # Shared command / authorization helpers
│   │   ├── completion_sticker.py# Movie/series completion tracking
│   │   ├── content_type.py      # Movie/series detection and filtering
│   │   ├── media_size.py        # Minimum media-size filtering
│   │   ├── user_bots.py         # Connected bot manager
│   │   └── user_clients.py      # Telegram account/session manager
│   ├── plugins/
│   │   ├── commands.py          # Main bot commands and rule management
│   │   ├── post.py              # Forwarding / filtering pipeline
│   │   ├── job_filters.py       # Shared content/size/sticker controls
│   │   ├── add_bot.py           # Connect a user bot
│   │   ├── broadcast.py         # Owner broadcast tools
│   │   ├── get_channel_id.py    # Chat ID helper
│   │   ├── join_required.py     # Force-subscription checks
│   │   └── userbot/
│   │       ├── add_user.py      # Connect Telegram account
│   │       ├── account.py       # Session management
│   │       └── global_copy.py   # Global Copy mode
│   ├── bot.py                   # Kurigram client bootstrap
│   └── __main__.py              # Application entry point
├── app.py                       # Flask health endpoint
├── Procfile                     # Deployment process definition
├── requirements.txt
└── start_web_cmd.txt
```

---

## 🚀 Setup

### 1. Clone the repository

```bash
git clone https://github.com/kynoze/CNL-Auto-Post-Bot.git
cd CNL-Auto-Post-Bot
```

### 2. Create a virtual environment

```bash
python3.13 -m venv venv
source venv/bin/activate
```

On Windows:

```powershell
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create a MongoDB database

Create a MongoDB database (MongoDB Atlas works well) and keep the connection URI ready.

Example:

```text
mongodb+srv://USERNAME:PASSWORD@cluster.example.mongodb.net/
```

### 5. Generate an encryption key

The bot requires `SESSION_ENCRYPTION_KEY` to encrypt Telegram sessions and stored bot tokens.

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the generated value securely.

### 6. Configure environment variables

Create a `.env` file (the repository's `.gitignore` already excludes `.env`) or set the variables in your hosting platform.

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:your_bot_token
ADMINS=123456789 987654321
DB_URL=mongodb+srv://USERNAME:PASSWORD@cluster.example.mongodb.net/
SESSION_ENCRYPTION_KEY=your_fernet_key
DAILY_FORWARD_LIMIT=2000
```

#### Variable reference

| Variable | Required | Description |
|---|---|---|
| `API_ID` | Yes | Telegram API ID |
| `API_HASH` | Yes | Telegram API hash |
| `BOT_TOKEN` | Yes | Main Telegram bot token |
| `ADMINS` | Recommended | Space-separated Telegram user IDs with bot-admin access |
| `DB_URL` | Yes | MongoDB connection URI |
| `SESSION_ENCRYPTION_KEY` | Yes | Fernet key used to encrypt sessions/tokens |
| `DAILY_FORWARD_LIMIT` | No | Daily forwarding limit for normal users; defaults to `2000` |

> Never commit `BOT_TOKEN`, `API_HASH`, MongoDB credentials, Telegram session strings, or `SESSION_ENCRYPTION_KEY` to Git.

---

## ▶️ Run the Bot

### Local

```bash
python3 -m AutoPost
```

### Web/health process

The included Flask app exposes a very small health endpoint:

```text
/
```

It returns `AutoPost` when the Flask process is running.

Run both processes with:

```bash
gunicorn app:app & python3 -m AutoPost
```

The same startup command is included in `cmd.txt` and `start_web_cmd.txt`.

### Procfile deployments

The included `Procfile` uses:

```text
worker: python3 -m AutoPost
```

Configure your hosting platform to run the worker and provide the environment variables listed above.

---

## ⚙️ First-Time Telegram Setup

Before creating a forwarding rule, connect **one** forwarding identity:

### Option A — Connect your own bot

```text
/addbot
```

The connected bot must have the required access in the source and target chats.

### Option B — Connect your Telegram account

```text
/connect
```

The bot walks through Telegram login, including 2-step verification when enabled. The resulting session is encrypted before it is saved.

### Create a rule

```text
/addrule <source_chat> <target_chat>
```

Example:

```text
/addrule @sourcechannel -1001234567890
```

Requirements enforced by the bot include:

- the selected forwarding identity must be connected;
- the forwarding identity must have suitable access to the source/target;
- you must be an admin/owner of the target chat.

When both a user bot and user account are connected, the bot lets you choose which identity should handle the rule.

---

## 📖 Command Reference

### General

```text
/start
/help
/commands
/feature
/format
/quota
/id
```

### Rule management

```text
/addrule <source> <target>
/delrule <source> <target>
/delallrules confirm
/deletemyrules
/listrules
/myrules
/rule <source> <target>
/settings <source> <target>
/enable <source> <target>
/disable <source> <target>
```

A normal user can create up to **10 rules**, with up to **10 targets per source**. Owner/admin accounts are handled separately by the database authorization layer.

### Caption controls

Simple caption:

```text
/setcaption <source> <target> start|end|end_with_gap <text>
/removecaption <source> <target>
```

Remove/keep original caption:

```text
/removeoldcaption <source> <target> on|off
```

Custom HTML template:

```text
/set_caption <source> <target>
```

Then send a template such as:

```html
<b>🎬 New Upload</b>

{caption}

<a href="https://t.me/example">Join Channel</a>
```

Clear it with:

```text
/clear_caption <source> <target>
```

Aliases are also available for the custom-caption commands.

> Simple `/setcaption` and the custom `/set_caption` template are mutually exclusive; setting one clears the other.

### Text filters

Remove links:

```text
/removelinks <source> <target> on|off
```

Add replacement:

```text
/addreplace <source> <target> old text | new text
/addreplace <source> <target> old text | new text | regex
```

Manage replacements:

```text
/delreplace <source> <target> old text
/listreplace <source> <target>
/clearreplace <source> <target>
```

Block words:

```text
/addblock <source> <target> word1,word2,word3
/delblock <source> <target> word
/listblock <source> <target>
/clearblock <source> <target>
```

Whitelist words:

```text
/addwhitelist <source> <target> word1,word2,word3
/delwhitelist <source> <target> word
/listwhitelist <source> <target>
/clearwhitelist <source> <target>
```

### Buttons & forwarding behavior

```text
/setbuttons <source> <target>
/clearbuttons <source> <target>
/forwardtag <source> <target> on|off
/settypes <source> <target> ...
/setdelay <source> <target> seconds
/antidupe <source> <target> on|off
```

Supported media types include:

```text
all
photo
video
document
sticker
animation
audio
voice
text
poll
contact
location
venue
```

### Content-type & media-size filtering

Movie/series filter:

```text
/contenttype <source> <target> all
/contenttype <source> <target> movies
/contenttype <source> <target> series
/contenttype <source> <target> both
```

Minimum media size:

```text
/sizefilter <source> <target>
/minsize <source> <target> 100 MB
```

### Completion stickers

```text
/sticker <source> <target>
/addsticker <source> <target>
/liststickers <source> <target>
/clearstickers <source> <target>
```

### Connected user bot

```text
/addbot
/mybot
/botinfo
/removebot
/delbot
/unlinkbot
```

### Connected Telegram account

```text
/connect
/account
/myaccount
/session
/disconnect
/logout
/unlink
/reconnect
/restartsession
```

### Global Copy

Enable or inspect Global Copy:

```text
/globalcopy <target_chat>
/globalcopy off
/gstatus
/gcopyinfo
```

Global Copy filtering:

```text
/gblock <word>
/gunblock <word>
/gwhite <word>
/gunwhite <word>
/greplace old_text ||| new_text
/gunreplace old_text
/gantidupe on|off
/gdelay <seconds>
```

Optional external duplicate database:

```text
/setdupedb <mongodb_uri>
/dupedb
/dupeinfo
/cleardupe
/removedupedb
/unlinkdupedb
```

> Treat a custom duplicate-database URI like a password. Do not publish it in source code, logs, screenshots, or chat.

### Owner / admin tools

```text
/broadcast
/stats
/dbstats
/addadmin <user_id>
/removeadmin <user_id>
/admins
/adminonly on|off
/deleteallrules confirm
/wipedb confirm
```

---

## 🧠 How Forwarding Works

For each configured source → target rule, the forwarding pipeline roughly follows this order:

```text
Incoming message
      │
      ├─ Rule enabled?
      │
      ├─ Content-type filter
      │
      ├─ Movie/Series filter
      │
      ├─ Media-size filter
      │
      ├─ Block / whitelist checks
      │
      ├─ Anti-duplicate check
      │
      ├─ Caption / link / replacement processing
      │
      ├─ Inline button generation
      │
      ├─ Optional delay
      │
      └─ Send / forward to target
                │
                └─ Completion-sticker tracking for movie/series groups
```

The implementation also supports albums/media groups and keeps forwarding sequential where the code requires ordering for grouped content.

---

## 🔐 Security Notes

1. **Use a strong `SESSION_ENCRYPTION_KEY`.** Losing it can make previously encrypted sessions/tokens undecryptable.
2. **Do not expose Telegram credentials.** Keep `BOT_TOKEN`, `API_HASH`, MongoDB URIs, and session secrets private.
3. **Run the bot with only the Telegram permissions it needs.**
4. **Review target permissions carefully.** A connected bot/account may be able to post into every target for which you create a rule.
5. **Back up MongoDB securely.** The database contains bot configuration and encrypted secrets.

---

## 🧪 Development

Useful checks before committing changes:

```bash
python --version
python -m compileall AutoPost
pip install -r requirements.txt
```

The repository also contains a GitHub Actions workflow under:

```text
.github/workflows/python-package.ym
```

---

## 📜 License

This project includes the **GNU General Public License v3.0 (GPL-3.0)** in `LICENSE`.

See the full license text in the repository before redistributing modified versions.

---

## 👨‍💻 Credits

Created by **Kynoze / @Kynoze**.

Repository:

https://github.com/kynoze/Auto-Post-Bot

---

## ⚠️ Disclaimer

This project is provided for Telegram automation and administration use. You are responsible for complying with Telegram's Terms of Service, applicable laws, and the rules of the chats/channels you operate or forward content between.
