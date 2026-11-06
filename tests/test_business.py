import pytest
from manufacturing.factory import factory, materials
from manufacturing.scheduler import solve
from manufacturing.workflow import Service

@pytest.fixture
def service(tmp_path):
    instance = Service(tmp_path)
    yield instance
    instance.close()

@pytest.mark.parametrize("scenario", ["normal", "rush", "breakdown"])
def test_schedule_respects_capacity_precedence_and_downtime(scenario):
    data = factory(scenario)
    plan = solve(data)
    assert plan["status"] in {"OPTIMAL", "FEASIBLE"}
    rows = plan["schedule"]
    for row in rows:
        assert 0 <= row["start"] < row["end"] <= data["shift_minutes"]
        order = next(o for o in data["orders"] if o["id"] == row["order"])
        assert row["end"] - row["start"] == data["route"][row["step"]]["minutes_per_unit"] * order["quantity"]
        machine = next(m for m in data["machines"] if m["id"] == row["machine"])
        assert machine["group"] == data["route"][row["step"]]["group"]
        if row["step"]:
            prior = next(r for r in rows if r["order"] == row["order"] and r["step"] == row["step"] - 1)
            assert prior["end"] <= row["start"]
        for other in rows:
            if row is not other and row["machine"] == other["machine"]:
                assert row["end"] <= other["start"] or other["end"] <= row["start"]
        for down in data["downtime"]:
            if down["machine"] == row["machine"]:
                assert row["end"] <= down["start"] or row["start"] >= down["end"]

def test_shortage_blocks_even_explicit_approval(service):
    run = service.create("缺料")
    assert run["status"] == "blocked"
    assert run["materials"]["shortages"] == {"铝坯": 15}
    assert not run["work_orders"]
    with pytest.raises(ValueError):
        service.decide(run["id"], True)
    assert not service.get(run["id"])["work_orders"]

def test_approval_is_required_and_idempotent(service):
    run = service.create("正常排程")
    assert run["status"] == "awaiting_approval"
    assert run["work_orders"] == []
    completed = service.decide(run["id"], True)
    assert completed["status"] == "completed"
    assert len(completed["work_orders"]) == 2
    assert len(service.decide(run["id"], True)["work_orders"]) == 2
    with pytest.raises(ValueError):
        service.decide(run["id"], False)

def test_rejected_plan_never_dispatches(service):
    run = service.create("紧急插单")
    run = service.decide(run["id"], False)
    assert run["status"] == "rejected" and not run["work_orders"]
    with pytest.raises(ValueError):
        service.decide(run["id"], True)

def test_checkpoint_resumes_after_restart(tmp_path):
    first = Service(tmp_path)
    rid = first.create("设备停机")["id"]
    first.close()
    second = Service(tmp_path)
    try:
        assert second.get(rid)["status"] == "awaiting_approval"
        assert second.decide(rid, True)["status"] == "completed"
        assert len(second.get(rid)["work_orders"]) == 2
    finally:
        second.close()

def test_infeasible_capacity_has_no_false_plan():
    data = factory("normal")
    data["shift_minutes"] = 30
    result = solve(data)
    assert result["status"] == "INFEASIBLE" and not result["schedule"]

def test_baseline_and_risk(service):
    run = service.create("设备故障")
    assert run["baseline"]["schedule"]
    assert any(row["change"] > 0 for row in run["changes"])
    assert any(row["delay"] > 0 for row in run["plan"]["orders"])
    assert any(d["id"] == "DEMO-SOP-002" for d in run["evidence"])

def test_no_inventory_mutation_between_independent_simulations(service):
    first = service.create("正常排程")
    service.decide(first["id"], True)
    second = service.create("正常排程")
    assert second["materials"] == first["materials"]
    assert materials(factory("normal"))["required"] == {"铝坯": 50}
