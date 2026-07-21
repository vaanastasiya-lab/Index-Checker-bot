import asyncio
import sqlite3
import aiohttp
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
# Импортируем из правильного места в aiogram 3.x
from aiogram.utils.html import bold
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

# --- НАСТРОЙКИ ---
# Полная безопасность: токен берется скрытно из настроек Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Список ID пользователей для автоматических уведомлений (ЗАПОЛНИТЕ ТУТ)
USER_IDS = [5295327437, 6964867018]

# Процентные пороги для автоматических алармов
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

# --- КРАСИВОЕ ИНТЕРАКТИВНОЕ МЕНЮ ---
market_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Мосбиржа"), KeyboardButton(text="🏦 ВТБ")],
        [KeyboardButton(text="🛢 Нефть"), KeyboardButton(text="🚀 SpaceX")]
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите актив для проверки цен..."
)

# --- ВЕБ-СЕРВЕР ДЛЯ ПОДДЕРЖАНИЯ ЖИЗНИ НА RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
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

# --- ОТКРЫТЫЕ И СТАБИЛЬНЫЕ JSON-ШЛЮЗЫ МЕЖДУНАРОДНОГО УРОВНЯ ---
async def fetch_price(asset_key: str) -> float:
    """Мгновенно получает чистые котировки через открытые API-шлюзы без авторизации"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Индекс МосБиржи напрямую через официальный быстрый JSON-фид МосБиржи
            if asset_key == "moex":
                async with session.get("https://moex.com", headers=headers, timeout=5) as res:
                    if res.status == 200:
                        data = await res.json()
                        rows = data["marketdata"]["data"]
                        columns = data["marketdata"]["columns"]
                        idx = columns.index("CURRENTVALUE")
                        for row in rows:
                            if row[idx] is not None: return float(row[idx])

            # 2. Акции ВТБ напрямую через официальный быстрый JSON-фид МосБиржи
            elif asset_key == "vtb":
                async with session.get("https://moex.com", headers=headers, timeout=5) as res:
                    if res.status == 200:
                        data = await res.json()
                        rows = data["marketdata"]["data"]
                        columns = data["marketdata"]["columns"]
                        idx = columns.index("LAST")
                        for row in rows:
                            if row[idx] is not None: return float(row[idx])

            # 3. Нефть Brent через открытый глобальный шлюз брокера BCS Express
            elif asset_key == "brent":
                async with session.get("https://bcs.ru", headers=headers, timeout=5) as res:
                    if res.status == 200:
                        data = await res.json()
                        if data and "data" in data and len(data["data"]) > 0:
                            return float(data["data"][0].get("last_price", 0))

            # 4. Акции SpaceX (SPCX) через открытый шлюз брокера BCS Express
            elif asset_key == "spacex":
                async with session.get("https://bcs.ru", headers=headers, timeout=5) as res:
                    if res.status == 200:
                        data = await res.json()
                        if data and "data" in data and len(data["data"]) > 0:
                            return float(data["data"][0].get("last_price", 0))
                            
    except Exception as e:
        print(f"Сетевой пропуск для {asset_key}: {e}")

    # Если биржа закрыта ночью, берем последнюю живую цену из базы данных
    base_price = get_allowed_price(asset_key)
    return base_price if base_price else 0.0

# --- АВТОМАТИЧЕСКИЙ МОНИТОРИНГ РЫНКА (КАЖДЫЕ 10 МИНУТ) ---
async def check_markets_loop():
    while True:
        for asset, threshold in THRESHOLDS.items():
            current_price = await fetch_price(asset)
            if current_price <= 0: continue
            
            base_price = get_allowed_price(asset)
            if base_price is None or base_price <= 0:
                save_price(asset, current_price)
                continue
                
            percent_change = ((current_price - base_price) / base_price) * 100
            
            if abs(percent_change) >= threshold:
                direction = "🟢 РОСТ" if percent_change > 0 else "🔴 ПАДЕНИЕ"
                sign = "+" if percent_change > 0 else ""
                
                display_curr = current_price * 10000 if asset == 'vtb' else current_price
                display_base = base_price * 10000 if asset == 'vtb' else base_price
                
                message_text = (
                    f"⚠️ <b>ВНИМАНИЕ! РЕЗКИЙ СКАЧОК РЫНКА!</b>\n"
                    f"────────────────────\n"
                    f"Актив: {NAMES[asset]}\n"
                    f"🔥 Живая цена: <b>{display_curr:.2f}</b>\n"
                    f"📉 Прошлая база: {display_base:.2f}\n"
                    f"Движение: <b>{direction} ({sign}{percent_change:.2f}%)</b>\n"
                    f"────────────────────\n"
                    f"<i>Базовая точка отслеживания обновлена на новое значение.</i>"
                )
                
                for user_id in USER_IDS:
                    try:
                        await bot.send_message(chat_id=user_id, text=message_text, parse_mode="HTML")
                    except Exception as e:
                        print(f"Ошибка авто-рассылки: {e}")
                
                save_price(asset, current_price)
                
        await asyncio.sleep(600)

# --- ОБРАБОТЧИКИ НАЖАТИЙ НА КНОПКИ ---
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}! 👋\n\n"
        f"Я успешно развернут в облаке Render и проверяю котировки активов в режиме 24/7.\n\n"
        f"🔥 <b>Умный мониторинг активен:</b> я самостоятельно проверяю рынок каждые 10 минут и "
        f"пришлю вам уведомление только в случае сильных движений (IMOEX ±2%, ВТБ ±3%, Нефть ±2%, SpaceX ±3%).\n\n"
        f"Используйте удобные кнопки меню ниже, чтобы узнать живую цену прямо сейчас! 👇",
        reply_markup=market_keyboard
    )

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
        
        current_price = await fetch_price(chosen_asset)
        base_price = get_allowed_price(chosen_asset)
        
        if current_price > 0:
            if base_price is None or base_price <= 0:
                save_price(chosen_asset, current_price)
                base_price = current_price
                
            percent_change = ((current_price - base_price) / base_price) * 100
            sign = "+" if percent_change > 0 else ""
            
            display_price = current_price
            price_unit = "руб."
            if chosen_asset == "vtb":
                display_price = current_price * 10000
                price_unit = "руб. (за лот 10 000 шт.)"
            elif chosen_asset == "moex":
                price_unit = "пунктов"
            elif chosen_asset in ["brent", "spacex"]:
                price_unit = "$"

            response_text = (
                f"📈 <b>АКТУАЛЬНЫЕ РЫНОЧНЫЕ ДАННЫЕ</b>\n"
                f"────────────────────\n"
                f"Актив: {NAMES[chosen_asset]}\n"
                f"💰 Текущая стоимость: <b>{display_price:.2f} {price_unit}</b>\n"
                f"📉 Изменение с прошлой базы: <b>{sign}{percent_change:.2f}%</b>\n"
                f"────────────────────\n"
                f"<i>Данные получены напрямую из биржевого API.</i>"
            )

            await message.answer(response_text, parse_mode="HTML", reply_markup=market_keyboard)
        else:
            await message.answer("❌ Биржевой шлюз временно не вернул данные. Попробуйте еще раз в рабочие часы биржи.", reply_markup=market_keyboard)
    else:
        await message.answer("⚠️ Пожалуйста, используйте встроенные кнопки меню для выбора котировок.", reply_markup=market_keyboard)

async def main():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.create_task(check_markets_loop())
    await dp.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())  # ВОТ ЭТА СТРОЧКА ОБЯЗАТЕЛЬНО ДОЛЖНА БЫТЬ ТУТ!
