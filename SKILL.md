---

name: comfyui-unirig-fbx-auto-rig

description: >-

  将本地 FBX 上传到 ComfyUI，并用 UniRig（UniRigLoadMesh）与 MIA（MIALoadModel、MIAAutoRig）

  等工作流节点自动绑定角色骨架；工具位于 Skill 下 scripts/ 与 assets/。

  在用户提到 FBX 自动绑定、UniRig、ComfyUI 角色绑定、Sundaybox/NDBox 上传时使用。

  Agent 执行绑定流程时须在提交 /prompt 后阻塞等待 history（或 WebSocket）直至完成或超时，再回复用户。

disable-model-invocation: true

---



# 角色自动绑定（ComfyUI + UniRig）



本 Skill 描述「本地 FBX → 服务端 input → `/prompt` 执行 UniRig 自动绑定」的步骤；实现代码在 **本 Skill 目录内**，不依赖仓库根目录副本。

## 核心功能
1. ** 上传文件到 comfyui ,支持 设置目标路径，如input,output 
2. ** 下载comfyui 的资源文件，如 input , output 路径下载的文件，传入文件的相对路径即可。
3. ** 自动绑定人形角色模型，支持输入 obj , fbx 格式的3d模型，输入路径 "\input\3d\autorig_actor-Apose.fbx"

## ComfyUI 地址（必填其一）



脚本 **不写死** ComfyUI 主机。使用前必须任选其一：



- 设置环境变量 **`COMFYUI_BASE_URL`**（例如 `http://127.0.0.1:8188`），或  

- 每次调用 Python API 时传入 **`base_url=...`**，或  

- 运行 **`scripts/handler.py`** 时传入 **`--base-url`**。



内部通过 `resolve_base_url()`（`ndbox_test` / `prompt_test`）统一解析；未配置时会报错提示。



## Skill 目录结构（当前布局）



| 路径 | 说明 |

|------|------|

| `scripts/ndbox_test.py` | NDBox 上传/下载、userdata 上传 |

| `scripts/prompt_test.py` | `/prompt`、队列、history、WebSocket、修补 UniRig 节点 |

| `scripts/handler.py` | 一键：上传 → 修补 workflow → 入队 → **默认等待** `/history` 直至任务落账 → 自动下载绑定结果 |

| `assets/unirig_prompt.json` | API 格式工作流模板 |

| [reference.md](reference.md) | 函数级索引 |



## 前置条件



- ComfyUI 可访问，并已配置 **`COMFYUI_BASE_URL`** 或调用处传入 **`base_url`**。

- 已安装 **UniRig**、**MIA**、**NDBox**（含 `NDBox_DownloadFile`）等与 `assets/unirig_prompt.json` 一致的自定义节点。

- 上传优先 **`POST …/NDBox/upload_files`**（`upload_ndbox_api`）；NDBox 路由常不带 `/api`，**`api_prefix` 多为 `""`**。

- Python：`requests`；实时进度可选 `websocket-client`。



## 工作流文件



- 模板：`assets/unirig_prompt.json`。

- 链路：`UniRigLoadMesh` → `MIAAutoRig` → `NDBox_DownloadFile`（节点 id 以 JSON 为准）。



## 操作步骤



### 1. 上传 FBX 到 ComfyUI `input`



使用 `upload_ndbox_api_json`（`scripts/ndbox_test.py`），传入 **`base_url`** 或依赖 **`COMFYUI_BASE_URL`**：



- `upload_target`: `"input"`

- `upload_subdir`: `"3d"`（可按需修改）

- 响应中的 `file_path`（如 `input/3d/xxx.fbx`）用于下一步。



无 NDBox 时可用 `upload_userdata`，注意与 `UniRigLoadMesh` 的路径约定一致。



### 2. 写入 `UniRigLoadMesh`



默认节点 id **`71`**。典型字段：`source_folder` = `"input"`，`file_path` / `mesh_selector` = `3d/<文件名>.fbx`，`obj_path` = `""`。



可用 `apply_fbx_to_unirig_load_mesh(..., load_via_obj_path=False)`，以上传响应的 `file_path` 为输入。



### 3. 提交执行



`load_workflow_json` → `submit_prompt_queue_info`（均需 **`base_url`** 或环境变量）。**注意：`POST /prompt` 成功只表示任务已入队，并不表示绑定已跑完。**



### 4. 等待完成（判定「工作流已结束」）



要让 Skill / 脚本 **在不追问用户的情况下** 知道绑定何时结束，必须在同一轮执行里 **主动阻塞等待「完成信号」**，再向用户汇报结果。可选方式：



1. **HTTP 轮询（推荐，实现简单）**  
   使用 `wait_for_history_entry(prompt_id, timeout=..., poll_interval=1.0, base_url=..., api_prefix=...)`：当 `GET /history/{prompt_id}` 能查到该 id，通常表示 ComfyUI 已为本任务写入历史（执行已结束或已失败落账）。  
   `timeout` 须覆盖自动绑定的最坏耗时（例如 **3600～7200 秒**）；超时则应明确告知用户「仍未写入 history」而非假装完成。

2. **WebSocket（可选，更细进度）**  
   使用与提交时相同的 `client_id` 连接 `/ws`，读取消息直至出现表示执行结束的类型（实现成本高于轮询；详见 `prompt_test.iter_comfy_ws_messages`）。



完成后请 **打开返回的 history 条目**：确认是否有节点错误、输出路径是否齐全；再下载输出文件（同样需要 **`base_url`** 或环境变量）。实测部分 NDBox 环境中，history 返回的 `download_id` 可能在 `/NDBox/download_file/{id}` 上返回 404；此时应回退到 `outputs[*].file_path` 或 `outputs[*].file_name`，调用 `download_ndbox_api_by_filepath_to_file(..., target_type="output")`。



## Agent 执行约定（无需用户追问进度）



当用户委托「跑一遍绑定」时，Agent **不得**在仅调用 `submit_prompt_queue_info` 后就结束回复。



- **必须**：拿到 `prompt_id` 后调用 `wait_for_history_entry`（或 WebSocket 等价逻辑），在同一轮工具流程内等到完成或超时，再根据 history **总结成功/失败与产出路径**。  
- **推荐**：直接运行 `scripts/handler.py`（默认已等待 `/history`，成功后自动下载为源文件同目录 `<原名>_skin.fbx`；可用 `--output` 指定保存路径，用 `--wait-history` 加长等待；若仅需入队则用 `--no-wait`）。  
- **禁止**：把「稍后再查进度」甩给用户，除非用户明确要求异步或 `--no-wait`。



## 一键脚本



在 `scripts/` 目录外执行时指定 Skill 内脚本路径即可。**默认**在入队后轮询 `/history` 最多 **3600 秒**；更长绑定请加 `--wait-history 7200`；只要入队不加等待用 **`--no-wait`**。任务成功后，`handler.py` 会默认下载绑定后的 FBX 到源 FBX 同目录，并把文件名追加 `_skin`，例如 `npc_02.fbx` → `npc_02_skin.fbx`；可用 `--output path/to/result.fbx` 改名，或 `--no-download` 只等待不下载。

下载策略：先尝试 history 中的 `download_id`，若服务端返回 404 或不可写，再回退到 `file_path` / `file_name` 的 output 相对路径下载。Windows 中文目录下建议直接使用 `handler.py` 完整流程；若自行写内联 Python 下载脚本，优先在目标目录作为当前工作目录并传相对输出名，避免控制台编码把中文路径替换成 `??`。

等待期间 **`handler.py` 默认**通过 WebSocket（`/ws`，与提交时同一 `client_id`）订阅 ComfyUI 的 **`progress`** 消息，并在终端显示进度条：**`pip install websocket-client`** 必选；安装 **`tqdm`** 时为小条形进度条，否则为同一行文本 **`ComfyUI 进度 v/max`**。Agent 通过终端运行该脚本时即可看到实时进度。关闭进度显示：**`--no-progress-bar`**。

若 Agent 自行编写脚本，可在后台线程调用 **`prompt_test.ws_progress_loop`**（见 [reference.md](reference.md)），主线程继续 **`wait_for_history_entry`**。



```bash

python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py path/to/model.fbx --base-url http://127.0.0.1:8188

```

指定输出文件：

```bash

python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py path/to/model.fbx --base-url http://127.0.0.1:8188 --output path/to/model_skin.fbx

```



或使用环境变量：



```bash

set COMFYUI_BASE_URL=http://127.0.0.1:8188

python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py path/to/model.fbx

```



## 配置项



- **`DEFAULT_API_PREFIX`**：常为 `"/api"`（标准 Comfy HTTP API）；与 NDBox 的 `api_prefix=""` 可同时存在。

- **`Comfy-User`**：多用户实例时在参数中传入 `comfy_user`。



## 自检清单



- [ ] 已设置 `COMFYUI_BASE_URL` 或每次传入 `base_url` / `handler.py --base-url`

- [ ] NDBox 上传成功且 `file_path` 指向 `input/…`

- [ ] `UniRigLoadMesh` 路径与上传一致（正斜杠）

- [ ] `POST /prompt` 无 `node_errors`

- [ ] 已 **`wait_for_history_entry`**（或等价 WS）等到任务落账，或脚本未使用 `--no-wait`

- [ ] 已查看 history 条目中的错误/输出；`handler.py` 已保存 `<原名>_skin.fbx`，或手动通过 `file_path` / `file_name` 回退下载成功


## 核心输出

绑定完成之后的 FBX 下载链接/输出路径，并默认下载到本地源文件同目录，文件名追加 `_skin`。
 

## 分享给他人



连同整个 **`comfyui-unirig-fbx-auto-rig/`** 文件夹分发（含 `SKILL.md`、`reference.md`、`scripts/`、`assets/`）。详见 [README.md](README.md)。


