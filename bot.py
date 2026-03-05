import os
import requests
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
POLYMARKET_URL = "https://gamma-api.polymarket.com/markets"

# ── Keep-alive server ──────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"alive")
    def log_message(self, *args):
        pass

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ── Polymarket ─────────────────────────────────────────────────
def fetch_markets(limit=10, keyword=None):
    try:
        params = {"limit": 30, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}
        res = requests.get(POLYMARKET_URL, params=params, timeout=10)
        res.raise_for_status()
        markets = res.json()
        if keyword:
            markets = [m for m in markets if keyword.lower() in (m.get("question") or "").lower()]
        return markets[:limit]
    except Exception as e:
        logger.error(f"Polymarket error: {e}")
        return []

def prob(m):
    try:
        return f"{round(float(json.loads(m.get('outcomePrices','[0.5]'))[0]) * 100)}%"
    except:
        return "N/A"

def vol(m):
    try:
        v = float(m.get("volume", 0))
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000:     return f"${v/1_000:.0f}K"
        return f"${v:.0f}"
    except:
        return "N/A"

# ── Claude ─────────────────────────────────────────────────────
def ask_claude(prompt):
    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        data = res.json()
        if "error" in data:
            return f"API error: {data['error']['message']}"
        return data["content"][0]["text"].strip()
    except Exception as e:
        return f"Error: {e}"

# ── Handlers ───────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👁 *POLYWATCH BOT*\n\n"
        "Your AI-powered Polymarket monitor.\n\n"
        "*Commands:*\n"
        "/markets — Top 10 markets by volume\n"
        "/top — Top 5 with AI summary\n"
        "/search bitcoin — Search markets\n"
        "/analyze fed rates — Deep AI analysis\n"
        "/help — Show this menu",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_markets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching live markets...")
    markets = fetch_markets(limit=10)
    if not markets:
        await update.message.reply_text("❌ Could not fetch markets. Try again.")
        return
    lines = ["📊 *TOP MARKETS BY VOLUME*\n"]
    for i, m in enumerate(markets, 1):
        q = (m.get("question") or "")[:80]
        lines.append(f"*{i}.* {q}\n    YES: `{prob(m)}` · Vol: `{vol(m)}`\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Generating AI market overview...")
    markets = fetch_markets(limit=5)
    if not markets:
        await update.message.reply_text("❌ Could not fetch markets.")
        return
    market_list = "\n".join([
        f"{i+1}. \"{m.get('question','')}\" — YES: {prob(m)}, Vol: {vol(m)}"
        for i, m in enumerate(markets)
    ])
    prompt = f"""You are a prediction market analyst. Here are today's top 5 Polymarket markets:

{market_list}

Give a punchy 3-4 sentence overview: what themes dominate, which probabilities look most interesting, and one market worth watching. Be direct and specific."""
    analysis = ask_claude(prompt)
    await update.message.reply_text(f"🔮 *TODAY'S OVERVIEW*\n\n{analysis}", parse_mode="Markdown")

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyword = " ".join(ctx.args) if ctx.args else ""
    if not keyword:
        await update.message.reply_text("Usage: /search [keyword]\nExample: /search bitcoin")
        return
    await update.message.reply_text(f"🔍 Searching *{keyword}*...", parse_mode="Markdown")
    markets = fetch_markets(limit=20, keyword=keyword)
    if not markets:
        await update.message.reply_text(f"No markets found for '{keyword}'.")
        return
    lines = [f"🔍 *Results for '{keyword}'*\n"]
    for i, m in enumerate(markets[:8], 1):
        q = (m.get("question") or "")[:80]
        lines.append(f"*{i}.* {q}\n    YES: `{prob(m)}` · Vol: `{vol(m)}`\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await update.message.reply_text("Usage: /analyze [topic]\nExample: /analyze bitcoin price")
        return
    await update.message.reply_text(f"🤖 Analyzing *{query}*...", parse_mode="Markdown")
    markets = fetch_markets(limit=30, keyword=query)
    market_ctx = ""
    if markets:
        m = markets[0]
        market_ctx = f"\nClosest market: \"{m.get('question','')}\" YES: {prob(m)}, Vol: {vol(m)}"
    prompt = f"""You are a sharp prediction market analyst. Topic: "{query}"{market_ctx}

Analyze in 4 short paragraphs:
1. Current odds and what drives them
2. Bull case (reasons YES wins)
3. Bear case (reasons NO wins)
4. One key signal to monitor

Be direct and specific."""
    analysis = ask_claude(prompt)
    await update.message.reply_text(f"📈 *ANALYSIS: {query.upper()}*\n\n{analysis}", parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_text("🤖 Thinking...")
    markets = fetch_markets(limit=5)
    market_list = "\n".join([
        f"- \"{m.get('question','')}\" YES: {prob(m)}, Vol: {vol(m)}"
        for m in markets
    ])
    prompt = f"""You are a helpful Polymarket Telegram bot assistant.

Current top markets:
{market_list}

User said: "{text}"

Reply helpfully and concisely (max 200 words). If they ask about a market or topic, give analysis. If they ask how to use the bot, explain: /markets, /top, /search [keyword], /analyze [topic]."""
    reply = ask_claude(prompt)
    await update.message.reply_text(reply)

# ── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info("Keep-alive server started")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("markets", cmd_markets))
    app.add_handler(CommandHandler("top",     cmd_top))
    app.add_handler(CommandHandler("search",  cmd_search))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Polywatch bot running...")
    app.run_polling(drop_pending_updates=True)