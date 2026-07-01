# influx-mcp

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

MCP-сервер для InfluxDB 2.x, заточенный под Home Assistant long-term storage (`ha_data`-style bucket с тегом `entity_id`). Позволяет LLM (Claude и любому другому MCP-клиенту) читать историю сенсоров, искать аномалии и выполнять произвольные Flux-запросы через единый HTTP MCP-эндпоинт.

## Инструменты

| Инструмент | Описание |
|---|---|
| `list_measurements` | Список всех измерений в бакете с `entity_id` внутри каждого |
| `list_fields` | Список полей для конкретного measurement с `entity_id` внутри него |
| `get_last_value` | Последнее значение сенсора по `entity_id` |
| `get_history` | История значений сенсора за период (с опциональной агрегацией по N минут) |
| `query_flux` | Произвольный Flux-запрос к InfluxDB |
| `find_anomalies` | Аномальные значения сенсора (отклонение от среднего более чем на N сигма) |

`query_flux` даёт полный read-доступ к базе — это осознанный компромисс ради гибкости, не баг. Обязательно закройте сервер `MCP_SECRET`, если он смотрит куда-то за пределы вашей локальной сети.

## Установка

```bash
git clone <this-repo> influx-mcp && cd influx-mcp
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # заполните INFLUX_URL / INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET / MCP_SECRET
uvicorn server:app --host 0.0.0.0 --port 8000
```

Systemd-юнит — пример в [`deploy/influx-mcp.service`](deploy/influx-mcp.service) (поправьте пути под свою установку).

## Security model

- Авторизация — `Authorization: Bearer $MCP_SECRET` на каждый запрос к `/mcp`. Если `MCP_SECRET` не задан — сервер отвечает без проверки (годится только для локальной сети/VPN).
- `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token` — не полноценный OAuth-провайдер, а совместимая заглушка. На момент написания [custom-коннекторы claude.ai не поддерживают ввод статического API-ключа](https://claude.com/docs/connectors/building/authentication) — только настоящий OAuth 2.1 или полное отсутствие авторизации. Эта заглушка проходит OAuth-хендшейк UI коннектора, а реальную защиту обеспечивает Bearer-токен на `/mcp` (см. выше). Если вы подключаетесь через Claude Code CLI (`claude mcp add --header ...`) — вся эта заглушка не нужна, можно слать заголовок напрямую.
- `redirect_uri` в `/oauth/authorize` проверяется по allowlist (`claude.ai`, `anthropic.com`, `console.anthropic.com`, `localhost`) — без этого был бы open redirect.
- **Транспорт**: сервер сам не терминирует TLS — слушает голый HTTP. Если он доступен за пределами localhost/доверенной LAN (а тем более если вы подключаете его как custom-коннектор в claude.ai — там HTTPS обязателен), обязательно ставьте перед ним TLS-терминацию: Cloudflare Tunnel, Tailscale Funnel, nginx/Caddy + Let's Encrypt и т.п. Без этого Bearer-токен (`MCP_SECRET`) в заголовке `Authorization` уходит в сеть открытым текстом.

## Требования

- InfluxDB 2.x с bucket, где точки имеют тег `entity_id` (стандартная схема при экспорте из Home Assistant, например через `influxdb`-интеграцию HA).
- Python 3.11+.

## Лицензия

MIT — см. [LICENSE](LICENSE).
