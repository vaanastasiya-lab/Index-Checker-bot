import asyncio
import sqlite3
import xml.etree.ElementTree as ET
import requests
import yfinance as yf
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8757758492:AAEOGGor6d9ON7gyqrk_K769OpwhcbUdteE"
USER_IDS = 

THRESHOLDS = {"moex": 2.0, "vtb": 3.0, "brent": 2.0, "spacex": 3.0}
NAMES = {
    "moex": "📊 Индекс МосБиржи (IMOEX)",
    "vtb": "🏦 Акции ВТБ (VTBR)",
    "brent": "🛢 Нефть Brent",
    "spacex": "🚀 Акции SpaceX (SPCX) [Оценка рынка]"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DB_NAME = "prices_cache.db"

# --- ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ ОБХОДА ОГРАНИЧЕНИЙ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return  # Отключаем спам-логи сервера в консоль

def run_health_server():
    server = HTTPServer(("0.0.0.0", 10000), HealthCheckHandler)
    server.serve_forever()

# --- РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS asset_prices (asset TEXT PRIMARY KEY, price REAL)')
        conn.commit()

def get_allowed_price(asset_key: str) -> float:
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT price FROM asset_prices WHERE asset = ?", (asset_key,))
        row = cursor.fetchone()
        return row if row else None

def save_price(asset_key: str, price: float):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO asset_prices (asset, price) VALUES (?, ?)', (asset_key, price))
        conn.commit()

# --- ПОЛУЧЕНИЕ КОТИРОВОК ЧЕРЕЗ XML-ШЛЮЗЫ ---
def fetch_price(asset_key: str) -> float:
    try:
        if asset_key == "moex":
            url = "https://moex.com"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for row in root.findall(".//row"):
                    if row.get("CURRENTVALUE"): return float(row.get("CURRENTVALUE"))
        elif asset_key == "vtb":
            url = "https://moex.com"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for row in root.findall(".//row"):
                    if row.get("LAST"): return float(row.get("LAST"))
        elif asset_key == "brent":
            ticker = yf.Ticker("BZ=F")
            todays_data = ticker.history(period="1d")
            if not todays_data.empty: return float(todays_data['Close'].iloc[-1])
        elif asset_key == "spacex":
            ticker = yf.Ticker("DXYZ")
            todays_data = ticker.history(period="1d")
            if not todays_data.empty: return float(todays_data['Close'].iloc[-1])
    except Exception as e:
        print(f"Сбой для {asset_key}: {e}")
    return None

# --- ФОНОВЫЙ МОНИТОРИНГ ---
async def check_markets_loop():
    while True:
        for asset, threshold in THRESHOLDS.items():
            current_price = fetch_price(asset)
            if current_price is None: continue
            base_price = get_allowed_price(asset)
            if base_price is None:
                save_price(asset, current_price)
                continue
            percent_change = ((current_price - base_price) / base_price) * 100
            if abs(percent_change) >= threshold:
                direction = "🟢 РОСТ" if percent_change > 0 else "🔴 ПАДЕНИЕ"
                sign = "+" if percent_change > 0 else ""
                message_text = (
                    f"⚠️ <b>Внимание! Сильное изменение цены!</b>\n\n"
                    f"{NAMES[asset]}\nТекущая цена: <b>{current_price}</b>\n"
                    f"Предыдущая база: {base_price}\nДвижение: {direction} ({sign}{percent_change:.2f}%)"
                )
                for user_id in USER_IDS:
                    try: await bot.send_message(chat_id=user_id, text=message_text, parse_mode="HTML")
                    except Exception as e: print(f"Ошибка отправки {user_id}: {e}")
                save_price(asset, current_price)
        await asyncio.sleep(600)

# --- ОБРАБОТЧИКИ КОМАНД И ТЕКСТА ---
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    await message.answer(f"Привет, {message.from_user.full_name}!\n\nЯ слежу за котировками в облаке 24/7.")

@dp.message(F.text)
async def send_price_on_request(message: Message):
    text = message.text.lower().strip()
    chosen_asset = None
    if "мосбиржа" in text or "imoex" in text: chosen_asset = "moex"
    elif "втб" in text or "vtb" in text: chosen_asset = "vtb"
    elif "нефть" in text or "brent" in text: chosen_asset = "brent"
    elif "spacex" in text or "спейс" in text: chosen_asset = "spacex"
    
    if chosen_asset:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        current_price = fetch_price(chosen_asset)
        base_price = get_allowed_price(chosen_asset)
        if current_price is not None:
            response_text = f"{NAMES[chosen_asset]}\n💰 Текущая цена: <b>{current_price}</b>\n"
            if base_price:
                percent_change = ((current_price - base_price) / base_price) * 100
                response_text += f"📉 Изменение с прошлой базы: {'+' if percent_change > 0 else ''}{percent_change:.2f}%"
            await message.answer(response_text, parse_mode="HTML")
        else:
            await message.answer("❌ Ошибка получения данных.")

async def main():
    init_db()
    # Запускаем наш фейковый веб-сайт в отдельном потоке
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.create_task(check_markets_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
