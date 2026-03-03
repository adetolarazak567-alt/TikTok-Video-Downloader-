from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import time
import threading
import sqlite3
import os
import re
import random
import string
import requests
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv

# ===== LOAD ENV VARIABLES =====
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD not set in environment variables")

app = Flask(__name__)
CORS(app)

DB_FILE = "toolifyx_stats.db"

# ===============================
# DATABASE INIT
# ===============================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            requests INTEGER DEFAULT 0,
            downloads INTEGER DEFAULT 0,
            cache_hits INTEGER DEFAULT 0,
            videos_served INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS unique_ips (
            ip TEXT PRIMARY KEY
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS download_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT,
            url TEXT,
            timestamp INTEGER
        )
    """)

    c.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")
    conn.commit()
    conn.close()

init_db()

stats = {
    "requests": 0,
    "downloads": 0,
    "cache_hits": 0,
    "videos_served": 0,
    "unique_ips": set(),
    "download_logs": []
}

cache = {}

# ===============================
# HELPERS
# ===============================
def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def clean_filename(name):
    name = re.sub(r'[^a-zA-Z0-9 ]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name[:40]
    return f"{name} ToolifyX_{random_string()}.mp4"

def increment_stat(field):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"UPDATE stats SET {field} = {field} + 1 WHERE id = 1")
    conn.commit()
    conn.close()

def add_unique_ip(ip):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO unique_ips (ip) VALUES (?)", (ip,))
    conn.commit()
    conn.close()

def add_download_log(ip, url):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO download_logs (ip, url, timestamp) VALUES (?, ?, ?)",
        (ip, url, int(time.time()))
    )
    conn.commit()
    conn.close()

# ===============================
# CLEAN & RESOLVE URL
# ===============================
def resolve_redirect(url):
    try:
        if "vt.tiktok.com" in url:
            r = requests.head(url, allow_redirects=True, timeout=10)
            return r.url
    except:
        pass
    return url

def clean_url(url):
    parsed = urlparse(url)
    clean = parsed._replace(query="")
    return urlunparse(clean)

# ===============================
# VIDEO EXTRACTION
# ===============================
def extract_video(url, result_holder):
    try:
        ydl_opts = {
            "quiet": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 20,
            "format": "bestvideo+bestaudio/best",
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if "entries" in info:
                info = info["entries"][0]

            formats = info.get("formats", [])

            # pick best mp4 video format
            best_format = None
            for f in formats:
                if f.get("ext") == "mp4" and f.get("vcodec") != "none":
                    best_format = f

            if best_format:
                result_holder["url"] = best_format.get("url")
            else:
                result_holder["url"] = info.get("url")

            result_holder["title"] = info.get("title", "Video")

    except Exception as e:
        result_holder["error"] = str(e)

def fetch_video_smart(url):
    if url in cache:
        stats["cache_hits"] += 1
        increment_stat("cache_hits")
        return cache[url]

    result = {}
    t = threading.Thread(target=extract_video, args=(url, result))
    t.start()
    t.join(timeout=30)

    if t.is_alive():
        return None

    video_url = result.get("url")
    title = result.get("title", "Video")

    if video_url:
        cache[url] = (video_url, title)

    return cache.get(url)

# ===============================
# DOWNLOAD ROUTE
# ===============================
@app.route("/download", methods=["POST"])
def download_video():

    stats["requests"] += 1
    increment_stat("requests")

    ip = request.remote_addr
    stats["unique_ips"].add(ip)
    add_unique_ip(ip)

    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400

    # Resolve short links
    url = resolve_redirect(url)

    # Clean tracking parameters
    url = clean_url(url)

    # Supported platforms
    if not any(domain in url for domain in [
        "tiktok.com",
        "instagram.com",
        "facebook.com",
        "fb.watch",
        "twitter.com",
        "x.com"
    ]):
        return jsonify({"success": False, "error": "Unsupported URL"}), 400

    result = fetch_video_smart(url)

    if not result:
        return jsonify({"success": False, "error": "Video extraction failed"}), 408

    video_url, title = result

    stats["downloads"] += 1
    stats["videos_served"] += 1
    increment_stat("downloads")
    increment_stat("videos_served")

    add_download_log(ip, url)

    return jsonify({
        "success": True,
        "url": video_url,
        "filename": clean_filename(title)
    })

# ===============================
# STATS ROUTE
# ===============================
@app.route("/stats", methods=["GET"])
def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT requests, downloads, cache_hits, videos_served FROM stats WHERE id=1")
    stats_row = c.fetchone()
    conn.close()

    return jsonify({
        "requests": stats_row[0],
        "downloads": stats_row[1],
        "cache_hits": stats_row[2],
        "videos_served": stats_row[3],
        "unique_ips": len(stats["unique_ips"])
    })

# ===============================
# ADMIN RESET
# ===============================
@app.route("/admin/reset", methods=["POST"])
def reset_stats():
    data = request.get_json()
    if data.get("password") != ADMIN_PASSWORD:
        return jsonify({"success": False}), 401

    cache.clear()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE stats SET requests=0, downloads=0, cache_hits=0, videos_served=0 WHERE id=1")
    conn.commit()
    conn.close()

    return jsonify({"success": True})

# ===============================
# RUN SERVER
# ===============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)