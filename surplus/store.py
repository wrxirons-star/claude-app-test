"""SQLite case store. One file, no ORM, JSON columns for the long tail.

Tables: cases, contacts, notes, sources, documents, audit.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CASE_STATUSES = (
    "new",          # imported/found, not yet reviewed
    "qualified",    # worth pursuing after scoring
    "locating",     # skip-trace in progress
    "contacting",   # outreach sent
    "engaged",      # owner responded
    "signed",       # agreement executed
    "filed",        # claim submitted
    "paid",         # funds disbursed
    "closed",       # dead, forfeited, or declined
)

CASE_FIELDS = (
    "state", "county", "sale_type", "case_number", "parcel_id", "property_address",
    "owner_name", "owner_type", "sale_date", "deposit_date", "notice_date",
    "surplus_amount", "sale_price", "judgment_amount", "holder", "source_url",
    "status", "score", "assigned_channel", "next_action", "next_action_date", "extra",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  state TEXT NOT NULL,
  county TEXT,
  sale_type TEXT,
  case_number TEXT,
  parcel_id TEXT,
  property_address TEXT,
  owner_name TEXT,
  owner_type TEXT,
  sale_date TEXT,
  deposit_date TEXT,
  notice_date TEXT,
  surplus_amount REAL,
  sale_price REAL,
  judgment_amount REAL,
  holder TEXT,
  source_url TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  score REAL,
  assigned_channel TEXT,
  next_action TEXT,
  next_action_date TEXT,
  extra TEXT NOT NULL DEFAULT '{}',
  UNIQUE(state, county, case_number)
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL,
  relationship TEXT,
  address TEXT,
  phone TEXT,
  email TEXT,
  confidence REAL,
  source_url TEXT,
  notes TEXT,
  verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note',
  text TEXT NOT NULL,
  source_url TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL,
  county TEXT,
  kind TEXT NOT NULL,
  url TEXT NOT NULL,
  notes TEXT,
  last_checked TEXT,
  UNIQUE(state, county, url)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  path TEXT NOT NULL,
  compliance TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS roll (
  id INTEGER PRIMARY KEY,
  state TEXT NOT NULL,
  county TEXT NOT NULL,
  year INTEGER NOT NULL,
  parcel_norm TEXT NOT NULL,
  parcel_raw TEXT,
  owner TEXT,
  addr1 TEXT,
  addr2 TEXT,
  city TEXT,
  st TEXT,
  zip TEXT,
  phy_addr TEXT,
  phy_city TEXT,
  sale_prc1 TEXT,
  sale_yr1 TEXT,
  sale_mo1 TEXT
);
CREATE INDEX IF NOT EXISTS roll_parcel ON roll(state, county, parcel_norm, year);
CREATE INDEX IF NOT EXISTS roll_owner ON roll(state, county, owner);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  case_id INTEGER,
  detail TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- audit ---------------------------------------------------------------
    def audit(self, actor: str, action: str, case_id: int | None = None, detail: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit (created_at, actor, action, case_id, detail) VALUES (?,?,?,?,?)",
            (now_iso(), actor, action, case_id, detail),
        )
        self.conn.commit()

    # -- cases ---------------------------------------------------------------
    def upsert_case(self, actor: str = "cli", **fields: Any) -> tuple[int, bool]:
        """Insert or update by (state, county, case_number). Returns (id, created)."""
        data = {k: v for k, v in fields.items() if k in CASE_FIELDS and v is not None}
        if "state" not in data:
            raise ValueError("state is required")
        data["state"] = data["state"].upper()
        if "extra" in data and not isinstance(data["extra"], str):
            data["extra"] = json.dumps(data["extra"], sort_keys=True)
        existing = None
        if data.get("case_number"):
            existing = self.conn.execute(
                "SELECT id FROM cases WHERE state=? AND IFNULL(county,'')=IFNULL(?, '') AND case_number=?",
                (data["state"], data.get("county"), data["case_number"]),
            ).fetchone()
        ts = now_iso()
        if existing:
            case_id = int(existing["id"])
            self.update_case(case_id, actor=actor, **data)
            return case_id, False
        data.setdefault("status", "new")
        data.setdefault("extra", "{}")
        cols = ["created_at", "updated_at", *data.keys()]
        vals = [ts, ts, *data.values()]
        cur = self.conn.execute(
            f"INSERT INTO cases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
        )
        self.conn.commit()
        case_id = int(cur.lastrowid)
        self.audit(actor, "create_case", case_id, json.dumps(data, default=str))
        return case_id, True

    def update_case(self, case_id: int, actor: str = "cli", **fields: Any) -> dict[str, Any]:
        data = {k: v for k, v in fields.items() if k in CASE_FIELDS}
        if "status" in data and data["status"] not in CASE_STATUSES:
            raise ValueError(f"Invalid status {data['status']!r}; choose from {', '.join(CASE_STATUSES)}")
        if "extra" in data and not isinstance(data["extra"], str):
            current = self.get_case(case_id)
            merged = {**(current["extra"] or {}), **data["extra"]}
            data["extra"] = json.dumps(merged, sort_keys=True)
        if not data:
            return self.get_case(case_id)
        sets = ", ".join(f"{k}=?" for k in data)
        self.conn.execute(
            f"UPDATE cases SET {sets}, updated_at=? WHERE id=?", [*data.values(), now_iso(), case_id]
        )
        self.conn.commit()
        self.audit(actor, "update_case", case_id, json.dumps(data, default=str))
        return self.get_case(case_id)

    def get_case(self, case_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"No case with id {case_id}")
        d = dict(row)
        d["extra"] = json.loads(d.get("extra") or "{}")
        return d

    def list_cases(
        self,
        state: str | None = None,
        status: str | None = None,
        county: str | None = None,
        min_amount: float | None = None,
        limit: int = 50,
        order: str = "score DESC, surplus_amount DESC",
        owner_type: str | None = None,
        sale_after: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if owner_type:
            where.append("owner_type=?"); params.append(owner_type)
        if sale_after:
            where.append("sale_date>=?"); params.append(sale_after)
        if state:
            where.append("state=?"); params.append(state.upper())
        if status:
            where.append("status=?"); params.append(status)
        if county:
            where.append("LOWER(county)=LOWER(?)"); params.append(county)
        if min_amount is not None:
            where.append("surplus_amount>=?"); params.append(min_amount)
        sql = "SELECT * FROM cases"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["extra"] = json.loads(d.get("extra") or "{}")
            out.append(d)
        return out

    # -- contacts / notes ----------------------------------------------------
    def add_contact(self, case_id: int, actor: str = "cli", **fields: Any) -> int:
        cols = ["case_id", "created_at", "name", "relationship", "address", "phone", "email",
                "confidence", "source_url", "notes", "verified"]
        vals = [case_id, now_iso(), fields.get("name"), fields.get("relationship"), fields.get("address"),
                fields.get("phone"), fields.get("email"), fields.get("confidence"), fields.get("source_url"),
                fields.get("notes"), 1 if fields.get("verified") else 0]
        if not vals[2]:
            raise ValueError("contact name is required")
        cur = self.conn.execute(
            f"INSERT INTO contacts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
        )
        self.conn.commit()
        self.audit(actor, "add_contact", case_id, json.dumps(fields, default=str))
        return int(cur.lastrowid)

    def contacts(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM contacts WHERE case_id=? ORDER BY confidence DESC, id", (case_id,)
        ).fetchall()]

    def add_note(self, case_id: int, text: str, kind: str = "note", source_url: str | None = None,
                 actor: str = "cli") -> int:
        cur = self.conn.execute(
            "INSERT INTO notes (case_id, created_at, kind, text, source_url) VALUES (?,?,?,?,?)",
            (case_id, now_iso(), kind, text, source_url),
        )
        self.conn.commit()
        self.audit(actor, f"note:{kind}", case_id, text[:200])
        return int(cur.lastrowid)

    def notes(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM notes WHERE case_id=? ORDER BY id", (case_id,)
        ).fetchall()]

    # -- sources -------------------------------------------------------------
    def add_source(self, state: str, url: str, kind: str, county: str | None = None,
                   notes: str | None = None, actor: str = "cli") -> int:
        self.conn.execute(
            "INSERT INTO sources (created_at, state, county, kind, url, notes, last_checked) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(state, county, url) DO UPDATE SET notes=excluded.notes, kind=excluded.kind, last_checked=excluded.last_checked",
            (now_iso(), state.upper(), county, kind, url, notes, now_iso()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM sources WHERE state=? AND IFNULL(county,'')=IFNULL(?, '') AND url=?",
            (state.upper(), county, url),
        ).fetchone()
        self.audit(actor, "add_source", None, f"{state}/{county}: {url}")
        return int(row["id"])

    def sources(self, state: str | None = None, county: str | None = None) -> list[dict[str, Any]]:
        where, params = [], []
        if state:
            where.append("state=?"); params.append(state.upper())
        if county:
            where.append("LOWER(county)=LOWER(?)"); params.append(county)
        sql = "SELECT * FROM sources"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY state, county, kind", params).fetchall()]

    # -- documents -----------------------------------------------------------
    def add_document(self, case_id: int, doc_type: str, path: str, compliance: dict[str, Any] | None = None,
                     actor: str = "cli") -> int:
        cur = self.conn.execute(
            "INSERT INTO documents (case_id, created_at, doc_type, path, compliance) VALUES (?,?,?,?,?)",
            (case_id, now_iso(), doc_type, str(path), json.dumps(compliance or {})),
        )
        self.conn.commit()
        self.audit(actor, f"document:{doc_type}", case_id, str(path))
        return int(cur.lastrowid)

    def documents(self, case_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM documents WHERE case_id=? ORDER BY id", (case_id,)
        ).fetchall()]

    def seed_sources(self, state: str, actor: str = "seed") -> int:
        """Load the bundled source list for a state (surplus/statutes/<state>_sources.json)."""
        path = Path(__file__).parent / "statutes" / f"{state.lower()}_sources.json"
        if not path.exists():
            return 0
        data = json.loads(path.read_text())
        n = 0
        for src in data.get("sources", []):
            self.add_source(state, src["url"], src["kind"], src.get("county"), src.get("notes"), actor=actor)
            n += 1
        return n

    # -- tax roll ------------------------------------------------------------
    def insert_roll_rows(self, rows: list[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO roll (state, county, year, parcel_norm, parcel_raw, owner, addr1, addr2, city, st, zip, "
            "phy_addr, phy_city, sale_prc1, sale_yr1, sale_mo1) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def roll_years(self, state: str | None = None, county: str | None = None) -> list[dict[str, Any]]:
        where, params = [], []
        if state:
            where.append("state=?"); params.append(state.upper())
        if county:
            where.append("LOWER(county)=LOWER(?)"); params.append(county)
        sql = "SELECT state, county, year, COUNT(*) AS rows FROM roll"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.conn.execute(
            sql + " GROUP BY state, county, year ORDER BY state, county, year", params).fetchall()]

    def roll_lookup(self, state: str, county: str, parcel_norm: str, year: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM roll WHERE state=? AND LOWER(county)=LOWER(?) AND parcel_norm=?"
        params: list[Any] = [state.upper(), county, parcel_norm]
        if year:
            sql += " AND year=?"; params.append(year)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY year DESC", params).fetchall()]

    def roll_owner_search(self, state: str, county: str, name_fragment: str, limit: int = 25) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM roll WHERE state=? AND LOWER(county)=LOWER(?) AND owner LIKE ? ORDER BY year DESC LIMIT ?",
            (state.upper(), county, f"%{name_fragment.upper()}%", limit)).fetchall()]

    # -- bulk ----------------------------------------------------------------
    def import_rows(self, rows: Iterable[dict[str, Any]], actor: str = "import") -> tuple[int, int, list[int]]:
        created = updated = 0
        ids: list[int] = []
        for row in rows:
            cid, was_created = self.upsert_case(actor=actor, **row)
            ids.append(cid)
            created += was_created
            updated += not was_created
        return created, updated, ids
