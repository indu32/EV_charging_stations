from flask import Flask, render_template, request
import sqlite3

app = Flask(__name__)

def get_connection():
    conn = sqlite3.connect('database/ev_data.db')
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/stations')
def stations():
    conn = get_connection()
    data = conn.execute("SELECT * FROM stations").fetchall()
    conn.close()
    return render_template('stations.html', stations=data)


# ✅ SEARCH BY CITY
@app.route('/search', methods=['POST'])
def search():
    city = request.form['city']

    conn = get_connection()
    query = "SELECT * FROM stations WHERE city LIKE ?"
    data = conn.execute(query, ('%' + city + '%',)).fetchall()
    conn.close()

    return render_template('stations.html', stations=data)


@app.route('/about')
def about():
    return render_template('about.html')


if __name__ == '__main__':
    app.run(debug=True)