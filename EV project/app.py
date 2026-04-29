import os
import pandas as pd
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DATASET_PATH = os.path.join(os.path.dirname(__file__), "ev_stations.xlsx")

def load_stations(city_filter=None):
    if not os.path.exists(DATASET_PATH):
        return [], f"File not found: {DATASET_PATH}"

    try:
        df = pd.read_excel(DATASET_PATH, sheet_name=0, dtype=str)
    except Exception as exc:
        return [], f"Could not open Excel file — {exc}"

    df.columns = [str(c).strip().lower() for c in df.columns]

    print("Columns found:", list(df.columns))
    print("Total rows:", len(df))

    required_cols = {"name", "city", "address", "type", "latitude", "longitude"}
    missing = required_cols - set(df.columns)
    if missing:
        return [], f"Missing columns in Excel: {', '.join(sorted(missing))}"

    df = df.dropna(subset=["name", "city", "latitude", "longitude"])

    if city_filter and city_filter.strip():
        keyword = city_filter.strip().lower()
        df = df[df["city"].str.strip().str.lower().str.contains(keyword, na=False)]

    def safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    stations = []
    for _, row in df.iterrows():
        stations.append({
            "name":      str(row.get("name", "")).strip(),
            "city":      str(row.get("city", "")).strip(),
            "address":   str(row.get("address", "")).strip(),
            "type":      str(row.get("type", "")).strip(),
            "latitude":  safe_float(row.get("latitude")),
            "longitude": safe_float(row.get("longitude")),
        })

    return stations, None


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/stations")
def stations():
    data, err = load_stations()
    if err:
        return render_template("stations.html", stations=[], db_error=err)
    return render_template("stations.html", stations=data)


@app.route("/search", methods=["POST"])
def search():
    city = request.form.get("city", "").strip()
    if not city:
        data, err = load_stations()
    else:
        data, err = load_stations(city_filter=city)
    if err:
        return render_template("stations.html", stations=[], db_error=err)
    return render_template("stations.html", stations=data)


@app.route("/about")
def about():
    return render_template("about.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
