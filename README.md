# comfyui-unirig-fbx-auto-rig

**Cursor Agent Skill**：把本地 **FBX / OBJ** 交给 **ComfyUI**，通过 **UniRig + MIA + NDBox** 工作流完成 **人形角色自动绑骨**，并自动等待任务结束、下载带蒙皮的结果 FBX。

适合：在 Cursor 里用自然语言驱动「上传 → 排队 → 等 history → 落盘」；或在终端 **一条命令** 跑完全流程。

---

## 为什么选择这个 Skill

| 痛点 | 本 Skill 的做法 |
|------|----------------|
| ComfyUI 只返回「已入队」，不知道何时跑完 | **默认阻塞等待** `GET /history/{prompt_id}`，超时时间可调（如 3600～7200 秒） |
| `download_id` 在部分环境 404 | **双路径下载**：优先 `download_id`，失败则回退 `outputs` 中的 `file_path` / `file_name` |
| 脚本写死 IP/端口，换机器就炸 | **不写死主机**：`COMFYUI_BASE_URL` 或 `--base-url` / `base_url=` |
| Agent 说「稍后再查」让用户自己盯 | [SKILL.md](SKILL.md) 约定：**同一轮内必须等到完成或超时**再回复（或显式 `--no-wait`） |
| Windows 中文路径、控制台编码 | 推荐直接用 **`handler.py`** 全流程；避免手写管道内联 Python 传绝对中文路径 |

---

## 功能一览

- **上传**：NDBox `POST …/NDBox/upload_files`（`upload_target` 如 `input`，`upload_subdir` 如 `3d`）；无 NDBox 时可走 userdata（需与节点路径约定一致）。
- **下载**：按 NDBox 的 `download_id` 或按 **output 相对路径** 拉取文件。
- **自动绑骨**：工作流模板见 [assets/unirig_prompt.json](assets/unirig_prompt.json) — `UniRigLoadMesh` → `MIALoadModel` → `MIAAutoRig` → `NDBox_DownloadFile`。
- **一键脚本**：[scripts/handler.py](scripts/handler.py) — 上传 → 修补 workflow → 入队 → **默认等待 history** → 保存为 **`<原名>_skin.fbx`**（可用 `--output` 指定）。
- **进度**：等待 history 时可选 **WebSocket** 订阅 ComfyUI `progress`（需 `websocket-client`；可选 `tqdm` 条形进度）。关闭：`--no-progress-bar`。

---

## 前置条件

1. 可访问的 **ComfyUI** 实例，并安装与模板一致的自定义节点：**UniRig**、**MIA**、**NDBox**（含 `NDBox_DownloadFile`）。
2. Python 依赖：**`requests`**；实时进度需 **`websocket-client`**。
3. 配置 **Comfy 根地址**（任选其一，见下文「快速开始」）。

---

## 安装与分发

将整个文件夹 **`comfyui-unirig-fbx-auto-rig`** 复制到：

- 项目内：**`.cursor/skills/comfyui-unirig-fbx-auto-rig/`**，或  
- 用户全局：**`~/.cursor/skills/`**

在 Cursor 对话中 **@ [SKILL.md](SKILL.md)**，或说明「按 UniRig FBX Skill 执行」，Agent 会按约定完成上传、排队与等待。

**Skill 标识**

- 目录名：`comfyui-unirig-fbx-auto-rig`  
- `SKILL.md` 顶部 YAML `name`：`comfyui-unirig-fbx-auto-rig`

---

## 快速开始

### 1. 配置 ComfyUI 地址（必填其一）

1. 环境变量 **`COMFYUI_BASE_URL`**（例如 `http://127.0.0.1:8188`），或  
2. 运行 `handler.py` 时传入 **`--base-url`**，或  
3. 在代码里调用 `ndbox_test` / `prompt_test` 的 API 时传入 **`base_url=...`**

未配置时会 **`ValueError`** 并提示配置，避免连错主机。

### 2. 一条命令跑绑定（示例）

路径按你的仓库实际位置调整：

```bash
python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py model.fbx --base-url http://127.0.0.1:8188
```

默认在源 FBX 同目录生成 **`model_skin.fbx`**。指定输出：

```bash
python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py model.fbx --base-url http://127.0.0.1:8188 --output path/to/model_skin.fbx
```

**Windows（PowerShell）** 使用环境变量示例：

```powershell
$env:COMFYUI_BASE_URL = "http://127.0.0.1:8188"
python .cursor/skills/comfyui-unirig-fbx-auto-rig/scripts/handler.py model.fbx
```

### 3. 常用 CLI 选项（摘要）

| 选项 | 说明 |
|------|------|
| `--no-wait` | 仅入队，不等待 history |
| `--wait-history <秒>` | 延长等待上限（默认例如 3600） |
| `--no-download` | 等待完成但不下载 |
| `--no-progress-bar` | 关闭 WS 进度显示 |

完整行为以 [scripts/handler.py](scripts/handler.py) 为准。

---

## 文档地图

| 文件 | 用途 |
|------|------|
| [SKILL.md](SKILL.md) | Agent 流程、约束、自检清单、与 Comfy 节点的路径约定 |
| [reference.md](reference.md) | `ndbox_test` / `prompt_test` **函数与常量索引** |
| [assets/unirig_prompt.json](assets/unirig_prompt.json) | API 格式工作流模板（节点 id 以 JSON 为准） |

---

## 工作流链路（概念）

```
本地 FBX
  → NDBox 上传到 input/3d/…
  → 写入 UniRigLoadMesh（如节点 71）
  → POST /prompt 入队
  → 轮询 /history/{prompt_id}（或 WebSocket 看进度）
  → 从 history / outputs 解析下载路径
  → 本地 <原名>_skin.fbx（或 --output）
```

**注意**：`POST /prompt` 成功只表示**入队**；判定「跑完」须依赖 **history 落账** 或 **WebSocket 结束信号**，详见 [SKILL.md](SKILL.md)。

---

## 面向开发者

- **`DEFAULT_API_PREFIX`**：标准 Comfy HTTP 多为 `"/api"`；NDBox 上传路由常 **不带** `/api`，`api_prefix` 多为 `""`。  
- 多用户实例可在参数中传入 **`Comfy-User`** 等（见源码与 [reference.md](reference.md)）。  
- 自行编排 API 时，推荐组合见 [reference.md](reference.md)「典型组合」一节。

---

## 许可证

若本 Skill 随仓库分发，以**仓库根目录**的许可证文件为准；若无单独声明，默认与父仓库一致。

---

**一句话**：把 **ComfyUI + UniRig 自动绑骨** 封装成 Cursor 可理解的 Skill + 可脚本化的一键流水线，**不甩锅给用户盯队列**，**不写死服务地址**，并在刁钻的下载环境下尽量 **自动回退**。
