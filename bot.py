#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, quote

import requests
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
APP_ID         = int(os.getenv("APP_ID", 0))
APP_HASH       = os.getenv("APP_HASH", "")
TERABOX_COOKIE = os.getenv("TERABOX_COOKIE", "")
WEBAPP_URL     = os.getenv("WEBAPP_URL", "https://teraboxsk.pages.dev")
PORT           = int(os.getenv("PORT", 8000))

# ── TeraBox domains ───────────────────────────────────────────────────────────
TERABOX_DOMAINS = [
    "terabox.com", "1024terabox.com", "1024tera.com",
    "teraboxapp.com", "freeterabox.com", "momerybox.com",
    "tibibox.com", "nephobox.com", "4funbox.co", "mirrobox.com",
]

def is_terabox_url(text: str) -> bool:
    try:
        host = urlparse(text.strip()).hostname or ""
        return any(host == d or host.endswith("." + d) for d in TERABOX_DOMAINS)
    except Exception:
        return False

def get_surl(url: str) -> str | None:
    try:
        u = urlparse(url.strip())
        surl = parse_qs(u.query).get("surl", [None])[0]
        if surl:
            return surl
        m = re.search(r"/s/([a-zA-Z0-9_-]+)", u.path)
        return m.group(1) if m else None
    except Exception:
        return None

# ── TeraBox extractor ─────────────────────────────────────────────────────────
def get_jstoken(session: requests.Session) -> str:
    try:
        r = session.get("https://www.terabox.com/main", timeout=10)
        m = re.search(r'locals\.mixin\.jsToken\s*=\s*["\']([^"\']+)["\']', r.text)
        return m.group(1) if m else ""
    except Exception:
        return ""

def extract_terabox(share_url: str) -> dict | None:
    surl = get_surl(share_url)
    if not surl:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.terabox.com/",
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    session = requests.Session()
    session.headers.update(headers)

    if TERABOX_COOKIE:
        session.cookies.update({"cookie": TERABOX_COOKIE})
        # Parse cookie string into dict
        for part in TERABOX_COOKIE.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                session.cookies.set(k.strip(), v.strip(), domain=".terabox.com")

    try:
        jstoken = get_jstoken(session)

        # Get file info
        info_url = f"https://www.terabox.com/api/shorturlinfo?app_id=250528&jsToken={jstoken}&shorturl={surl}&root=1"
        info_r = session.get(info_url, timeout=15)
        info = info_r.json()

        if info.get("errno", -1) != 0 or not info.get("list"):
            logger.error(f"TeraBox info error: {info}")
            return None

        file = info["list"][0]
        fs_id    = file["fs_id"]
        filename = file.get("server_filename", "Unknown")
        size     = file.get("size", 0)
        thumb    = file.get("thumbs", {}).get("url3") or file.get("thumbs", {}).get("url1") or ""

        # Get download link
        dl_url = f"https://www.terabox.com/api/download?app_id=250528&jsToken={jstoken}&shorturl={surl}&fid_list=[{fs_id}]"
        dl_r = session.get(dl_url, timeout=15)
        dl = dl_r.json()

        dlink = dl.get("dlink") or (dl.get("list") or [{}])[0].get("dlink", "")
        if not dlink:
            logger.error(f"No dlink in response: {dl}")
            return None

        return {"filename": filename, "size": size, "thumb": thumb, "dlink": dlink}

    except Exception as e:
        logger.exception(f"Extraction error: {e}")
        return None

def fmt_size(b: int) -> str:
    if not b:
        return "Unknown size"
    if b >= 1024**3: return f"{b/1024**3:.2f} GB"
    if b >= 1024**2: return f"{b/1024**2:.1f} MB"
    return f"{b/1024:.0f} KB"

# ── Health check ──────────────────────────────────────────────────────────────
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *a): pass

def start_health():
    HTTPServer(("0.0.0.0", PORT), Health).serve_forever()

# ── Bot ───────────────────────────────────────────────────────────────────────
app = Client("terabox_bot", api_id=APP_ID, api_hash=APP_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    await message.reply_text(
        "🎬 **TeraBox Downloader Bot**\n\n"
        "Send me any TeraBox share link and I'll give you:\n\n"
        "  📥 Direct download link\n"
        "  ▶️ Online stream link\n"
        "  📋 File info (name, size)\n"
        "  🖼 Thumbnail preview\n\n"
        "**Supported:**\n"
        "`terabox.com/s/...`\n"
        "`1024tera.com/wap/share/filelist?surl=...`\n\n"
        "Just paste a link! 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Open Web App", web_app={"url": WEBAPP_URL}),
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ]])
    )

@app.on_message(filters.command("help"))
async def cmd_help(client: Client, message: Message):
    await message.reply_text(
        "❓ **How to use TeraBox Bot**\n\n"
        "**1.** Copy any TeraBox share link\n"
        "**2.** Paste it here in chat\n"
        "**3.** Get download + stream links!\n\n"
        "**Commands:**\n"
        "/start — Welcome\n"
        "/help — This message\n\n"
        "**Note:** Links expire in ~1 hour.\n"
        "Re-send the TeraBox URL to refresh.",
        parse_mode=ParseMode.MARKDOWN,
    )

@app.on_message(filters.text & ~filters.command([]))
async def handle_link(client: Client, message: Message):
    url = message.text.strip()
    if not is_terabox_url(url):
        await message.reply_text(
            "👆 Please send a valid **TeraBox** link.\n\n"
            "Examples:\n"
            "`https://terabox.com/s/xxxxx`\n"
            "`https://1024tera.com/wap/share/filelist?surl=xxxxx`\n\n"
            "Type /help for more info.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    status = await message.reply_text("⏳ **Processing...**\n\n🔍 Connecting to TeraBox...")

    await status.edit_text("⏳ **Processing...**\n\n✅ Connected\n📦 Extracting file info...")

    # Run blocking extraction in thread pool
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, extract_terabox, url)

    if not info or not info.get("dlink"):
        await status.edit_text(
            "❌ **Failed to extract link**\n\n"
            "Possible reasons:\n"
            "• File is private or deleted\n"
            "• Cookie session expired — update `TERABOX_COOKIE`\n"
            "• TeraBox servers busy\n\n"
            "_Try again in a few seconds._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    size_str   = fmt_size(info["size"])
    webapp_link = (
        f"{WEBAPP_URL}?name={quote(info['filename'])}"
        f"&size={info['size']}"
        f"&dlink={quote(info['dlink'])}"
        f"&thumb={quote(info['thumb'])}"
    )

    caption = (
        f"✅ **File Found!**\n\n"
        f"📁 **Name:** `{info['filename']}`\n"
        f"💾 **Size:** {size_str}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Watch Online", web_app={"url": webapp_link}),
            InlineKeyboardButton("📥 Download Link", callback_data=f"dl_{info['dlink']}"),
        ],
        [InlineKeyboardButton("🔗 Copy Stream URL", callback_data=f"stream_{info['dlink']}")],
    ])

    await status.delete()

    if info["thumb"]:
        try:
            await client.send_photo(
                message.chat.id, info["thumb"],
                caption=caption, parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@app.on_callback_query()
async def handle_callback(client: Client, query: CallbackQuery):
    data = query.data

    if data == "help":
        await query.answer()
        await query.message.reply_text(
            "❓ **How to use:**\n\n"
            "Send any TeraBox link → get direct download + stream URLs instantly.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("dl_"):
        link = data[3:]
        await query.answer("📥 Link ready!")
        await query.message.reply_text(
            f"📥 **Direct Download Link:**\n\n`{link}`\n\n"
            "⚠️ _Expires in ~1 hour._",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("stream_"):
        link = data[7:]
        await query.answer("▶️ Stream URL ready!")
        await query.message.reply_text(
            f"▶️ **Stream URL:**\n\n`{link}`\n\n"
            "💡 _Paste in VLC, MX Player, or nPlayer._",
            parse_mode=ParseMode.MARKDOWN,
        )

    else:
        await query.answer()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=start_health, daemon=True).start()
    logger.info(f"Health server on port {PORT}")
    logger.info("Starting TeraBox Bot...")
    app.run()
