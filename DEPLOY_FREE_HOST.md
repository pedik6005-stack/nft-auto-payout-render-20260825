# Free hosting deploy

Prepared for Render free Docker worker.

## Start command
python -m app.main

## Required secrets in host dashboard
- BOT_TOKEN
- ADMIN_IDS
- DB_PATH=/app/data/bot.sqlite3
- DATA_DIR=/app/data
- LOG_DIR=/app/logs
- PROVIDER=mrkt
- PAYOUT_MODE=real
- TON_MNEMONIC
- TON_API_KEY
- PROFIT_GROUP_ID
- MRKT_SESSION_STRING

Use `render.yaml` Blueprint or create a Docker Worker manually.
