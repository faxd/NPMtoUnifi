#!/bin/sh
set -e

echo "NPM to UniFi DNS Sync Container Starting..."
echo "Sync will run every 5 minutes"
echo ""

# Run immediately on startup
echo "[$(date)] Running initial sync..."
python /app/NPMtoUnifi.py
echo ""

# Then run every 5 minutes
while true; do
    sleep 300  # 5 minutes
    echo "[$(date)] Running scheduled sync..."
    python /app/NPMtoUnifi.py
    echo ""
done
