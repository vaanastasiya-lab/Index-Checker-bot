import asyncio
import sqlite3
import requests
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_IDS =  USER_IDS = [5295327437, 6964867018]

THRESHOLDS = {"moex": 2.0, "vtb": 3.0, "brent": 2.0, "spacex": 3.0}
NAMES = {
    "moex": "📊 Индекс МосБиржи (IMOEX)",
    "vtb": "🏦 Акции ВТБ (VTBR)",
    "brent": "🛢 Нефть Brent",
    "spacex": "🚀 Акции SpaceX (SPCX)"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DB_NAME = "prices_cache.db"

# --- ВЕБ-СЕРВЕР ДЛЯ СТАБИЛЬНОЙ РАБОТЫ НА RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args): return

def run_health_server():
    server = HTTPServer(("0.0.0.0", 10000), HealthCheckHandler)
    server.serve_forever()

# --- БАЗА ДАННЫХ (SQLite) ---
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

# --- ИСПРАВЛЕННЫЙ ШЛЮЗ КОТИРОВОК (ОФИЦИАЛЬНЫЙ ТИКЕР SPCX) ---
def fetch_price(asset_key: str) -> float:
    """Получает точные данные: РФ через XML MOEX, мир через шлюз РБК и Nasdaq (SPCX)"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        # 1. Индекс МосБиржи через открытый XML-экспорт MOEX
        if asset_key == "moex":
            url = "https://moex.com"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for row in root.findall(".//row"):
                    if row.get("CURRENTVALUE"): return float(row.get("CURRENTVALUE"))

        # 2. Акции ВТБ через открытый XML-экспорт MOEX
        elif asset_key == "vtb":
            url = "https://moex.com"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                for row in root.findall(".//row"):
                    if row.get("LAST"): return float(row.get("LAST"))

        # 3. Нефть Brent через прямой шлюз РБК-Инвестиции
        elif asset_key == "brent":
            url = "https://rbc.ru"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if "data" in data and len(data["data"]) > 0:
                    return float(data["data"].get("last_price"))
                
        # 4. Официальные акции SpaceX (SPCX) через API биржевых котировок Nasdaq / РБК-Мир
        elif asset_key == "spacex":
            url = "https://rbc.ru"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if "data" in data and len(data["data"]) > 0:
                    return float(data["data"].get("last_price"))
            
            # Резервный шлюз напрямую к Nasdaq, если на РБК задержка
            nasdaq_url = "https://nasdaq.com"
            nasdaq_headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            res_nasdaq = requests.get(nasdaq_url, headers=nasdaq_headers, timeout=10)
            if res_nasdaq.status_code == 200:
                n_data = res_nasdaq.json()
                price_str = n_data.get("data", {}).get("primaryData", {}).get("lastSalePrice", "")
                if price_str:
                    return float(price_str.replace("$", "").strip())

    except Exception as e:
        print(f"Ошибка шлюза для {asset_key}: {e}")
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
    await message.answer(f"Привет, {message.from_user.full_name}!\n\nЯ успешно запущен в облаке Render и проверяю официальные акции SpaceX и индексы РФ 24/7.")

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
            if base_price is None:
                save_price(chosen_asset, current_price)
                base_price = current_price
            percent_change = ((current_price - base_price) / base_price) * 100
            sign = "+" if percent_change > 0 else ""
            response_text += f"📉 Изменение с прошлой базы: {sign}{percent_change:.2f}%"
            await message.answer(response_text, parse_mode="HTML")
        else:
            await message.answer("❌ Сервер котировок временно не вернул данные. Попробуйте чуть позже.")
    else:
        await message.answer("⚠️ Напишите: <i>Мосбиржа, ВТБ, Нефть</i> или <i>SpaceX</i>.")

async def main():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.create_task(check_markets_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
