import asyncio
import sqlite3
import aiohttp
import os
import json
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, Update

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Ссылка на ваше будущее приложение Vercel (мы заполним её чуть позже)
VERCEL_URL = os.getenv("VERCEL_URL") 
# Список ID пользователей для автоматических уведомлений
USER_IDS = [5295327437, 6964867018]

NAMES = {
    "moex": "📊 Индекс МосБиржи (IMOEX)",
    "vtb": "🏦 Акции ВТБ (VTBR)",
    "brent": "🛢 Нефть Brent",
    "spacex": "🚀 Акции SpaceX (SPCX)"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DB_NAME = "/tmp/prices_cache.db" # В Vercel писать можно только в папку /tmp

market_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Мосбиржа"), KeyboardButton(text="🏦 ВТБ")],
        [KeyboardButton(text="🛢 Нефть"), KeyboardButton(text="🚀 SpaceX")]
    ],
    resize_keyboard=True
)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS asset_prices (asset TEXT PRIMARY KEY, price REAL)')

def get_allowed_price(asset_key: str) -> float:
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute("SELECT price FROM asset_prices WHERE asset = ?", (asset_key,)).fetchone()
        return row[0] if row else None

def save_price(asset_key: str, price: float):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('INSERT OR REPLACE INTO asset_prices (asset, price) VALUES (?, ?)', (asset_key, price))

# --- ЧИСТЫЙ МЕЖДУНАРОДНЫЙ ШЛЮЗ КОТИРОВОК ---
async def fetch_price(asset_key: str) -> float:
    tickers = {"moex": "IMOEX.ME", "vtb": "VTBR.ME", "brent": "BZ=F", "spacex": "SPCX"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    url = f"https://yahoo.com{tickers[asset_key]}?range=1d&interval=1m"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    result = data.get("chart", {}).get("result")
                    if result: return float(result[0]["meta"]["regularMarketPrice"])
    except Exception as e:
        print(f"Ошибка шлюза: {e}")
        
    base_price = get_allowed_price(asset_key)
    return base_price if base_price else 0.0

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    await message.answer(f"Привет, {message.from_user.full_name}! 👋\nЯ успешно переехал на сверхбыстрое облако Vercel! Нажмите на кнопку, чтобы проверить живую цену:", reply_markup=market_keyboard)

@dp.message(F.text)
async def send_price_on_request(message: Message):
    text = message.text.lower().strip()
    chosen_asset = None
    if "мосбиржа" in text or "imoex" in text: chosen_asset = "moex"
    elif "втб" in text or "vtb" in text: chosen_asset = "vtb"
    elif "нефть" in text or "brent" in text: chosen_asset = "brent"
    elif "spacex" in text or "спейс" in text: chosen_asset = "spacex"
    
    if chosen_asset:
        current_price = await fetch_price(chosen_asset)
        base_price = get_allowed_price(chosen_asset)
        if current_price > 0:
            if not base_price: save_price(chosen_asset, current_price); base_price = current_price
            percent_change = ((current_price - base_price) / base_price) * 100
            
            price_unit = "руб."
            if chosen_asset == "moex": price_unit = "пунктов"
            elif chosen_asset in ["brent", "spacex"]: price_unit = "$"
            
            await message.answer(
                f"📈 <b>{NAMES[chosen_asset]}</b>\n"
                f"💰 Живая цена: <b>{current_price:.2f} {price_unit}</b>\n"
                f"📉 Изменение: <b>{'+' if percent_change > 0 else ''}{percent_change:.2f}%</b>",
                parse_mode="HTML", reply_markup=market_keyboard
            )

# --- ТОЧКА ВХОДА ДЛЯ VERCEL (Сервер принимает вебхук от Telegram) ---
async def handler(request):
    init_db()
    if request.method == "POST":
        try:
            body = await request.json()
            update = Update.model_validate(body, context={"bot": bot})
            await dp.feed_update(bot, update)
        except Exception as e:
            print(f"Ошибка обработки: {e}")
    return {"statusCode": 200, "body": "ok"}

# Точка входа для Vercel Serverless WSGI
def app(environ, start_response):
    payload = environ['wsgi.input'].read(environ.get('CONTENT_LENGTH', 0))
    asyncio.run(dp.feed_update(bot, Update.model_validate(json.loads(payload), context={"bot": bot})))
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b"OK"]
