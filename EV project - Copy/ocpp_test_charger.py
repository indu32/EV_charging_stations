"""
ocpp_test_charger.py  —  Simulated OCPP 2.0.1 Charger
=======================================================
Simulates a real EV charger connecting to the OCPP server.
Runs a full session cycle:
  BootNotification → Heartbeat → StatusNotification (Available)
  → TransactionEvent (Started) → MeterValues (periodic)
  → TransactionEvent (Ended) → StatusNotification (Available)

Usage:
    python ocpp_test_charger.py --station ST-001 --connectors 2
"""

import asyncio
import json
import uuid
import argparse
import logging
from datetime import datetime, timezone

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CHARGER %(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S"
)

def ts():
    return datetime.now(timezone.utc).isoformat()

def call(action, payload):
    return json.dumps([2, str(uuid.uuid4())[:8], action, payload])


async def run_charger(station_id: str, server_url: str, num_connectors: int):
    log = logging.getLogger(station_id)
    ws_url = f"{server_url}/ocpp/{station_id}"
    log.info(f"Connecting to {ws_url}")

    async with websockets.connect(
        ws_url,
        subprotocols=["ocpp2.0.1"],
        ping_interval=None
    ) as ws:
        log.info("Connected ✓")

        # ── BootNotification ──────────────────────────────────────
        await ws.send(call("BootNotification", {
            "chargingStation": {
                "serialNumber": station_id,
                "model": "Lastica-DC-60",
                "vendorName": "Lastica.EV",
                "firmwareVersion": "2.0.1-rc4"
            },
            "reason": "PowerUp"
        }))
        resp = json.loads(await ws.recv())
        log.info(f"BootNotification response: {resp[2].get('status')}")

        # ── StatusNotification: all connectors Available ──────────
        for cid in range(1, num_connectors + 1):
            await ws.send(call("StatusNotification", {
                "timestamp": ts(),
                "connectorStatus": "Available",
                "evseId": 1,
                "connectorId": cid
            }))
            await ws.recv()
        log.info(f"All {num_connectors} connector(s) → Available")

        # ── Heartbeat loop + simulated charging session ───────────
        heartbeat_count = 0
        while True:
            await asyncio.sleep(15)
            heartbeat_count += 1

            # Heartbeat
            await ws.send(call("Heartbeat", {}))
            await ws.recv()
            log.info(f"Heartbeat #{heartbeat_count}")

            # Every 3rd heartbeat, simulate a charging session on connector 1
            if heartbeat_count % 3 == 0:
                await simulate_session(ws, station_id, connector_id=1, log=log)


async def simulate_session(ws, station_id, connector_id, log):
    """Simulate a full EV charging session."""
    tx_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    vehicle_id = f"AP39AB{uuid.uuid4().hex[:4].upper()}"
    log.info(f"▶ Session START  txn={tx_id}  vehicle={vehicle_id}")

    # StatusNotification: Preparing
    await ws.send(call("StatusNotification", {
        "timestamp": ts(), "connectorStatus": "Preparing",
        "evseId": 1, "connectorId": connector_id
    }))
    await ws.recv()

    # TransactionEvent: Started
    await ws.send(call("TransactionEvent", {
        "eventType": "Started",
        "timestamp": ts(),
        "triggerReason": "Authorized",
        "seqNo": 0,
        "transactionInfo": {"transactionId": tx_id, "chargingState": "Charging"},
        "evse": {"id": 1, "connectorId": connector_id},
        "idToken": {"idToken": vehicle_id, "type": "Central"},
        "meterValue": []
    }))
    await ws.recv()

    # StatusNotification: Charging
    await ws.send(call("StatusNotification", {
        "timestamp": ts(), "connectorStatus": "Charging",
        "evseId": 1, "connectorId": connector_id
    }))
    await ws.recv()

    # Simulate 3 meter update ticks (every 5 s)
    energy = 0.0
    power_kw = 60.0
    for tick in range(1, 4):
        await asyncio.sleep(5)
        energy += power_kw * (5 / 3600)   # kWh for 5 s at 60 kW
        duration = tick * 5 / 60           # minutes

        # Periodic MeterValues
        await ws.send(call("MeterValues", {
            "evseId": 1,
            "transactionId": tx_id,
            "meterValue": [{
                "timestamp": ts(),
                "sampledValue": [
                    {"value": round(energy, 4), "measurand": "Energy.Active.Import.Register",
                     "unit": {"unit": "kWh"}, "context": "Sample.Periodic"},
                    {"value": power_kw, "measurand": "Power.Active.Import",
                     "unit": {"unit": "kW"}, "context": "Sample.Periodic"},
                    {"value": round(duration, 2), "measurand": "Session.Duration",
                     "unit": {"unit": "min"}, "context": "Sample.Periodic"},
                ]
            }]
        }))
        await ws.recv()
        log.info(f"  MeterValues tick {tick}: {energy:.4f} kWh @ {power_kw} kW")

    # TransactionEvent: Ended
    await ws.send(call("TransactionEvent", {
        "eventType": "Ended",
        "timestamp": ts(),
        "triggerReason": "EVDeparted",
        "seqNo": 1,
        "transactionInfo": {
            "transactionId": tx_id,
            "chargingState": "SuspendedEVSE",
            "stoppedReason": "EVDisconnected"
        },
        "evse": {"id": 1, "connectorId": connector_id},
        "idToken": {"idToken": vehicle_id, "type": "Central"},
        "meterValue": [{
            "timestamp": ts(),
            "sampledValue": [
                {"value": round(energy, 4), "measurand": "Energy.Active.Import.Register",
                 "unit": {"unit": "kWh"}, "context": "Transaction.End"},
                {"value": 0, "measurand": "Power.Active.Import",
                 "unit": {"unit": "kW"}, "context": "Transaction.End"},
                {"value": round(3 * 5 / 60, 2), "measurand": "Session.Duration",
                 "unit": {"unit": "min"}, "context": "Transaction.End"},
            ]
        }]
    }))
    await ws.recv()

    # StatusNotification: Available
    await ws.send(call("StatusNotification", {
        "timestamp": ts(), "connectorStatus": "Available",
        "evseId": 1, "connectorId": connector_id
    }))
    await ws.recv()
    log.info(f"■ Session END    txn={tx_id}  energy={energy:.4f} kWh")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulated OCPP 2.0.1 charger")
    parser.add_argument("--station",    default="ST-001",                 help="Station ID (must match your Excel station_id column)")
    parser.add_argument("--server",     default="ws://localhost:9000",     help="OCPP server WebSocket URL")
    parser.add_argument("--connectors", type=int, default=2,               help="Number of connectors to register")
    args = parser.parse_args()

    asyncio.run(run_charger(args.station, args.server, args.connectors))