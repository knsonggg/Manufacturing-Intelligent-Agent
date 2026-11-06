import os
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool
from typing import Literal
from .factory import SCENARIOS, factory
from .workflow import Service, ROOT
from .workspace import Workspace

@asynccontextmanager
async def lifespan(app):
    app.state.service = Service(Path(os.getenv("MANUFACTURING_RUNTIME", str(ROOT / "runtime"))))
    yield
    app.state.service.close()

app = FastAPI(title="制造业生产协同智能体", version="0.2.0", lifespan=lifespan)

class RunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    scenario: Literal["normal", "rush", "breakdown", "shortage"] | None = None
    mode: Literal["demo", "llm"] = "demo"

class Decision(BaseModel):
    approve: StrictBool

class WorkspaceUpdate(BaseModel):
    revision: int = Field(ge=1)
    data: Workspace

class PlanRequest(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=50)
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    strict_due: StrictBool = False
    baseline_id: str | None = None

@app.get("/workspace")
def workspace(request: Request):
    return request.app.state.service.store.workspace()

@app.put("/workspace")
def update_workspace(body: WorkspaceUpdate, request: Request):
    try:
        with request.app.state.service.lock:
            return request.app.state.service.store.update_workspace(body.revision, body.data.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post("/plans", status_code=201)
def create_plan(body: PlanRequest, request: Request):
    try:
        return request.app.state.service.create_plan(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "对比方案不存在") from exc

@app.get("/work-orders")
def work_orders(request: Request):
    return request.app.state.service.store.all_work_orders()

@app.get("/health")
def health():
    return {"status": "ok", "data": "synthetic"}

@app.get("/scenarios")
def scenarios():
    return SCENARIOS

@app.get("/factory/{scenario}")
def get_factory(scenario: str):
    if scenario not in SCENARIOS:
        raise HTTPException(404, "未知场景")
    return factory(scenario)

@app.get("/runs")
def runs(request: Request):
    return request.app.state.service.store.list_runs()

@app.post("/runs", status_code=201)
def create(body: RunRequest, request: Request):
    try:
        return request.app.state.service.create(body.message, body.scenario, body.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (httpx.HTTPError, KeyError, TypeError) as exc:
        raise HTTPException(502, "模型服务请求失败或响应格式错误；检查配置，或改用离线演示模式。") from exc

@app.get("/runs/{rid}")
def get_run(rid: str, request: Request):
    try:
        return request.app.state.service.get(rid)
    except KeyError as exc:
        raise HTTPException(404, "方案不存在") from exc

@app.post("/runs/{rid}/decision")
def decide(rid: str, body: Decision, request: Request):
    try:
        return request.app.state.service.decide(rid, body.approve)
    except KeyError as exc:
        raise HTTPException(404, "方案不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
