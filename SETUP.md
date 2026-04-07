# Setup

## 1) Requirements

- Windows PC
- Python 3.10+
- Bloxstrap installed
- Dependencies installed in your Python environment

## 2) Files You Should Edit

### `.env`

Fill in these values:

- `DISCORD_BOT_TOKEN` - your Discord bot token
- `PRC_API_KEY` - the target server's PRC API key
- `LOG_CHANNEL_ID` - channel for logs
- `WEBSOCKET_SECRET` - secret shared with your backend websocket service
- `RENDER_URL` - websocket URL (example: `wss://your-service.onrender.com/ws`)

Defaults for CSRP are already present where relevant (for example `DYNO_BOT_ID` and `PRIVATE_SERVER_CODE=calf`).

### `discord_bot.py`

Change:

- `OWNER_USER_ID` to your Discord user ID

### `config.json`

Check/adjust:

- `max_accounts`
- `erlc_place_id` (CSRP is already set)
- `private_server_code` (CSRP is already set to `calf`)
- anti-AFK and performance settings to your preference/needs

## 3) Cookies

Add Roblox `.ROBLOSECURITY` cookies into `cookies.txt`:

- one cookie per line
- no extra spaces

## 4) Render Environment Variables

### If hosting `discord_bot.py` on Render

- `DISCORD_BOT_TOKEN`
- `PRC_API_KEY`
- `LOG_CHANNEL_ID`

### If hosting websocket backend (the service local agent connects to)

- `WEBSOCKET_SECRET` (must exactly match local `.env`)

## 5) Run Modes

### Option A: Local agent only

Use this if your Discord controller/backend is already hosted 24/7.

```bash
python local_agent.py
```

### Option B: Local agent + local Discord controller

Use this if you also want to run the controller process from your PC.

1. Start local agent:

```bash
python local_agent.py
```

2. Start Discord controller:

```bash
python discord_bot.py
```

### Option C: Run it locally

```bash
python main.py
```

## 6) Quick Validation

- Local agent shows websocket connected
- Accounts validate and launch from `cookies.txt`

## 7) Common Issues

- `Permission denied` in slash commands:
  - `OWNER_USER_ID` is wrong
- Websocket not connecting:
  - `RENDER_URL` or `WEBSOCKET_SECRET` mismatch
- No player count:
  - `PRC_API_KEY` missing/invalid
- Bots do not launch:
  - cookies invalid or `cookies.txt` empty
