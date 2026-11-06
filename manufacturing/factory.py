"""Deterministic, internally consistent factory fixtures. Minutes from 08:00."""
import copy

SCENARIOS = {"normal": "正常订单", "rush": "紧急插单", "breakdown": "设备故障", "shortage": "物料短缺"}

def factory(scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("未知演示场景")
    data = {
        "scenario": scenario, "start": "2026-11-09T08:00:00", "shift_minutes": 480,
        "machines": [{"id": "CNC-01", "name": "数控一号", "group": "cnc"},
                     {"id": "CNC-02", "name": "数控二号", "group": "cnc"},
                     {"id": "QC-01", "name": "检测台", "group": "qc"}],
        "inventory": {"铝坯": 100}, "bom": {"零件A": {"铝坯": 1}},
        "route": [{"name": "数控加工", "group": "cnc", "minutes_per_unit": 4},
                  {"name": "质量检测", "group": "qc", "minutes_per_unit": 2}],
        "orders": [{"id": "ORD-001", "product": "零件A", "quantity": 20, "due": 180, "weight": 1},
                   {"id": "ORD-002", "product": "零件A", "quantity": 30, "due": 220, "weight": 1}],
        "downtime": [], "data_source": "手工构造的教学模拟数据，非真实工厂记录",
    }
    if scenario == "rush":
        data["orders"].append({"id": "RUSH-001", "product": "零件A", "quantity": 15, "due": 110, "weight": 3})
    if scenario == "breakdown":
        data["downtime"] = [{"machine": "CNC-01", "start": 0, "end": 480, "reason": "模拟停机"}]
    if scenario == "shortage":
        data["inventory"]["铝坯"] = 35
    return copy.deepcopy(data)

def materials(data: dict) -> dict:
    needs = {}
    for order in data["orders"]:
        for material, qty in data["bom"][order["product"]].items():
            needs[material] = needs.get(material, 0) + qty * order["quantity"]
    return {"required": needs, "available": data["inventory"],
            "shortages": {m: q - data["inventory"].get(m, 0) for m, q in needs.items()
                          if q > data["inventory"].get(m, 0)}}
