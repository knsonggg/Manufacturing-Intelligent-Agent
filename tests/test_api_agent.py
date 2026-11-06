import json
from fastapi.testclient import TestClient
from manufacturing.api import app
from manufacturing.agent import select_scenario
import pytest

def test_api_full_flow_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUFACTURING_RUNTIME", str(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/runs/missing").status_code == 404
        assert client.post("/runs", json={"message": "hello", "scenario": "invalid"}).status_code == 422
        run = client.post("/runs", json={"message": "紧急插单"}).json()
        rid = run["id"]
        assert run["status"] == "awaiting_approval"
        assert client.post(f"/runs/{rid}/decision", json={"approve": "false"}).status_code == 422
        result = client.post(f"/runs/{rid}/decision", json={"approve": True})
        assert result.status_code == 200 and len(result.json()["work_orders"]) == 3
        assert len(client.get("/runs").json()) == 1
        blocked = client.post("/runs", json={"message": "缺料"}).json()
        assert client.post(f'/runs/{blocked["id"]}/decision', json={"approve": True}).status_code == 409

def test_unknown_request_does_not_silently_schedule():
    with pytest.raises(ValueError):
        select_scenario("帮我查天气", "demo")

def test_model_tool_call_is_validated(monkeypatch):
    for key, value in {"LLM_API_KEY": "test-only", "LLM_BASE_URL": "https://example.invalid/v1", "LLM_MODEL": "test"}.items():
        monkeypatch.setenv(key, value)
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "get_factory_scenario", "arguments": json.dumps({"scenario": "breakdown"})}}]}}]}
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert url.endswith("/chat/completions")
            assert kwargs["json"]["tools"][0]["function"]["name"] == "get_factory_scenario"
            return Response()
    monkeypatch.setattr("manufacturing.agent.httpx.Client", Client)
    assert select_scenario("设备坏了", "llm")[0] == "breakdown"

def test_llm_missing_configuration(monkeypatch):
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        select_scenario("紧急订单", "llm")
