"""SQLite persistence layer. One short-lived connection per operation."""
import os
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DASH_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "dash.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS services(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'http',
  url TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'GET',
  expected_status INTEGER NOT NULL DEFAULT 200,
  header_name TEXT,
  header_value TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  links TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  ok INTEGER NOT NULL,
  status_code INTEGER,
  latency_ms REAL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_svc_ts ON checks(service_id, ts DESC);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS machine_meta(
  key TEXT PRIMARY KEY,   -- hostname (lower) preferred, else tailnet IP
  category TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  tool TEXT NOT NULL,
  ok INTEGER NOT NULL,
  ms REAL,
  client TEXT,
  args_summary TEXT
);
CREATE TABLE IF NOT EXISTS discoveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL,
  hostname TEXT,
  port INTEGER NOT NULL,
  kind TEXT,
  http_status INTEGER,
  server TEXT,
  url TEXT,
  first_seen REAL,
  last_seen REAL,
  dismissed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(ip, port)
);
"""


def conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        # lightweight column migrations for existing DBs
        for col in ("title TEXT", "health_path TEXT"):
            try:
                c.execute(f"ALTER TABLE discoveries ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists
        try:
            c.execute("ALTER TABLE services ADD COLUMN links TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists


def seed_if_empty() -> None:
    with conn() as c:
        if c.execute("SELECT COUNT(*) n FROM services").fetchone()["n"]:
            return
        now = time.time()
        seed = [
            ("SearXNG (example)", "searxng", "http://127.0.0.1:8888"),
            ("Crawl4AI (example)", "crawl4ai", "http://127.0.0.1:11235"),
            ("Ollama (example)", "ollama", "http://127.0.0.1:11434"),
            ("Open WebUI (example)", "openwebui", "http://127.0.0.1:3000"),
        ]
        c.executemany(
            "INSERT INTO services(name,type,url,method,expected_status,enabled,created_at) "
            "VALUES(?,?,?,?,?,1,?)",
            [(n, t, u, "GET", 200, now) for n, t, u in seed],
        )


# ---------- services ----------

def list_services(enabled_only: bool = False):
    q = "SELECT * FROM services" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name COLLATE NOCASE"
    with conn() as c:
        return [dict(r) for r in c.execute(q)]


def get_service(sid: int):
    with conn() as c:
        r = c.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def add_service(d: dict) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO services(name,type,url,method,expected_status,
               header_name,header_value,enabled,notes,links,created_at)
               VALUES(:name,:type,:url,:method,:expected_status,
               :header_name,:header_value,:enabled,:notes,:links,:created_at)""",
            {**d, "created_at": time.time()},
        )
        return cur.lastrowid


def update_service(sid: int, d: dict) -> bool:
    fields = ["name", "type", "url", "method", "expected_status",
              "header_name", "header_value", "enabled", "notes", "links"]
    sets = ", ".join(f"{f}=:{f}" for f in fields)
    with conn() as c:
        cur = c.execute(f"UPDATE services SET {sets} WHERE id=:id", {**d, "id": sid})
        return cur.rowcount > 0


def delete_service(sid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM services WHERE id=?", (sid,))
        c.execute("DELETE FROM checks WHERE service_id=?", (sid,))
        return cur.rowcount > 0


# ---------- checks ----------

def record_check(sid: int, result: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO checks(service_id,ts,ok,status_code,latency_ms,detail) VALUES(?,?,?,?,?,?)",
            (sid, time.time(), int(result["ok"]), result.get("status_code"),
             result.get("latency_ms"), result.get("detail")),
        )


def last_check(sid: int):
    with conn() as c:
        r = c.execute("SELECT * FROM checks WHERE service_id=? ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
        return dict(r) if r else None


def history(sid: int, hours: float = 24, limit: int = 500):
    since = time.time() - hours * 3600
    with conn() as c:
        rows = c.execute(
            "SELECT ts,ok,status_code,latency_ms,detail FROM checks "
            "WHERE service_id=? AND ts>? ORDER BY ts DESC LIMIT ?",
            (sid, since, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def uptime_pct(sid: int, hours: float = 24):
    since = time.time() - hours * 3600
    with conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n, SUM(ok) up FROM checks WHERE service_id=? AND ts>?", (sid, since)
        ).fetchone()
        return round(100.0 * r["up"] / r["n"], 1) if r["n"] else None


def trim_checks(max_age_hours: float = 168.0) -> None:
    with conn() as c:
        c.execute("DELETE FROM checks WHERE ts < ?", (time.time() - max_age_hours * 3600,))


# ---------- discoveries ----------

def upsert_discovery(d: dict) -> None:
    now = time.time()
    with conn() as c:
        c.execute(
            """INSERT INTO discoveries(ip,hostname,port,kind,http_status,server,url,title,health_path,first_seen,last_seen)
               VALUES(:ip,:hostname,:port,:kind,:http_status,:server,:url,:title,:health_path,:fs,:ls)
               ON CONFLICT(ip,port) DO UPDATE SET
                 hostname=excluded.hostname, kind=excluded.kind, http_status=excluded.http_status,
                 server=excluded.server, url=excluded.url, title=excluded.title,
                 health_path=excluded.health_path, last_seen=excluded.last_seen, dismissed=0""",
            {"title": None, "health_path": None, **d, "fs": now, "ls": now},
        )


def list_discoveries(include_dismissed: bool = False):
    q = "SELECT * FROM discoveries" + ("" if include_dismissed else " WHERE dismissed=0") + " ORDER BY ip, port"
    with conn() as c:
        return [dict(r) for r in c.execute(q)]


def get_discovery(did: int):
    with conn() as c:
        r = c.execute("SELECT * FROM discoveries WHERE id=?", (did,)).fetchone()
        return dict(r) if r else None


def dismiss_discovery(did: int) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE discoveries SET dismissed=1 WHERE id=?", (did,))
        return cur.rowcount > 0


# ---------- machine_meta (topology category overrides) ----------

def machine_categories() -> dict:
    with conn() as c:
        rows = c.execute("SELECT key, category FROM machine_meta").fetchall()
    return {r["key"]: r["category"] for r in rows}


def set_machine_category(key: str, category: str) -> None:
    key = key.strip().lower()
    with conn() as c:
        if category in ("", "auto", "reset"):
            c.execute("DELETE FROM machine_meta WHERE key=?", (key,))
        else:
            c.execute(
                "INSERT INTO machine_meta(key,category,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET category=excluded.category, "
                "updated_at=excluded.updated_at",
                (key, category, time.time()))


# ---------- settings (kv) ----------

def get_setting(key: str, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---------- mcp call log ----------

def log_mcp_call(tool: str, ok: bool, ms: float, client: str, args_summary: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO mcp_calls(ts,tool,ok,ms,client,args_summary) VALUES(?,?,?,?,?,?)",
                  (time.time(), tool, int(ok), ms, client, args_summary[:200]))
        c.execute("DELETE FROM mcp_calls WHERE id < (SELECT MAX(id) FROM mcp_calls) - 1000")


def recent_mcp_calls(limit: int = 50):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts,tool,ok,ms,client,args_summary FROM mcp_calls ORDER BY ts DESC LIMIT ?", (limit,))]


# ---------- registry export / import ----------

EXPORT_FIELDS = ["name", "type", "url", "method", "expected_status",
                 "header_name", "header_value", "enabled", "notes", "links"]


def export_services(include_secrets: bool = False):
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM services ORDER BY id")]
    out = []
    for r in rows:
        item = {k: r.get(k) for k in EXPORT_FIELDS}
        if not include_secrets:
            item.pop("header_value", None)
        out.append(item)
    return {
        "export_format": "tailnet-dash-services",
        "version": 1,
        "exported_at": time.time(),
        "services": out,
    }


def import_services(services: list, mode: str = "merge") -> dict:
    """merge: skip URLs already tracked. replace: wipe registry + history first."""
    added = skipped = 0
    now = time.time()
    with conn() as c:
        if mode == "replace":
            c.execute("DELETE FROM checks")
            c.execute("DELETE FROM services")
        existing = {r["url"] for r in c.execute("SELECT url FROM services")}
        for s in services:
            if not s.get("name") or not s.get("url"):
                skipped += 1
                continue
            if mode == "merge" and s["url"] in existing:
                skipped += 1
                continue
            c.execute(
                """INSERT INTO services(name,type,url,method,expected_status,
                   header_name,header_value,enabled,notes,links,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (s["name"], s.get("type", "http"), s["url"], s.get("method", "GET"),
                 s.get("expected_status", 200), s.get("header_name"),
                 s.get("header_value"), 1 if s.get("enabled", 1) else 0,
                 s.get("notes"), s.get("links"), now),
            )
            existing.add(s["url"])
            added += 1
    return {"added": added, "skipped": skipped, "mode": mode}
