import os
import requests
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ── Setup logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Read API keys from environment variables ───────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]

# ── Polymarket API ─────────────────────────────────────────────
POLYMARKET_URL = "https://gamma-api.polymarket.com/markets"

def fetch_markets(limit=10, keyword=None):
    """Fetch active markets from Polymarket, sorted by volume."""
    try:
        params = {
            "limit": 30,
            "active": "true",
            "closed": "false",
            "order": "volume",
            "ascending": "false",
        }
        res = requests.get(POLYMARKET_URL, params=params, timeout=10)
        res.raise_for_status()
        markets = res.json()

        if keyword:
            keyword = keyword.lower()
            markets = [m for m in markets if keyword in (m.get("question") or "").lower()]

        return markets[:limit]
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return []

def format_prob(market):
    """Extract YES probability as a percentage string."""
    try:
        prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
        return f"{round(float(prices[0]) * 100)}%"
    except:
        return "N/A"

def format_volume(market):
    """Format volume as $1.2M / $500K etc."""
    try:
        v = float(market.get("volume", 0))
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000:     return f"${v/1_000:.0f}K"
        return f"${v:.0f}"
    except:
        return "N/A"

# ── Claude API ─────────────────────────────────────────────────
def ask_claude(prompt):
    """Send a prompt to Claude and return the text response."""
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
        return f"Error contacting Claude: {e}"

# ── Bot command handlers ───────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👁 *POLYWATCH BOT*\n\n"
        "Your AI-powered Polymarket monitor.\n\n"
        "*Commands:*\n"
        "/markets — Top 10 markets by volume\n"
        "/top — Top 5 with AI summary\n"
        "/search [keyword] — Search markets\n"
        "/analyze [question] — Deep AI analysis\n"
        "/help — Show this menu",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_markets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show top 10 markets with probabilities."""
    await update.message.reply_text("⏳ Fetching live markets...")
    markets = fetch_markets(limit=10)

    if not markets:
        await update.message.reply_text("❌ Could not fetch markets. Try again.")
        return

    lines = ["📊 *TOP MARKETS BY VOLUME*\n"]
    for i, m in enumerate(markets, 1):
        q = m.get("question", "Unknown")[:80]
        prob = format_prob(m)
        vol = format_volume(m)
        lines.append(f"*{i}.* {q}\n    YES: `{prob}` · Vol: `{vol}`\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Top 5 markets with a brief AI overview."""
    await update.message.reply_text("🤖 Fetching markets and generating AI summary...")
    markets = fetch_markets(limit=5)

    if not markets:
        await update.message.reply_text("❌ Could not fetch markets.")
        return

    market_list = "\n".join([
        f"{i+1}. \"{m.get('question', '')}\" — YES: {format_prob(m)}, Vol: {format_volume(m)}"
        for i, m in enumerate(markets)
    ])

    prompt = f"""You are a prediction market analyst. Here are today's top 5 Polymarket markets by volume:

{market_list}

Give a punchy 3-4 sentence overview: what themes dominate today's markets, which probabilities look most interesting or surprising, and one market worth watching closely. Be direct and specific."""

    analysis = ask_claude(prompt)
    reply = f"🔮 *TODAY'S MARKET OVERVIEW*\n\n{analysis}\n\n_Use /markets to see all top markets_"
    await update.message.reply_text(reply, parse_mode="Markdown")

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Search markets by keyword."""
    keyword = " ".join(ctx.args) if ctx.args else ""
    if not keyword:
        await update.message.reply_text("Usage: /search [keyword]\nExample: /search bitcoin")
        return

    await update.message.reply_text(f"🔍 Searching for *{keyword}*...", parse_mode="Markdown")
    markets = fetch_markets(limit=20, keyword=keyword)

    if not markets:
        await update.message.reply_text(f"No markets found for '{keyword}'.")
        return

    lines = [f"🔍 *Results for '{keyword}'*\n"]
    for i, m in enumerate(markets[:8], 1):
        q = m.get("question", "Unknown")[:80]
        prob = format_prob(m)
        vol = format_volume(m)
        lines.append(f"*{i}.* {q}\n    YES: `{prob}` · Vol: `{vol}`\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Deep AI analysis of a specific market or topic."""
    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: /analyze [market question or topic]\n"
            "Example: /analyze bitcoin price"
        )
        return

    await update.message.reply_text(f"🤖 Analyzing *{query}*...", parse_mode="Markdown")

    # Try to find a matching market
    markets = fetch_markets(limit=30, keyword=query)
    market_context = ""
    if markets:
        m = markets[0]
        market_context = f"\nClosest matching market: \"{m.get('question','')}\" — YES: {format_prob(m)}, Volume: {format_volume(m)}"

    prompt = f"""You are a sharp prediction market analyst. The user wants analysis on: "{query}"{market_context}

Provide a focused analysis in 4 short paragraphs:
1. Current state: what's driving odds and whether they seem fair
2. Bull case: main reasons YES could win
3. Bear case: main reasons NO could win  
4. Key signal: one specific thing to monitor that will move the market

Be direct, specific, and concise."""

    analysis = ask_claude(prompt)
    reply = f"📈 *ANALYSIS: {query.upper()}*\n\n{analysis}"
    await update.message.reply_text(reply, parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle free-form text messages as natural language queries."""
    text = update.message.text.strip()
    await update.message.reply_text("🤖 Thinking...")

    markets = fetch_markets(limit=10)
    market_list = "\n".join([
        f"- \"{m.get('question','')}\" YES: {format_prob(m)}, Vol: {format_volume(m)}"
        for m in markets[:5]
    ])

    prompt = f"""You are a helpful Polymarket prediction market assistant accessible via Telegram.

Current top markets:
{market_list}

User message: "{text}"

Respond helpfully and concisely (max 200 words). If they're asking about a market or topic, give your analysis. If they're asking how to use the bot, explain the commands (/markets, /top, /search, /analyze). Be conversational."""

    reply = ask_claude(prompt)
    await update.message.reply_text(reply)

# ── Keep-alive server (prevents Render free tier from sleeping) ──
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Polywatch bot is alive!")
    def log_message(self, format, *args):
        pass  # Silence request logs

def run_keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    logger.info(f"Keep-alive server running on port {port}")
    server.serve_forever()

# ── Main entry point ───────────────────────────────────────────
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("markets", cmd_markets))
    app.add_handler(CommandHandler("top",     cmd_top))
    app.add_handler(CommandHandler("search",  cmd_search))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start keep-alive server in background thread
    t = threading.Thread(target=run_keep_alive, daemon=True)
    t.start()

    logger.info("🤖 Polywatch bot is running...")
    app.run_polling()
