"""
ocpp_ws_server.py  —  OCPP 2.0.1 WebSocket Server
Compatible with websockets v16 (Python 3.14 on Windows)
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OCPP] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ocpp_server")

# ── Live State Store ──────────────────────────────────────────────────────────
CHARGER_STATE: Dict[str, Any] = {}
UI_LISTENERS: set = set()
CHARGER_CONNECTIONS: Dict[str, ServerConnection] = {}


def ts_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_or_create_station(station_id: str) -> dict:
    if station_id not in CHARGER_STATE:
        CHARGER_STATE[station_id] = {
            "station_id": station_id,
            "station_name": station_id,
            "connected": False,
            "last_heartbeat": None,
            "firmware_version": None,
            "model": None,
            "vendor": None,
            "connectors": {}
        }
    return CHARGER_STATE[station_id]


def get_or_create_connector(station_id: str, connector_id: int) -> dict:
    state = get_or_create_station(station_id)
    if connector_id not in state["connectors"]:
        state["connectors"][connector_id] = {
            "status": "Unknown",
            "error_code": "NoError",
            "transaction_id": None,
            "vehicle_id": None,
            "energy_kwh": 0.0,
            "power_kw": 0.0,
            "duration_min": 0.0,
            "session_start": None,
        }
    return state["connectors"][connector_id]


# ── Broadcast ─────────────────────────────────────────────────────────────────

async def broadcast_state(station_id: str):
    global UI_LISTENERS
    if not UI_LISTENERS:
        return
    payload = json.dumps({
        "event": "state_update",
        "station_id": station_id,
        "data": CHARGER_STATE.get(station_id, {})
    })
    dead = set()
    for ws in list(UI_LISTENERS):
        try:
            await ws.send(payload)
        except Exception:
            dead.add(ws)
    UI_LISTENERS -= dead


async def broadcast_invoice_trigger(station_id: str, connector_id: int):
    global UI_LISTENERS
    if not UI_LISTENERS:
        return
    conn = CHARGER_STATE.get(station_id, {}).get("connectors", {}).get(connector_id, {})
    state = CHARGER_STATE.get(station_id, {})
    payload = json.dumps({
        "event":        "invoice_trigger",
        "station_id":   station_id,
        "station_name": state.get("station_name", station_id),
        "connector_id": connector_id,
        "vehicle_id":   conn.get("vehicle_id", ""),
        "energy_kwh":   conn.get("energy_kwh", 0.0),
        "duration_min": conn.get("duration_min", 0.0),
        "power_kw":     conn.get("power_kw", 0.0),
    })
    dead = set()
    for ws in list(UI_LISTENERS):
        try:
            await ws.send(payload)
        except Exception:
            dead.add(ws)
    UI_LISTENERS -= dead
# ── OCPP Handlers ─────────────────────────────────────────────────────────────

async def handle_boot_notification(station_id, payload):
    state = get_or_create_station(station_id)
    state["connected"] = True
    cs = payload.get("chargingStation", {})
    state["vendor"]           = cs.get("vendorName", "")
    state["model"]            = cs.get("model", "")
    state["firmware_version"] = cs.get("firmwareVersion", "")
    state["station_name"]     = cs.get("serialNumber", station_id)
    log.info(f"BootNotification from {station_id} model={state['model']}")
    return {"currentTime": ts_now(), "interval": 30, "status": "Accepted"}


async def handle_heartbeat(station_id, payload):
    state = get_or_create_station(station_id)
    state["last_heartbeat"] = ts_now()
    state["connected"] = True
    return {"currentTime": ts_now()}


async def handle_status_notification(station_id, payload):
    connector_id = payload.get("connectorId", 0)
    status       = payload.get("connectorStatus", "Unknown")
    error_code   = payload.get("errorCode", "NoError")
    evse_id      = payload.get("evseId", 1)
    conn = get_or_create_connector(station_id, connector_id)
    conn["status"]     = status
    conn["error_code"] = error_code
    log.info(f"StatusNotification {station_id}:EVSE{evse_id}:C{connector_id} -> {status}")
    return {}


async def handle_transaction_event(station_id, payload):
    event_type   = payload.get("eventType", "")
    connector_id = payload.get("evse", {}).get("connectorId", 1)
    tx_info      = payload.get("transactionInfo", {})
    tx_id        = tx_info.get("transactionId", str(uuid.uuid4()))
    id_token     = payload.get("idToken", {}).get("idToken", "")
    conn = get_or_create_connector(station_id, connector_id)

    energy_kwh = power_kw = duration_min = 0.0
    for mv in payload.get("meterValue", []):
        for sv in mv.get("sampledValue", []):
            m = sv.get("measurand", "")
            v = float(sv.get("value", 0))
            if m == "Energy.Active.Import.Register": energy_kwh = v
            elif m == "Power.Active.Import":         power_kw   = v
            elif m == "Session.Duration":            duration_min = v

    if event_type == "Started":
        conn.update({"transaction_id": tx_id, "vehicle_id": id_token,
                     "session_start": ts_now(), "status": "Charging",
                     "energy_kwh": 0.0, "power_kw": power_kw, "duration_min": 0.0})
        log.info(f"Transaction STARTED {station_id}:C{connector_id} txn={tx_id}")
    elif event_type == "Updated":
        conn.update({"energy_kwh": energy_kwh, "power_kw": power_kw,
                     "duration_min": duration_min,
                     "status": tx_info.get("chargingState", "Charging")})
        log.info(f"Transaction UPDATED {station_id}:C{connector_id} {energy_kwh:.2f} kWh")
    elif event_type == "Ended":
        conn.update({"energy_kwh": energy_kwh, "power_kw": 0.0,
                 "duration_min": duration_min, "status": "Finishing",
                 "transaction_id": tx_id, "vehicle_id": id_token,
                 "session_start": None})
        log.info(f"Transaction ENDED {station_id}:C{connector_id} {energy_kwh:.2f} kWh")
        await broadcast_invoice_trigger(station_id, connector_id)  

    return {"idTokenInfo": {"status": "Accepted"}}


async def handle_meter_values(station_id, payload):
    evse_id = payload.get("evseId", 1)
    conn = get_or_create_connector(station_id, 1)
    for mv in payload.get("meterValue", []):
        for sv in mv.get("sampledValue", []):
            m = sv.get("measurand", "")
            v = float(sv.get("value", 0))
            if m == "Energy.Active.Import.Register": conn["energy_kwh"] = v
            elif m == "Power.Active.Import":         conn["power_kw"]   = v
            elif m == "Session.Duration":            conn["duration_min"] = v
    log.info(f"MeterValues {station_id}:EVSE{evse_id} {conn['energy_kwh']:.2f} kWh")
    return {}

async def handle_authorize(station_id, payload):
    id_token = payload.get("idToken", {}).get("idToken", "")
    log.info(f"Authorize request from {station_id} token={id_token}")
    # Store the RFID on the connector so invoice can use it
    conn = get_or_create_connector(station_id, 1)
    conn["vehicle_id"] = id_token
    return {
        "idTokenInfo": {
            "status": "Accepted",
            "personalMessage": {"format": "UTF8", "content": f"Welcome! {id_token}"}
        }
    }

HANDLERS = {
    "BootNotification":   handle_boot_notification,
    "Heartbeat":          handle_heartbeat,
    "StatusNotification": handle_status_notification,
    "TransactionEvent":   handle_transaction_event,
    "MeterValues":        handle_meter_values,
    "Authorize":          handle_authorize,
}



async def process_ocpp_message(station_id: str, raw: str):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"Bad JSON from {station_id}")
        return None

    if msg[0] == 2:
        msg_id, action, payload = msg[1], msg[2], msg[3]
        handler = HANDLERS.get(action)
        if handler:
            try:
                resp = await handler(station_id, payload)
                await broadcast_state(station_id)
                return json.dumps([3, msg_id, resp])
            except Exception as exc:
                log.error(f"Handler error [{action}]: {exc}")
                return json.dumps([4, msg_id, "InternalError", str(exc), {}])
        else:
            log.warning(f"Unknown OCPP action: {action}")
            return json.dumps([4, msg_id, "NotImplemented", f"Action '{action}' not supported", {}])
    return None


# ── Connection Handlers ───────────────────────────────────────────────────────

async def charger_handler(websocket: ServerConnection, station_id: str):
    CHARGER_CONNECTIONS[station_id] = websocket
    state = get_or_create_station(station_id)
    state["connected"] = True
    log.info(f"Charger CONNECTED: {station_id}")
    await broadcast_state(station_id)
    try:
        async for raw in websocket:
            response = await process_ocpp_message(station_id, raw)
            if response:
                await websocket.send(response)
    except websockets.exceptions.ConnectionClosed as exc:
        log.info(f"Charger DISCONNECTED: {station_id} code={exc.code}")
    finally:
        CHARGER_CONNECTIONS.pop(station_id, None)
        s = CHARGER_STATE.get(station_id)
        if s:
            s["connected"] = False
        await broadcast_state(station_id)


async def ui_handler(websocket: ServerConnection):
    UI_LISTENERS.add(websocket)
    log.info(f"UI client connected (total: {len(UI_LISTENERS)})")
    try:
        await websocket.send(json.dumps({"event": "full_state", "data": CHARGER_STATE}))
        async for _ in websocket:
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        UI_LISTENERS.discard(websocket)
        log.info(f"UI client disconnected (remaining: {len(UI_LISTENERS)})")


# ── Router: called AFTER handshake succeeds ───────────────────────────────────

async def router(websocket: ServerConnection):
    path = websocket.request.path if websocket.request else "/"
    log.info(f"WS connected: {path}")

    if path.startswith("/ui"):
        await ui_handler(websocket)
    elif path.startswith("/ocpp/"):
        station_id = path.split("/ocpp/", 1)[1].rstrip("/") or "UNKNOWN"
        await charger_handler(websocket, station_id)
    else:
        log.warning(f"Unknown WS path: {path}")
        await websocket.close(1008, "Unknown path")


# ── Pre-handshake hook: decide subprotocol per path ──────────────────────────

def select_subprotocol(connection: ServerConnection, subprotocols):
    """
    Called during handshake BEFORE the connection is established.
    - /ui  (browser): no subprotocol needed -> return None
    - /ocpp/<id> (charger): accept ocpp2.0.1 if offered, else None
    This prevents NegotiationError when the browser connects without a subprotocol.
    """
    path = connection.request.path if connection.request else "/"
    if path.startswith("/ocpp/"):
        if "ocpp2.0.1" in subprotocols:
            return "ocpp2.0.1"
        log.warning(f"Charger at {path} did not advertise ocpp2.0.1, accepting anyway")
        return None
    # Browser UI path — no subprotocol
    return None


# ── REST helper for Flask ─────────────────────────────────────────────────────

def get_station_status_summary() -> dict:
    summary = {}
    for sid, state in CHARGER_STATE.items():
        connectors = state.get("connectors", {})
        total      = len(connectors)
        available  = sum(1 for c in connectors.values() if c.get("status") == "Available")
        faulted    = any(c.get("status") in ("Faulted", "Unavailable") for c in connectors.values())
        connected  = state.get("connected", False)
        summary[sid] = {
            "station_id":      sid,
            "station_name":    state.get("station_name", sid),
            "connected":       connected,
            "last_heartbeat":  state.get("last_heartbeat"),
            "total_slots":     total,
            "available_slots": available,
            "status": (
                "faulted"   if faulted   else
                "powercut"  if not connected else
                "busy"      if available == 0 and total > 0 else
                "available"
            ),
            "connectors": connectors,
        }
    return summary


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    log.info("Starting OCPP 2.0.1 WebSocket server on ws://0.0.0.0:9000")
    log.info("  Charger path : ws://<host>:9000/ocpp/<station_id>")
    log.info("  UI path      : ws://<host>:9000/ui")
    async with serve(
        router,
        "0.0.0.0",
        9000,
        select_subprotocol=select_subprotocol,   # <-- path-aware, no global enforcement
        ping_interval=20,
        ping_timeout=60,
    ):
        await asyncio.Future()   # run forever


def start_ocpp_server():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())