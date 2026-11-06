"""Persistent manufacturing planning workspace, backed by synthetic master data."""
import copy
import csv
import io
import json
import os
from datetime import datetime, timedelta
from html import escape
import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="ForgeFlow | 生产计划中心", page_icon="🏭", layout="wide")
API = os.getenv("MANUFACTURING_API", "http://127.0.0.1:8010")
PAGES = ["生产排产", "订单管理", "设备与工艺", "物料库存", "计划审批", "生产工单"]
STATUS = {"awaiting_approval": "待审批", "approved": "下发待恢复", "blocked": "校验未通过", "completed": "已下发", "rejected": "已驳回", "running": "处理中"}
COLORS = ["#2563eb", "#12a88a", "#8b5cf6", "#f59e0b", "#06a6c6", "#ec6481"]

def request(method, path, **kwargs):
    try:
        response = httpx.request(method, API + path, timeout=90, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "请求失败")
            if isinstance(detail, list):
                detail = "；".join(f'{" / ".join(str(p) for p in item["loc"])}：{item["msg"]}' for item in detail)
        except ValueError:
            detail = "服务响应异常"
        st.error(str(detail))
    except httpx.RequestError:
        st.error("生产服务暂时不可用，请确认 API 已在 8010 端口启动。")
    return None

def go(page):
    st.session_state.page = page

def clock(minutes, start=None):
    dt = datetime.fromisoformat(start or data["start"]) + timedelta(minutes=int(minutes))
    return dt.strftime("%m-%d %H:%M")

def metrics(items):
    cards = ''.join(f'<div class="kpi"><div class="kpi-label">{escape(str(label))}</div><div class="kpi-value">{escape(str(value))}</div><div class="kpi-note">{escape(str(note))}</div></div>' for label, value, note in items)
    st.markdown('<div class="kpi-grid">' + cards + '</div>', unsafe_allow_html=True)

def save_workspace(updated):
    response = request("PUT", "/workspace", json={"revision": workspace["revision"], "data": updated})
    if response:
        st.session_state.notice = "工厂数据已保存。请按新数据重新排产。"
        st.rerun()

def create_plan(selected, strict=False, baseline=None, name="早班 · 生产排产方案"):
    if not selected:
        st.warning("至少选择一个待排订单。")
        return
    with st.spinner("核对物料、计算设备分配与交期…"):
        result = request("POST", "/plans", json={"order_ids": selected, "revision": workspace["revision"],
                         "name": name, "strict_due": strict, "baseline_id": baseline})
    if result:
        st.session_state.rid = result["id"]
        st.rerun()

def show_chart(run, group, order_filter):
    rows = []
    base = datetime.fromisoformat(run["data"]["start"])
    for task in run.get("plan", {}).get("schedule", []):
        if order_filter != "全部订单" and task["order"] != order_filter:
            continue
        rows.append({"设备": task["machine"], "订单": task["order"], "工序": task["operation"],
                     "开始": base + timedelta(minutes=task["start"]), "结束": base + timedelta(minutes=task["end"]), "类型": task["order"]})
    for down in run["data"]["downtime"] + run["data"].get("reservations", []):
        rows.append({"设备": down["machine"], "订单": down.get("order", "设备保养"), "工序": down.get("reason", "已下发占用"),
                     "开始": base + timedelta(minutes=down["start"]), "结束": base + timedelta(minutes=down["end"]), "类型": "已有占用 / 保养"})
    if not rows:
        st.info("当前方案没有可执行排程，请查看物料缺口或调整订单范围。")
        return
    fig = px.timeline(rows, x_start="开始", x_end="结束", y=group, color="类型", hover_data=["订单", "设备", "工序"],
                      color_discrete_sequence=COLORS, color_discrete_map={"已有占用 / 保养": "#cbd5e1"})
    fig.update_yaxes(autorange="reversed", title=None, gridcolor="#edf0f5", tickfont=dict(size=11))
    fig.update_xaxes(title=None, tickformat="%H:%M", dtick=3600000, showgrid=True, gridcolor="#edf0f5",
                     range=[base, base + timedelta(minutes=run["data"]["shift_minutes"])])
    fig.update_layout(height=350, paper_bgcolor="white", plot_bgcolor="white", font=dict(color="#475569"),
                      margin=dict(l=5, r=10, t=15, b=10), legend=dict(orientation="h", y=-0.16, title=None), bargap=0.42)
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})

def order_table(orders, start=None):
    return [{"订单编号": o["id"], "客户": o["customer"], "产品": o["product"], "数量": o["quantity"],
             "交付时间": clock(o["due"], start), "优先级": "紧急" if o["weight"] >= 3 else "优先" if o["weight"] == 2 else "普通",
             "状态": "待排产" if o["status"] == "pending" else "已下发"} for o in orders]

st.markdown('''<style>
.stApp {background:#f5f7fb;color:#172b4d}.block-container{padding-top:1.2rem;max-width:1550px;padding-bottom:3rem}
header[data-testid="stHeader"]{background:rgba(245,247,251,.95)}
[data-testid="stSidebar"]{background:#10213a;color:#c3cede;min-width:245px;max-width:245px}
[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3,[data-testid="stSidebar"] p{color:inherit}
[data-testid="stSidebar"] .stCaption p{color:#8b9db6}
h1{font-size:1.8rem!important;letter-spacing:-.6px}h3{font-size:1.08rem!important}
[data-testid="stVerticalBlockBorderWrapper"]{background:white;border-radius:10px}
.eyebrow{font-size:11px;letter-spacing:1.6px;color:#7889a2;font-weight:600;margin:0 0 5px}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:12px 0 20px}
.kpi{background:white;border:1px solid #e3e9f1;border-radius:9px;padding:15px 18px;border-top:3px solid #2563eb}
.kpi-label{font-size:12px;color:#728198}.kpi-value{font-size:27px;font-weight:650;color:#172b4d;line-height:1.6}.kpi-note{font-size:11px;color:#94a3b8}
.panel-label{font-size:11px;color:#2563eb;font-weight:700;letter-spacing:1px;margin-bottom:5px}
[data-testid="stDataFrame"]{border-radius:7px}.stButton>button{border-radius:6px;font-size:13px}
[data-testid="stRadio"] label p{font-size:13px}[data-testid="stAlert"] p{font-size:13px}
@media(max-width:700px){.kpi-grid{grid-template-columns:repeat(2,1fr);gap:8px}.kpi{padding:10px 13px}.kpi-value{font-size:23px}.block-container{padding-left:1rem;padding-right:1rem}h1{font-size:1.5rem!important}}
</style>''', unsafe_allow_html=True)

workspace = request("GET", "/workspace")
if not workspace:
    st.stop()
data = workspace["data"]
history = [r for r in (request("GET", "/runs") or []) if r["scenario"] == "workspace"]
workspace_ids = {r["id"] for r in history}
work_orders = [w for w in (request("GET", "/work-orders") or []) if w["run_id"] in workspace_ids]
pending = [o for o in data["orders"] if o["status"] == "pending"]
with st.sidebar:
    st.markdown("## ◈ ForgeFlow")
    st.caption("制造运营 · 生产计划中心")
    st.divider()
    st.markdown("### 零部件制造一厂")
    st.caption("机加工车间 / 计划管理工作区")
    st.markdown("**班次**  " + clock(0).split(" ")[0] + " 早班")
    st.caption(clock(0).split(" ")[1] + " — " + clock(data["shift_minutes"]).split(" ")[1])
    st.divider()
    st.write(f"● 工厂台账 V{workspace['revision']}")
    st.caption("变更自动留存 · 本地工作区")
    st.divider()
    st.caption("样例工厂数据，未连接真实 MES。审批会更新本地库存、订单和设备占用。")
    if st.button("刷新业务数据", width="stretch"):
        st.rerun()
st.markdown('<div class="eyebrow">MANUFACTURING OPERATIONS / 计划与交付</div>', unsafe_allow_html=True)
page = st.radio("功能导航", PAGES, horizontal=True, key="page", label_visibility="collapsed")
if notice := st.session_state.pop("notice", None):
    st.success(notice)

if "rid" not in st.session_state or (st.session_state.rid not in workspace_ids):
    if history:
        st.session_state.rid = history[0]["id"]
    elif pending:
        initial = request("POST", "/plans", json={"order_ids": [o["id"] for o in pending],
                          "revision": workspace["revision"], "name": "早班 · 首轮排产草案"})
        if initial:
            st.session_state.rid = initial["id"]
            workspace_ids.add(initial["id"])
run = request("GET", "/runs/" + st.session_state.rid) if st.session_state.get("rid") else None

if page == "生产排产":
    head, actions = st.columns([3, 1])
    head.title("生产排产工作台")
    head.caption("核对资源与订单交期，生成、对比并下发生产计划。")
    if actions.button("重算待排订单", type="primary", width="stretch", disabled=not pending):
        create_plan([o["id"] for o in pending], baseline=run["id"] if run else None)
    actions.button("进入计划审批 →", on_click=go, args=("计划审批",), width="stretch")
    plan = run.get("plan", {}) if run else {}
    summary = plan.get("orders", [])
    metrics([("待排订单", len(pending), f"台账共 {len(data['orders'])} 笔"),
             ("方案准交率", f"{round(100 * sum(r['delay'] == 0 for r in summary) / len(summary))}%" if summary else "—", "按当前草案计算"),
             ("延期风险", sum(r["delay"] > 0 for r in summary) if summary else "—", "订单交期异常"),
             ("设备资源", len(data["machines"]), f"已下发 {len(workspace['reservations'])} 道工序")])
    with st.expander("排产范围与约束 · 选择订单 / 交付策略 / 对比版本"):
        with st.form("planning"):
            selected = st.multiselect("待排订单", [o["id"] for o in pending], default=[o["id"] for o in pending])
            a, b = st.columns(2)
            name = a.text_input("方案名称", value="早班 · 生产排产方案", max_chars=80)
            strict = b.selectbox("交付策略", [False, True], format_func=lambda v: "交期必须满足（无解则阻止）" if v else "交期优先（允许延期并预警）")
            baseline = st.selectbox("对比基线", [None] + [r["id"] for r in history], format_func=lambda v: "不对比" if v is None else next(r.get("name", "排产方案") + " / " + v[:8] for r in history if r["id"] == v))
            if st.form_submit_button("生成排产方案", type="primary"):
                create_plan(selected, strict, baseline, name)
    if run:
        if run.get("workspace_revision") != workspace["revision"] and run["status"] != "completed":
            st.warning("工厂数据已更新，当前为历史草案。请展开排产范围重新计算，下发前将强制检查版本。")
        with st.container(border=True):
            st.markdown('<div class="panel-label">PRODUCTION SCHEDULE</div>', unsafe_allow_html=True)
            st.subheader("生产计划甘特图")
            st.caption(f'{run.get("name", "排产方案")} / {run["id"][:8]} / {STATUS.get(run["status"], run["status"])}')
            a, b = st.columns(2)
            group = a.radio("排产视图", ["设备", "订单"], horizontal=True)
            order_filter = b.selectbox("订单筛选", ["全部订单"] + [o["id"] for o in run["data"]["orders"]])
            show_chart(run, group, order_filter)
            st.caption("色块为工序占用；灰色为保养或已下发任务。悬停查看订单与工序。")
        left, right = st.columns([2, 1])
        with left, st.container(border=True):
            st.subheader("订单交期与完工预测")
            order_meta = {o["id"]: o for o in run["data"]["orders"]}
            st.dataframe([{"订单": r["order"], "产品": order_meta[r["order"]]["product"], "需求交期": clock(r["due"], run["data"]["start"]),
                           "预计完工": clock(r["completion"], run["data"]["start"]), "延期 / 分钟": r["delay"],
                           "交付状态": "有延期风险" if r["delay"] else "可按期交付"} for r in summary], hide_index=True, width="stretch")
            if run.get("changes"):
                st.markdown("**与基线方案对比**")
                st.dataframe([{"订单": r["order"], "原完工": clock(r["before"], run["data"]["start"]), "新完工": clock(r["after"], run["data"]["start"]), "变化 / 分钟": r["change"]} for r in run["changes"]], hide_index=True, width="stretch")
            st.download_button("导出工序排产 CSV", pd.DataFrame(plan.get("schedule", [])).to_csv(index=False).encode("utf-8-sig"), file_name="生产排产计划.csv", mime="text/csv")
        with right, st.container(border=True):
            st.markdown('<div class="panel-label">PLANNING ASSISTANT</div>', unsafe_allow_html=True)
            st.subheader("计划核查助手")
            (st.error if run["status"] == "blocked" else st.write)(run["summary"])
            for material, qty in run["materials"]["shortages"].items():
                st.warning(f"{material} 缺口 {qty} 件，补料后重新排产。")
            for row in summary:
                if row["delay"]:
                    st.warning(f'{row["order"]} 延期 {row["delay"]} 分钟，请检查优先级或调整班次订单。')
            st.caption("基于物料核查和求解结果生成。")
            st.button("维护订单优先级 →", on_click=go, args=("订单管理",))
            with st.expander("核查过程与规程"):
                for event in run["trace"]:
                    st.caption("✓ " + event["detail"])
                for doc in run["evidence"]:
                    st.markdown(f'**{doc["id"]} / {doc["section"]}**')
                    st.caption(doc["text"])
        with st.container(border=True):
            st.subheader("设备负荷")
            loads = []
            for machine in run["data"]["machines"]:
                busy = sum(r["end"] - r["start"] for r in plan.get("schedule", []) if r["machine"] == machine["id"])
                occupied = sum(r["end"] - r["start"] for r in run["data"].get("reservations", []) if r["machine"] == machine["id"])
                down = sum(r["end"] - r["start"] for r in run["data"]["downtime"] if r["machine"] == machine["id"])
                available = run["data"]["shift_minutes"] - down
                loads.append({"设备": machine["id"], "设备名称": machine["name"], "本方案 / 分钟": busy,
                              "已下发 / 分钟": occupied, "可用 / 分钟": available, "总负荷": (busy + occupied) / available if available else 0})
            st.dataframe(loads, hide_index=True, width="stretch", column_config={"总负荷": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})

elif page == "订单管理":
    st.title("生产订单管理")
    st.caption("维护数量、交期与优先级。保存后草案需重新计算，已下发订单不可修改。")
    metrics([("全部订单", len(data["orders"]), "本班次生产需求"), ("待排产", len(pending), "可进入求解器"),
             ("已下发", len(data["orders"]) - len(pending), "订单与资源已锁定"), ("紧急订单", sum(o["weight"] >= 3 for o in pending), "权重 ≥ 3")])
    a, b = st.columns(2)
    search = a.text_input("搜索订单 / 客户 / 产品", placeholder="输入关键词")
    state_filter = b.selectbox("订单状态", ["全部", "待排产", "已下发"])
    st.dataframe([r for r in order_table(data["orders"]) if (not search or search in str(r)) and (state_filter == "全部" or r["状态"] == state_filter)], hide_index=True, width="stretch")
    with st.container(border=True):
        st.subheader("新增 / 编辑订单")
        target = st.selectbox("选择订单", ["新增订单"] + [o["id"] for o in pending])
        current = next((o for o in pending if o["id"] == target), None)
        with st.form("order_edit"):
            c1, c2, c3 = st.columns(3)
            oid = c1.text_input("订单编号", value=current["id"] if current else f"MO-1109-{len(data['orders'])+1:03d}", disabled=bool(current), max_chars=50)
            customer = c2.text_input("客户名称", value=current["customer"] if current else "", max_chars=80)
            product = c3.selectbox("产品", list(data["routes"]), index=list(data["routes"]).index(current["product"]) if current else 0)
            c1, c2, c3 = st.columns(3)
            qty = c1.number_input("生产数量 / 件", min_value=1, max_value=10000, value=current["quantity"] if current else 20, step=1)
            due = c2.number_input("交期：距班次开始 / 分钟", min_value=0, max_value=1440, value=current["due"] if current else 480, step=10)
            weight = c3.number_input("优先级权重", min_value=1, max_value=10, value=current["weight"] if current else 1, step=1)
            if st.form_submit_button("保存订单", type="primary"):
                updated = copy.deepcopy(data)
                row = {"id": oid, "customer": customer, "product": product, "quantity": qty, "due": due, "weight": weight, "status": "pending"}
                if current:
                    updated["orders"] = [row if o["id"] == oid else o for o in updated["orders"]]
                else:
                    updated["orders"].append(row)
                save_workspace(updated)
    with st.expander("CSV 批量导入订单"):
        st.caption("仅新增，不覆盖。due 为距班次开始分钟数，产品须匹配工艺台账。")
        st.download_button("下载导入模板", "id,customer,product,quantity,due,weight\nMO-NEW-001,样例客户,精密支架,20,480,1\n".encode("utf-8-sig"), file_name="订单模板.csv")
        uploaded = st.file_uploader("选择 CSV 文件", type=["csv"])
        if st.button("校验并导入", disabled=not uploaded):
            try:
                if uploaded.size > 1000000:
                    raise ValueError("文件不能超过 1MB")
                imported = list(csv.DictReader(io.StringIO(uploaded.getvalue().decode("utf-8-sig"))))
                if not 1 <= len(imported) <= 50:
                    raise ValueError("请导入 1—50 条订单")
                updated = copy.deepcopy(data)
                for row in imported:
                    updated["orders"].append({"id": row["id"], "customer": row["customer"], "product": row["product"],
                        "quantity": int(row["quantity"]), "due": int(row["due"]), "weight": int(row["weight"]), "status": "pending"})
                save_workspace(updated)
            except (ValueError, KeyError, UnicodeError):
                st.error("CSV 格式错误，请使用模板；编号需唯一，数值需为整数。")

elif page == "设备与工艺":
    st.title("设备资源与工艺路线")
    st.caption("设备组决定加工范围，停机和保养时段作为不可占用区间进入排产。")
    st.dataframe([{"设备编号": m["id"], "名称": m["name"], "设备组": m["group"], "计划停机 / 分钟": sum(d["end"] - d["start"] for d in data["downtime"] if d["machine"] == m["id"])} for m in data["machines"]], hide_index=True, width="stretch")
    with st.container(border=True):
        st.subheader("设备停机日历")
        with st.form("downtime"):
            frame = pd.DataFrame(data["downtime"], columns=["machine", "start", "end", "reason"])
            edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch", disabled=bool(workspace["reservations"]), column_config={
                "machine": st.column_config.SelectboxColumn("设备", options=[m["id"] for m in data["machines"]], required=True),
                "start": st.column_config.NumberColumn("起始分钟", min_value=0, max_value=data["shift_minutes"], step=1, required=True),
                "end": st.column_config.NumberColumn("结束分钟", min_value=1, max_value=data["shift_minutes"], step=1, required=True),
                "reason": st.column_config.TextColumn("停机原因", required=True)})
            shift = st.number_input("班次总时长 / 分钟", min_value=60, max_value=1440, value=data["shift_minutes"], step=30, disabled=bool(workspace["reservations"]))
            if st.form_submit_button("保存设备日历", disabled=bool(workspace["reservations"]), type="primary"):
                try:
                    updated = copy.deepcopy(data)
                    updated["downtime"] = [{**r, "start": int(r["start"]), "end": int(r["end"])} for r in edited.to_dict("records")]
                    updated["shift_minutes"] = shift
                    save_workspace(updated)
                except (ValueError, TypeError):
                    st.error("请补齐设备、起止分钟和原因。")
        if workspace["reservations"]:
            st.info("已有工单下发，本班次日历已锁定。当前不支持已下发计划的在制重排。")
    st.subheader("产品工艺路线与 BOM")
    for product, route in data["routes"].items():
        with st.container(border=True):
            st.markdown("**" + product + "**")
            st.write(" → ".join(f'{op["name"]} / {op["group"]} / {op["minutes_per_unit"]} 分钟·件' for op in route))
            st.caption("单件物料：" + " / ".join(f"{m} × {q}" for m, q in data["bom"][product].items()))

elif page == "物料库存":
    st.title("物料库存与齐套检查")
    st.caption("计划下发时扣减可用库存；重复批准不会重复扣减。补料保存后重新排产。")
    required = {}
    for order in pending:
        for material, qty in data["bom"][order["product"]].items():
            required[material] = required.get(material, 0) + qty * order["quantity"]
    st.dataframe([{"物料": m, "可用库存": q, "待排订单需求": required.get(m, 0), "缺口": max(0, required.get(m, 0) - q), "齐套状态": "库存不足" if q < required.get(m, 0) else "可满足"} for m, q in data["inventory"].items()], hide_index=True, width="stretch")
    with st.container(border=True):
        st.subheader("库存台账维护")
        with st.form("inventory"):
            edited = st.data_editor(pd.DataFrame([{"物料": m, "可用库存": q} for m, q in data["inventory"].items()]), hide_index=True, disabled=["物料"], width="stretch",
                column_config={"可用库存": st.column_config.NumberColumn(min_value=0, max_value=1000000, step=1, required=True)})
            if st.form_submit_button("保存库存", type="primary"):
                try:
                    updated = copy.deepcopy(data)
                    updated["inventory"] = {r["物料"]: int(r["可用库存"]) for r in edited.to_dict("records")}
                    save_workspace(updated)
                except (ValueError, TypeError):
                    st.error("库存必须为非负整数。")

elif page == "计划审批":
    st.title("计划版本与审批")
    st.caption("审批前核对订单、物料与交期。数据版本变化后，旧草案不能下发。")
    options = [r["id"] for r in history]
    if run and run["id"] not in options:
        options.insert(0, run["id"])
    if options:
        chosen = st.selectbox("方案版本", options, index=options.index(run["id"]) if run and run["id"] in options else 0,
                              format_func=lambda v: next((r.get("name", "排产方案") + " / " + STATUS.get(r["status"], r["status"]) + " / " + v[:8] for r in history if r["id"] == v), "PLAN-" + v[:8].upper()))
        run = request("GET", "/runs/" + chosen)
        st.session_state.rid = chosen
        if run:
            st.subheader(run.get("name", "排产方案"))
            stale = run["workspace_revision"] != workspace["revision"]
            st.write(f'状态：{STATUS.get(run["status"], run["status"])}　｜　方案数据 V{run["workspace_revision"]}　｜　当前数据 V{workspace["revision"]}')
            st.info(run["summary"])
            st.dataframe(order_table(run["data"]["orders"], run["data"]["start"]), hide_index=True, width="stretch")
            if run["status"] == "approved":
                st.warning("已批准，下发流程未完成。可以安全重试，系统会避免重复扣料和重复建单。")
                if st.button("恢复下发", type="primary"):
                    if request("POST", f"/runs/{chosen}/decision", json={"approve": True}):
                        st.rerun()
            if run["status"] == "awaiting_approval":
                if stale:
                    st.warning("工厂数据已变化，请重新排产。")
                checked = st.checkbox("已核对交期与资源，确认扣减本地库存并下发工单", disabled=stale, key="review_" + chosen)
                c1, c2 = st.columns(2)
                if c1.button("批准并下发计划", type="primary", disabled=stale or not checked):
                    if request("POST", f"/runs/{chosen}/decision", json={"approve": True}):
                        st.session_state.notice = "计划已下发，库存、订单状态和设备占用已同步。"
                        st.rerun()
                if c2.button("驳回草案"):
                    if request("POST", f"/runs/{chosen}/decision", json={"approve": False}):
                        st.rerun()
            st.subheader("审批留痕")
            st.dataframe([{"事件": r["action"], "说明": r["detail"], "时间 / UTC": r["created_at"]} for r in run["audit"]], hide_index=True, width="stretch")
            st.download_button("下载方案快照 JSON", json.dumps(run, ensure_ascii=False, indent=2), file_name=f"PLAN-{chosen[:8]}.json", mime="application/json")
    else:
        st.info("尚无方案，请先在排产工作台生成草案。")

elif page == "生产工单":
    st.title("已下发生产工单")
    st.caption("展示本地审批产生的工序任务；未连接真实设备，不会自动回报完工。")
    metrics([("已下发工单", len(work_orders), "经人工审批生成"), ("已排工序", sum(len(json.loads(w["plan"])) for w in work_orders), "设备资源已占用"),
             ("执行反馈", "待接入", "尚无 MES 回报接口"), ("计划版本", len({w["run_id"] for w in work_orders}), "可追溯至快照")])
    rows = []
    for work in work_orders:
        for op in json.loads(work["plan"]):
            rows.append({"工单": f'WO-{work["id"]:05d}', "生产订单": work["order_id"], "工序": op["operation"], "设备": op["machine"],
                         "计划开始": clock(op["start"]), "计划结束": clock(op["end"]), "状态": "已下发 / 待反馈", "方案": work["run_id"][:8]})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.download_button("导出工序任务", pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig"), file_name="生产工单.csv", mime="text/csv")
    else:
        st.info("暂无下发工单。在计划审批中确认草案后，工单将显示在这里。")
st.caption("ForgeFlow v0.2 · 本地制造运营工作台 · 初始台账为合成数据，业务变更会持久化")
