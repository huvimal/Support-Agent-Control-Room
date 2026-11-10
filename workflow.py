"""Durable approval workflow and separate simulated payment ledger."""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW_DB = Path(os.environ.get("SUPPORT_WORKFLOW_DB", ROOT / "workflow.sqlite3"))
LEDGER_DB = Path(os.environ.get("SUPPORT_LEDGER_DB", ROOT / "ledger.sqlite3"))


class InterruptedRun(Exception):
    """Controlled fault after the simulated provider commits its side effect."""


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def record(db, case_id, event, detail=""):
    timestamp = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO events(case_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
               (case_id, event, detail, timestamp))


class Workflow:
    def __init__(self, db_path=WORKFLOW_DB, ledger_path=LEDGER_DB):
        self.db_path = db_path
        self.ledger_path = ledger_path
        with connect(db_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, amount_vnd INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL REFERENCES orders(id),
                    amount_vnd INTEGER NOT NULL, state TEXT NOT NULL
                        CHECK (state IN ('awaiting_approval','approved','rejected','completed')),
                    transaction_id TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO orders VALUES
                    ('VN1001','returned',250000), ('VN1002','shipped',185000), ('VN1003','returned',320000);
            """)
        with connect(ledger_path) as ledger:
            ledger.execute("""CREATE TABLE IF NOT EXISTS refunds (
                case_id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL,
                amount_vnd INTEGER NOT NULL, transaction_id TEXT UNIQUE NOT NULL
            )""")

    def lookup_order(self, order_id):
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def propose(self, order_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM cases WHERE order_id = ?", (order_id,)).fetchone()
            if existing:
                return {"action": "refund_case_exists", "case": dict(existing)}
            order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order or order["state"] != "returned":
                return {"action": "handoff", "reason": "order_missing_or_not_returned"}
            case_id = uuid.uuid4().hex
            created = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO cases VALUES (?, ?, ?, 'awaiting_approval', NULL, ?)",
                       (case_id, order_id, order["amount_vnd"], created))
            record(db, case_id, "approval_requested")
            return {"action": "approval_required", "case": {
                "id": case_id, "order_id": order_id, "amount_vnd": order["amount_vnd"],
                "state": "awaiting_approval", "transaction_id": None, "created_at": created,
            }}

    def review(self, case_id, approve, note):
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            state = "approved" if approve else "rejected"
            updated = db.execute("UPDATE cases SET state = ? WHERE id = ? AND state = 'awaiting_approval'",
                                 (state, case_id)).rowcount
            if not updated:
                return False
            record(db, case_id, state, note)
            return True

    def inspect(self, case_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
            if not row:
                return None
            events = db.execute("SELECT event, detail, created_at FROM events WHERE case_id = ? ORDER BY id",
                                (case_id,)).fetchall()
        with connect(self.ledger_path) as ledger:
            refund = ledger.execute("SELECT * FROM refunds WHERE case_id = ?", (case_id,)).fetchone()
        return {"case": dict(row), "events": [dict(event) for event in events],
                "ledger_entry": dict(refund) if refund else None}

    def issue_refund(self, approved):
        with connect(self.ledger_path) as ledger:
            ledger.execute("BEGIN IMMEDIATE")
            stored = ledger.execute("SELECT * FROM refunds WHERE order_id = ?", (approved["order_id"],)).fetchone()
            if stored:
                if stored["case_id"] != approved["id"] or stored["amount_vnd"] != approved["amount_vnd"]:
                    raise RuntimeError("Ledger already contains a different refund for this order.")
                return stored["transaction_id"], True
            transaction = "DEMO-" + uuid.uuid4().hex[:16]
            ledger.execute("INSERT INTO refunds VALUES (?, ?, ?, ?)",
                           (approved["id"], approved["order_id"], approved["amount_vnd"], transaction))
            return transaction, False

    def work_once(self, interrupt=False):
        with connect(self.db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM cases WHERE state = 'approved' ORDER BY created_at, id LIMIT 1").fetchone()
            if not row:
                return None
            approved = dict(row)
            record(db, approved["id"], "worker_attempt")
        transaction, reused = self.issue_refund(approved)
        if interrupt:
            raise InterruptedRun("Simulated interruption after ledger commit and before workflow completion.")
        with connect(self.db_path) as db:
            updated = db.execute(
                "UPDATE cases SET state = 'completed', transaction_id = ? WHERE id = ? AND state = 'approved'",
                (transaction, approved["id"]),
            ).rowcount
            if updated:
                record(db, approved["id"], "completed", "ledger_replay" if reused else "new_transaction")
        return {"case_id": approved["id"], "transaction_id": transaction, "reused_ledger_entry": reused}
