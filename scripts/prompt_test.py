"""
通过 ComfyUI POST /prompt 提交并排队执行工作流。

文档：https://docs.comfy.org/development/comfyui-server/comms_routes

执行进度怎么查（三种常见方式）：
1. WebSocket ``/ws?clientId=…``：与提交 /prompt 时使用同一 ``client_id``，可收到
   ``progress`` / ``executing`` / ``execution_start`` 等实时消息（推荐）。
2. HTTP ``GET /queue``：看 ``queue_running`` / ``queue_pending``，只知道是否在跑、排第几。
3. HTTP ``GET /history/{prompt_id}``：任务结束后可查输出与状态；未完成时可能查不到或为空。

本模块提供 ``iter_comfy_ws_messages``、后台进度循环 ``ws_progress_loop``（需 ``pip install websocket-client``），以及
``get_queue`` / ``get_prompt_status`` / ``get_history`` / ``wait_for_history_entry``。
"""
from __future__ import annotations

import copy
import json
import os
import time
import urllib.parse
import uuid
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

COMFYUI_BASE_URL_ENV = "COMFYUI_BASE_URL"


def resolve_base_url(base_url: str | None = None) -> str:
    """
    ComfyUI HTTP(S) 根地址（无末尾斜杠）。
    须在各调用中传入 ``base_url``，或设置环境变量 ``COMFYUI_BASE_URL``。
    """
    u = (base_url or os.environ.get(COMFYUI_BASE_URL_ENV, "") or "").strip().rstrip("/")
    if not u:
        raise ValueError(
            "未配置 ComfyUI 地址：请传入 base_url=...，"
            f"或设置环境变量 {COMFYUI_BASE_URL_ENV}（例如 http://127.0.0.1:8188）"
        )
    return u


DEFAULT_API_PREFIX = "/api"
SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_FILE = SKILL_ROOT / "assets" / "unirig_prompt.json"


def _api_url(base_url: str, api_prefix: str, subpath: str) -> str:
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    sp = subpath if subpath.startswith("/") else f"/{subpath}"
    return f"{base_url.rstrip('/')}{prefix}{sp}"


def comfy_websocket_url(base_url: str, client_id: str) -> str:
    """Comfy 前端约定：WebSocket 在站点根路径 ``/ws``，一般不带 ``/api`` 前缀。"""
    bu = base_url.rstrip("/")
    if bu.startswith("http://"):
        host = "ws://" + bu[len("http://") :]
    elif bu.startswith("https://"):
        host = "wss://" + bu[len("https://") :]
    else:
        host = bu
    q = urllib.parse.urlencode({"clientId": client_id})
    return f"{host}/ws?{q}"


def get_queue(
    *,
    base_url: str | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
    timeout: float = 60,
    comfy_user: str | None = None,
) -> dict[str, Any]:
    """GET /queue：当前排队与正在运行的任务。"""
    bu = resolve_base_url(base_url)
    url = _api_url(bu, api_prefix, "/queue")
    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user
    r = requests.get(url, headers=headers or None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_prompt_status(
    *,
    base_url: str | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
    timeout: float = 60,
    comfy_user: str | None = None,
) -> dict[str, Any]:
    """GET /prompt：队列与执行相关概要（与官方文档一致）。"""
    bu = resolve_base_url(base_url)
    url = _api_url(bu, api_prefix, "/prompt")
    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user
    r = requests.get(url, headers=headers or None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_history(
    prompt_id: str,
    *,
    base_url: str | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
    timeout: float = 60,
    comfy_user: str | None = None,
) -> dict[str, Any]:
    """GET /history/{prompt_id}：该任务的完整历史（完成后才有可靠输出）。"""
    bu = resolve_base_url(base_url)
    safe = urllib.parse.quote(prompt_id, safe="")
    url = _api_url(bu, api_prefix, f"/history/{safe}")
    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user
    r = requests.get(url, headers=headers or None, timeout=timeout)
    r.raise_for_status()
    return r.json()


def wait_for_history_entry(
    prompt_id: str,
    *,
    poll_interval: float = 1.0,
    timeout: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    轮询 ``/history/{prompt_id}``，直到该 ``prompt_id`` 出现在响应中。
    返回该 id 对应的历史对象（非整份 ``get_history`` 外层字典）。
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        h = get_history(prompt_id, **kwargs)
        if isinstance(h, dict) and prompt_id in h:
            return h[prompt_id]
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"等待 history 超时: {prompt_id!r}")
        time.sleep(poll_interval)


def iter_comfy_ws_messages(
    client_id: str,
    *,
    base_url: str | None = None,
    recv_timeout: float | None = None,
) -> Iterator[dict[str, Any]]:
    """
    阻塞读取 WebSocket 消息，每条为 JSON 对象，常见 ``type``：
    ``status``, ``execution_start``, ``executing``, ``progress``, ``executed``, ``execution_cached`` 等。

    需安装：``pip install websocket-client``

    注意：``client_id`` 必须与 ``submit_prompt(..., client_id=同一值)`` 一致。
    """
    try:
        from websocket import WebSocketConnectionClosedException, create_connection
    except ImportError as e:
        raise ImportError(
            "实时进度需要 websocket-client，请执行：pip install websocket-client"
        ) from e

    bu = resolve_base_url(base_url)
    url = comfy_websocket_url(bu, client_id)
    ws = create_connection(url, timeout=recv_timeout)
    try:
        while True:
            try:
                raw = ws.recv()
            except WebSocketConnectionClosedException:
                break
            if not raw:
                break
            yield json.loads(raw)
    finally:
        ws.close()


def ws_progress_loop(
    client_id: str,
    *,
    base_url: str | None = None,
    stop_event: threading.Event,
    poll: float = 0.25,
    on_progress: Callable[[int, int], None] | None = None,
    on_ws_message: Callable[[dict[str, Any]], None] | None = None,
    ws_out: list[Any] | None = None,
) -> None:
    """
    在**独立线程**中连接 ``/ws``，直到 ``stop_event`` 被置位。

    解析 ``type`` 为 ``progress`` 的消息（``data.value`` / ``data.max``），回调 ``on_progress``。
    可选 ``on_ws_message`` 接收每条 JSON（例如自行处理 ``executing``）。
    若传入 ``ws_out``，会把创建的 WebSocket 实例 append 进去，便于主线程在结束时 ``close()``。

    依赖 ``websocket-client``；未安装时由调用方捕获 ``ImportError``。
    """
    try:
        from websocket import WebSocketConnectionClosedException, create_connection
    except ImportError:
        raise ImportError(
            "实时进度需要 websocket-client，请执行：pip install websocket-client"
        ) from None

    bu = resolve_base_url(base_url)
    url = comfy_websocket_url(bu, client_id)
    ws = create_connection(url)
    if ws_out is not None:
        ws_out.append(ws)
    try:
        try:
            ws.settimeout(poll)
        except AttributeError:
            try:
                ws.sock.settimeout(poll)
            except AttributeError:
                pass
        while not stop_event.is_set():
            try:
                raw = ws.recv()
            except socket.timeout:
                continue
            except WebSocketConnectionClosedException:
                break
            if not raw:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if on_ws_message:
                on_ws_message(msg)
            if msg.get("type") == "progress" and on_progress:
                data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                try:
                    v = int(data.get("value", 0))
                    mx = max(int(data.get("max", 1)), 1)
                except (TypeError, ValueError):
                    continue
                on_progress(v, mx)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def to_input_relative(path: str | Path) -> str:
    """
    转成 Comfy「input」目录下的相对路径（正斜杠）。
    支持 input/3d/a.fbx、绝对路径 .../input/3d/a.fbx，或已是 3d/a.fbx。
    """
    raw = Path(path)
    try:
        p = raw.resolve()
    except OSError:
        p = raw
    parts = p.parts
    for i, part in enumerate(parts):
        if part.lower() == "input":
            rest = parts[i + 1 :]
            return "/".join(rest) if rest else ""
    rel_parts = raw.parts
    if rel_parts and rel_parts[0].lower() == "input":
        return "/".join(rel_parts[1:])
    return raw.as_posix().replace("\\", "/")


def load_workflow_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def apply_fbx_to_unirig_load_mesh(
    prompt: dict[str, Any],
    fbx_path_under_input: str | Path,
    *,
    node_id: str = "71",
    load_via_obj_path: bool = False,
) -> dict[str, Any]:
    """
    为 UniRigLoadMesh（默认节点 71）写入网格路径。

    - ``load_via_obj_path=False``（默认）：按 ``source_folder`` + ``file_path`` /
      ``mesh_selector`` 从 Comfy 的 input 目录加载（与导出的 JSON 一致）。
    - ``load_via_obj_path=True``：只填 ``obj_path``，并清空 ``file_path`` /
      ``mesh_selector``（仅当节点确实支持该用法时使用，否则易触发 API 400）。
    """
    rel = to_input_relative(fbx_path_under_input)
    p = copy.deepcopy(prompt)
    node = p.get(node_id)
    if not node or not isinstance(node.get("inputs"), dict):
        raise KeyError(f"节点 {node_id} 不存在或缺少 inputs")

    if load_via_obj_path:
        node["inputs"]["obj_path"] = rel
        node["inputs"]["file_path"] = ""
        node["inputs"]["mesh_selector"] = ""
    else:
        node["inputs"]["obj_path"] = ""
        node["inputs"]["file_path"] = rel
        node["inputs"]["mesh_selector"] = rel
    return p


def submit_prompt(
    prompt: dict[str, Any],
    *,
    base_url: str | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
    client_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
    timeout: float = 300,
    comfy_user: str | None = None,
) -> requests.Response:
    bu = resolve_base_url(base_url)
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    url = f"{bu}{prefix}/prompt"
    body: dict[str, Any] = {
        "prompt": prompt,
        "client_id": client_id or uuid.uuid4().hex,
    }
    if extra_data is not None:
        body["extra_data"] = extra_data

    headers = {"Content-Type": "application/json"}
    if comfy_user:
        headers["Comfy-User"] = comfy_user

    return requests.post(url, json=body, headers=headers, timeout=timeout)


def submit_prompt_queue_info(
    prompt: dict[str, Any],
    *,
    client_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    提交工作流；成功时返回 JSON（含 prompt_id、number 等），并附带 ``client_id`` 便于连 WebSocket。

    HTTP 4xx/5xx 或响应里带 error/node_errors 时抛错。
    """
    cid = client_id or uuid.uuid4().hex
    r = submit_prompt(prompt, client_id=cid, **kwargs)

    def _body_preview() -> str:
        raw = (r.text or "").strip()
        if not raw:
            return "(empty body)"
        try:
            parsed = r.json()
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except ValueError:
            return raw[:8000]

    if not r.ok:
        raise RuntimeError(
            f"POST /prompt HTTP {r.status_code} for {r.url}\n{_body_preview()}"
        )

    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"POST /prompt 返回非 JSON（HTTP {r.status_code}）:\n{r.text[:4000]}"
        ) from None

    if isinstance(data, dict) and data.get("error") is not None:
        err = data.get("error")
        detail = data.get("node_errors", data)
        raise RuntimeError(f"ComfyUI prompt 校验失败: {err!r} | {detail}")
    if not isinstance(data, dict):
        return {"result": data, "client_id": cid}
    out = dict(data)
    out["client_id"] = cid
    return out


if __name__ == "__main__":
    wf = load_workflow_json(DEFAULT_PROMPT_FILE)
    wf = apply_fbx_to_unirig_load_mesh(wf, "input/3d/autorig_actor-Apose-test.fbx")
    info = submit_prompt_queue_info(wf)
    print("已入队:", info)
