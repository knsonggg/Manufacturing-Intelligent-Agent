import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from .agent import select_scenario
from .factory import factory, materials
from .scheduler import solve, compare
from .store import Store

ROOT = Path(__file__).resolve().parents[1]

class State(TypedDict, total=False):
    id: str
    scenario: str
    message: str
    mode: str
    routing: str
    status: str
    data: dict
    materials: dict
    plan: dict
    baseline: dict
    changes: list
    evidence: list
    trace: list
    summary: str
    workspace_revision: int
    baseline_input: dict
    name: str

class Service:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.store.workspace()
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(root / "checkpoints.sqlite", check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        graph = StateGraph(State)
        for name, node in [("load", self.load), ("material_check", self.check),
                           ("schedule", self.schedule), ("evidence", self.evidence),
                           ("review", self.review), ("dispatch", self.dispatch)]:
            graph.add_node(name, node)
        graph.add_edge(START, "load")
        graph.add_edge("load", "material_check")
        graph.add_conditional_edges("material_check", lambda s: "evidence" if s["materials"]["shortages"] else "schedule")
        graph.add_edge("schedule", "evidence")
        graph.add_conditional_edges("evidence", lambda s: END if s["status"] == "blocked" else "review")
        graph.add_conditional_edges("review", lambda s: "dispatch" if s["status"] == "approved" else END)
        graph.add_edge("dispatch", END)
        self.graph = graph.compile(checkpointer=self.saver)

    def close(self):
        self.connection.close()

    @staticmethod
    def trace(state, node, detail):
        return state.get("trace", []) + [{"node": node, "detail": detail}]

    def load(self, state):
        return {"data": state.get("data") or factory(state["scenario"]),
                "trace": self.trace(state, "get_factory_scenario", state["routing"])}

    def check(self, state):
        result = materials(state["data"])
        return {"materials": result, "trace": self.trace(state, "check_materials", "按 BOM 汇总物料需求并核对库存")}

    def schedule(self, state):
        plan = solve(state["data"])
        baseline = state.get("baseline_input", {})
        if not baseline and state["scenario"] in {"rush", "breakdown"}:
            baseline = solve(factory("normal"))
        return {"plan": plan, "baseline": baseline, "changes": compare(baseline, plan),
                "trace": self.trace(state, "solve_schedule", f'CP-SAT: {plan["status"]}')}

    def evidence(self, state):
        docs = json.loads((ROOT / "data" / "sops.json").read_text(encoding="utf-8"))
        docs = [d for d in docs if state["scenario"] == "workspace" or state["scenario"] in d["tags"]]
        shortages = state["materials"]["shortages"]
        feasible = state.get("plan", {}).get("status") in {"OPTIMAL", "FEASIBLE"}
        if shortages:
            summary = "物料不足，方案被阻止：" + "，".join(f"{m}缺{q}件" for m, q in shortages.items())
        elif not feasible:
            summary = state["plan"]["message"]
        else:
            delayed = [r for r in state["plan"]["orders"] if r["delay"] > 0]
            summary = f'排程完成，共 {len(state["data"]["orders"])} 个订单；{len(delayed)} 个订单延期。'
            if delayed:
                summary += "风险：" + "，".join(f'{r["order"]}延期{r["delay"]}分钟' for r in delayed) + "。"
            summary += "方案待人工确认，尚未创建工单。"
        return {"evidence": docs, "summary": summary, "status": "awaiting_approval" if feasible and not shortages else "blocked",
                "trace": self.trace(state, "retrieve_demo_sop", "按场景标签检索演示规程；输出文档编号、版本、条款")}

    def review(self, state):
        decision = interrupt({"run_id": state["id"], "summary": state["summary"], "action": "approve_or_reject"})
        if not isinstance(decision, dict) or type(decision.get("approve")) is not bool:
            raise ValueError("审批必须包含明确的布尔值")
        approved = decision["approve"]
        return {"status": "approved" if approved else "rejected",
                "summary": "人工批准，准备创建模拟工单。" if approved else "人工驳回，未创建工单。",
                "trace": self.trace(state, "human_review", "批准" if approved else "驳回")}

    def dispatch(self, state):
        if state["status"] != "approved" or state["materials"]["shortages"]:
            raise ValueError("方案不满足执行条件")
        if "workspace_revision" in state:
            self.store.release(state)
        else:
            self.store.execute(state["id"], state["plan"]["schedule"])
        return {"status": "completed", "summary": "审批完成，已创建模拟生产工单。",
                "trace": self.trace(state, "create_simulated_work_orders", "幂等写入；没有连接真实设备")}

    def create(self, message: str, scenario: str | None = None, mode: str = "demo"):
        scenario, routing = select_scenario(message, mode, scenario)
        rid = uuid.uuid4().hex
        state = {"id": rid, "message": message, "scenario": scenario, "routing": routing,
                 "mode": mode, "status": "running", "trace": []}
        with self.lock:
            self.store.save(rid, state)
            self.store.audit(rid, "created", routing)
            self.graph.invoke(state, {"configurable": {"thread_id": rid}})
            return self.get(rid)

    def get(self, rid):
        with self.lock:
            state = self.store.get(rid)
            snap = self.graph.get_state({"configurable": {"thread_id": rid}})
            if snap.values:
                state = dict(snap.values)
                self.store.save(rid, state)
            return {**state, "work_orders": self.store.records("work_orders", rid),
                    "audit": self.store.records("audit", rid)}

    def create_plan(self, order_ids, revision, name, strict_due=False, baseline_id=None):
        with self.lock:
            workspace = self.store.workspace()
            if revision != workspace["revision"]:
                raise ValueError("工厂数据已更新，请刷新后重新选择订单")
            data = workspace["data"]
            selected = [o for o in data["orders"] if o["id"] in order_ids]
            if not selected or len(selected) != len(set(order_ids)) or len(order_ids) != len(set(order_ids)):
                raise ValueError("请选择存在且不重复的订单")
            if any(o["status"] != "pending" for o in selected):
                raise ValueError("已下发订单不可再次排产")
            data["orders"] = selected
            data["reservations"] = workspace["reservations"]
            data["strict_due"] = strict_due
            baseline = {}
            if baseline_id:
                previous = self.get(baseline_id)
                if previous.get("workspace_revision") is None or previous["data"]["start"] != data["start"]:
                    raise ValueError("对比方案必须来自同一工作空间班次")
                baseline = previous.get("plan", {})
            rid = uuid.uuid4().hex
            state = {"id": rid, "scenario": "workspace", "mode": "workspace", "name": name,
                     "message": name, "status": "running", "data": data, "trace": [],
                     "workspace_revision": revision, "baseline_input": baseline,
                     "routing": f"计划员选择 {len(selected)} 个订单 / 工厂版本 V{revision}"}
            self.store.save(rid, state)
            self.store.audit(rid, "created", state["routing"])
            self.graph.invoke(state, {"configurable": {"thread_id": rid}})
            return self.get(rid)

    def decide(self, rid, approve: bool):
        with self.lock:
            state = self.get(rid)
            if state["status"] == "approved":
                if not approve:
                    raise ValueError("方案已批准并进入下发阶段，只能重试完成下发")
                self.store.audit(rid, "retry_dispatch", "恢复已批准方案的下发节点")
                self.graph.invoke(None, {"configurable": {"thread_id": rid}})
                return self.get(rid)
            if state["status"] in {"completed", "rejected"}:
                if (state["status"] == "completed") != approve:
                    raise ValueError("审批已完成，不可修改；请创建新方案")
                return state
            if state["status"] != "awaiting_approval":
                raise ValueError("当前方案不可审批")
            if approve and "workspace_revision" in state and self.store.workspace()["revision"] != state["workspace_revision"]:
                raise ValueError("工厂数据版本已变化，禁止下发旧方案，请重新排产")
            self.store.audit(rid, "approval" if approve else "rejection", "本地演示用户明确操作")
            self.graph.invoke(Command(resume={"approve": approve}), {"configurable": {"thread_id": rid}})
            return self.get(rid)
