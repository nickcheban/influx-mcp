import os, json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from influxdb_client import InfluxDBClient

INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.1.10:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "ha_data")
MCP_SECRET = os.getenv("MCP_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "influx-mcp.example.com")

app = FastAPI()

def get_client():
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

TOOLS = [
    {
        "name": "list_measurements",
        "description": "List of all measurements in the bucket, with entity_id inside each one",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_fields",
        "description": "List of fields for a specific measurement, with entity_id inside it",
        "inputSchema": {
            "type": "object",
            "properties": {
                "measurement": {"type": "string", "description": "Measurement name, e.g. % or kWh"}
            },
            "required": ["measurement"]
        }
    },
    {
        "name": "get_last_value",
        "description": "Latest value of a sensor by entity_id",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Sensor entity_id, e.g. sensor.f1_battery"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "get_history",
        "description": "History of sensor values over a period. Returns the last N points from the range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Sensor entity_id"},
                "hours": {"type": "number", "description": "Depth in hours (default 24)"},
                "limit": {"type": "number", "description": "Max points (default 1000)"},
                "aggregate_minutes": {"type": "number", "description": "Aggregation window in N minutes (0 = no aggregation, default 0)"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "query_flux",
        "description": "Arbitrary Flux query against InfluxDB. Returns all tags including entity_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "flux": {"type": "string", "description": "Flux query"}
            },
            "required": ["flux"]
        }
    },
    {
        "name": "find_anomalies",
        "description": "Find anomalous sensor values (deviation from the mean by more than N sigma)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Sensor entity_id"},
                "hours": {"type": "number", "description": "Depth in hours (default 24)"},
                "sigma": {"type": "number", "description": "Threshold in sigma (default 3)"}
            },
            "required": ["entity_id"]
        }
    }
]

def check_auth(request: Request):
    if not MCP_SECRET:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {MCP_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

ALLOWED_REDIRECT_HOSTS = {"claude.ai", "anthropic.com", "console.anthropic.com"}

def validate_redirect_uri(uri: str):
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1")
    is_trusted = host in ALLOWED_REDIRECT_HOSTS or any(host.endswith("." + h) for h in ALLOWED_REDIRECT_HOSTS)
    ok = (parsed.scheme == "http" and is_local) or (parsed.scheme == "https" and (is_local or is_trusted))
    if not ok:
        raise HTTPException(status_code=400, detail=f"redirect_uri not allowed: {uri}")

def row_to_dict(row):
    """Converts an InfluxDB row to a dict, including all tags.
    Safe against Flux transformations (mean, sum, count, join, pivot, etc.)
    that can strip the standard _time/_field/_value/_measurement columns."""
    result = {}

    try:
        result["time"] = str(row.get_time())
    except Exception:
        pass

    try:
        result["field"] = row.get_field()
    except Exception:
        pass

    try:
        result["value"] = row.get_value()
    except Exception:
        pass

    result["measurement"] = row.values.get("_measurement")

    skip = {"result", "table"}
    for k, v in row.values.items():
        if k.startswith("_value") and k != "_value":
            # After join()/pivot(), value columns can be renamed to
            # _value_<suffix> (e.g. _value_battery, _value_forecast).
            # Strip the leading underscore so they aren't lost.
            result[k[1:]] = v
        elif not k.startswith("_") and k not in skip:
            result[k] = v

    return result

import re
ENTITY_RE = re.compile(r'^[\w\.\-:]+$')

def validate_entity(eid):
    if not ENTITY_RE.match(eid):
        raise ValueError(f"Invalid entity_id: {eid}")

def flux_escape(s):
    """Escapes a value for safe interpolation into a Flux string literal "...".
    Without this, arbitrary quotes/parentheses in the value would let an attacker
    break out of the literal and append their own Flux code (Flux injection)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')

def strip_domain(eid):
    """InfluxDB stores entity_id without the domain prefix (f1_battery, not sensor.f1_battery).
    If an HA-style entity_id with a dot comes in, strip the prefix before filtering in Flux."""
    if "." in eid:
        return eid.split(".", 1)[1]
    return eid

def run_tool(name, args):
    with get_client() as client:
        qa = client.query_api()

        if name == "list_measurements":
            # Single query instead of N+1: fetch all measurement+entity_id pairs at once
            flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30d)
  |> keep(columns: ["_measurement", "entity_id"])
  |> unique(column: "entity_id")
  |> limit(n: 10000)'''
            tables = qa.query(flux)
            result = {}
            for table in tables:
                for row in table.records:
                    m = row.values.get("_measurement")
                    eid = row.values.get("entity_id")
                    if m and eid:
                        if m not in result:
                            result[m] = []
                        if eid not in result[m]:
                            result[m].append(eid)
            return {"measurements": result}

        elif name == "list_fields":
            m = args["measurement"]
            m_esc = flux_escape(m)
            flux = f'import "influxdata/influxdb/schema"\nschema.measurementFieldKeys(bucket: "{INFLUX_BUCKET}", measurement: "{m_esc}")'
            tables = qa.query(flux)
            fields = [row.values["_value"] for table in tables for row in table.records]

            flux2 = f'''
import "influxdata/influxdb/schema"
schema.tagValues(
  bucket: "{INFLUX_BUCKET}",
  tag: "entity_id",
  predicate: (r) => r._measurement == "{m_esc}",
  start: -30d
)'''
            try:
                t2 = qa.query(flux2)
                entities = [row.values["_value"] for table in t2 for row in table.records]
            except Exception:
                entities = []

            return {"measurement": m, "fields": fields, "entity_ids": entities}

        elif name == "get_last_value":
            eid = args["entity_id"]
            validate_entity(eid)
            flux_eid = strip_domain(eid)
            # -365d instead of -7d, to find even rarely-updated sensors
            flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r["entity_id"] == "{flux_eid}")
  |> last()'''
            tables = qa.query(flux)
            rows = [row_to_dict(row) for table in tables for row in table.records]
            return {"entity_id": eid, "data": rows}

        elif name == "get_history":
            eid = args["entity_id"]
            validate_entity(eid)
            flux_eid = strip_domain(eid)
            hours = int(float(args.get("hours") or 24))
            limit = int(float(args.get("limit") or 1000))
            agg = int(float(args.get("aggregate_minutes") or 0))

            if agg > 0:
                flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r["entity_id"] == "{flux_eid}")
  |> filter(fn: (r) => r["_field"] == "value")
  |> group(columns: ["entity_id", "_field", "_measurement"])
  |> aggregateWindow(every: {agg}m, fn: mean, createEmpty: false)
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
  |> sort(columns: ["_time"])'''
            else:
                flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r["entity_id"] == "{flux_eid}")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
  |> sort(columns: ["_time"])'''

            tables = qa.query(flux)
            rows = [row_to_dict(row) for table in tables for row in table.records]
            return {"entity_id": eid, "hours": hours, "points": len(rows), "data": rows}

        elif name == "query_flux":
            tables = qa.query(args["flux"])
            rows = [row_to_dict(row) for table in tables for row in table.records]
            return {"rows": rows, "count": len(rows)}

        elif name == "find_anomalies":
            eid = args["entity_id"]
            validate_entity(eid)
            flux_eid = strip_domain(eid)
            hours = int(float(args.get("hours") or 24))
            sigma = float(args.get("sigma") or 3.0)

            flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r["entity_id"] == "{flux_eid}" and r["_field"] == "value")
  |> sort(columns: ["_time"])'''
            tables = qa.query(flux)
            all_rows = [row_to_dict(row) for table in tables for row in table.records]

            if len(all_rows) < 3:
                return {"entity_id": eid, "anomalies": [], "count": 0, "error": "Not enough data"}

            # Guard against non-numeric values (on/off/unavailable, etc.)
            values = []
            for r in all_rows:
                try:
                    values.append((r, float(r["value"])))
                except (TypeError, ValueError):
                    pass

            if len(values) < 3:
                return {"entity_id": eid, "anomalies": [], "count": 0, "error": "Not enough numeric values"}

            nums = [v for _, v in values]
            mean = sum(nums) / len(nums)
            stddev = (sum((v - mean) ** 2 for v in nums) / (len(nums) - 1)) ** 0.5

            if stddev == 0:
                return {"entity_id": eid, "anomalies": [], "count": 0, "mean": mean, "stddev": 0}

            anomalies = [
                {**row, "z_score": round((fval - mean) / stddev, 2)}
                for row, fval in values
                if abs(fval - mean) / stddev > sigma
            ]

            return {
                "entity_id": eid,
                "hours": hours,
                "sigma_threshold": sigma,
                "mean": round(mean, 4),
                "stddev": round(stddev, 4),
                "total_points": len(all_rows),
                "numeric_points": len(values),
                "anomalies": anomalies,
                "count": len(anomalies)
            }

        else:
            return {"error": f"Unknown tool: {name}"}


@app.get("/")
async def root():
    return {"status": "influx-mcp running", "version": "2.1.0", "org": INFLUX_ORG, "bucket": INFLUX_BUCKET}

@app.get("/mcp")
async def mcp_info(request: Request):
    check_auth(request)
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "influx-mcp", "version": "2.1.0"}
    }

@app.post("/mcp")
async def mcp_handler(request: Request):
    check_auth(request)
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    if method == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "influx-mcp", "version": "2.1.0"}
        }})
    elif method == "notifications/initialized":
        from fastapi.responses import Response
        return Response(status_code=204)
    elif method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result = run_tool(tool_name, tool_args)
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            }})
        except Exception as e:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32603,
                "message": str(e)
            }})
    else:
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601,
            "message": f"Method not found: {method}"
        }})


@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    return {
        "issuer": f"https://{DOMAIN}",
        "authorization_endpoint": f"https://{DOMAIN}/oauth/authorize",
        "token_endpoint": f"https://{DOMAIN}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"]
    }

@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    from fastapi.responses import RedirectResponse
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri required")
    validate_redirect_uri(redirect_uri)
    return RedirectResponse(url=f"{redirect_uri}?code=influx-mcp-static-code&state={state}")

@app.post("/oauth/token")
async def oauth_token(request: Request):
    form = await request.form()
    if MCP_SECRET and form.get("client_secret") != MCP_SECRET:
        raise HTTPException(status_code=401, detail="Invalid client_secret")
    return {
        "access_token": MCP_SECRET,
        "token_type": "bearer",
        "expires_in": 86400
    }
