import copy
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from manufacturing.api import app
from manufacturing.workspace import Workspace, seed_workspace
from manufacturing.workflow import Service

@pytest.fixture
def service(tmp_path):
    instance = Service(tmp_path)
    yield instance
    instance.close()

def plan(service, ids=None, **kwargs):
    ws = service.store.workspace()
    ids = ids or [o["id"] for o in ws["data"]["orders"] if o["status"] == "pending"]
    return service.create_plan(ids, ws["revision"], "验证方案", **kwargs)

def test_seed_and_multi_product_schedule(service):
    Workspace.model_validate(seed_workspace())
    run = plan(service)
    assert run["status"] == "awaiting_approval"
    assert len(run["plan"]["orders"]) == 6
    for task in run["plan"]["schedule"]:
        order = next(o for o in run["data"]["orders"] if o["id"] == task["order"])
        route = run["data"]["routes"][order["product"]][task["step"]]
        assert task["end"] - task["start"] == route["minutes_per_unit"] * order["quantity"]
        assert next(m["group"] for m in run["data"]["machines"] if m["id"] == task["machine"]) == route["group"]

def test_release_updates_inventory_order_status_and_reservations_atomically(service):
    before = service.store.workspace()
    run = plan(service, ["MO-1109-001"])
    assert not service.get(run["id"])["work_orders"]
    service.decide(run["id"], True)
    after = service.store.workspace()
    assert after["revision"] == before["revision"] + 1
    assert after["data"]["inventory"]["铝合金毛坯"] == before["data"]["inventory"]["铝合金毛坯"] - 35
    assert after["data"]["orders"][0]["status"] == "released"
    assert len(after["reservations"]) == 2
    service.decide(run["id"], True)
    assert service.store.workspace() == after
    # Checkpoint replay after business commit also remains idempotent.
    service.store.release(service.get(run["id"]))
    assert service.store.workspace() == after

def test_later_plan_avoids_all_released_reservations(service):
    first = plan(service, ["MO-1109-001"])
    service.decide(first["id"], True)
    second = plan(service, ["MO-1109-004"])
    assert second["status"] == "awaiting_approval"
    for task in second["plan"]["schedule"]:
        for prior in service.store.workspace()["reservations"]:
            if task["machine"] == prior["machine"]:
                assert task["end"] <= prior["start"] or task["start"] >= prior["end"]

def test_changed_master_data_blocks_old_approval_and_lost_update(service):
    run = plan(service)
    ws = service.store.workspace()
    changed = copy.deepcopy(ws["data"])
    changed["inventory"]["铝合金毛坯"] += 1
    service.store.update_workspace(ws["revision"], changed)
    with pytest.raises(ValueError, match="版本"):
        service.decide(run["id"], True)
    assert not service.get(run["id"])["work_orders"]
    with pytest.raises(ValueError, match="更新"):
        service.store.update_workspace(ws["revision"], changed)

def test_competing_drafts_cannot_double_release(service):
    first = plan(service)
    second = plan(service)
    service.decide(first["id"], True)
    with pytest.raises(ValueError, match="版本"):
        service.decide(second["id"], True)
    assert not service.get(second["id"])["work_orders"]
    with pytest.raises(ValueError, match="再次"):
        plan(service, ["MO-1109-001"])

def test_quantity_and_stock_edits_change_plan_and_shortage(service):
    ws = service.store.workspace()
    ws["data"]["orders"][0]["quantity"] = 60
    service.store.update_workspace(ws["revision"], ws["data"])
    run = plan(service, ["MO-1109-001"])
    assert run["materials"]["required"] == {"铝合金毛坯": 60}
    assert next(t for t in run["plan"]["schedule"] if t["step"] == 0)["end"] - next(t for t in run["plan"]["schedule"] if t["step"] == 0)["start"] == 180
    ws = service.store.workspace()
    ws["data"]["inventory"]["铝合金毛坯"] = 10
    service.store.update_workspace(ws["revision"], ws["data"])
    blocked = plan(service, ["MO-1109-001"])
    assert blocked["status"] == "blocked"
    assert blocked["materials"]["shortages"] == {"铝合金毛坯": 50}

def test_strict_due_blocks_an_unachievable_commitment(service):
    ws = service.store.workspace()
    ws["data"]["orders"][0]["due"] = 10
    service.store.update_workspace(ws["revision"], ws["data"])
    assert plan(service, ["MO-1109-001"])["status"] == "awaiting_approval"
    strict = plan(service, ["MO-1109-001"], strict_due=True)
    assert strict["status"] == "blocked" and strict["plan"]["status"] == "INFEASIBLE"

@pytest.mark.parametrize("change", ["duplicate", "negative", "missing_route", "overlap"])
def test_invalid_workspace_rejected(change):
    data = seed_workspace()
    if change == "duplicate":
        data["orders"].append(data["orders"][0])
    elif change == "negative":
        data["inventory"]["密封圈"] = -1
    elif change == "missing_route":
        data["routes"].pop("精密支架")
    else:
        data["downtime"].append({"machine": "CNC-03", "start": 50, "end": 120, "reason": "冲突停机"})
    with pytest.raises(ValidationError):
        Workspace.model_validate(data)

def test_released_rows_and_calendar_cannot_be_rewritten(service):
    run = plan(service, ["MO-1109-001"])
    service.decide(run["id"], True)
    ws = service.store.workspace()
    changed = copy.deepcopy(ws["data"])
    changed["orders"][0]["quantity"] += 1
    with pytest.raises(ValueError, match="已下发"):
        service.store.update_workspace(ws["revision"], changed)
    changed = copy.deepcopy(ws["data"])
    changed["shift_minutes"] = 900
    with pytest.raises(ValueError, match="日历"):
        service.store.update_workspace(ws["revision"], changed)

def test_workspace_api_validation_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUFACTURING_RUNTIME", str(tmp_path))
    with TestClient(app) as client:
        ws = client.get("/workspace").json()
        body = {"revision": ws["revision"], "data": ws["data"]}
        body["data"]["orders"].append({"id": "MO-NEW", "customer": "测试客户", "product": "精密支架", "quantity": 10, "due": 400, "weight": 2, "status": "pending"})
        updated = client.put("/workspace", json=body)
        assert updated.status_code == 200
        assert client.put("/workspace", json=body).status_code == 409
        payload = {"revision": updated.json()["revision"], "name": "自定义订单", "order_ids": ["MO-NEW"]}
        result = client.post("/plans", json=payload)
        assert result.status_code == 201
        run = result.json()
        assert client.post(f'/runs/{run["id"]}/decision', json={"approve": True}).status_code == 200
        assert len(client.get("/work-orders").json()) == 1
    with TestClient(app) as client:
        assert next(o for o in client.get("/workspace").json()["data"]["orders"] if o["id"] == "MO-NEW")["status"] == "released"

def test_crash_after_release_commit_recovers_without_double_consumption(tmp_path, monkeypatch):
    first = Service(tmp_path)
    run = plan(first, ["MO-1109-001"])
    release = first.store.release
    def commit_then_crash(state):
        release(state)
        raise RuntimeError("simulated crash before checkpoint")
    monkeypatch.setattr(first.store, "release", commit_then_crash)
    with pytest.raises(RuntimeError):
        first.decide(run["id"], True)
    committed = first.store.workspace()
    assert first.get(run["id"])["status"] == "approved"
    first.close()
    second = Service(tmp_path)
    try:
        recovered = second.decide(run["id"], True)
        assert recovered["status"] == "completed"
        assert len(recovered["work_orders"]) == 1
        assert second.store.workspace() == committed
    finally:
        second.close()
