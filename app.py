import os
import sqlite3
import time
import base64
import uuid
from pathlib import Path

from flask import Flask, render_template, redirect, url_for, request, session, jsonify, abort
from flask_socketio import SocketIO, join_room

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "mystery.db"
JUKEBOX_DIR = APP_DIR / "static" / "jukebox"
PHOTOBOOTH_DIR = APP_DIR / "static" / "photobooth"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")
last_accuse_times = {}
ACCUSE_COOLDOWN_SECONDS = 300

# ---------- DB helpers ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def ensure_characters_table(conn):
    conn.execute("""
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

def ensure_messages_table(conn):
    existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()
    if not existing:
        conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            sender_id INTEGER,
            recipient_id INTEGER,
            body TEXT NOT NULL,
            is_anonymous INTEGER NOT NULL DEFAULT 0,
            is_read INTEGER NOT NULL DEFAULT 0,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(sender_id) REFERENCES characters(id),
            FOREIGN KEY(recipient_id) REFERENCES characters(id)
        )
        """)
        return

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    required = {"id", "type", "sender_id", "recipient_id", "body", "is_anonymous", "is_read", "ts"}
    if required.issubset(cols):
        return

    conn.execute("ALTER TABLE messages RENAME TO messages_old")
    conn.execute("""
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        sender_id INTEGER,
        recipient_id INTEGER,
        body TEXT NOT NULL,
        is_anonymous INTEGER NOT NULL DEFAULT 0,
        is_read INTEGER NOT NULL DEFAULT 0,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(sender_id) REFERENCES characters(id),
        FOREIGN KEY(recipient_id) REFERENCES characters(id)
    )
    """)

    if "body" in cols:
        if "is_read" in cols:
            conn.execute("""
            INSERT INTO messages (id, type, sender_id, recipient_id, body, is_anonymous, is_read, ts)
            SELECT id, type, sender_id, recipient_id, body, is_anonymous, is_read, COALESCE(ts, CURRENT_TIMESTAMP)
            FROM messages_old
            """)
        else:
            conn.execute("""
            INSERT INTO messages (id, type, sender_id, recipient_id, body, is_anonymous, is_read, ts)
            SELECT id, type, sender_id, recipient_id, body, is_anonymous, 1, COALESCE(ts, CURRENT_TIMESTAMP)
            FROM messages_old
            """)
    elif "content" in cols:
        conn.execute("""
        INSERT INTO messages (id, type, sender_id, recipient_id, body, is_anonymous, is_read, ts)
        SELECT id, 'public', author_id, NULL, content, is_anonymous, 1, COALESCE(created_at, CURRENT_TIMESTAMP)
        FROM messages_old
        """)
    conn.execute("DROP TABLE messages_old")

def ensure_accusations_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS accusations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        accuser_id INTEGER NOT NULL,
        accused_id INTEGER NOT NULL,
        points INTEGER NOT NULL DEFAULT 1,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(accuser_id) REFERENCES characters(id),
        FOREIGN KEY(accused_id) REFERENCES characters(id)
    )
    """)

def ensure_jukebox_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS jukebox_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_filename TEXT NOT NULL,
        song_title TEXT NOT NULL,
        song_artist TEXT NOT NULL,
        requester_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        started_at DATETIME,
        ended_at DATETIME,
        FOREIGN KEY(requester_id) REFERENCES characters(id)
    )
    """)

def ensure_photobooth_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS photostrips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        img1 TEXT NOT NULL,
        img2 TEXT NOT NULL,
        img3 TEXT NOT NULL,
        img4 TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

def init_db():
    conn = get_db()
    ensure_characters_table(conn)
    ensure_messages_table(conn)
    ensure_accusations_table(conn)
    ensure_jukebox_table(conn)
    ensure_photobooth_table(conn)
    conn.commit()
    conn.close()

def reset_and_seed():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS messages")
    cur.execute("DROP TABLE IF EXISTS accusations")
    cur.execute("DROP TABLE IF EXISTS jukebox_queue")
    # photostrips are preserved on reset
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

# ---------- Helpers ----------
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

def fetch_public_messages(limit=50):
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*, c.name AS sender_name, c.avatar_emoji
        FROM messages m
        LEFT JOIN characters c ON m.sender_id = c.id
        WHERE m.type = 'public'
        ORDER BY m.ts DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows

def serialize_public_message(row):
    author = "Anonymous" if row["is_anonymous"] else (row["sender_name"] or "Unknown")
    avatar = "" if row["is_anonymous"] else (row["avatar_emoji"] or "")
    return {
        "id": row["id"],
        "body": row["body"],
        "ts": row["ts"],
        "author": author,
        "avatar": avatar,
        "is_anonymous": bool(row["is_anonymous"]),
    }

def get_song_catalog():
    if not JUKEBOX_DIR.exists():
        return []
    songs = []
    for path in JUKEBOX_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".mp3", ".wav", ".ogg"}:
            continue
        stem = path.stem
        if " - " in stem:
            artist, title = stem.split(" - ", 1)
        else:
            artist = "Unknown"
            title = stem
        songs.append({
            "filename": path.name,
            "title": title.strip(),
            "artist": artist.strip(),
        })
    songs.sort(key=lambda s: (s["artist"].lower(), s["title"].lower()))
    return songs

def get_current_playing(conn):
    return conn.execute("""
        SELECT q.*, c.name AS requester_name
        FROM jukebox_queue q
        LEFT JOIN characters c ON q.requester_id = c.id
        WHERE q.status = 'playing'
        ORDER BY q.started_at DESC, q.id DESC
        LIMIT 1
    """).fetchone()

def ensure_now_playing(conn):
    current = get_current_playing(conn)
    if current:
        return current
    next_row = conn.execute("""
        SELECT *
        FROM jukebox_queue
        WHERE status = 'queued'
        ORDER BY requested_at ASC, id ASC
        LIMIT 1
    """).fetchone()
    if not next_row:
        return None
    conn.execute("""
        UPDATE jukebox_queue
        SET status = 'playing', started_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (next_row["id"],))
    conn.commit()
    return get_current_playing(conn)

def serialize_now_playing(row):
    return {
        "queue_id": row["id"],
        "filename": row["song_filename"],
        "title": row["song_title"],
        "artist": row["song_artist"],
        "requester": row["requester_name"] or "Unknown",
    }

def get_up_next(conn, limit=2):
    rows = conn.execute("""
        SELECT q.*, c.name AS requester_name
        FROM jukebox_queue q
        LEFT JOIN characters c ON q.requester_id = c.id
        WHERE q.status = 'queued'
        ORDER BY q.requested_at ASC, q.id ASC
        LIMIT ?
    """, (limit,)).fetchall()
    return rows

def serialize_queue_row(row):
    return {
        "queue_id": row["id"],
        "title": row["song_title"],
        "artist": row["song_artist"],
        "requester": row["requester_name"] or "Unknown",
    }

def get_photostrips(limit=12):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM photostrips
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    strips = []
    for row in rows:
        strips.append({
            "id": row["id"],
            "images": [
                f"/static/photobooth/{row['img1']}",
                f"/static/photobooth/{row['img2']}",
                f"/static/photobooth/{row['img3']}",
                f"/static/photobooth/{row['img4']}",
            ],
            "created_at": row["created_at"],
        })
    return strips

def save_photostrip(images):
    PHOTOBOOTH_DIR.mkdir(parents=True, exist_ok=True)
    filenames = []
    for idx, data_url in enumerate(images):
        if not data_url:
            raise ValueError("Missing image data")
        if "," in data_url:
            _, payload = data_url.split(",", 1)
        else:
            payload = data_url
        binary = base64.b64decode(payload)
        filename = f"{uuid.uuid4().hex}_{idx+1}.jpg"
        with open(PHOTOBOOTH_DIR / filename, "wb") as f:
            f.write(binary)
        filenames.append(filename)
    conn = get_db()
    conn.execute("""
        INSERT INTO photostrips (img1, img2, img3, img4)
        VALUES (?, ?, ?, ?)
    """, (filenames[0], filenames[1], filenames[2], filenames[3]))
    conn.commit()
    strip_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return {
        "id": strip_id,
        "images": [f"/static/photobooth/{name}" for name in filenames],
    }

def build_dm_threads(conn, user_id, characters):
    threads = []
    for c in characters:
        if c["id"] == user_id:
            continue
        last = conn.execute("""
            SELECT id, body, ts, sender_id
            FROM messages
            WHERE type = 'dm' AND (
                (sender_id = ? AND recipient_id = ?) OR
                (sender_id = ? AND recipient_id = ?)
            )
            ORDER BY ts DESC, id DESC
            LIMIT 1
        """, (user_id, c["id"], c["id"], user_id)).fetchone()
        unread = conn.execute("""
            SELECT COUNT(*) AS cnt
            FROM messages
            WHERE type = 'dm' AND sender_id = ? AND recipient_id = ? AND is_read = 0
        """, (c["id"], user_id)).fetchone()["cnt"]

        threads.append({
            "other_id": c["id"],
            "name": c["name"],
            "avatar_emoji": c["avatar_emoji"],
            "role_tag": c["role_tag"],
            "last_id": last["id"] if last else None,
            "last_body": last["body"] if last else None,
            "last_ts": last["ts"] if last else None,
            "last_sender_id": last["sender_id"] if last else None,
            "unread_count": unread,
        })

    threads.sort(key=lambda t: t["last_ts"] or "", reverse=True)
    return threads

def fetch_thread_messages(user_id, other_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*, s.name AS sender_name, s.avatar_emoji AS sender_avatar
        FROM messages m
        LEFT JOIN characters s ON m.sender_id = s.id
        WHERE m.type = 'dm' AND (
            (m.sender_id = ? AND m.recipient_id = ?) OR
            (m.sender_id = ? AND m.recipient_id = ?)
        )
        ORDER BY m.ts ASC, m.id ASC
    """, (user_id, other_id, other_id, user_id)).fetchall()
    conn.close()
    return rows

def mark_thread_read(user_id, other_id):
    conn = get_db()
    conn.execute("""
        UPDATE messages
        SET is_read = 1
        WHERE type = 'dm' AND recipient_id = ? AND sender_id = ? AND is_read = 0
    """, (user_id, other_id))
    conn.commit()
    conn.close()

# ---------- Routes ----------
_db_initialized = False

@app.before_request
def ensure_tables():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


@app.route("/")
def home():
    return redirect(url_for("tv"))

@app.route("/tv")
def tv():
    conn = get_db()
    chars = conn.execute("SELECT * FROM characters ORDER BY suspect_score DESC, id ASC").fetchall()
    conn.close()
    messages = fetch_public_messages()
    return render_template("tv.html", characters=chars, messages=messages)

@app.route("/api/messages")
def api_messages():
    messages = fetch_public_messages()
    data = [serialize_public_message(m) for m in messages]
    return jsonify(data)

@app.route("/api/jukebox/now")
def api_jukebox_now():
    conn = get_db()
    now_playing = ensure_now_playing(conn)
    conn.close()
    if not now_playing:
        return jsonify({})
    return jsonify(serialize_now_playing(now_playing))

@app.route("/api/jukebox/queue")
def api_jukebox_queue():
    conn = get_db()
    rows = get_up_next(conn, limit=2)
    conn.close()
    return jsonify([serialize_queue_row(r) for r in rows])

@app.route("/api/photobooth/strips")
def api_photobooth_strips():
    return jsonify(get_photostrips())

@app.route("/api/photobooth/upload", methods=["POST"])
def api_photobooth_upload():
    data = request.get_json(silent=True) or {}
    images = data.get("images") or []
    if len(images) != 4:
        return jsonify({"error": "Expected 4 images"}), 400
    try:
        strip = save_photostrip(images)
    except Exception:
        return jsonify({"error": "Failed to save images"}), 400
    socketio.emit("photobooth_new", strip)
    return jsonify(strip)

@app.route("/api/thread/<int:other_id>")
def api_thread(other_id):
    character = get_logged_in_character()
    if not character:
        abort(401)
    if other_id == character["id"]:
        abort(400)
    mark_thread_read(character["id"], other_id)
    rows = fetch_thread_messages(character["id"], other_id)
    data = []
    for r in rows:
        data.append({
            "id": r["id"],
            "body": r["body"],
            "ts": r["ts"],
            "sender_id": r["sender_id"],
            "sender_name": r["sender_name"],
            "sender_avatar": r["sender_avatar"],
        })
    return jsonify(data)

@app.route("/api/thread/<int:other_id>/read", methods=["POST"])
def api_thread_read(other_id):
    character = get_logged_in_character()
    if not character:
        abort(401)
    if other_id == character["id"]:
        abort(400)
    mark_thread_read(character["id"], other_id)
    return jsonify({"ok": True})

@app.route("/app")
def player_app():
    character = get_logged_in_character()
    conn = get_db()
    characters = conn.execute("SELECT * FROM characters ORDER BY id").fetchall()
    dm_threads = []
    if character:
        dm_threads = build_dm_threads(conn, character["id"], characters)
    conn.close()
    public_messages = fetch_public_messages()
    songs = get_song_catalog()
    selected_dm = request.args.get("dm", type=int)
    error = request.args.get("error")
    tab = request.args.get("tab") or "feed"
    cooldown_remaining = 0
    if character:
        now = time.time()
        last_session = session.get("last_accuse_ts", 0)
        last_memory = last_accuse_times.get(character["id"], 0)
        last = max(last_session, last_memory)
        if last > last_session:
            session["last_accuse_ts"] = last
        remaining = ACCUSE_COOLDOWN_SECONDS - (now - last)
        cooldown_remaining = int(remaining) if remaining > 0 else 0

    if character and selected_dm is None:
        if dm_threads:
            selected_dm = dm_threads[0]["other_id"]
        else:
            for c in characters:
                if c["id"] != character["id"]:
                    selected_dm = c["id"]
                    break

    thread_messages = []
    if character and selected_dm and tab == "dm":
        mark_thread_read(character["id"], selected_dm)
        thread_messages = fetch_thread_messages(character["id"], selected_dm)
        for thread in dm_threads:
            if thread["other_id"] == selected_dm:
                thread["unread_count"] = 0
                break
    dm_unread_total = sum(t["unread_count"] for t in dm_threads) if character else 0

    selected_dm_name = None
    selected_dm_role = None
    selected_dm_avatar = None
    if character and selected_dm:
        for c in characters:
            if c["id"] == selected_dm:
                selected_dm_name = c["name"]
                selected_dm_role = c["role_tag"]
                selected_dm_avatar = c["avatar_emoji"]
                break

    return render_template(
        "app.html",
        character=character,
        characters=characters,
        messages=public_messages,
        error=error,
        selected_dm=selected_dm,
        thread_messages=thread_messages,
        tab=tab,
        dm_threads=dm_threads,
        dm_unread_total=dm_unread_total,
        selected_dm_name=selected_dm_name,
        selected_dm_role=selected_dm_role,
        selected_dm_avatar=selected_dm_avatar,
        cooldown_remaining=cooldown_remaining,
        songs=songs,
    )

@app.route("/photobooth")
def photobooth():
    return render_template("photobooth.html")

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
        INSERT INTO messages (type, sender_id, recipient_id, body, is_anonymous, is_read)
        VALUES ('public', ?, NULL, ?, ?, 1)
    """, (character["id"], content, is_anonymous))
    new_id = cur.lastrowid
    conn.commit()
    row = conn.execute("""
        SELECT m.*, c.name AS sender_name, c.avatar_emoji
        FROM messages m
        LEFT JOIN characters c ON m.sender_id = c.id
        WHERE m.id = ?
    """, (new_id,)).fetchone()
    conn.close()

    payload = serialize_public_message(row)
    socketio.emit("public_message", payload)

    return redirect(url_for("player_app"))

@app.route("/app/jukebox/queue", methods=["POST"])
def app_jukebox_queue():
    character = get_logged_in_character()
    if not character:
        return redirect(url_for("player_app", error="Log in to queue songs.", tab="jukebox"))

    filename = (request.form.get("song_filename") or "").strip()
    if not filename:
        return redirect(url_for("player_app", error="Pick a song to queue.", tab="jukebox"))

    songs = get_song_catalog()
    selected = next((s for s in songs if s["filename"] == filename), None)
    if not selected:
        return redirect(url_for("player_app", error="Song not found.", tab="jukebox"))

    conn = get_db()
    current = get_current_playing(conn)
    conn.execute("""
        INSERT INTO jukebox_queue (song_filename, song_title, song_artist, requester_id, status)
        VALUES (?, ?, ?, ?, 'queued')
    """, (selected["filename"], selected["title"], selected["artist"], character["id"]))
    conn.commit()
    now_playing = None
    if not current:
        now_playing = ensure_now_playing(conn)
    queue_rows = get_up_next(conn, limit=2)
    conn.close()

    if now_playing:
        socketio.emit("jukebox_now", serialize_now_playing(now_playing))
    socketio.emit("jukebox_queue", [serialize_queue_row(r) for r in queue_rows])
    return redirect(url_for("player_app", tab="jukebox"))

@app.route("/app/dm", methods=["POST"])
def app_dm():
    character = get_logged_in_character()
    if not character:
        return redirect(url_for("player_app", error="Log in first."))

    recipient_id = request.form.get("recipient_id", type=int)
    body = (request.form.get("body") or "").strip()

    if not recipient_id:
        return redirect(url_for("player_app", error="Choose someone to DM.", tab="dm"))
    if recipient_id == character["id"]:
        return redirect(url_for("player_app", error="You cannot DM yourself.", dm=recipient_id, tab="dm"))
    if not body:
        return redirect(url_for("player_app", error="Message cannot be empty.", dm=recipient_id, tab="dm"))
    if len(body) > 280:
        body = body[:280]

    conn = get_db()
    target = conn.execute("SELECT id FROM characters WHERE id = ?", (recipient_id,)).fetchone()
    if not target:
        conn.close()
        return redirect(url_for("player_app", error="Recipient not found.", tab="dm"))
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO messages (type, sender_id, recipient_id, body, is_anonymous, is_read)
        VALUES ('dm', ?, ?, ?, 0, 0)
    """, (character["id"], recipient_id, body))
    new_id = cur.lastrowid
    conn.commit()
    row = conn.execute("""
        SELECT m.*, s.name AS sender_name, s.avatar_emoji AS sender_avatar
        FROM messages m
        LEFT JOIN characters s ON m.sender_id = s.id
        WHERE m.id = ?
    """, (new_id,)).fetchone()
    conn.close()

    payload = {
        "id": row["id"],
        "body": row["body"],
        "ts": row["ts"],
        "sender_id": row["sender_id"],
        "sender_name": row["sender_name"],
        "sender_avatar": row["sender_avatar"],
        "recipient_id": recipient_id,
    }
    room_sender = f"char-{character['id']}"
    room_recipient = f"char-{recipient_id}"
    socketio.emit("dm", payload, room=room_sender)
    if room_recipient != room_sender:
        socketio.emit("dm", payload, room=room_recipient)

    return redirect(url_for("player_app", dm=recipient_id, tab="dm"))

@app.route("/app/accuse", methods=["POST"])
def app_accuse():
    character = get_logged_in_character()
    if not character:
        return redirect(url_for("player_app", error="Log in first."))

    accused_id = request.form.get("accused_id", type=int)
    if not accused_id:
        return redirect(url_for("player_app", error="Pick someone to accuse.", tab="suspect"))
    if accused_id == character["id"]:
        return redirect(url_for("player_app", error="You cannot accuse yourself.", tab="suspect"))

    now = time.time()
    last_session = session.get("last_accuse_ts", 0)
    last_memory = last_accuse_times.get(character["id"], 0)
    last = max(last_session, last_memory)
    if now - last < ACCUSE_COOLDOWN_SECONDS:
        remaining = int(ACCUSE_COOLDOWN_SECONDS - (now - last))
        session["last_accuse_ts"] = last
        return redirect(url_for("player_app", error=f"Wait {remaining//60}:{remaining%60:02d} before accusing again.", tab="suspect"))
    last_accuse_times[character["id"]] = now
    session["last_accuse_ts"] = now

    conn = get_db()
    exists = conn.execute("SELECT id FROM characters WHERE id = ?", (accused_id,)).fetchone()
    if not exists:
        conn.close()
        return redirect(url_for("player_app", error="That character doesn't exist.", tab="suspect"))
    cur = conn.cursor()
    cur.execute("INSERT INTO accusations (accuser_id, accused_id, points) VALUES (?, ?, 1)", (character["id"], accused_id))
    cur.execute("UPDATE characters SET suspect_score = suspect_score + 1 WHERE id = ?", (accused_id,))
    new_score = conn.execute("SELECT suspect_score FROM characters WHERE id = ?", (accused_id,)).fetchone()["suspect_score"]
    conn.commit()
    conn.close()

    socketio.emit("suspect_update", {"character_id": accused_id, "suspect_score": new_score})
    return redirect(url_for("player_app", tab="suspect"))

@app.route("/gm")
def gm():
    return render_template("gm.html")

@app.route("/gm/seed")
def gm_seed():
    reset_and_seed()
    last_accuse_times.clear()
    conn = get_db()
    scores = conn.execute("SELECT id, suspect_score FROM characters").fetchall()
    conn.close()
    for row in scores:
        socketio.emit("suspect_update", {"character_id": row["id"], "suspect_score": row["suspect_score"]})
    socketio.emit("public_cleared")
    socketio.emit("jukebox_stop")
    socketio.emit("jukebox_queue", [])
    return redirect(url_for("tv"))

@app.route("/gm/clear_public")
def gm_clear_public():
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE type = 'public'")
    conn.commit()
    conn.close()
    socketio.emit("public_cleared")
    return redirect(url_for("gm"))

# ---------- Socket.IO ----------
@socketio.on("join")
def socket_join(data):
    char_id = data.get("character_id")
    if not char_id:
        return
    room = f"char-{char_id}"
    join_room(room)

@socketio.on("jukebox_finished")
def jukebox_finished(data):
    queue_id = data.get("queue_id") if data else None
    if not queue_id:
        return
    conn = get_db()
    conn.execute("""
        UPDATE jukebox_queue
        SET status = 'played', ended_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'playing'
    """, (queue_id,))
    conn.commit()
    next_row = ensure_now_playing(conn)
    queue_rows = get_up_next(conn, limit=2)
    conn.close()
    if next_row:
        socketio.emit("jukebox_now", serialize_now_playing(next_row))
    else:
        socketio.emit("jukebox_stop")
    socketio.emit("jukebox_queue", [serialize_queue_row(r) for r in queue_rows])

@socketio.on("jukebox_skip")
def jukebox_skip(data):
    queue_id = data.get("queue_id") if data else None
    if not queue_id:
        return
    conn = get_db()
    conn.execute("""
        UPDATE jukebox_queue
        SET status = 'skipped', ended_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'playing'
    """, (queue_id,))
    conn.commit()
    next_row = ensure_now_playing(conn)
    queue_rows = get_up_next(conn, limit=2)
    conn.close()
    if next_row:
        socketio.emit("jukebox_now", serialize_now_playing(next_row))
    else:
        socketio.emit("jukebox_stop")
    socketio.emit("jukebox_queue", [serialize_queue_row(r) for r in queue_rows])

if __name__ == "__main__":
    init_db()
    socketio.run(app, host="0.0.0.0", port=5001, debug=True)
