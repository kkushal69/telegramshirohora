import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "data/shop.db")
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "test").strip().lower()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Copy .env.example to .env and add your bot token.")
if not ADMIN_ID_RAW.isdigit():
    raise RuntimeError("ADMIN_ID must be your numeric Telegram user ID.")
ADMIN_ID = int(ADMIN_ID_RAW)

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("subscription-shop")

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price_cents INTEGER NOT NULL,
            duration TEXT NOT NULL DEFAULT '1 month',
            stock INTEGER NOT NULL DEFAULT -1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            product_id INTEGER,
            product_name TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_method TEXT NOT NULL DEFAULT 'test',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL
        );
        """)
        count = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if count == 0:
            con.executemany("INSERT INTO categories(name) VALUES (?)", [
                ("AI Chatbots",), ("Coding Tools",), ("Design & Creative",),
                ("Video & Image AI",), ("Other Tools",)
            ])
        count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count == 0:
            cats = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM categories")}
            now = utcnow()
            samples = [
                (cats["AI Chatbots"], "ChatGPT subscription", "Sample listing — replace with your real offer in Admin.", 999, "1 month", -1, 1, now),
                (cats["AI Chatbots"], "Claude subscription", "Sample listing — replace with your real offer in Admin.", 999, "1 month", -1, 1, now),
                (cats["AI Chatbots"], "Perplexity subscription", "Sample listing — replace with your real offer in Admin.", 699, "1 month", -1, 1, now),
                (cats["Video & Image AI"], "Higgsfield subscription", "Sample listing — replace with your real offer in Admin.", 999, "1 month", -1, 1, now),
                (cats["AI Chatbots"], "Kimi subscription", "Sample listing — replace with your real offer in Admin.", 699, "1 month", -1, 1, now),
                (cats["Coding Tools"], "Cursor subscription", "Sample listing — replace with your real offer in Admin.", 999, "1 month", -1, 1, now),
            ]
            con.executemany("""INSERT INTO products
                (category_id,name,description,price_cents,duration,stock,active,created_at)
                VALUES (?,?,?,?,?,?,?,?)""", samples)

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def money(cents):
    return f"${cents / 100:.2f}"

def is_admin(user_id):
    return user_id == ADMIN_ID

def home_kb(admin=False):
    rows = [
        [KeyboardButton(text="🛍 Browse products"), KeyboardButton(text="📦 My orders")],
        [KeyboardButton(text="ℹ️ Help")]
    ]
    if admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add product", callback_data="adm:add_product"),
         InlineKeyboardButton(text="📋 Products", callback_data="adm:products")],
        [InlineKeyboardButton(text="🗂 Categories", callback_data="adm:categories"),
         InlineKeyboardButton(text="📦 Orders", callback_data="adm:orders")],
        [InlineKeyboardButton(text="📊 Summary", callback_data="adm:summary")]
    ])

def categories_kb():
    with db() as con:
        cats = con.execute("""
            SELECT c.id,c.name,COUNT(p.id) AS n
            FROM categories c LEFT JOIN products p ON p.category_id=c.id AND p.active=1
            GROUP BY c.id ORDER BY c.name
        """).fetchall()
    rows = [[InlineKeyboardButton(text=f"{r['name']} ({r['n']})", callback_data=f"cat:{r['id']}")] for r in cats]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def products_kb(category_id):
    with db() as con:
        products = con.execute("SELECT id,name,price_cents FROM products WHERE active=1 AND category_id=? ORDER BY name", (category_id,)).fetchall()
    rows = [[InlineKeyboardButton(text=f"{p['name']} — {money(p['price_cents'])}", callback_data=f"product:{p['id']}")] for p in products]
    rows.append([InlineKeyboardButton(text="⬅️ Categories", callback_data="shop:categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def product_detail_kb(product_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Place order", callback_data=f"order:{product_id}")],
        [InlineKeyboardButton(text="⬅️ Back to categories", callback_data="shop:categories")]
    ])

class AddProduct(StatesGroup):
    category = State()
    name = State()
    description = State()
    price = State()
    duration = State()
    stock = State()

class EditProduct(StatesGroup):
    product_id = State()
    field = State()
    value = State()

class AddCategory(StatesGroup):
    name = State()

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

async def notify_admin(text):
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        log.exception("Could not notify admin")

@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    with db() as con:
        con.execute("""INSERT INTO users(user_id,username,first_seen) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
            (user.id, user.username or "", utcnow()))
    welcome = (
        "👋 <b>Welcome to the AI Subscription Shop!</b>\n\n"
        "Browse available products, choose a subscription, and create an order.\n"
        "💵 Prices are shown in USD.\n\n"
        "⚠️ Payments are in <b>TEST MODE</b> until a lawful payment provider is configured."
    )
    await message.answer(welcome, reply_markup=home_kb(is_admin(user.id)))

@dp.message(Command("admin"))
@dp.message(F.text == "🛠 Admin panel")
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ You are not authorized to use the admin panel.")
        return
    await message.answer("🛠 <b>Admin panel</b>\nManage products, categories and orders:", reply_markup=admin_kb())

@dp.message(F.text == "🛍 Browse products")
async def browse(message: Message):
    await message.answer("🗂 <b>Choose a category:</b>", reply_markup=categories_kb())

@dp.callback_query(F.data == "shop:categories")
async def show_categories(call: CallbackQuery):
    await call.message.edit_text("🗂 <b>Choose a category:</b>", reply_markup=categories_kb())
    await call.answer()

@dp.callback_query(F.data.startswith("cat:"))
async def show_products(call: CallbackQuery):
    category_id = int(call.data.split(":")[1])
    with db() as con:
        cat = con.execute("SELECT name FROM categories WHERE id=?", (category_id,)).fetchone()
    if not cat:
        await call.answer("Category not found", show_alert=True); return
    kb = products_kb(category_id)
    if len(kb.inline_keyboard) == 1 and kb.inline_keyboard[0][0].callback_data == "shop:categories":
        await call.message.edit_text(f"📭 No products in <b>{cat['name']}</b> yet.", reply_markup=kb)
    else:
        await call.message.edit_text(f"🗂 <b>{cat['name']}</b>\nChoose a product:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("product:"))
async def show_product(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])
    with db() as con:
        p = con.execute("""SELECT p.*,c.name AS category FROM products p
            LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=? AND p.active=1""", (product_id,)).fetchone()
    if not p:
        await call.answer("Product is no longer available.", show_alert=True); return
    stock = "Available" if p["stock"] < 0 else (f"{p['stock']} left" if p["stock"] > 0 else "Out of stock")
    text = (f"✨ <b>{p['name']}</b>\n"
            f"🗂 Category: {p['category'] or 'Other'}\n"
            f"💵 Price: <b>{money(p['price_cents'])}</b>\n"
            f"⏳ Duration: {p['duration']}\n"
            f"📦 Stock: {stock}\n\n"
            f"{p['description'] or 'No description provided.'}")
    await call.message.edit_text(text, reply_markup=product_detail_kb(product_id))
    await call.answer()

@dp.callback_query(F.data.startswith("order:"))
async def create_order(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])
    u = call.from_user
    with db() as con:
        p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
        if not p or p["stock"] == 0:
            await call.answer("This product is unavailable.", show_alert=True); return
        now = utcnow()
        cur = con.execute("""INSERT INTO orders
            (user_id,username,product_id,product_name,amount_cents,status,payment_method,created_at,updated_at)
            VALUES (?,?,?,?,?,'pending','test',?,?)""",
            (u.id, u.username or "", p["id"], p["name"], p["price_cents"], now, now))
        order_id = cur.lastrowid
    text = (f"✅ <b>Order created!</b>\n\n"
            f"🧾 Order: <code>#{order_id}</code>\n"
            f"📦 Product: {p['name']}\n"
            f"💵 Total: <b>{money(p['price_cents'])}</b>\n"
            f"📌 Status: <b>PENDING</b>\n\n"
            "🧪 This bot is currently in test-payment mode. No real payment was taken.\n"
            "The admin can mark a test order paid from the Admin panel.")
    await call.message.answer(text, reply_markup=home_kb(is_admin(u.id)))
    await notify_admin(f"🛒 <b>New test order #{order_id}</b>\nProduct: {p['name']}\nAmount: {money(p['price_cents'])}\nCustomer ID: <code>{u.id}</code>\nUsername: @{u.username or 'not set'}")
    await call.answer("Order created!")

@dp.message(F.text == "📦 My orders")
async def my_orders(message: Message):
    with db() as con:
        rows = con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15", (message.from_user.id,)).fetchall()
    if not rows:
        await message.answer("You haven't placed any orders yet.")
        return
    lines = ["📦 <b>Your recent orders</b>"]
    for o in rows:
        lines.append(f"#{o['id']} · {o['product_name']} · {money(o['amount_cents'])} · <b>{o['status'].upper()}</b>")
    await message.answer("\n".join(lines))

@dp.message(F.text == "ℹ️ Help")
async def help_message(message: Message):
    await message.answer("Need help? Contact the shop admin. This bot is currently in test-payment mode; do not send cryptocurrency to it.")

# Admin callbacks
@dp.callback_query(F.data.startswith("adm:"))
async def admin_actions(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    action = call.data.split(":")[1]
    if action == "add_product":
        with db() as con:
            cats = con.execute("SELECT id,name FROM categories ORDER BY name").fetchall()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=r["name"], callback_data=f"newcat:{r['id']}")] for r in cats
        ] + [[InlineKeyboardButton(text="➕ Add category first", callback_data="adm:add_category")]])
        await call.message.answer("Choose a category for the new product:", reply_markup=kb)
    elif action == "products":
        with db() as con:
            rows = con.execute("SELECT id,name,price_cents,active FROM products ORDER BY id DESC").fetchall()
        if not rows:
            await call.message.answer("No products yet."); await call.answer(); return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{'🟢' if r['active'] else '⚪'} {r['name']} · {money(r['price_cents'])}", callback_data=f"manage:{r['id']}")]
            for r in rows
        ])
        await call.message.answer("📋 <b>Products</b> (tap one to edit):", reply_markup=kb)
    elif action == "categories":
        with db() as con:
            rows = con.execute("SELECT id,name FROM categories ORDER BY name").fetchall()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🗂 {r['name']}", callback_data=f"catmanage:{r['id']}")] for r in rows
        ] + [[InlineKeyboardButton(text="➕ Add category", callback_data="adm:add_category")]])
        await call.message.answer("🗂 <b>Categories</b>", reply_markup=kb)
    elif action == "orders":
        with db() as con:
            rows = con.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall()
        if not rows:
            await call.message.answer("No orders yet."); await call.answer(); return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"#{r['id']} {r['product_name']} · {r['status'].upper()}", callback_data=f"ordermanage:{r['id']}")]
            for r in rows
        ])
        await call.message.answer("📦 <b>Recent orders</b>:", reply_markup=kb)
    elif action == "summary":
        with db() as con:
            users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            products = con.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
            orders = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            paid = con.execute("SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE status IN ('paid','fulfilled')").fetchone()[0]
        await call.message.answer(f"📊 <b>Shop summary</b>\nUsers: {users}\nActive products: {products}\nOrders: {orders}\nRecorded paid/fulfilled total: {money(paid)}")
    await call.answer()

@dp.callback_query(F.data.startswith("newcat:"))
async def choose_new_product_category(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    cat_id = int(call.data.split(":")[1])
    with db() as con:
        cat = con.execute("SELECT id,name FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not cat:
        await call.answer("Category not found", show_alert=True); return
    await state.update_data(category_id=cat_id)
    await state.set_state(AddProduct.name)
    await call.message.answer(f"Adding product to <b>{cat['name']}</b>.\nSend the product name:")
    await call.answer()

@dp.message(AddProduct.name)
async def add_product_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(AddProduct.description)
    await message.answer("Send a short description (or send - for no description):")

@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(description="" if message.text.strip() == "-" else message.text.strip()[:1500])
    await state.set_state(AddProduct.price)
    await message.answer("Send the price in USD, e.g. 9.99 (positive number):")

@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        value = Decimal(message.text.strip())
        if value <= 0 or value > Decimal("100000"):
            raise InvalidOperation
        cents = int((value * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        await message.answer("Invalid price. Send a positive number such as 9.99.")
        return
    await state.update_data(price_cents=cents)
    await state.set_state(AddProduct.duration)
    await message.answer("Send duration, e.g. 1 month, 3 months, or lifetime:")

@dp.message(AddProduct.duration)
async def add_product_duration(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(duration=message.text.strip()[:80])
    await state.set_state(AddProduct.stock)
    await message.answer("Send stock count. Use -1 for unlimited stock, or 0 for out of stock:")

@dp.message(AddProduct.stock)
async def add_product_stock(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        stock = int(message.text.strip())
        if stock < -1: raise ValueError
    except ValueError:
        await message.answer("Enter a whole number: -1, 0, 1, 2, ...")
        return
    d = await state.get_data()
    with db() as con:
        con.execute("""INSERT INTO products
            (category_id,name,description,price_cents,duration,stock,active,created_at)
            VALUES (?,?,?,?,?,?,1,?)""",
            (d["category_id"], d["name"], d["description"], d["price_cents"], d["duration"], stock, utcnow()))
    await state.clear()
    await message.answer("✅ Product added.", reply_markup=home_kb(True))
    await message.answer("Admin panel:", reply_markup=admin_kb())

@dp.callback_query(F.data == "adm:add_category")
async def start_add_category(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    await state.set_state(AddCategory.name)
    await call.message.answer("Send the new category name:")
    await call.answer()

@dp.message(AddCategory.name)
async def save_category(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    name = message.text.strip()[:80]
    if not name:
        await message.answer("Category name cannot be empty."); return
    data = await state.get_data()
    try:
        with db() as con:
            if data.get("category_id"):
                con.execute("UPDATE categories SET name=? WHERE id=?", (name, data["category_id"]))
                action_word = "renamed"
            else:
                con.execute("INSERT INTO categories(name) VALUES (?)", (name,))
                action_word = "created"
    except sqlite3.IntegrityError:
        await message.answer("That category name already exists."); return
    await state.clear()
    await message.answer(f"✅ Category <b>{name}</b> {action_word}.", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("manage:"))
async def manage_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    pid = int(call.data.split(":")[1])
    with db() as con:
        p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        await call.answer("Not found", show_alert=True); return
    status = "Active" if p["active"] else "Hidden"
    text = (f"🧰 <b>{p['name']}</b> (ID {pid})\nPrice: {money(p['price_cents'])}\n"
            f"Duration: {p['duration']}\nStock: {p['stock']}\nStatus: {status}\n\n"
            "Choose an action:")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit name", callback_data=f"edit:{pid}:name"),
         InlineKeyboardButton(text="💵 Edit price", callback_data=f"edit:{pid}:price")],
        [InlineKeyboardButton(text="📝 Edit description", callback_data=f"edit:{pid}:description")],
        [InlineKeyboardButton(text="⏳ Edit duration", callback_data=f"edit:{pid}:duration"),
         InlineKeyboardButton(text="📦 Edit stock", callback_data=f"edit:{pid}:stock")],
        [InlineKeyboardButton(text="👁 Toggle visibility", callback_data=f"toggle:{pid}"),
         InlineKeyboardButton(text="🗑 Delete", callback_data=f"delete:{pid}")],
    ])
    await call.message.answer(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("edit:"))
async def begin_edit(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    _, pid, field = call.data.split(":")
    await state.update_data(product_id=int(pid), field=field)
    await state.set_state(EditProduct.value)
    prompts = {"name": "Send the new product name:", "price": "Send the new USD price, e.g. 12.99:",
               "description": "Send the new description (or - for none):",
               "duration": "Send the new duration:", "stock": "Send stock count (-1 for unlimited):"}
    await call.message.answer(prompts[field])
    await call.answer()

@dp.message(EditProduct.value)
async def save_edit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    d = await state.get_data()
    field, pid, value = d["field"], d["product_id"], message.text.strip()
    column = {"name": "name", "price": "price_cents", "description": "description", "duration": "duration", "stock": "stock"}[field]
    if field == "price":
        try:
            dec = Decimal(value)
            if dec <= 0: raise InvalidOperation
            value = int((dec * 100).quantize(Decimal("1")))
        except (InvalidOperation, ValueError):
            await message.answer("Invalid price. Example: 9.99"); return
    elif field == "stock":
        try:
            value = int(value)
            if value < -1: raise ValueError
        except ValueError:
            await message.answer("Enter -1 or a non-negative whole number."); return
    elif field == "description" and value == "-":
        value = ""
    else:
        value = value[:1500]
    with db() as con:
        con.execute(f"UPDATE products SET {column}=? WHERE id=?", (value, pid))
    await state.clear()
    await message.answer("✅ Product updated.", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    pid = int(call.data.split(":")[1])
    with db() as con:
        con.execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
    await call.answer("Visibility updated.")
    await manage_product_result(call, pid)

async def manage_product_result(call, pid):
    with db() as con:
        p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if p:
        await call.message.answer(f"{p['name']} is now {'active' if p['active'] else 'hidden'}.")

@dp.callback_query(F.data.startswith("delete:"))
async def delete_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    pid = int(call.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Confirm delete", callback_data=f"delconfirm:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data="adm:products")
    ]])
    await call.message.answer(f"Delete product ID {pid}? This cannot be undone.", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("delconfirm:"))
async def confirm_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    pid = int(call.data.split(":")[1])
    with db() as con:
        con.execute("DELETE FROM products WHERE id=?", (pid,))
    await call.message.answer("🗑 Product deleted.")
    await call.answer()

@dp.callback_query(F.data.startswith("catmanage:"))
async def manage_category(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    cid = int(call.data.split(":")[1])
    with db() as con:
        c = con.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        n = con.execute("SELECT COUNT(*) FROM products WHERE category_id=?", (cid,)).fetchone()[0]
    if not c:
        await call.answer("Not found", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Rename", callback_data=f"catrename:{cid}")],
        [InlineKeyboardButton(text="🗑 Delete category", callback_data=f"catdelete:{cid}")]
    ])
    await call.message.answer(f"🗂 <b>{c['name']}</b>\nProducts assigned: {n}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("catrename:"))
async def rename_category_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    cid = int(call.data.split(":")[1])
    await state.update_data(category_id=cid)
    await state.set_state(AddCategory.name)
    await call.message.answer("Send the new category name:")
    await call.answer()

@dp.callback_query(F.data.startswith("catdelete:"))
async def delete_category(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    cid = int(call.data.split(":")[1])
    with db() as con:
        con.execute("DELETE FROM categories WHERE id=?", (cid,))
    await call.message.answer("Category deleted. Its products remain but become uncategorized.")
    await call.answer()

@dp.callback_query(F.data.startswith("ordermanage:"))
async def manage_order(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    oid = int(call.data.split(":")[1])
    with db() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        await call.answer("Order not found", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Mark paid (TEST ONLY)", callback_data=f"orderstatus:{oid}:paid")],
        [InlineKeyboardButton(text="📨 Mark fulfilled", callback_data=f"orderstatus:{oid}:fulfilled")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"orderstatus:{oid}:cancelled")]
    ])
    await call.message.answer(
        f"📦 <b>Order #{oid}</b>\nProduct: {o['product_name']}\nAmount: {money(o['amount_cents'])}\n"
        f"Customer ID: <code>{o['user_id']}</code>\nUsername: @{o['username'] or 'not set'}\nStatus: <b>{o['status'].upper()}</b>",
        reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("orderstatus:"))
async def set_order_status(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Unauthorized", show_alert=True); return
    _, oid, status = call.data.split(":")
    allowed = {"paid", "fulfilled", "cancelled"}
    if status not in allowed:
        await call.answer("Invalid status", show_alert=True); return
    with db() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (int(oid),)).fetchone()
        if not o:
            await call.answer("Order not found", show_alert=True); return
        con.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?", (status, utcnow(), int(oid)))
    try:
        await bot.send_message(o["user_id"], f"📦 Update for order #{oid}: <b>{status.upper()}</b>.")
    except Exception:
        pass
    await call.message.answer(f"Order #{oid} updated to {status.upper()}.")
    await call.answer()

async def main():
    init_db()
    log.info("Starting bot in %s mode", PAYMENT_MODE.upper())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
