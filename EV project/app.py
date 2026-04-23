from flask import Flask, render_template, request
import sqlite3
import os

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'ev_data.db')


def get_connection():
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"Database not found at: {DB_PATH}. "
            "Make sure 'database/ev_data.db' exists next to app.py."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/stations')
def stations():
    try:
        conn = get_connection()
        data = conn.execute("SELECT * FROM stations").fetchall()
        conn.close()
    except Exception as e:
        return render_template('stations.html', stations=[], db_error=str(e))
    return render_template('stations.html', stations=data)


@app.route('/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        city = request.form.get('city', '').strip()
    else:
        city = request.args.get('city', '').strip()

    if not city:
        try:
            conn = get_connection()
            data = conn.execute("SELECT * FROM stations").fetchall()
            conn.close()
        except Exception as e:
            return render_template('stations.html', stations=[], db_error=str(e))
        return render_template('stations.html', stations=data, search_query='')

    city_lower = city.lower()

    try:
        conn = get_connection()
        query = "SELECT * FROM stations WHERE LOWER(TRIM(city)) LIKE ?"
        data = conn.execute(query, ('%' + city_lower + '%',)).fetchall()
        conn.close()
    except Exception as e:
        return render_template('stations.html', stations=[], db_error=str(e))

    return render_template('stations.html', stations=data, search_query=city)


@app.route('/about')
def about():
    products = []
    # Replace with real DB query if you have a products table:
    # try:
    #     conn = get_connection()
    #     products = conn.execute("SELECT * FROM products").fetchall()
    #     conn.close()
    # except Exception:
    #     products = []
    return render_template('about.html', products=products)


@app.errorhandler(404)
def not_found(e):
    return render_template('index.html'), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return render_template('stations.html', stations=[], db_error=None), 405


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
