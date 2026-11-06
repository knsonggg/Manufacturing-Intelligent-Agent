"""Editable single-shift planning workspace, with strict input validation."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[\w\-]+$")]
Positive = Annotated[int, Field(strict=True, ge=1, le=10000)]

class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Order(Row):
    id: Id
    customer: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=50)
    quantity: Positive
    due: Annotated[int, Field(strict=True, ge=0, le=1440)]
    weight: Annotated[int, Field(strict=True, ge=1, le=10)] = 1
    status: Literal["pending", "released"] = "pending"

class Machine(Row):
    id: Id
    name: str = Field(min_length=1, max_length=80)
    group: str = Field(min_length=1, max_length=30)

class Operation(Row):
    name: str = Field(min_length=1, max_length=50)
    group: str = Field(min_length=1, max_length=30)
    minutes_per_unit: Annotated[int, Field(strict=True, ge=1, le=120)]

class Downtime(Row):
    machine: str
    start: Annotated[int, Field(strict=True, ge=0, le=1440)]
    end: Annotated[int, Field(strict=True, ge=1, le=1440)]
    reason: str = Field(min_length=1, max_length=100)

class Workspace(Row):
    start: str
    shift_minutes: Annotated[int, Field(strict=True, ge=60, le=1440)]
    machines: list[Machine] = Field(min_length=1, max_length=30)
    orders: list[Order] = Field(min_length=1, max_length=50)
    inventory: dict[str, Annotated[int, Field(strict=True, ge=0, le=1000000)]]
    bom: dict[str, dict[str, Positive]]
    routes: dict[str, list[Operation]]
    downtime: list[Downtime] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_relations(self):
        datetime.fromisoformat(self.start)
        for rows in (self.orders, self.machines):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError("订单或设备编号不能重复")
        groups = {m.group for m in self.machines}
        for order in self.orders:
            if not self.bom.get(order.product) or not self.routes.get(order.product):
                raise ValueError(f"{order.product} 缺少 BOM 或工艺路线")
            if any(m not in self.inventory for m in self.bom[order.product]):
                raise ValueError("BOM 物料必须存在于库存台账")
        for operations in self.routes.values():
            if not 1 <= len(operations) <= 10 or any(op.group not in groups for op in operations):
                raise ValueError("工艺必须有 1—10 道工序，且设备组必须存在")
        for down in self.downtime:
            if down.machine not in {m.id for m in self.machines} or not 0 <= down.start < down.end <= self.shift_minutes:
                raise ValueError("停机设备或时间区间无效")
        for mid in {d.machine for d in self.downtime}:
            intervals = sorted((d.start, d.end) for d in self.downtime if d.machine == mid)
            if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
                raise ValueError("同一设备的停机区间不能重叠")
        return self

def seed_workspace():
    return {
        "start": "2026-11-09T08:00:00", "shift_minutes": 600,
        "machines": [
            {"id": "CNC-01", "name": "精加工中心 01", "group": "cnc"},
            {"id": "CNC-02", "name": "精加工中心 02", "group": "cnc"},
            {"id": "CNC-03", "name": "精加工中心 03", "group": "cnc"},
            {"id": "LAT-01", "name": "数控车床 01", "group": "lathe"},
            {"id": "LAT-02", "name": "数控车床 02", "group": "lathe"},
            {"id": "QC-01", "name": "三坐标检测台", "group": "qc"}],
        "orders": [
            {"id": "MO-1109-001", "customer": "华东汽车", "product": "精密支架", "quantity": 35, "due": 240, "weight": 3, "status": "pending"},
            {"id": "MO-1109-002", "customer": "远航装备", "product": "传动轴套", "quantity": 25, "due": 300, "weight": 2, "status": "pending"},
            {"id": "MO-1109-003", "customer": "华东汽车", "product": "阀体组件", "quantity": 20, "due": 360, "weight": 1, "status": "pending"},
            {"id": "MO-1109-004", "customer": "启明自动化", "product": "精密支架", "quantity": 30, "due": 390, "weight": 1, "status": "pending"},
            {"id": "MO-1109-005", "customer": "远航装备", "product": "传动轴套", "quantity": 40, "due": 480, "weight": 1, "status": "pending"},
            {"id": "MO-1109-006", "customer": "启明自动化", "product": "阀体组件", "quantity": 15, "due": 540, "weight": 1, "status": "pending"}],
        "inventory": {"铝合金毛坯": 180, "合金钢棒": 100, "密封圈": 90},
        "bom": {"精密支架": {"铝合金毛坯": 1}, "传动轴套": {"合金钢棒": 1}, "阀体组件": {"铝合金毛坯": 1, "密封圈": 2}},
        "routes": {
            "精密支架": [{"name": "铣削加工", "group": "cnc", "minutes_per_unit": 3}, {"name": "尺寸检测", "group": "qc", "minutes_per_unit": 1}],
            "传动轴套": [{"name": "车削加工", "group": "lathe", "minutes_per_unit": 3}, {"name": "精加工", "group": "cnc", "minutes_per_unit": 2}, {"name": "尺寸检测", "group": "qc", "minutes_per_unit": 1}],
            "阀体组件": [{"name": "孔系加工", "group": "cnc", "minutes_per_unit": 4}, {"name": "密封检测", "group": "qc", "minutes_per_unit": 2}]},
        "downtime": [{"machine": "CNC-03", "start": 0, "end": 90, "reason": "例行保养"}],
    }
