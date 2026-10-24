#!/usr/bin/env python3
"""Graduation site server: static files + RSVP/guestbook API backed by SQLite.

Layout (under APP_DIR, default ~/apps/totnghiep):
  public/        static files served at /
  admin.html     served at /admin (data endpoints require the admin token)
  data/site.db   SQLite database
  admin.token    bearer token for /api/admin/*
"""
import csv
import html
import io
import json
import mimetypes
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(os.environ.get("APP_DIR", Path.home() / "apps" / "totnghiep"))
PUBLIC = APP_DIR / "public"
DB_PATH = APP_DIR / "data" / "site.db"
TOKEN_PATH = APP_DIR / "admin.token"
SITE_URL = os.environ.get("SITE_URL", "https://totnghiep.mchieens.io.vn")
HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", "8088"))

# Each IP gets at most this many writes per window.
RATE_MAX, RATE_WINDOW = 12, 600
_hits = defaultdict(deque)


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS rsvps(
              id INTEGER PRIMARY KEY,
              client_id TEXT UNIQUE NOT NULL,
              name TEXT NOT NULL,
              attend INTEGER NOT NULL,
              companions TEXT NOT NULL DEFAULT '',
              party_size TEXT NOT NULL DEFAULT '1',
              invited_as TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wishes(
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              message TEXT NOT NULL,
              hidden INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL
            );
            """
        )
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(24) + "\n")
        TOKEN_PATH.chmod(0o600)


def admin_token():
    return TOKEN_PATH.read_text().strip()


def clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


class Handler(BaseHTTPRequestHandler):
    server_version = "totnghiep"

    def log_message(self, fmt, *args):
        print(f"{self.client_ip()} {fmt % args}", flush=True)

    def client_ip(self):
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    # ---------- helpers ----------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, ctype, cache="no-cache", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 8192:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return None

    def rate_limited(self):
        now, q = time.time(), _hits[self.client_ip()]
        while q and q[0] < now - RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_MAX:
            return True
        q.append(now)
        return False

    def is_admin(self, query=None):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else (query or {}).get("token", [""])[0]
        return bool(token) and secrets.compare_digest(token, admin_token())

    # ---------- routing ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)

        if path == "/api/wishes":
            with db() as con:
                rows = con.execute(
                    "SELECT id,name,message,created_at FROM wishes WHERE hidden=0 ORDER BY id DESC LIMIT 200"
                ).fetchall()
            return self.send_json({"wishes": [dict(r) for r in rows]})

        if path == "/api/admin/summary":
            if not self.is_admin():
                return self.send_json({"error": "unauthorized"}, 401)
            with db() as con:
                rsvps = [dict(r) for r in con.execute("SELECT * FROM rsvps ORDER BY updated_at DESC")]
                wishes = [dict(r) for r in con.execute("SELECT * FROM wishes ORDER BY id DESC")]
            return self.send_json({"rsvps": rsvps, "wishes": wishes})

        if path == "/api/admin/rsvps.csv":
            if not self.is_admin(query):
                return self.send_json({"error": "unauthorized"}, 401)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Họ tên", "Tham dự", "Đi cùng", "Số người", "Mời với tên", "Cập nhật"])
            with db() as con:
                for r in con.execute("SELECT * FROM rsvps ORDER BY updated_at DESC"):
                    w.writerow([r["name"], "Có" if r["attend"] else "Không", r["companions"], r["party_size"],
                                r["invited_as"], time.strftime("%d/%m/%Y %H:%M", time.localtime(r["updated_at"]))])
            return self.send_bytes(("﻿" + buf.getvalue()).encode(), "text/csv; charset=utf-8", "no-store",
                                   extra={"Content-Disposition": 'attachment; filename="xac-nhan-tham-du.csv"'})

        if path in ("/admin", "/admin/"):
            return self.send_bytes((APP_DIR / "admin.html").read_bytes(), "text/html; charset=utf-8", "no-store",
                                   extra={"X-Robots-Tag": "noindex"})

        if path in ("/", "/index.html"):
            return self.send_index(query)

        return self.send_static(path)

    def do_POST(self):
        path = urlparse(self.path).path

        if path in ("/api/rsvp", "/api/wishes"):
            if self.rate_limited():
                return self.send_json({"error": "Bạn gửi hơi nhiều rồi, thử lại sau ít phút nhé."}, 429)
            data = self.read_json()
            if not isinstance(data, dict):
                return self.send_json({"error": "Dữ liệu không hợp lệ."}, 400)
            now = int(time.time())

            if path == "/api/rsvp":
                cid = clean(data.get("client_id"), 64)
                name = clean(data.get("name"), 80)
                if not cid or not name:
                    return self.send_json({"error": "Vui lòng nhập họ tên."}, 400)
                attend = 1 if data.get("attend") == "yes" else 0
                size = clean(data.get("count"), 4) if attend else "0"
                with db() as con:
                    con.execute(
                        """INSERT INTO rsvps(client_id,name,attend,companions,party_size,invited_as,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(client_id) DO UPDATE SET name=excluded.name, attend=excluded.attend,
                             companions=excluded.companions, party_size=excluded.party_size,
                             invited_as=excluded.invited_as, updated_at=excluded.updated_at""",
                        (cid, name, attend, clean(data.get("with"), 120), size or "1",
                         clean(data.get("invited_as"), 60), now, now),
                    )
                return self.send_json({"ok": True})

            name = clean(data.get("name"), 60)
            message = str(data.get("message") or "").strip()[:280]
            if not name or not message:
                return self.send_json({"error": "Vui lòng nhập tên và lời chúc."}, 400)
            with db() as con:
                cur = con.execute("INSERT INTO wishes(name,message,created_at) VALUES(?,?,?)", (name, message, now))
            return self.send_json({"ok": True, "id": cur.lastrowid, "created_at": now})

        if path.startswith("/api/admin/wishes/"):
            if not self.is_admin():
                return self.send_json({"error": "unauthorized"}, 401)
            parts = path.strip("/").split("/")  # api admin wishes <id> <hide|show>
            if len(parts) != 5 or not parts[3].isdigit() or parts[4] not in ("hide", "show"):
                return self.send_json({"error": "not found"}, 404)
            with db() as con:
                con.execute("UPDATE wishes SET hidden=? WHERE id=?", (1 if parts[4] == "hide" else 0, int(parts[3])))
            return self.send_json({"ok": True})

        self.send_json({"error": "not found"}, 404)

    # ---------- pages ----------
    def send_index(self, query):
        page = (PUBLIC / "index.html").read_text()
        guest = clean(query.get("to", [""])[0], 40)
        if guest:
            title = f"Thân mời {guest} · Lễ tốt nghiệp Nông Minh Chiến"
            page = page.replace(
                '<meta property="og:title" content="Thiệp mời Lễ tốt nghiệp · Nông Minh Chiến">',
                f'<meta property="og:title" content="{html.escape(title)}">', 1)
        return self.send_bytes(page.encode(), "text/html; charset=utf-8")

    def send_static(self, path):
        rel = unquote(path).lstrip("/")
        target = (PUBLIC / rel).resolve()
        if target.is_dir() and PUBLIC.resolve() in target.parents:
            if not path.endswith("/"):
                self.send_response(301)
                self.send_header("Location", path + "/")
                self.end_headers()
                return
            target = target / "index.html"
        if (not rel or any(p.startswith(".") for p in Path(rel).parts)
                or PUBLIC.resolve() not in target.parents or not target.is_file()):
            return self.send_bytes(b"Not found", "text/plain; charset=utf-8", status=404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        cache = "no-cache" if target.suffix == ".html" else "public, max-age=3600"
        return self.send_bytes(target.read_bytes(), ctype, cache)


if __name__ == "__main__":
    init_db()
    print(f"Serving {PUBLIC} on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
