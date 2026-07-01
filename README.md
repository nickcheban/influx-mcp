# influx-mcp

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

MCP server for InfluxDB 2.x, tuned for Home Assistant long-term storage (an `ha_data`-style bucket with an `entity_id` tag). Lets any MCP client (Claude or otherwise) read sensor history, find anomalies, and run arbitrary Flux queries through a single HTTP MCP endpoint.

## Tools

| Tool | Description |
|---|---|
| `list_measurements` | List of all measurements in the bucket, with `entity_id` inside each one |
| `list_fields` | List of fields for a specific measurement, with `entity_id` inside it |
| `get_last_value` | Latest value of a sensor by `entity_id` |
| `get_history` | History of sensor values over a period (with optional aggregation over N minutes) |
| `query_flux` | Arbitrary Flux query against InfluxDB |
| `find_anomalies` | Anomalous sensor values (deviation from the mean by more than N sigma) |

`query_flux` gives full read access to the database — that's an intentional tradeoff for flexibility, not a bug. Make sure to lock the server down with `MCP_SECRET` if it's reachable from anywhere outside your local network.

## Setup

```bash
git clone <this-repo> influx-mcp && cd influx-mcp
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # fill in INFLUX_URL / INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET / MCP_SECRET
uvicorn server:app --host 0.0.0.0 --port 8000
```

Systemd unit example: [`deploy/influx-mcp.service`](deploy/influx-mcp.service) (adjust paths for your install).

**Docker:**

```bash
docker build -t influx-mcp .
docker run -p 8000:8000 --env-file .env influx-mcp
```

## Security model

- Auth is an `Authorization: Bearer $MCP_SECRET` header on every request to `/mcp`. If `MCP_SECRET` is unset, the server responds without checking auth (fine for a local network/VPN only).
- `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token` are not a real OAuth provider — just a compatible stub. As of this writing, [claude.ai custom connectors don't support pasting a static API key](https://claude.com/docs/connectors/building/authentication) — only full OAuth 2.1 or no auth at all. This stub satisfies the connector setup UI's OAuth handshake; the actual protection is the Bearer token on `/mcp` (see above). If you're connecting via Claude Code CLI (`claude mcp add --header ...`), you don't need this stub at all — just send the header directly.
- `redirect_uri` in `/oauth/authorize` is checked against an allowlist (`claude.ai`, `anthropic.com`, `console.anthropic.com`, `localhost`) — without that it would be an open redirect.
- **Transport**: the server does not terminate TLS itself — it listens on plain HTTP. If it's reachable beyond localhost/a trusted LAN (and especially if you're connecting it as a custom connector in claude.ai, where HTTPS is required), put TLS termination in front of it: Cloudflare Tunnel, Tailscale Funnel, nginx/Caddy + Let's Encrypt, etc. Without that, the Bearer token (`MCP_SECRET`) in the `Authorization` header goes out in plaintext.

## Requirements

- InfluxDB 2.x with a bucket where points carry an `entity_id` tag (the standard schema when exporting from Home Assistant, e.g. via its `influxdb` integration).
- Python 3.11+.

## License

MIT — see [LICENSE](LICENSE).
