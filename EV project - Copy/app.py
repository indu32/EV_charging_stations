import os
import json
import uuid
import threading
from datetime import datetime, timezone
import pandas as pd
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DATASET_PATH = os.path.join(os.path.dirname(__file__), "ev_stations.xlsx")

# ── OCPP WebSocket server (background thread) ──────────────────────────────
# Import here so Flask can also query live charger state
from ocpp_ws_server import start_ocpp_server, get_station_status_summary

_ocpp_thread = threading.Thread(target=start_ocpp_server, daemon=True, name="ocpp-ws")
_ocpp_thread.start()

# ── OCPP Tariff Configuration ──────────────────────────────────────────────
TARIFF_CONFIG = {
    "tariff_id":        "LASTICA-TARIFF-001",
    "currency":         "INR",
    "price_per_kwh":    12.0,
    "price_per_minute": 0.5,
    "min_charge_kwh":   1.0,
    "tax_rate":         0.18,
    "connector_types": {
        "CCS2 DC":   {"max_kw": 360, "idle_fee_per_min": 1.0},
        "DC Fast":   {"max_kw": 150, "idle_fee_per_min": 0.75},
        "Type 2 AC": {"max_kw": 22,  "idle_fee_per_min": 0.25},
        "CHAdeMO":   {"max_kw": 100, "idle_fee_per_min": 0.5},
        "default":   {"max_kw": 50,  "idle_fee_per_min": 0.5},
    }
}

# ── OCPP 2.0.1 helpers ─────────────────────────────────────────────────────

def build_ocpp_meter_values(session_id, station_name, connector_id,
                             energy_kwh, duration_minutes, power_kw):
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "customData": {"vendorId": "Lastica.EV", "version": "2.0.1"},
        "transactionId": session_id,
        "evse": {"id": 1, "connectorId": connector_id},
        "meterValue": [
            {
                "timestamp": ts,
                "sampledValue": [
                    {
                        "value": energy_kwh,
                        "measurand": "Energy.Active.Import.Register",
                        "unit": {"multiplier": 0, "unit": "kWh"},
                        "location": "Outlet",
                        "context": "Transaction.End"
                    },
                    {
                        "value": power_kw,
                        "measurand": "Power.Active.Import",
                        "unit": {"multiplier": 0, "unit": "kW"},
                        "location": "Outlet",
                        "context": "Transaction.End"
                    },
                    {
                        "value": duration_minutes,
                        "measurand": "Session.Duration",
                        "unit": {"multiplier": 0, "unit": "min"},
                        "location": "EV",
                        "context": "Transaction.End"
                    }
                ]
            }
        ]
    }


def build_ocpp_transaction_event(session_id, station_id, station_name,
                                  vehicle_id, energy_kwh, duration_min,
                                  power_kw, connector_type):
    ts_end = datetime.now(timezone.utc).isoformat()
    return {
        "messageTypeId": 2,
        "messageId": str(uuid.uuid4()),
        "action": "TransactionEvent",
        "payload": {
            "eventType": "Ended",
            "timestamp": ts_end,
            "triggerReason": "EVDeparted",
            "seqNo": 1,
            "transactionInfo": {
                "transactionId": session_id,
                "chargingState": "SuspendedEVSE",
                "stoppedReason": "EVDisconnected"
            },
            "evse": {"id": 1, "connectorId": 1},
            "idToken": {
                "idToken": vehicle_id or "GUEST-TOKEN",
                "type": "Central"
            },
            "meterValue": build_ocpp_meter_values(
                session_id, station_name, 1,
                energy_kwh, duration_min, power_kw
            )["meterValue"],
            "customData": {
                "vendorId": "Lastica.EV",
                "stationId": station_id,
                "stationName": station_name,
                "connectorType": connector_type
            }
        }
    }


def calculate_invoice(energy_kwh, duration_min, connector_type="default"):
    cfg    = TARIFF_CONFIG
    energy = max(energy_kwh, cfg["min_charge_kwh"])
    energy_cost = round(energy * cfg["price_per_kwh"], 2)
    time_cost   = round(duration_min * cfg["price_per_minute"], 2)
    subtotal    = round(energy_cost + time_cost, 2)
    tax         = round(subtotal * cfg["tax_rate"], 2)
    total       = round(subtotal + tax, 2)
    return {
        "tariff_id":     cfg["tariff_id"],
        "currency":      cfg["currency"],
        "energy_kwh":    energy,
        "price_per_kwh": cfg["price_per_kwh"],
        "energy_cost":   energy_cost,
        "duration_min":  duration_min,
        "price_per_min": cfg["price_per_minute"],
        "time_cost":     time_cost,
        "subtotal":      subtotal,
        "tax_rate_pct":  int(cfg["tax_rate"] * 100),
        "tax":           tax,
        "total":         total,
        "connector_type": connector_type,
    }

# ── Station loader ─────────────────────────────────────────────────────────

def load_stations(city_filter=None):
    if not os.path.exists(DATASET_PATH):
        return [], f"File not found: {DATASET_PATH}"
    try:
        df = pd.read_excel(DATASET_PATH, sheet_name=0, dtype=str)
    except Exception as exc:
        return [], f"Could not open Excel file — {exc}"

    df.columns = [str(c).strip().lower() for c in df.columns]
    df["city"] = df["city"].str.strip().str.title()

    required_cols = {"name", "city", "address", "type", "latitude", "longitude"}
    missing = required_cols - set(df.columns)
    if missing:
        return [], f"Missing columns: {', '.join(sorted(missing))}"

    df = df.dropna(subset=["name", "city", "latitude", "longitude"])

    if city_filter and city_filter.strip():
        keyword = city_filter.strip().lower()
        df = df[df["city"].str.lower().str.contains(keyword, na=False)]

    def safe_float(val):
        try:    return float(val)
        except: return 0.0

    def safe_int(val, default=0):
        try:    return int(float(val))
        except: return default

    # Merge live OCPP state into station records
    live_state = get_station_status_summary()

    stations = []
    for _, row in df.iterrows():
        station_id = str(row.get("station_id", row.get("name", ""))).strip()
        live = live_state.get(station_id, {})

        # OCPP live data overrides spreadsheet values when a charger is connected
        base_slots = safe_int(row.get("slots", 0))
        total_slots = live.get("total_slots", base_slots) or base_slots
        avail_slots = live.get("available_slots", base_slots) if live else base_slots
        ocpp_status = live.get("status", str(row.get("status", "available")).strip())

        stations.append({
            "station_id":       station_id,
            "name":             str(row.get("name", "")).strip(),
            "city":             str(row.get("city", "")).strip(),
            "address":          str(row.get("address", "")).strip(),
            "type":             str(row.get("type", "")).strip(),
            "latitude":         safe_float(row.get("latitude")),
            "longitude":        safe_float(row.get("longitude")),
            "is_priority":      safe_int(row.get("is_priority", 0)),
            "image_url":        "" if str(row.get("image_url", "") or "").strip().lower() in ("nan", "none", "") else str(row.get("image_url", "")).strip().replace('\n','').replace('\r',''),
            "speed":            str(row.get("speed", "") or "").strip(),
            "slots":            base_slots,
            "total_slots":      total_slots,
            "available_slots":  avail_slots,
            "status":           ocpp_status,
            "ocpp_connected":   live.get("connected", False),
            "connectors":       live.get("connectors", {}),
        })
    return stations, None

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/stations")
def stations():
    data, err = load_stations()
    if err:
        return render_template("stations.html", stations=[], db_error=err)
    return render_template("stations.html", stations=data, searched_city="")

@app.route("/search", methods=["POST"])
def search():
    city = request.form.get("city", "").strip()
    data, err = load_stations(city_filter=city or None)
    if err:
        return render_template("stations.html", stations=[], db_error=err, searched_city=city)
    return render_template("stations.html", stations=data, searched_city=city)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/invoice")
def invoice_page():
    return render_template("invoice.html")

# ── NEW: Live charger status API ───────────────────────────────────────────

@app.route("/api/charger-status", methods=["GET"])
def charger_status():
    """Returns live OCPP state for all connected chargers."""
    return jsonify(get_station_status_summary()), 200

@app.route("/api/charger-status/<station_id>", methods=["GET"])
def charger_status_single(station_id):
    """Returns live OCPP state for a specific station."""
    summary = get_station_status_summary()
    if station_id not in summary:
        return jsonify({"error": "Station not found or not connected"}), 404
    return jsonify(summary[station_id]), 200

# ── Invoice APIs ───────────────────────────────────────────────────────────

@app.route("/api/invoice/generate", methods=["POST"])
def generate_invoice():
    body = request.get_json(force=True, silent=True) or {}
    missing = [f for f in ["energy_kwh", "duration_min"] if f not in body]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    session_id     = body.get("session_id") or f"TXN-{uuid.uuid4().hex[:12].upper()}"
    station_id     = body.get("station_id", "ST-LASTICA")
    station_name   = body.get("station_name", "Lastica EV Charging Station")
    connector_type = body.get("connector_type", "default")
    vehicle_id     = body.get("vehicle_id", "")
    energy_kwh     = float(body["energy_kwh"])
    duration_min   = float(body["duration_min"])
    power_kw       = float(body.get("power_kw", 0))

    inv          = calculate_invoice(energy_kwh, duration_min, connector_type)
    ocpp_payload = build_ocpp_transaction_event(
        session_id, station_id, station_name,
        vehicle_id, energy_kwh, duration_min, power_kw, connector_type
    )

    return jsonify({
        "invoice_number":   f"INV-{datetime.now().strftime('%Y%m%d')}-{session_id[-6:]}",
        "session_id":       session_id,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "station":          {"id": station_id, "name": station_name, "connector_type": connector_type},
        "vehicle_id":       vehicle_id,
        "invoice":          inv,
        "ocpp_transaction": ocpp_payload,
    }), 200

@app.route("/api/invoice/tariff", methods=["GET"])
def get_tariff():
    return jsonify(TARIFF_CONFIG), 200

@app.route("/invoice-modal")
def invoice_modal():
    return render_template("invoice_modal.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)  # debug=False avoids double-starting the OCPP thread