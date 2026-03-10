# NPM to UniFi DNS Sync

Automatically synchronizes DNS records from Nginx Proxy Manager to UniFi Network Controller.

## Features

- 🔄 Syncs DNS A records from NPM to UniFi
- 📝 State file tracking - only manages records it creates
- ⏱️ Configurable sync interval (default: 5 minutes)
- 🔒 Secure credential management via environment variables
- 🐳 Docker/LXC container support
- 📊 Comprehensive debug logging

## Requirements

- Python 3.8+
- Nginx Proxy Manager instance
- UniFi Network Controller (Cloud Key, UDM, or self-hosted)
- UniFi API key with DNS policy management permissions

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/faxd/NPMtoUnifi.git
cd NPMtoUnifi
```

### 2. Configure environment variables

Copy the example environment file and edit with your credentials:

```bash
cp .env.example .env
nano .env
```

Edit `.env` with your settings:

```env
NPM_BASE=https://npm.example.com
NPM_USER=admin@example.com
NPM_PASS=your_password

UNIFI_BASE=https://192.168.1.1
UNIFI_API_KEY=your_api_key
UNIFI_SITE_ID=your_site_uuid

STATE_FILE=npm_unifi_state.json
```

### 3. Run locally

```bash
pip install -r requirements.txt
python NPMtoUnifi.py
```

## Deployment Options

### Option 1: Docker Container (Recommended)

Build and run using Docker Compose:

```bash
docker-compose up -d
```

Or build manually:

```bash
docker build -t npm-unifi-sync .
docker run -d --name npm-unifi-sync \
  --env-file .env \
  -v $(pwd)/npm_unifi_state.json:/app/npm_unifi_state.json \
  npm-unifi-sync
```

View logs:
```bash
docker logs -f npm-unifi-sync
```

### Option 2: Proxmox LXC Container

1. **Create Ubuntu LXC container** in Proxmox:
   - Template: Ubuntu 22.04
   - RAM: 256MB
   - Storage: 2GB
   - CPU: 1 core

2. **Install dependencies** in the container:

```bash
apt update && apt install -y python3 python3-pip git
```

3. **Clone and configure**:

```bash
cd /opt
git clone https://github.com/faxd/NPMtoUnifi.git
cd NPMtoUnifi
cp .env.example .env
nano .env  # Edit with your credentials
pip3 install -r requirements.txt
```

4. **Create systemd service** `/etc/systemd/system/npm-unifi-sync.service`:

```ini
[Unit]
Description=NPM to UniFi DNS Sync
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/NPMtoUnifi
ExecStart=/usr/bin/python3 /opt/NPMtoUnifi/NPMtoUnifi.py
Restart=always
RestartSec=300

[Install]
WantedBy=multi-user.target
```

5. **Enable and start**:

```bash
systemctl daemon-reload
systemctl enable npm-unifi-sync
systemctl start npm-unifi-sync
systemctl status npm-unifi-sync
```

### Option 3: Cron Job

Add to crontab to run every 5 minutes:

```bash
crontab -e
```

Add line:
```
*/5 * * * * cd /opt/NPMtoUnifi && /usr/bin/python3 NPMtoUnifi.py >> /var/log/npm-unifi-sync.log 2>&1
```

## Configuration

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `NPM_BASE` | Nginx Proxy Manager URL | `https://npm.example.com` |
| `NPM_USER` | NPM admin username | `admin@example.com` |
| `NPM_PASS` | NPM admin password | `your_password` |
| `NPM_IP` | NPM server IP address (where clients connect) | `192.168.1.100` |
| `UNIFI_BASE` | UniFi Controller URL | `https://192.168.1.1` |
| `UNIFI_API_KEY` | UniFi API key | `your_api_key` |
| `UNIFI_SITE_ID` | UniFi Site UUID | `88f7af54-98f8-306a-...` |
| `STATE_FILE` | State tracking file path | `npm_unifi_state.json` |

### Getting UniFi Credentials

1. **API Key**: Settings → Integrations → Create API Key
2. **Site ID**:
   ```bash
   curl -k -H "X-API-Key: YOUR_KEY" \
     https://YOUR_CONTROLLER/proxy/network/integration/v1/sites
   ```

### Sync Interval

To change the sync interval, edit `docker-entrypoint.sh`:

```bash
sleep 300  # Change 300 to desired seconds (e.g., 600 = 10 minutes)
```

## How It Works

1. **Fetches** all proxy hosts from NPM
2. **Creates DNS records** pointing to your NPM server IP (not backend services)
3. **Fetches** all existing DNS policies from UniFi (with pagination)
4. **Compares** NPM records with UniFi records
5. **Creates** missing DNS A records in UniFi (all pointing to `NPM_IP`)
6. **Updates** DNS records when NPM domains change
7. **Deletes** DNS records that were removed from NPM (only script-managed records)
8. **Tracks** managed records in `npm_unifi_state.json`

**Traffic Flow**: Client → DNS lookup (gets NPM_IP) → NPM Server → Backend Service

All DNS records point to your NPM server, which then proxies traffic to backend services.

## State File

The script maintains a state file (`npm_unifi_state.json`) to track which DNS records it created. This ensures:

- ✅ Only deletes records it created
- ✅ Never touches manually created DNS records
- ✅ Survives container restarts
- ✅ Allows safe cleanup

## Troubleshooting

### Enable Debug Logging

The script has comprehensive debug logging enabled by default. Check logs for detailed output.

### Common Issues

**401 Unauthorized**
- Verify API key is correct
- Check that API key has been used at least once
- Ensure you're connecting to the correct UniFi controller IP

**400 Bad Request - "DNS policy already exists"**
- Script now handles this automatically with pagination
- Delete the state file if it becomes out of sync: `rm npm_unifi_state.json`

**SSL Warnings**
- Normal for self-signed certificates on local network
- Warnings are suppressed by default

## Security

- 🔒 Credentials stored in `.env` (excluded from git)
- 🔒 Never commit `.env` file to version control
- 🔒 Use API keys instead of username/password where possible
- 🔒 Run container with minimal privileges

## License

MIT

## Contributing

Pull requests welcome! Please open an issue first to discuss proposed changes.
