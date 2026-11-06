"""Optional bounded tool selection through a Chat Completions compatible provider."""
import json
import os
import httpx
from .factory import SCENARIOS

def select_scenario(message: str, mode: str, explicit: str | None = None) -> tuple[str, str]:
    if explicit:
        if explicit not in SCENARIOS:
            raise ValueError("未知场景")
        return explicit, "用户选择演示场景"
    if mode == "demo":
        # Explicitly disclosed rule-based demo, not an LLM inference.
        if any(word in message for word in ("缺料", "短缺", "库存不足")):
            return "shortage", "离线关键词路由：物料短缺"
        if any(word in message for word in ("故障", "停机", "坏了")):
            return "breakdown", "离线关键词路由：设备故障"
        if any(word in message for word in ("紧急", "插单", "加急")):
            return "rush", "离线关键词路由：紧急插单"
        if any(word in message for word in ("正常", "排程", "生产计划")):
            return "normal", "离线关键词路由：正常订单"
        raise ValueError("演示模式支持正常排程、紧急插单、设备故障、缺料四类请求；数量和交期使用固定场景数据。")
    key, base, model = (os.environ.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))
    if not all((key, base, model)):
        raise ValueError("模型模式需要设置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL")
    tool = {"type": "function", "function": {"name": "get_factory_scenario",
        "description": "选择并查询一个固定的模拟工厂场景；不支持自定义数量、日期或设备操作。",
        "parameters": {"type": "object", "properties": {"scenario": {"type": "string", "enum": list(SCENARIOS)}},
                       "required": ["scenario"], "additionalProperties": False}}}
    with httpx.Client(timeout=30) as client:
        response = client.post(base.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "messages": [
                {"role": "system", "content": "你是模拟工厂助手。仅在请求匹配四类固定场景时调用工具。其他请求说明不支持，不得编造执行结果。"},
                {"role": "user", "content": message}], "tools": [tool], "tool_choice": "auto"})
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]
    calls = result.get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"]["name"] != "get_factory_scenario":
        raise ValueError("模型没有选择支持的工厂查询工具，请明确选择演示场景。")
    args = json.loads(calls[0]["function"]["arguments"])
    if set(args) != {"scenario"} or args["scenario"] not in SCENARIOS:
        raise ValueError("模型工具参数未通过校验")
    return args["scenario"], "模型工具调用：get_factory_scenario（参数已校验）"
