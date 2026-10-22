"""
ocpp_multi_simulator.py — OCPP 2.0.1 Multi-Station Simulator
Simulates 4 charging stations with correct slot counts simultaneously.
ST-001: 12 slots, ST-002: 2 slots, ST-003: 2 slots, ST-004: 4 slots
Usage: python ocpp_multi_simulator.py
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
import websockets

SERVER_BASE = "ws://127.0.0.1:9000/ocpp"

# ── 4 Station Profiles with correct slot counts ───────────────────────────────
STATIONS = [
    {
        "station_id":   "ST-001",
        "vendor":       "Lastica",
        "model":        "CCS2-360kW",
        "connector":    "CCS2 DC",
        "slots":        12,          # 12 connectors
        "energy_kwh":   45.5,
        "duration_min": 30.0,
        "power_kw":     360.0,
        "vehicle_id":   "VEHICLE-ALPHA-001",
    },
    {
        "station_id":   "ST-002",
        "vendor":       "Lastica",
        "model":        "DCFast-150kW",
        "connector":    "DC Fast",
        "slots":        2,           # 2 connectors
        "energy_kwh":   22.0,
        "duration_min": 20.0,
        "power_kw":     150.0,
        "vehicle_id":   "VEHICLE-BETA-002",
    },
    {
        "station_id":   "ST-003",
        "vendor":       "Lastica",
        "model":        "AC22-Type2",
        "connector":    "Type 2 AC",
        "slots":        2,           # 2 connectors
        "energy_kwh":   10.0,
        "duration_min": 60.0,
        "power_kw":     22.0,
        "vehicle_id":   "VEHICLE-GAMMA-003",
    },
    {
        "station_id":   "ST-004",
        "vendor":       "Lastica",
        "model":        "CHAdeMO-100kW",
        "connector":    "CHAdeMO",
        "slots":        4,           # 4 connectors
        "energy_kwh":   30.0,
        "duration_min": 40.0,
        "power_kw":     100.0,
        "vehicle_id":   "VEHICLE-DELTA-004",
    },
]

def ts_now():
    return datetime.now(timezone.utc).isoformat()

def make_call(action, payload):
    return json.dumps([2, str(uuid.uuid4()), action, payload])

def check_response(station_id, action, raw):
    try:
        msg = json.loads(raw)
        if msg[0] == 3:
            print(f"  [{station_id}] ✅ {action}")
            return True
        elif msg[0] == 4:
            print(f"  [{station_id}] ❌ {action} → Error: {msg}")
            return False
    except Exception as e:
        print(f"  [{station_id}] ❌ {action} → Parse error: {e}")
        return False

async def simulate_station(profile):
    sid    = profile["station_id"]
    slots  = profile["slots"]
    url    = f"{SERVER_BASE}/{sid}"

    try:
        async with websockets.connect(url, subprotocols=["ocpp2.0.1"]) as ws:
            print(f"\n🟢 [{sid}] Connected! ({slots} slots)")

            # ── 1. BootNotification ──────────────────────────────
            await ws.send(make_call("BootNotification", {
                "chargingStation": {
                    "vendorName":      profile["vendor"],
                    "model":           profile["model"],
                    "serialNumber":    sid,
                    "firmwareVersion": "2.0.1"
                },
                "reason": "PowerUp"
            }))
            check_response(sid, "BootNotification", await ws.recv())
            await asyncio.sleep(0.5)

            # ── 2. Heartbeat ─────────────────────────────────────
            await ws.send(make_call("Heartbeat", {}))
            check_response(sid, "Heartbeat", await ws.recv())
            await asyncio.sleep(0.5)

            # ── 3. StatusNotification for ALL connectors (slots) ─
            # This is the key fix — one StatusNotification per slot
            print(f"  [{sid}] Registering {slots} connectors...")
            for connector_id in range(1, slots + 1):
                await ws.send(make_call("StatusNotification", {
                    "timestamp":       ts_now(),
                    "connectorStatus": "Available",
                    "evseId":          1,
                    "connectorId":     connector_id
                }))
                check_response(sid, f"StatusNotification:C{connector_id}", await ws.recv())
                await asyncio.sleep(0.1)  # small delay between each connector

            await asyncio.sleep(0.5)

            # ── 4. Simulate charging on connector 1 only ─────────
            tx_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"

            # Authorize first (RFID card tap)
            await ws.send(make_call("Authorize", {
                "idToken": {
                    "idToken": profile["vehicle_id"],
                    "type": "Central"
                }
            }))
            check_response(sid, "Authorize", await ws.recv())
            await asyncio.sleep(0.5)

            # TransactionEvent Started
            await ws.send(make_call("TransactionEvent", {
                "eventType":     "Started",
                "timestamp":     ts_now(),
                "triggerReason": "Authorized",
                "seqNo":         0,
                "transactionInfo": {
                    "transactionId": tx_id,
                    "chargingState": "Charging"
                },
                "evse":    {"id": 1, "connectorId": 1},
                "idToken": {"idToken": profile["vehicle_id"], "type": "Central"},
                "meterValue": []
            }))
            check_response(sid, "TransactionEvent:Started", await ws.recv())

            # Update connector 1 status to Charging
            await ws.send(make_call("StatusNotification", {
                "timestamp":       ts_now(),
                "connectorStatus": "Charging",
                "evseId":          1,
                "connectorId":     1
            }))
            check_response(sid, "StatusNotification:C1→Charging", await ws.recv())
            await asyncio.sleep(2)

            # TransactionEvent Updated (mid-session meter values)
            await ws.send(make_call("TransactionEvent", {
                "eventType":     "Updated",
                "timestamp":     ts_now(),
                "triggerReason": "MeterValuePeriodic",
                "seqNo":         1,
                "transactionInfo": {
                    "transactionId": tx_id,
                    "chargingState": "Charging"
                },
                "evse": {"id": 1, "connectorId": 1},
                "meterValue": [{
                    "timestamp": ts_now(),
                    "sampledValue": [
                        {"value": profile["energy_kwh"] / 2, "measurand": "Energy.Active.Import.Register"},
                        {"value": profile["power_kw"],        "measurand": "Power.Active.Import"},
                        {"value": profile["duration_min"] / 2,"measurand": "Session.Duration"}
                    ]
                }]
            }))
            check_response(sid, "TransactionEvent:Updated", await ws.recv())
            await asyncio.sleep(2)

            # TransactionEvent Ended
            await ws.send(make_call("TransactionEvent", {
                "eventType":     "Ended",
                "timestamp":     ts_now(),
                "triggerReason": "EVDeparted",
                "seqNo":         2,
                "transactionInfo": {
                    "transactionId": tx_id,
                    "chargingState": "SuspendedEVSE",
                    "stoppedReason": "EVDisconnected"
                },
                "evse":    {"id": 1, "connectorId": 1},
                "idToken": {"idToken": profile["vehicle_id"], "type": "Central"},
                "meterValue": [{
                    "timestamp": ts_now(),
                    "sampledValue": [
                        {"value": profile["energy_kwh"],    "measurand": "Energy.Active.Import.Register"},
                        {"value": 0.0,                      "measurand": "Power.Active.Import"},
                        {"value": profile["duration_min"],  "measurand": "Session.Duration"}
                    ]
                }]
            }))
            check_response(sid, "TransactionEvent:Ended", await ws.recv())

            # Connector 1 back to Available after session
            await ws.send(make_call("StatusNotification", {
                "timestamp":       ts_now(),
                "connectorStatus": "Available",
                "evseId":          1,
                "connectorId":     1
            }))
            check_response(sid, "StatusNotification:C1→Available", await ws.recv())

            print(f"  [{sid}] 🏁 Done! {slots} slots registered, 1 session completed.")

    except ConnectionRefusedError:
        print(f"  [{sid}] ❌ Connection refused — is the server running?")
    except Exception as e:
        print(f"  [{sid}] ❌ Error: {e}")


async def main():
    print(f"\n{'='*60}")
    print(f"  OCPP 2.0.1 Multi-Station Simulator")
    print(f"  ST-001: 12 slots | ST-002: 2 slots | ST-003: 2 slots | ST-004: 4 slots")
    print(f"  Simulating all 4 stations simultaneously...")
    print(f"{'='*60}")

    await asyncio.gather(*[simulate_station(s) for s in STATIONS])

    print(f"\n{'='*60}")
    print(f"  ✅ ALL 4 STATIONS TESTED!")
    print(f"  Check all stations → http://127.0.0.1:5000/api/charger-status")
    print(f"  ST-001 → http://127.0.0.1:5000/api/charger-status/ST-001  (12 slots)")
    print(f"  ST-002 → http://127.0.0.1:5000/api/charger-status/ST-002  (2 slots)")
    print(f"  ST-003 → http://127.0.0.1:5000/api/charger-status/ST-003  (2 slots)")
    print(f"  ST-004 → http://127.0.0.1:5000/api/charger-status/ST-004  (4 slots)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())