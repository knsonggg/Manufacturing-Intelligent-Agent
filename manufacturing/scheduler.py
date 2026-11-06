"""Flexible job-shop CP-SAT: one machine per operation, no overlap, precedence."""
from ortools.sat.python import cp_model

def solve(data: dict) -> dict:
    model = cp_model.CpModel()
    horizon = data["shift_minutes"]
    resources = {m["id"]: [] for m in data["machines"]}
    operations, lateness, completions = [], [], []
    for down in data["downtime"] + data.get("reservations", []):
        resources[down["machine"]].append(model.new_fixed_size_interval_var(
            down["start"], down["end"] - down["start"], "downtime_" + down["machine"]))
    for order in data["orders"]:
        previous = None
        route_steps = data["routes"][order["product"]] if "routes" in data else data["route"]
        for step, route in enumerate(route_steps):
            name = f'{order["id"]}_{step}'
            duration = route["minutes_per_unit"] * order["quantity"]
            start = model.new_int_var(0, horizon, name + "_start")
            end = model.new_int_var(0, horizon, name + "_end")
            model.add(end == start + duration)
            if previous is not None:
                model.add(start >= previous)
            choices = []
            for machine in data["machines"]:
                if machine["group"] != route["group"]:
                    continue
                chosen = model.new_bool_var(name + machine["id"])
                interval = model.new_optional_interval_var(start, duration, end, chosen, name + machine["id"] + "_slot")
                resources[machine["id"]].append(interval)
                choices.append((machine["id"], chosen))
            model.add_exactly_one([c for _, c in choices])
            operations.append((order["id"], step, route["name"], start, end, choices))
            previous = end
        late = model.new_int_var(0, horizon, order["id"] + "_late")
        if data.get("strict_due"):
            model.add(previous <= order["due"])
        model.add_max_equality(late, [previous - order["due"], 0])
        lateness.append(late * order["weight"])
        completions.append(previous)
    for intervals in resources.values():
        model.add_no_overlap(intervals)
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, completions)
    # Weighted delay is primary; makespan breaks ties. Bound exceeds any makespan.
    model.minimize(sum(lateness) * (horizon + 1) + makespan)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.status_name(status), "schedule": [], "orders": [],
                "message": "当前班次内没有找到可行方案；请调整班次、产能或订单范围。"}
    schedule = [{"order": oid, "step": step, "operation": op,
                 "start": solver.value(s), "end": solver.value(e),
                 "machine": next(mid for mid, chosen in choices if solver.value(chosen))}
                for oid, step, op, s, e, choices in operations]
    summaries = []
    for order in data["orders"]:
        end = max(row["end"] for row in schedule if row["order"] == order["id"])
        summaries.append({"order": order["id"], "due": order["due"], "completion": end,
                          "delay": max(0, end - order["due"])})
    return {"status": solver.status_name(status), "schedule": schedule, "orders": summaries,
            "makespan": solver.value(makespan), "objective": solver.objective_value,
            "message": "已找到最优方案" if status == cp_model.OPTIMAL else "已找到可行方案，尚未证明最优"}

def compare(before: dict, after: dict) -> list[dict]:
    base = {r["order"]: r for r in before.get("orders", [])}
    return [{"order": r["order"], "before": base[r["order"]]["completion"],
             "after": r["completion"], "change": r["completion"] - base[r["order"]]["completion"]}
            for r in after.get("orders", []) if r["order"] in base]
