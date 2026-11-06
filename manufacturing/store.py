import json
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "business.sqlite"
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, scenario TEXT NOT NULL,
                status TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS work_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, order_id TEXT NOT NULL, plan TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(run_id, order_id));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS workspace(id INTEGER PRIMARY KEY CHECK(id=1),
                revision INTEGER NOT NULL, data TEXT NOT NULL, reservations TEXT NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def save(self, rid, state):
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,scenario,status,state) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,state=excluded.state",
                       (rid, state["scenario"], state["status"], json.dumps(state, ensure_ascii=False)))

    def get(self, rid):
        with self.connect() as db:
            row = db.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        return json.loads(row[0])

    def list_runs(self):
        with self.connect() as db:
            result = []
            for row in db.execute("SELECT id,scenario,status,created_at,state FROM runs ORDER BY created_at DESC,rowid DESC"):
                record = dict(row)
                state = json.loads(record.pop("state"))
                record["name"] = state.get("name", state.get("message", "排产方案"))
                record["revision"] = state.get("workspace_revision")
                result.append(record)
            return result

    def execute(self, rid, schedule):
        # Single transaction + unique key protects retry after checkpoint/process failure.
        with self.connect() as db:
            for oid in sorted({r["order"] for r in schedule}):
                db.execute("INSERT OR IGNORE INTO work_orders(run_id,order_id,plan) VALUES(?,?,?)",
                           (rid, oid, json.dumps([r for r in schedule if r["order"] == oid], ensure_ascii=False)))

    def audit(self, rid, action, detail):
        with self.connect() as db:
            db.execute("INSERT INTO audit(run_id,action,detail) VALUES(?,?,?)", (rid, action, detail))

    def records(self, table, rid):
        if table not in {"audit", "work_orders"}:
            raise ValueError("invalid table")
        with self.connect() as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE run_id=? ORDER BY id", (rid,))]

    def workspace(self):
        from .workspace import seed_workspace
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO workspace VALUES(1,1,?, '[]')", (json.dumps(seed_workspace(), ensure_ascii=False),))
            row = db.execute("SELECT * FROM workspace WHERE id=1").fetchone()
        return {"revision": row["revision"], "data": json.loads(row["data"]), "reservations": json.loads(row["reservations"])}

    def update_workspace(self, revision, data):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workspace WHERE id=1").fetchone()
            if not row or row["revision"] != revision:
                raise ValueError("工厂数据已更新，请刷新后重试")
            old = json.loads(row["data"])
            released = {o["id"]: o for o in old["orders"] if o["status"] == "released"}
            if {o["id"]: o for o in data["orders"] if o["status"] == "released"} != released:
                raise ValueError("已下发订单不可修改，订单状态只能通过审批流转")
            # Resource calendar and shift are frozen once work is released.
            if json.loads(row["reservations"]):
                if any(data[k] != old[k] for k in ("start", "shift_minutes", "machines", "downtime", "bom", "routes")):
                    raise ValueError("已有下发任务，本班次设备日历和工艺不可修改")
            db.execute("UPDATE workspace SET revision=revision+1,data=? WHERE id=1", (json.dumps(data, ensure_ascii=False),))
        return self.workspace()

    def release(self, state):
        rid = state["id"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM work_orders WHERE run_id=?", (rid,)).fetchone():
                return  # replay after transaction committed but checkpoint not saved
            row = db.execute("SELECT * FROM workspace WHERE id=1").fetchone()
            if row["revision"] != state["workspace_revision"]:
                raise ValueError("数据版本已变化，当前方案不可下发，请重新排产")
            data, reservations = json.loads(row["data"]), json.loads(row["reservations"])
            ids = {o["id"] for o in state["data"]["orders"]}
            if any(o["status"] != "pending" for o in data["orders"] if o["id"] in ids):
                raise ValueError("订单已下发，不可重复排产")
            for material, qty in state["materials"]["required"].items():
                if data["inventory"].get(material, 0) < qty:
                    raise ValueError("库存不足，禁止下发")
                data["inventory"][material] -= qty
            for order in data["orders"]:
                if order["id"] in ids:
                    order["status"] = "released"
            for oid in sorted(ids):
                plan = [r for r in state["plan"]["schedule"] if r["order"] == oid]
                db.execute("INSERT INTO work_orders(run_id,order_id,plan) VALUES(?,?,?)", (rid, oid, json.dumps(plan, ensure_ascii=False)))
            reservations.extend({"machine": r["machine"], "start": r["start"], "end": r["end"], "order": r["order"]} for r in state["plan"]["schedule"])
            db.execute("UPDATE workspace SET revision=revision+1,data=?,reservations=? WHERE id=1",
                       (json.dumps(data, ensure_ascii=False), json.dumps(reservations, ensure_ascii=False)))

    def all_work_orders(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM work_orders ORDER BY id DESC")]
