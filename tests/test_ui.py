from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from manufacturing.api import app

def test_all_workspace_pages_render_with_real_api(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUFACTURING_RUNTIME", str(tmp_path))
    with TestClient(app) as client:
        def local_request(method, url, **kwargs):
            path = url.split("8010", 1)[1]
            kwargs.pop("timeout", None)
            return client.request(method, path, **kwargs)
        monkeypatch.setattr(httpx, "request", local_request)
        ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "ui.py"), default_timeout=20)
        ui.run()
        assert not ui.exception
        assert any(h.value == "生产排产工作台" for h in ui.title)
        for page in ["订单管理", "设备与工艺", "物料库存", "计划审批", "生产工单", "生产排产"]:
            ui.radio(key="page").set_value(page).run()
            assert not ui.exception, page
        # Edit a real pending order through the form and verify persisted master data.
        ui.radio(key="page").set_value("订单管理").run()
        ui.selectbox[1].select("MO-1109-001").run()
        ui.number_input[0].set_value(42)
        next(button for button in ui.button if button.label == "保存订单").click().run()
        assert not ui.exception
        assert client.get("/workspace").json()["data"]["orders"][0]["quantity"] == 42
