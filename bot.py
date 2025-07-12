import discord
import os
import re
import json
import gspread
import pytz
from youtubeListener import start_youtube_watcher
from openai import OpenAI
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

# Load secrets
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
USER_ID = int(os.getenv("USER_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Setup AI
openAIClient = OpenAI(api_key=OPENAI_API_KEY)

# Setup Google Sheets
scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name("google-creds.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open(GOOGLE_SHEET_NAME)
trades_sheet = sheet.worksheet("Trades")
error_sheet = sheet.worksheet("Errors")
EDT = pytz.timezone("America/New_York")

# Discord client
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Trade tracking (basic in-memory)
open_trades = {}

def get_current_friday():
    """Get the date of the current week's Friday"""
    today = datetime.now()
    days_ahead = 4 - today.weekday()  # Friday is 4
    if days_ahead < 0:  # Friday already happened this week
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

def get_next_friday():
    """Get the date of next Friday"""
    today = datetime.now()
    days_ahead = 4 - today.weekday()  # Friday is 4
    if days_ahead <= 0:  # Friday already happened this week or is today
        days_ahead += 7
    else:
        days_ahead += 7  # Next Friday, not this Friday
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

# Enhanced AI parser
def parse_trade_message_ai(message):
    # Get current date context for relative date parsing
    current_friday = get_current_friday()
    next_friday = get_next_friday()
    current_year = datetime.now().year

    prompt = f"""
    You're an expert trading assistant. Parse this trading message into structured JSON. Handle complex language and trading slang.

    Current context:
    - Today's date: {datetime.now().strftime("%Y-%m-%d")}
    - This Friday: {current_friday}
    - Next Friday: {next_friday}
    - Current year: {current_year}

    PARSING RULES:
    1. ACTIONS: BTO/BUY (buy to open), STO/SELL (sell to open), BTC (buy to close), STC (sell to close), SOLD(sell)
    2. SYMBOLS: Extract ticker symbols (RDDT, SPY, VZ, HOOD, TLT, etc.)
    3. QUANTITIES: 
       - If dollar amount mentioned (e.g., "200 worth"), convert to estimated contracts
       - If quantity is explicitly stated, use that number
       - If quantity is NOT clear or specified, leave blank (do not assume 1)
    4. STRIKES & TYPES:
       - "120C" = 120 strike call
       - "575s" = 575 strike (assume calls unless specified)
       - "60P" = 60 strike put
       - "47.00 C" = 47 strike call
    5. DATES:
       - "Friday" = this Friday ({current_friday})
       - "next Friday" = next Friday ({next_friday})
       - "7/11" = 2024-07-11 (assume current year if not specified)
       - "9/19/2025" = 2025-09-19
       - "1/26" = assume next occurrence (2025-01-26 if we're past it in 2024)
    6. PRICES: Extract from "@", "for", or context
    7. CONTEXT:
       - "potential break out" = trading reason (put in notes)
       - "magic FF set being sold out" = catalyst (put in notes)

    EXAMPLES:
    "STO RDDT 120C for Friday" → action: "STO", symbol: "RDDT", strike: 120, type: "C", expiry: "{current_friday}"
    "SPY 575s for 7/11 5.2" → action: "BTO", symbol: "SPY", strike: 575, type: "C", expiry: "2024-07-11", price: 5.2
    "200 worth of HOOD 60P" → action: "BTO", symbol: "HOOD", quantity: 3, strike: 60, type: "P" (estimate 3 contracts for $200)
    "Sold reddit right now by selling the 400 shares at 120" → action: "SOLD", symbol: "RDDT", quantity: "400"
    "3xing my hasbro position" → action: "BUY", symbol: "HAS", notes: "3xing position on potential breakout"
    "Short rklb target is 28-29 by EoW" → action: "STO", symbol: "RKLB", notes: "Short target 28-29 by EoW"

    Respond ONLY with valid JSON:
    {{
        "action": "BTO",
        "symbol": "RDDT", 
        "quantity": "",
        "price": 5.2,
        "strike_price": 120,
        "expiry": "2024-07-11",
        "option_type": "C",
        "notes": "any additional context"
    }}

    Here is another example:
    Respond ONLY with valid JSON:
    {{
        "action": "BTO",
        "symbol": "HAS", 
        "quantity": "",
        "price": 0.0,
        "strike_price": "",
        "expiry": "",
        "option_type": "",
        "notes": "3xing position on potential breakout with magic FF set being sold out"
    }}

    For stock trades (no options):
    {{
        "action": "BUY",
        "symbol": "AAPL",
        "quantity": 10,
        "price": 150.0,
        "notes": "relevant context"
    }}

    and here is another example:
    For stock trades (no options):
    {{
        "action": "SELL",
        "symbol": "RDDT",
        "quantity": 400,
        "price": 120.0,
        "notes": "relevant context"
    }}


    IMPORTANT: 
    - Always try to extract an action and symbol, even from casual language
    - Put trading context, catalysts, and targets in notes
    - Convert company names to ticker symbols when possible

    If completely unclear or invalid:
    {{ "error": "specific reason why parsing failed" }}

    Message to parse: "{message}"
    """

    try:
        response = openAIClient.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise trading message parser. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1  # Lower temperature for more consistent parsing
        )
        content = response.choices[0].message.content.strip()

        # Clean up response in case AI adds markdown formatting
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        data = json.loads(content)

        # Post-processing validation and cleanup
        if "error" not in data:
            # Set defaults but don't assume quantity
            data.setdefault("quantity", "")  # Leave blank if not specified
            data.setdefault("price", 0.0)
            data.setdefault("strike_price", "")
            data.setdefault("expiry", "")
            data.setdefault("option_type", "")
            data.setdefault("notes", "")

            # Validate and normalize action
            action = data.get("action", "").upper()
            valid_actions = {"BUY", "SELL", "BTO", "STO", "BTC", "STC"}
            if action not in valid_actions:
                # Try to map common variations
                action_mapping = {
                    "LONG": "BUY",
                    "SHORT": "STO",
                    "ADDING": "BUY",
                    "TRIPLE": "BUY",
                    "3X": "BUY",
                    "SOLD": "SELL" 
                }
                if action in action_mapping:
                    data["action"] = action_mapping[action]
                else:
                    return {"error": f"Invalid action: {action}"}

        return data

    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse AI response as JSON: {str(e)}"}
    except Exception as e:
        return {"error": f"AI parsing error: {str(e)}"}

# Trade logging (enhanced)
def log_trade(action, symbol, quantity, price, message, date_str, time_str, strike_price="", expiry="", option_type="", notes=""):
    is_buy = action in {"BUY", "BTO", "BTC"}
    buy_price = price if is_buy else ""
    sell_price = price if not is_buy else ""

    trades_sheet.append_row([
        action, symbol, quantity, buy_price, sell_price,
        strike_price, expiry, option_type,
        date_str, time_str, message, notes
    ])

def convert_to_edt(dt_utc):
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=pytz.UTC)
    dt_edt = dt_utc.astimezone(EDT)
    return dt_edt

# Trading terms for detection
TRADING_KEYWORDS = {
    'actions': ['buy', 'sell', 'bto', 'sto', 'btc', 'stc', 'long', 'short', 'hedge', 'adding', 'gamble'],
    'symbols': ['spy', 'qqq', 'rddt', 'hood', 'tlt', 'vz', 'aapl', 'tsla', 'nvda', 'msft'],  # Add your common symbols
    'options': ['call', 'calls', 'put', 'puts', 'strike', 'expiry', 'friday', 'monday'],
    'general': ['trade', 'position', 'contract', 'worth', 'limit', 'market', '@']
}

def looks_like_trade_message(message):
    """Check if a message contains trading-related keywords"""
    message_lower = message.lower()

    # Count keyword matches
    matches = 0
    for category, keywords in TRADING_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message_lower:
                matches += 1

    # Also check for common patterns
    patterns = [
        r'\$\d+',  # Dollar amounts
        r'\d+[CP]',  # Strike notation like 120C or 60P  
        r'\d+/\d+',  # Date notation like 7/11
        r'@\s*\$?\d+',  # Price notation like @ $5.20
        r'\d+\s*(worth|contracts?)',  # Quantity descriptions
        r'[A-Z]{2,5}\s+\d+[CP]?',  # Symbol + strike pattern
    ]

    for pattern in patterns:
        if re.search(pattern, message, re.IGNORECASE):
            matches += 1

    # Consider it trade-related if it has 2+ matches
    return matches >= 2

# Error logging
def log_error(message):
    error_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message])

# Unparsed trade logging
def log_unparsed_trade(message_content, date_str, time_str, error_reason):
    """Log messages that look like trades but couldn't be parsed"""
    trades_sheet.append_row([
        "UNPARSED",  # action
        "",  # symbol
        "",  # quantity
        "",  # buy_price
        "",  # sell_price
        "",  # strike_price
        "",  # expiry
        "",  # option_type
        date_str,
        time_str,
        message_content,  # original message
        f"PARSE_ERROR: {error_reason}"  # notes
    ])

@client.event
async def on_ready():
    print(f'Logged in as {client.user}!')
    client.loop.create_task(start_youtube_watcher(client))

@client.event
async def on_message(message):
    if message.channel.id != CHANNEL_ID or message.author.id != USER_ID:
        return

    dt_edt = convert_to_edt(message.created_at)
    date_str = dt_edt.strftime("%Y-%m-%d")
    time_str = dt_edt.strftime("%H:%M:%S")

    parsed = parse_trade_message_ai(message.content)
    print(f"Parsed result: {parsed}")

    if "error" in parsed:
        # Check if this looks like a trade message even though it failed to parse
        if looks_like_trade_message(message.content):
            log_unparsed_trade(message.content, date_str, time_str, parsed['error'])
            print(f"⚠️ Logged unparsed trade-like message: {message.content}")
        else:
            log_error(f"AI Error (non-trade): {parsed['error']} | Original: {message.content}")
        return

    try:
        action = parsed['action'].upper()
        symbol = parsed['symbol'].upper()
        quantity = parsed.get('quantity', "")  # Keep as string, could be empty
        price = float(parsed.get('price', 0.0))
        strike_price = parsed.get('strike_price', "")
        expiry = parsed.get('expiry', "")
        option_type = parsed.get('option_type', "")
        notes = parsed.get('notes', "")

        valid_actions = {"BUY", "SELL", "BTO", "STO", "BTC", "STC"}
        if action in valid_actions:
            log_trade(action, symbol, quantity, price, message.content, 
                     date_str, time_str, strike_price, expiry, option_type, notes)
            print(f"✅ Logged trade: {action} {quantity} {symbol} {strike_price}{option_type}")
        else:
            # Invalid action but still looks like a trade
            if looks_like_trade_message(message.content):
                log_unparsed_trade(message.content, date_str, time_str, f"Invalid action: {action}")
                print(f"⚠️ Logged unparsed trade (invalid action): {message.content}")
            else:
                log_error(f"Invalid action: {action} | Original: {message.content}")

    except (ValueError, KeyError) as e:
        # Data processing error but might still be a trade
        if looks_like_trade_message(message.content):
            log_unparsed_trade(message.content, date_str, time_str, f"Data processing error: {str(e)}")
            print(f"⚠️ Logged unparsed trade (processing error): {message.content}")
        else:
            log_error(f"Data processing error: {str(e)} | Parsed: {parsed} | Original: {message.content}")

client.run(DISCORD_TOKEN)