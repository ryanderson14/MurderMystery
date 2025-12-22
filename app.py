from flask import Flask, render_template, redirect, url_for
import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "mystery.db"

app = Flask(__name__)

# ---------- DB helpers ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        role_tag TEXT NOT NULL,
        bio TEXT NOT NULL,
        avatar_emoji TEXT NOT NULL,
        is_alive INTEGER NOT NULL DEFAULT 1,
        suspect_score INTEGER NOT NULL DEFAULT 0,
        login_code TEXT NOT NULL UNIQUE
    )
    """)
    conn.commit()
    conn.close()

def reset_and_seed():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS characters")
    conn.commit()
    conn.close()

    init_db()

    characters = [
        # name, role_tag, bio, avatar_emoji, is_alive, suspect_score, login_code
        ("Alex Neon",      "Prom King Candidate", "Smooth talker with a perfect smile.", "😎", 1, 0, "ALEX9"),
        ("Casey Cassette", "DJ",                  "Controls the music… and the rumors.", "🎧", 1, 0, "CASEY9"),
        ("Jamie Jocks",    "Football Star",       "Popular, loud, and always in the spotlight.", "🏈", 1, 0, "JAMIE9"),
        ("Morgan Makeup",  "Makeup Artist",       "Knows everyone’s secrets backstage.", "💄", 1, 0, "MORGN9"),
        ("Riley Rebel",    "Punk Outsider",       "Doesn’t care about prom… or so they claim.", "🧷", 1, 0, "RILEY9"),
        ("Taylor Tiara",   "Prom Queen Candidate","Perfect hair, perfect outfit, perfect alibi?", "👑", 1, 0, "TAYLR9"),
        ("Sam Snapshot",   "Yearbook Photographer","Always watching. Always recording.", "📸", 1, 0, "SNAP9"),
        ("Drew Detention", "Troublemaker",        "Has beef with half the school.", "🚬", 1, 0, "DREW9"),
        ("Jordan Jetset",  "New Kid",             "Transferred mid-year. Mysterious past.", "🕶️", 1, 0, "JORD9"),
    ]

    conn = get_db()
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO characters (name, role_tag, bio, avatar_emoji, is_alive, suspect_score, login_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, characters)
    conn.commit()
    conn.close()

# ---------- Routes ----------
@app.route("/")
def home():
    return redirect(url_for("tv"))

@app.route("/tv")
def tv():
    conn = get_db()
    chars = conn.execute("SELECT * FROM characters ORDER BY id").fetchall()
    conn.close()
    return render_template("tv.html", characters=chars)

@app.route("/gm")
def gm():
    return render_template("gm.html")

@app.route("/gm/seed")
def gm_seed():
    reset_and_seed()
    return redirect(url_for("tv"))

if __name__ == "__main__":
    init_db()
    # Host on LAN so phones + TV can reach it
    app.run(host="0.0.0.0", port=5001, debug=True)