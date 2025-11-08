import json
import os
import io
import re
import logging
import aiohttp
import sys
import random
from datetime import timedelta, datetime, timezone
from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import asyncio
from aiohttp import web
from gist_sync import load_all_files, save_json_dict


# =====================
# Load Environment & Logging
# =====================
load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# =====================
# Config
# =====================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
VAULT_CHANNEL_ID = int(os.getenv("VAULT_CHANNEL_ID"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")  
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Example: https://your-render-app.onrender.com


GIST_ENABLED = bool(os.getenv("GIST_ID") and os.getenv("GITHUB_TOKEN"))

if not TOKEN or not WEBHOOK_URL:
    logging.error("❌ Missing BOT_TOKEN or WEBHOOK_URL in environment variables.")
    sys.exit(1)

DATA_FILE = "files.json"
ALIAS_FILE = "aliases.json"


# Track last activity & sent messages clean up

LAST_ACTIVITY = datetime.now(timezone.utc)
SENT_MESSAGES = []  # Track all messages for cleanup

# dummy messages and render warmup activity
STARTUP_TIME = datetime.now(timezone.utc)
RENDER_WARMED = False  # 🔥 Flag to avoid dummy progress once warmed




# =====================
# Helpers
# =====================
def load_json(path):
    # prefer local file if present (so dev/test still easy)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # fallback to gist if enabled
    if GIST_ENABLED:
        all_files = load_all_files()
        filename = os.path.basename(path)
        content = all_files.get(filename)
        if content:
            try:
                return json.loads(content)
            except Exception:
                return {}
    return {}


def save_json(path, data):
    # save local copy for convenience
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    # update gist if enabled
    if GIST_ENABLED:
        filename = os.path.basename(path)
        ok = save_json_dict(filename, data)
        if not ok:
            logging.warning(f"Failed to save {filename} to gist.")


def remove_emojis(text):
    """Remove emojis and unwanted Unicode symbols."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002700-\U000027BF"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()

def update_activity():
    """Update last activity timestamp."""
    global LAST_ACTIVITY
    LAST_ACTIVITY = datetime.now(timezone.utc)


# =====================
# Smart Dummy Progress
# =====================

async def smart_progress(update, stop_event: asyncio.Event, mode: str = "token"):
    """Show dynamic progress based on mode until Render wakes up."""
    if mode == "token":
        msgs = [
        "👋 Hey there! Warming up the system...",
        "⚙ Getting everything ready for you...",
        "📂 Preparing your secure file vault...",
        "🔍 Checking access token validity...",
        "🚀 Almost done, just a few seconds more..."
    ]
    elif mode == "start":
        msgs = [
            "🤖 Checking if you're a bot...",
            "😄 Haha sorry, I’m the bot!",
            "🔎 Verifying system readiness...",
            "🚀 Getting things ready for you..."
        ]
    else:  # mode == "random"
        msgs = [
            "👀 Thanks for your message...",
            "🗂 Searching command database...",
            "📡 Looking for valid responses...",
            "🤔 Hmm... almost there..."
        ]

    sent = []
    for text in msgs:
        if stop_event.is_set():
            break
        m = await update.message.reply_text(text)
        sent.append(m)
        await asyncio.sleep(random.uniform(1.5, 3.2))

    if not stop_event.is_set():
        final = await update.message.reply_text("✨ System ready — finishing up...")
        sent.append(final)
        await asyncio.sleep(1.5)

    for m in sent:
        try:
            await m.delete()
        except Exception:
            pass



# =====================
# Core Commands
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RENDER_WARMED
    update_activity()
    args = context.args
    user_id = update.effective_user.id
    stop_event = asyncio.Event()

    # Detect cold start
    time_since_start = (datetime.now(timezone.utc) - STARTUP_TIME).total_seconds()
    cold_start = not RENDER_WARMED and time_since_start < 90

    # CASE 2: plain /start (no token)
    if not args:
        if cold_start:
            asyncio.create_task(smart_progress(update, stop_event, mode="start"))

        await asyncio.sleep(0.3)
        stop_event.set()
        RENDER_WARMED = True  # mark warmed up

        msg = await update.message.reply_text(
            "🎌 Welcome to Anime File Downloader!\n\n"
            "Kindly use secure links from our official channel to access your files.\n"
            f"👉 <a href='https://t.me/{CHANNEL_USERNAME}'>Join Anime Share Point</a>",
            parse_mode="HTML"
        )
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        return

    # CASE 1: token provided
    key = " ".join(args).strip()
    if len(key) >= 10 and " " not in key:
        wait_msg = await update.message.reply_text("⏳ Preparing your download session...")
        SENT_MESSAGES.append((wait_msg.chat_id, wait_msg.message_id))

        if cold_start:
            asyncio.create_task(smart_progress(update, stop_event, mode="token"))

        verify_url = f"https://mkcycles.pythonanywhere.com/tokens/verify?token={key}&user_id={user_id}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(verify_url, timeout=8) as resp:
                    result = await resp.json()
            except Exception as e:
                logging.error(f"Token verification failed: {e}")
                stop_event.set()
                return await wait_msg.edit_text("⚠ Token verification failed. Try again later.")

        stop_event.set()
        await wait_msg.delete()
        RENDER_WARMED = True  # ✅ mark system warmed

        if not result.get("valid"):
            msg = await update.message.reply_text(
                "❌ Invalid or expired token.\n"
                "Please use a valid link from our <b>Official Anime Share Point</b> channel.",
                parse_mode="HTML"
            )
            SENT_MESSAGES.append((msg.chat_id, msg.message_id))
            return

        alias_name = result.get("alias") or result.get("file")
        if not alias_name:
            msg = await update.message.reply_text(
                "⚠ Token verified but no file found. It might have been deleted or moved. 😢"
            )
            SENT_MESSAGES.append((msg.chat_id, msg.message_id))
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{alias_name}")]
        ])
        msg = await update.message.reply_text(
            "📂 Your file is ready!\nPlease join our channel first 👇",
            reply_markup=keyboard
        )
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        return

    # CASE 3: invalid command
    if cold_start:
        asyncio.create_task(smart_progress(update, stop_event, mode="random"))

    await asyncio.sleep(0.3)
    stop_event.set()
    RENDER_WARMED = True

    msg = await update.message.reply_text(
        "😅 Invalid command or request.\n"
        "Please use the download link from our <b>Official Channel</b> to access files.",
        parse_mode="HTML"
    )
    SENT_MESSAGES.append((msg.chat_id, msg.message_id))


# =====================
# File Processing
# =====================
async def process_alias_or_file(update: Update, context: ContextTypes.DEFAULT_TYPE, alias_name: str):
    update_activity()
    data = load_json(DATA_FILE)
    aliases = load_json(ALIAS_FILE)
    sent_count = 0

    # ✅ If alias found
    if alias_name in aliases:
        msg = await update.message.reply_text("📦 Preparing your files... please wait.")
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        await asyncio.sleep(1.5)

        for fname in aliases[alias_name]:
            for name, file_id in data.items():
                if fname.lower() in name.lower():
                    video_msg = await context.bot.send_video(chat_id=update.effective_chat.id, video=file_id)
                    SENT_MESSAGES.append((video_msg.chat_id, video_msg.message_id))
                    sent_count += 1

        if sent_count == 0:
            msg = await update.message.reply_text("❌ No matching files found for this request.")
            SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        else:
            msg = await update.message.reply_text(
                f"✅ Sent {sent_count} files for: <b>{alias_name}</b>\n\n"
                "🕒 Files auto-delete in 10 minutes.",
                parse_mode="HTML"
            )
            SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        return

    # ✅ If single file found
    if alias_name in data:
        file_id = data[alias_name]
        msg = await update.message.reply_text("📦 Fetching your file... please wait.")
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        video_msg = await context.bot.send_video(chat_id=update.effective_chat.id, video=file_id)
        SENT_MESSAGES.append((video_msg.chat_id, video_msg.message_id))
        await msg.delete()

        # Auto delete file after 10 min (optional track)
        SENT_MESSAGES.append((video_msg.chat_id, video_msg.message_id))
        # schedule job (do NOT await run_once — it returns a Job object)
        context.application.job_queue.run_once(
            delete_message,
            when=timedelta(minutes=10),
            data={"chat_id": video_msg.chat_id, "msg_id": video_msg.message_id}
        )

        msg2 = await update.message.reply_text("✅ File sent successfully.")
        SENT_MESSAGES.append((msg2.chat_id, msg2.message_id))
    else:
        msg = await update.message.reply_text("❌ No matching files found for this request.")
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))


# =====================
# Channel Verification (Public Channel)
# =====================
async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    alias_name = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    try:
        # ✅ Check if user is a member of your PUBLIC channel
        member = await context.bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        status = member.status.lower()
    except Exception as e:
        logging.error(f"Error verifying membership for {user_id}: {e}")
        msg = await query.edit_message_text("⚠ Couldn’t verify your channel join. Please try again later.")
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))
        return


    # ✅ If user is already a member or admin
    if status in ["member", "administrator", "creator"]:
        msg = await query.edit_message_text("✅ Channel verified! Fetching your files...")
        SENT_MESSAGES.append((msg.chat_id, msg.message_id))


        # ✅ Proper fake message class
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat_id = chat_id
                self.chat = type("Chat", (), {"id": chat_id})()

            async def reply_text(self, text, **kwargs):
               m = await context.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
               SENT_MESSAGES.append((m.chat_id, m.message_id))
               return m


        # ✅ Create a fake update that mimics a normal user message
        fake_msg = FakeMessage(query.message.chat_id)
        fake_update = Update(update.update_id, message=fake_msg)

        # ✅ Call alias processing normally
        await process_alias_or_file(fake_update, context, alias_name)
        return

    # ❌ Not joined yet
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{alias_name}")]
    ])
    msg = await query.edit_message_text(
        "❌ You must join our public channel first to access files.\n"
        "After joining, click ‘Refresh’ below 👇",
        reply_markup=keyboard
    )
    SENT_MESSAGES.append((msg.chat_id, msg.message_id))

# =====================
# Auto Delete (job_queue)
# =====================

async def delete_message(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["msg_id"])
        SENT_MESSAGES.remove((data["chat_id"], data["msg_id"]))
    except Exception:
        pass


# =====================
# Auto Clean / Restart
# =====================
async def check_inactivity(app: Application):
    """Deletes all messages and restarts bot if inactive for 20 mins."""
    global SENT_MESSAGES
    while True:
        await asyncio.sleep(300)  # check every 5 minutes
        now = datetime.now(timezone.utc)
        diff = (now - LAST_ACTIVITY).total_seconds()

        if diff > 600:  # 10 minutes
            logging.warning("⚠ No activity for 20 minutes. Cleaning up messages & restarting...")

            for chat_id, msg_id in SENT_MESSAGES:
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass

            SENT_MESSAGES.clear()
            logging.info("♻ Restarting bot process...")
            os.execl(sys.executable, sys.executable, *sys.argv)


# =====================
# /about Command
# =====================
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🤖 <b>About Anime File Downloader</b>\n\n"
        "This bot helps you securely fetch anime files using special access links.\n\n"
        "🎥 Files are hosted in our private vault.\n"
        f"📢 Join our public channel:<a href='https://t.me/{CHANNEL_USERNAME}'><b>Anime Share Point</b></a>\n"
        "🕒 Files auto-delete after 20 minutes for safety.\n\n"
        "Created with ❤ by MK",
        parse_mode="HTML"
    )
    SENT_MESSAGES.append((msg.chat_id, msg.message_id))



# =====================
# Admin Commands
# =====================
async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user is admin."""
    return update.effective_user.id == ADMIN_ID



async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually add a file entry."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")


    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /add <file name> <file_id>")

    file_name = remove_emojis(" ".join(context.args[:-1]))
    file_id = context.args[-1]
    data = load_json(DATA_FILE)

    data[file_name] = file_id
    save_json(DATA_FILE, data)
    await update.message.reply_text(f"✅ Added file:\n<b>{file_name}</b>", parse_mode="HTML")


async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved files."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")


    data = load_json(DATA_FILE)
    if not data:
        return await update.message.reply_text("📂 No files saved yet.")

    text = "<b>📜 Saved Files:</b>\n\n"
    for i, name in enumerate(data.keys(), start=1):
        safe_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text += f"{i}. {safe_name}\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def remove_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a file entry."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")


    if not context.args:
        return await update.message.reply_text("Usage: /remove <file name>")

    key = " ".join(context.args)
    data = load_json(DATA_FILE)

    if key in data:
        del data[key]
        save_json(DATA_FILE, data)
        await update.message.reply_text(f"✅ Successfully removed file:\n<b>{key}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ File not found.")


async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all saved files."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")
    save_json(DATA_FILE, {})
    await update.message.reply_text("⚠ All files cleared!")


# =====================
# Alias System (Improved)
# =====================
async def add_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add alias with syntax: /addalias [Alias Name] <file1, file2, file3>"""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")

    text = update.message.text.strip()

    # Match alias pattern like: /addalias [Alias Name] <file1, file2, file3>
    match = re.match(r"^/addalias\s*\[(.+?)\]\s*<(.+)>", text)
    if not match:
        return await update.message.reply_text(
            "⚠ Invalid format.\nUse:\n<b>/addalias [Alias Name] <file1, file2, ...></b>",
            parse_mode="HTML"
        )

    alias_name = remove_emojis(match.group(1).strip())
    files_part = match.group(2)
    file_patterns = [remove_emojis(f.strip()) for f in files_part.split(",") if f.strip()]

    aliases = load_json(ALIAS_FILE)
    aliases[alias_name] = file_patterns
    save_json(ALIAS_FILE, aliases)

    await update.message.reply_text(
        f"✅ Alias <b>{alias_name}</b> added with {len(file_patterns)} files.",
        parse_mode="HTML"
    )


async def list_aliases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all alias mappings."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")


    aliases = load_json(ALIAS_FILE)
    if not aliases:
        return await update.message.reply_text("📂 No aliases saved.")

    text = "<b>🔗 Saved Aliases:</b>\n\n"
    for i, (alias, items) in enumerate(aliases.items(), start=1):
        # ✅ Ensure items is iterable (list)
        if isinstance(items, str):
            items = [items]
        elif not isinstance(items, list):
            items = list(items)

        safe_alias = alias.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_items = [str(it).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for it in items]

        text += f"{i}. <b>{safe_alias}</b> → {', '.join(safe_items)}\n"

    await update.message.reply_text(text, parse_mode="HTML")



async def remove_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove alias by name."""
    if not await admin_only(update, context):
        return await update.message.reply_text("⛔ Unauthorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /removealias <alias name>")

    alias_name = " ".join(context.args)
    aliases = load_json(ALIAS_FILE)

    if alias_name in aliases:
        del aliases[alias_name]
        save_json(ALIAS_FILE, aliases)
        await update.message.reply_text(f"✅ Removed alias: {alias_name}")
    else:
        await update.message.reply_text("❌ Alias not found.")

async def debug_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current contents of JSON data files (for admin only). Sends two files: files.json and aliases.json"""
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Unauthorized.")

    data = load_json(DATA_FILE)
    aliases = load_json(ALIAS_FILE)

    # Prepare two small files to send back
    files = {
        "files.json": json.dumps(data, ensure_ascii=False, indent=2),
        "aliases.json": json.dumps(aliases, ensure_ascii=False, indent=2),
    }

    for fname, content in files.items():
        bio = io.BytesIO(content.encode("utf-8"))
        bio.name = fname
        # send as document (so big content is handled) — keep small though
        await context.bot.send_document(chat_id=update.effective_chat.id, document=bio)

    await update.message.reply_text("✅ Sent debug JSON files.")
    
    

# =====================
# Auto Save
# =====================
async def save_new_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != VAULT_CHANNEL_ID:
        return

    msg = update.channel_post
    if not msg:
        return

    file_obj = msg.video or msg.document or msg.animation
    if not file_obj:
        return

    raw_name = getattr(file_obj, "file_name", None) or f"file_{file_obj.file_unique_id}"
    clean_name = remove_emojis(raw_name)
    file_id = file_obj.file_id

    data = load_json(DATA_FILE)
    if clean_name not in data:
        data[clean_name] = file_id
        save_json(DATA_FILE, data)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Auto-saved: {clean_name}")
        print(f"[SAVED] {clean_name} -> {file_id}")
    else:
        print(f"[SKIPPED] {clean_name} already exists")

# =====================
# Fallback: random/unrecognized text handler
# =====================
async def handle_random_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_event = asyncio.Event()

    # Show short warm-up animation
    asyncio.create_task(smart_progress(update, stop_event))
    await asyncio.sleep(0.3)
    stop_event.set()

    msg = await update.message.reply_text(
        "👋 Hey there! I’m your friendly bot.\n"
        "Kindly use secure links from our official channel to access your files.\n"
         f"👉 <a href='https://t.me/{CHANNEL_USERNAME}'>Join Anime Share Point</a>",
        parse_mode="HTML"
    )
    SENT_MESSAGES.append((msg.chat_id, msg.message_id))
# =====================
# Main
# =====================
async def main():
    app = Application.builder().token(TOKEN).build()

    # -------------------
    # Register all Commands
    # -------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("add", add_file))
    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(CommandHandler("remove", remove_file))
    app.add_handler(CommandHandler("clearall", clear_all))
    app.add_handler(CommandHandler("addalias", add_alias))
    app.add_handler(CommandHandler("listaliases", list_aliases))
    app.add_handler(CommandHandler("removealias", remove_alias))
    app.add_handler(CommandHandler("debugjson", debug_json))
   

    # Handle random text messages (non-command)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_random_message))

    # Auto-save all incoming messages
    app.add_handler(MessageHandler(filters.ALL, save_new_file))

    # Handle refresh for join channel
    app.add_handler(CallbackQueryHandler(handle_refresh, pattern="^refresh:"))

    # -------------------
    # post_init block (runs after bot starts)
    # -------------------
    async def on_startup(app: Application):
        # [1] Background task for inactivity check
        asyncio.create_task(check_inactivity(app))

        # [2] Setup command menu
        #Normal User Commmands 
        user_cmds = [
            BotCommand("start", "Fetch your file"),
            BotCommand("about", "About this bot")
        ]
        #Admin Commmands 
        admin_cmds = user_cmds + [
            BotCommand("add", "Add file manually"),
            BotCommand("list", "List files"),
            BotCommand("remove", "Remove a file"),
            BotCommand("clearall", "Clear all files"),
            BotCommand("addalias", "Add alias for grouped files"),
            BotCommand("listaliases", "List aliases"),
            BotCommand("removealias", "Remove alias"),
            BotCommand("debugjson", "List all data and alias"),
        ]

        await app.bot.set_my_commands(user_cmds)
        await app.bot.set_my_commands(admin_cmds, scope={"type": "chat", "chat_id": ADMIN_ID})

        print("✅ Startup setup complete (menu + background tasks started)")

    app.post_init = on_startup

    # -------------------
    # aiohttp web server
    # -------------------
    web_app = web.Application()

    # ✅ Webhook route for Telegram
    async def handle_webhook(request):
        data = await request.json()
        print("📩 Update received:", data)  # Debugging
        await app.update_queue.put(Update.de_json(data, app.bot))
        return web.Response(text="OK")

    # ✅ Root route for browser (health check)
    async def handle_root(request):
        return web.Response(text="Bot is alive 🟢", content_type="text/plain")

    # Register routes
    web_app.add_routes([
        web.post(f"/webhook/{TOKEN}", handle_webhook),
        web.get("/", handle_root)
    ])

    # -------------------
    # Start Webhook Server
    # -------------------
    async with app:
        # Set Telegram webhook
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}/webhook/{TOKEN}"
        await app.bot.set_webhook(webhook_url)
        print(f"✅ Webhook set successfully at: {webhook_url}")

        # Initialize and start Telegram bot
        await app.initialize()
        asyncio.create_task(app.start())
        print("🌀 Telegram bot event loop started ")

        # Start aiohttp web server
        runner = web.AppRunner(web_app)
        await runner.setup()
        port = int(os.getenv("PORT", 10000))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

        print(f"🚀 Bot running via webhook on port {port}")
        await asyncio.Event().wait()  # keep running


if __name__ == "__main__":
    asyncio.run(main())
