#!/bin/bash
# Start PKA Discord bot in the background
# Run this once — it keeps running even after you close the terminal

cd "$(dirname "$0")"

# Kill any existing bot
pkill -f "bot.py" 2>/dev/null
sleep 1

# Start in background with line-buffered logs
nohup ./venv/bin/python3 -u bot.py > /tmp/pka-discord-bot.log 2>&1 &

echo "✓ PKA Discord bot started (PID: $!)"
echo "  Logs: /tmp/pka-discord-bot.log"
echo "  Stop: pkill -f 'bot.py'"
