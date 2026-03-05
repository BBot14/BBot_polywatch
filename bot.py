import os
import time
import requests
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
POLYMARKET_URL = "https://gamma-api.polymarket.com/markets"

# ── Keep-alive (prevents Render free tier sleeping) ────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"alive")
    def log_message(self, *args): pass

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ── Telegram helpers ───────────────────────────────────────────
def send(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Send error: {e}")

def get_updates(offset=None):
    try:
        params = {"timeout": 30, "offset": offset}
        res = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        return res.json().get("result", [])
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
        return []

# ── Polymarket ─────────────────────────────────────────────────
def fetch_markets(limit=10, keyword=None):
    try:
        res = requests.get(POLYMARKET_URL, params={
            "limit": 30, "active": "true",
            "closed": "false", "order": "volume", "ascending": "false"
        }, timeout=10)
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
    except: return "N/A"

def vol(m):
    try:
        v = float(m.get("volume", 0))
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000:     return f"${v/1_000:.0f}K"
        return f"${v:.0f}"
    except: return "N/A"

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

# ── Command handlers ───────────────────────────────────────────
def handle_start(chat_id, _):
    send(chat_id,
        "👁 *POLYWATCH BOT*\n\n"
        "Your AI-powered Polymarket monitor.\n\n"
        "*Commands:*\n"
        "/markets — Top 10 markets by volume\n"
        "/top — Top 5 with AI summary\n"
        "/search bitcoin — Search markets\n"
        "/analyze fed rates — Deep AI analysis\n"
        "/help — Show this menu"
    )

def handle_markets(chat_id, _):
    send(chat_id, "⏳ Fetching live markets...")
    markets = fetch_markets(10)
    if not markets:
        send(chat_id, "❌ Could not fetch markets. Try again.")
        return
    lines = ["📊 *TOP MARKETS BY VOLUME*\n"]
    for i, m in enumerate(markets, 1):
        q = (m.get("question") or "")[:80]
        lines.append(f"*{i}.* {q}\n    YES: `{prob(m)}` · Vol: `{vol(m)}`\n")
    send(chat_id, "\n".join(lines))

def handle_top(chat_id, _):
    send(chat_id, "🤖 Generating AI market overview...")
    markets = fetch_markets(5)
    if not markets:
        send(chat_id, "❌ Could not fetch markets.")
        return
    market_list = "\n".join([
        f"{i+1}. \"{m.get('question','')}\" YES:{prob(m)} Vol:{vol(m)}"
        for i, m in enumerate(markets)
    ])
    prompt = f"""Prediction market analyst. Top 5 Polymarket markets today:
{market_list}
Give a punchy 3-4 sentence overview: themes, surprising odds, one market to watch. Be direct."""
    send(chat_id, f"🔮 *TODAY'S OVERVIEW*\n\n{ask_claude(prompt)}")

def handle_search(chat_id, args):
    if not args:
        send(chat_id, "Usage: /search [keyword]\nExample: /search bitcoin")
        return
    keyword = " ".join(args)
    send(chat_id, f"🔍 Searching *{keyword}*...")
    markets = fetch_markets(20, keyword)
    if not markets:
        send(chat_id, f"No markets found for '{keyword}'.")
        return
    lines = [f"🔍 *Results for '{keyword}'*\n"]
    for i, m in enumerate(markets[:8], 1):
        q = (m.get("question") or "")[:80]
        lines.append(f"*{i}.* {q}\n    YES: `{prob(m)}` · Vol: `{vol(m)}`\n")
    send(chat_id, "\n".join(lines))

def handle_analyze(chat_id, args):
    if not args:
        send(chat_id, "Usage: /analyze [topic]\nExample: /analyze bitcoin")
        return
    query = " ".join(args)
    send(chat_id, f"🤖 Analyzing *{query}*...")
    markets = fetch_markets(30, query)
    ctx = ""
    if markets:
        m = markets[0]
        ctx = f"\nClosest market: \"{m.get('question','')}\" YES:{prob(m)} Vol:{vol(m)}"
    prompt = f"""Sharp prediction market analyst. Topic: "{query}"{ctx}
4 short paragraphs: 1) Current odds & drivers 2) Bull case 3) Bear case 4) Key signal to watch. Be direct."""
    send(chat_id, f"📈 *ANALYSIS: {query.upper()}*\n\n{ask_claude(prompt)}")

def handle_freetext(chat_id, text):
    markets = fetch_markets(5)
    market_list = "\n".join([
        f"- \"{m.get('question','')}\" YES:{prob(m)}" for m in markets
    ])
    prompt = f"""Polymarket Telegram bot. Top markets: {market_list}
User: "{text}"
Reply helpfully, max 150 words. Explain commands if needed: /markets /top /search /analyze"""
    send(chat_id, ask_claude(prompt))

# ── Main polling loop ──────────────────────────────────────────
COMMANDS = {
    "/start":   handle_start,
    "/help":    handle_start,
    "/markets": handle_markets,
    "/top":     handle_top,
    "/search":  handle_search,
    "/analyze": handle_analyze,
}

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg: return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    if not text: return

    parts = text.split()
    # Strip bot username suffix e.g. /start@mybotname
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd in COMMANDS:
        COMMANDS[cmd](chat_id, args)
    else:
        handle_freetext(chat_id, text)

def main():
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info("🤖 Polywatch bot running (raw HTTP mode)...")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                threading.Thread(target=process_update, args=(update,), daemon=True).start()
        except Exception as e:
            logger.error(f"Poll loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()