# Telegram Subscription Shop (Starter)

A Python + aiogram Telegram shop with SQLite persistence, customer catalog, order history, and a private admin panel.

## Features
- Category and product catalogue
- USD prices, duration, descriptions, and stock
- Admin-only product add/edit/hide/delete
- Admin-only category creation, rename, and delete
- Customer orders and order history
- Admin order notifications and status management
- SQLite database persisted in `data/shop.db`
- Payment flow is deliberately **TEST MODE only**

## Important payment note
This starter does not accept or verify real cryptocurrency payments and does not connect to a payment provider. Do not send funds to this bot. Before adding a live payment integration, confirm the business and payment method are lawful where you operate and select a provider that explicitly supports your country and intended use. A production integration should verify signed provider webhooks, invoice IDs, amounts, currencies, and final payment state server-side. Never trust screenshots or customer-submitted transaction hashes as sole proof.

## Requirements
- Python 3.10+
- A Telegram bot token from BotFather
- Your numeric Telegram user ID for the admin lock

## Setup on macOS
Open Terminal and run:

```bash
cd ~/telegram-subscription-shop
```

If you downloaded and extracted this project somewhere else, `cd` into that extracted folder instead.

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
nano .env
```

Replace the placeholder values:
- `BOT_TOKEN`: your Telegram bot token
- `ADMIN_ID`: your numeric Telegram user ID

Save in nano with `Ctrl+O`, press `Enter`, then `Ctrl+X`. Keep `.env private; never post it or share it.

Run:

```bash
python bot.py
```

Leave Terminal open while the bot is running. Press `Ctrl+C` to stop it.

## Admin commands
- Send `/admin` in a private chat with the bot.
- Use **Add product** to add a product with category, description, USD price, duration, and stock.
- Open **Products** to edit product fields, hide/show, or delete.
- Open **Categories** to create, rename, or delete categories.
- Open **Orders** to inspect orders and update statuses.
- The “Mark paid (TEST ONLY)” action is only a test workflow, not payment verification.

## Data and backups
The SQLite database is stored at `data/shop.db`. Back it up before changing code or moving the project.

## Production checklist
Before launch:
1. Confirm local laws and provider terms.
2. Configure a real payment provider only if permitted.
3. Add a public HTTPS webhook endpoint and verify provider signatures.
4. Add audit logs, rate limiting, backups, error monitoring, and a fulfillment workflow.
5. Replace sample product listings/prices with accurate offers and ensure you have the right to sell/fulfill them.
