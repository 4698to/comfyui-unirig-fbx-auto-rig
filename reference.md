# 工具索引：`scripts/ndbox_test.py` 与 `scripts/prompt_test.py`

下文列出与本 Skill 相关的 **公开函数与常量**。实现与签名以 `scripts/` 下源文件为准。

**依赖**：`requests`；WebSocket 相关需 `websocket-client`。

---

## ComfyUI 根地址（必读）

**不在代码中写死主机。** 每次调用 HTTP API 时：

- 传入参数 **`base_url`**（例如 `http://127.0.0.1:8188`），或  
- 设置环境变量 **`COMFYUI_BASE_URL`**（两个模块均读取该变量）。

若二者皆未提供，相关函数会抛出 **`ValueError`**，提示配置地址。

解析逻辑在两模块中均为 **`resolve_base_url(base_url=None) -> str`**。

---

## `ndbox_test.py`（NDBox / userdata）

### 默认与常量

| 名称 | 说明 |
|------|------|
| `COMFYUI_BASE_URL_ENV` | 固定为字符串 `COMFYUI_BASE_URL`，与 `prompt_test` 一致 |
| `DEFAULT_API_PREFIX` | 常为 `"/api"`（userdata 等走带前缀路由时使用） |
| `resolve_base_url(base_url)` | 解析最终使用的根地址 |

### 上传到 input（UniRig 首选）

| 函数 | 作用 |
|------|------|
| `upload_ndbox_api(local_path, *, file_type="any", upload_target="input", upload_subdir="3d", base_url=None, api_prefix="", ...)` | `POST …/NDBox/upload_files`，multipart；**NDBox 路由常不带 `/api`，`api_prefix` 多为 `""`** |
| `upload_ndbox_api_json(local_path, **kwargs)` | 同上，`raise_for_status`，解析 JSON；若响应 `ok is False` 则抛错 |

成功响应常见字段：`file_path`（如 `input/3d/xxx.fbx`），可供 `apply_fbx_to_unirig_load_mesh` / `to_input_relative` 使用。

### 备选：userdata 上传

| 函数 | 作用 |
|------|------|
| `upload_userdata(local_path, remote_path, *, base_url=None, api_prefix=DEFAULT_API_PREFIX, ...)` | `POST …/userdata/{编码后的路径}`，原始 body 为文件字节 |
| `upload_userdata_json(local_path, remote_path, **kwargs)` | 同上，成功后 `json()` |

文件落在 `user/default/...` 一类路径；**是否与 `UniRigLoadMesh` 的 `source_folder` 约定一致需自行核对**。

### NDBox 下载（绑定结果落盘）

| 函数 | 作用 |
|------|------|
| `download_ndbox_api(download_id, *, base_url=None, api_prefix="", encode_path=True, ...)` | `GET …/NDBox/download_file/{id}` |
| `download_ndbox_api_by_filepath(file_path, *, target_type="output", base_url=None, api_prefix="", ...)` | `GET …/NDBox/download_file_by_filepath` |
| `download_ndbox_api_to_file(...)` | 下载写入本地；失败时 **不抛 HTTP 异常**，返回 `DownloadNdBoxToFileResult` |
| `download_ndbox_api_by_filepath_to_file(...)` | 同上，按相对路径下载 |

类型 `DownloadNdBoxToFileResult`：`ok`, `path`, `status_code`, `url`, `error`, `body_preview`。

---

## `prompt_test.py`（`/prompt`、队列、history、工作流）

### 默认与常量

| 名称 | 说明 |
|------|------|
| `SKILL_ROOT` | Skill 根目录路径 |
| `DEFAULT_PROMPT_FILE` | `<Skill>/assets/unirig_prompt.json` |
| `DEFAULT_API_PREFIX` | 常为 `"/api"`（`/prompt`、`/history`、`/queue`） |
| `resolve_base_url(base_url)` | 与 `ndbox_test` 行为一致 |

### URL 与实时进度

| 函数 | 作用 |
|------|------|
| `comfy_websocket_url(base_url, client_id)` | 构造 `ws://…/ws?clientId=…`（一般 **不带** `/api`）；传入的 `base_url` 须已为完整根地址 |
| `iter_comfy_ws_messages(client_id, *, base_url=None, recv_timeout=None)` | 阻塞迭代 WS JSON；`client_id` 须与提交 `/prompt` 时一致 |
| `ws_progress_loop(client_id, *, stop_event, base_url=None, on_progress=None, on_ws_message=None, ws_out=None)` | **后台线程**：解析 WS 中 `type==progress`（`data.value`/`max`）；主线程结束时 `stop_event.set()` 并可 `close` 写入 `ws_out` 的连接 |

`handler.py` 在等待 `/history` 时会并行启动 `ws_progress_loop` 以刷新终端进度条（需 `websocket-client`，可选 `tqdm`）。

### 队列与历史

| 函数 | 作用 |
|------|------|
| `get_queue(**kwargs)` | `GET /queue` |
| `get_prompt_status(**kwargs)` | `GET /prompt` |
| `get_history(prompt_id, **kwargs)` | `GET /history/{prompt_id}` |
| `wait_for_history_entry(prompt_id, *, poll_interval=1.0, timeout=None, **kwargs)` | 轮询直至 history 中出现该 `prompt_id` |

以上 `**kwargs` 须能传给 `get_history`，包含 **`base_url`**（或依赖环境变量）。

### 工作流与提交

| 函数 | 作用 |
|------|------|
| `to_input_relative(path)` | 转为 Comfy `input` 下相对路径（正斜杠），如 `3d/a.fbx` |
| `load_workflow_json(path)` | 读取 API 格式 workflow JSON |
| `apply_fbx_to_unirig_load_mesh(prompt, fbx_path_under_input, *, node_id="71", load_via_obj_path=False)` | 写入 `UniRigLoadMesh` |
| `submit_prompt(prompt, *, base_url=None, ...)` | `POST /prompt`，返回 `Response` |
| `submit_prompt_queue_info(prompt, *, client_id=None, **kwargs)` | 同上；成功解析 JSON，附带 `client_id` |

---

## 典型组合（与本 Skill 一致）

1. （可选）`export COMFYUI_BASE_URL=...` 或在每一步传入 `base_url=...`
2. `upload_ndbox_api_json` → 得到 `file_path`
3. `load_workflow_json` → `apply_fbx_to_unirig_load_mesh(..., file_path)` → `submit_prompt_queue_info`
4. `wait_for_history_entry` 或 `iter_comfy_ws_messages` 等待结束
5. 若输出在 `output/…`：`download_ndbox_api_by_filepath_to_file` / `download_ndbox_api_to_file`

一键串联：**`scripts/handler.py`**（`--base-url` 或环境变量 `COMFYUI_BASE_URL`）。

---

## 何时算「工作流已跑完」

`POST /prompt` 仅表示入队。**判定结束**的常用做法：

1. **`wait_for_history_entry(prompt_id, ...)`**：直到 `GET /history/{prompt_id}` 出现该任务（默认最长多久由 `timeout` 决定）。适合 Agent/脚本在同一轮调用里阻塞等待，无需用户追问进度。
2. **WebSocket**：同一 `client_id` 读 `/ws`，直到收到执行结束相关消息（ finer progress，实现略复杂）。

history 出现后仍应检查条目内 **是否有报错**；失败任务有时也会写入 history。
