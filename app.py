from flask import Flask, render_template, redirect, url_for, request, session, jsonify
import sqlite3
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "mystery.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# ---------- DB helpers ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        author_id INTEGER,
        is_anonymous INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(author_id) REFERENCES characters(id)
    )
    """)
    conn.commit()
    conn.close()

def reset_and_seed():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS messages")
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
@app.before_first_request
def ensure_tables():
    init_db()

@app.route("/")
def home():
    return redirect(url_for("tv"))

@app.route("/tv")
def tv():
    conn = get_db()
    chars = conn.execute("SELECT * FROM characters ORDER BY id").fetchall()
    messages = conn.execute("""
        SELECT m.*, c.name AS author_name, c.avatar_emoji
        FROM messages m
        LEFT JOIN characters c ON m.author_id = c.id
        ORDER BY m.created_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return render_template("tv.html", characters=chars, messages=messages)

@app.route("/api/messages")
def api_messages():
    conn = get_db()
    messages = conn.execute("""
        SELECT m.*, c.name AS author_name, c.avatar_emoji
        FROM messages m
        LEFT JOIN characters c ON m.author_id = c.id
        ORDER BY m.created_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    data = []
    for m in messages:
        author = "Anonymous" if m["is_anonymous"] else (m["author_name"] or "Unknown")
        avatar = "" if m["is_anonymous"] else (m["avatar_emoji"] or "")
        data.append({
            "id": m["id"],
            "content": m["content"],
            "created_at": m["created_at"],
            "author": author,
            "avatar": avatar,
            "is_anonymous": bool(m["is_anonymous"]),
        })
    return jsonify(data)

def get_logged_in_character():
    char_id = session.get("character_id")
    if not char_id:
        return None
    conn = get_db()
    char = conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    conn.close()
    if char is None:
        session.pop("character_id", None)
    return char

@app.route("/app")
def player_app():
    character = get_logged_in_character()
    conn = get_db()
    messages = conn.execute("""
        SELECT m.*, c.name AS author_name, c.avatar_emoji
        FROM messages m
        LEFT JOIN characters c ON m.author_id = c.id
        ORDER BY m.created_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    error = request.args.get("error")
    return render_template("app.html", character=character, messages=messages, error=error)

@app.route("/app/login", methods=["POST"])
def app_login():
    code = request.form.get("code", "").strip().upper()
    if not code:
        return redirect(url_for("player_app", error="Enter your code."))

    conn = get_db()
    char = conn.execute("SELECT * FROM characters WHERE UPPER(login_code) = ?", (code,)).fetchone()
    conn.close()

    if not char:
        return redirect(url_for("player_app", error="Code not found. Check with the GM."))

    session["character_id"] = char["id"]
    return redirect(url_for("player_app"))

@app.route("/app/logout")
def app_logout():
    session.pop("character_id", None)
    return redirect(url_for("player_app"))

@app.route("/app/post", methods=["POST"])
def app_post():
    character = get_logged_in_character()
    if not character:
        return redirect(url_for("player_app", error="Log in first."))

    content = (request.form.get("content") or "").strip()
    is_anonymous = 1 if request.form.get("anonymous") == "on" else 0

    if not content:
        return redirect(url_for("player_app", error="Message cannot be empty."))
    if len(content) > 280:
        content = content[:280]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO messages (content, author_id, is_anonymous)
        VALUES (?, ?, ?)
    """, (content, character["id"], is_anonymous))
    conn.commit()
    conn.close()

    return redirect(url_for("player_app"))

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
