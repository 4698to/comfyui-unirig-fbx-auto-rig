#!/usr/bin/env python3
"""
一键：本机 FBX → NDBox 上传到 input → 修补 UniRig prompt → POST /prompt → 下载绑定结果。

与同目录的 ``ndbox_test.py``、``prompt_test.py`` 配合使用；默认工作流为 Skill 目录下
``assets/unirig_prompt.json``。

**ComfyUI 地址**：必须通过 ``--base-url`` 或环境变量 ``COMFYUI_BASE_URL`` 提供（无内置默认主机）。

用法示例：

  python handler.py path/to/model.fbx --base-url http://127.0.0.1:8188

  默认会最长等待 3600s 直至 /history 出现该任务；更长绑定可加 --wait-history 7200。
  绑定成功后默认下载到源 FBX 同目录，文件名追加 _skin。

  默认在等待期间通过 WebSocket 显示进度（需 pip install websocket-client；可选 pip install tqdm）。

  python handler.py model.fbx --base-url http://127.0.0.1:8188 --no-wait   # 只入队不等待

  python handler.py model.fbx --base-url http://127.0.0.1:8188 --no-progress-bar
"""
from __future__ import annotations

import argparse
import json
import os
import ntpath
import sys
import threading
import time
import uuid
from pathlib import Path


def _skill_root() -> Path:
    """Skill 根目录（含 ``assets/``、``scripts/``）。"""
    return Path(__file__).resolve().parent.parent


def _require_base_url(cli_value: str | None) -> str:
    u = (cli_value or os.environ.get("COMFYUI_BASE_URL", "") or "").strip().rstrip("/")
    if not u:
        print(
            "错误：未配置 ComfyUI 根地址。请传入 --base-url，或设置环境变量 COMFYUI_BASE_URL\n"
            "示例：--base-url http://127.0.0.1:8188",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return u


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _output_path_candidates(value: object) -> list[str]:
    """Return NDBox output-relative candidates from history output values."""
    candidates: list[str] = []
    for item in _as_list(value):
        if not isinstance(item, str) or not item.strip():
            continue
        raw = item.replace("\\", "/").strip()
        lowered = raw.lower()
        if "/output/" in lowered:
            raw = raw[lowered.rfind("/output/") + len("/output/") :]
        elif lowered.startswith("output/"):
            raw = raw[len("output/") :]
        basename = ntpath.basename(raw)
        for candidate in (raw.lstrip("/"), basename):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _default_output_path(fbx: Path) -> Path:
    return fbx.with_name(f"{fbx.stem}_skin{fbx.suffix}")


def _download_bound_output(
    hist: dict,
    *,
    dest_path: Path,
    base_url: str,
    ndbox_api_prefix: str,
    comfy_user: str | None,
    ndbox_test: object,
) -> bool:
    outputs = hist.get("outputs") if isinstance(hist, dict) else None
    if not isinstance(outputs, dict):
        print("history 条目缺少 outputs，无法自动下载绑定结果", file=sys.stderr)
        return False

    attempts: list[tuple[str, object, str | None]] = []
    fallback_name = dest_path.name
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for download_id in _as_list(node_out.get("download_id")):
            if isinstance(download_id, str) and download_id.strip():
                attempts.append(("download_id", download_id.strip(), str(node_id)))
        for file_path in _output_path_candidates(node_out.get("file_path")):
            attempts.append(("file_path", file_path, str(node_id)))
        for file_name in _output_path_candidates(node_out.get("file_name")):
            attempts.append(("file_path", file_name, str(node_id)))

    seen: set[tuple[str, object]] = set()
    deduped: list[tuple[str, object, str | None]] = []
    for kind, value, node_id in attempts:
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            deduped.append((kind, value, node_id))

    if not deduped:
        print("history outputs 中没有 download_id / file_path / file_name，无法自动下载", file=sys.stderr)
        return False

    print(f"准备下载绑定结果到: {dest_path}")
    last_error = ""
    for kind, value, node_id in deduped:
        if kind == "download_id":
            result = ndbox_test.download_ndbox_api_to_file(
                value,
                dest_path,
                fallback_filename=fallback_name,
                base_url=base_url,
                api_prefix=ndbox_api_prefix,
                comfy_user=comfy_user,
            )
        else:
            result = ndbox_test.download_ndbox_api_by_filepath_to_file(
                value,
                dest_path,
                target_type="output",
                fallback_filename=fallback_name,
                base_url=base_url,
                api_prefix=ndbox_api_prefix,
                comfy_user=comfy_user,
            )

        if result.ok:
            print(f"下载成功: {result.path} (node {node_id}, {kind}={value})")
            return True

        last_error = f"{kind}={value}: HTTP {result.status_code}; {result.error}"
        print(f"下载尝试失败，继续回退: {last_error}", file=sys.stderr)

    print(f"自动下载失败: {last_error}", file=sys.stderr)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="上传 FBX 并以 UniRig 工作流入队 ComfyUI")
    parser.add_argument("fbx", type=Path, help="本地 FBX 文件路径")
    parser.add_argument(
        "--workflow",
        type=Path,
        default=None,
        help="workflow JSON（默认：<skill>/assets/unirig_prompt.json）",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="ComfyUI 根地址；可不传，改用环境变量 COMFYUI_BASE_URL",
    )
    parser.add_argument(
        "--api-prefix",
        default=None,
        help='标准 API 前缀（默认用 prompt_test.DEFAULT_API_PREFIX，常为 "/api"）',
    )
    parser.add_argument(
        "--ndbox-api-prefix",
        default="",
        help='NDBox 路由前缀（默认 ""，即 BASE + /NDBox/...）',
    )
    parser.add_argument("--upload-subdir", default="3d", help="NDBox upload_subdir")
    parser.add_argument("--comfy-user", default=None, help="Comfy-User 请求头")
    parser.add_argument(
        "--wait-history",
        type=float,
        default=3600.0,
        metavar="SEC",
        help="入队后轮询 /history 直至出现该任务的最长等待秒数（默认 3600）",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="仅提交队列，不等待执行结束（Skill/自动化场景不推荐）",
    )
    parser.add_argument(
        "--no-progress-bar",
        action="store_true",
        dest="no_progress_bar",
        help="等待执行时不连接 /ws 显示进度（与 --no-wait 同时出现时无效果）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="绑定结果保存路径（默认：源 FBX 同目录，文件名追加 _skin）",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="等待完成后不自动下载绑定结果",
    )
    args = parser.parse_args()

    skill_root = _skill_root()
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import ndbox_test
    import prompt_test

    base = _require_base_url(args.base_url)
    api_prefix = (
        args.api_prefix if args.api_prefix is not None else prompt_test.DEFAULT_API_PREFIX
    )

    fbx = args.fbx.expanduser().resolve()
    if not fbx.is_file():
        print(f"找不到文件: {fbx}", file=sys.stderr)
        return 1

    wf_path = args.workflow
    if wf_path is None:
        wf_path = skill_root / "assets" / "unirig_prompt.json"
    else:
        wf_path = wf_path.expanduser()
        if not wf_path.is_absolute():
            wf_path = (Path.cwd() / wf_path).resolve()

    if not wf_path.is_file():
        print(f"找不到工作流文件: {wf_path}", file=sys.stderr)
        return 1

    print("上传:", fbx)
    up = ndbox_test.upload_ndbox_api_json(
        fbx,
        base_url=base,
        api_prefix=args.ndbox_api_prefix,
        upload_target="input",
        upload_subdir=args.upload_subdir,
        comfy_user=args.comfy_user,
    )
    print("上传响应:", json.dumps(up, ensure_ascii=False, indent=2))

    file_path = up.get("file_path") if isinstance(up, dict) else None
    if not file_path:
        print("上传响应缺少 file_path，无法自动修补 UniRig 节点", file=sys.stderr)
        return 2

    wf = prompt_test.load_workflow_json(wf_path)
    wf = prompt_test.apply_fbx_to_unirig_load_mesh(wf, file_path)

    common = {"base_url": base, "api_prefix": api_prefix, "comfy_user": args.comfy_user}
    client_id = uuid.uuid4().hex

    info = prompt_test.submit_prompt_queue_info(wf, client_id=client_id, **common)
    print("已入队:", json.dumps(info, ensure_ascii=False, indent=2))

    pid = info.get("prompt_id") if isinstance(info, dict) else None
    if pid and not args.no_wait:
        show_progress = not args.no_progress_bar
        stop_ev = threading.Event()
        ws_holder: list[object] = []
        ws_thread: threading.Thread | None = None
        pbar = None
        text_progress = False

        if show_progress:
            try:
                import websocket  # noqa: F401

                try:
                    from tqdm import tqdm

                    pbar = tqdm(
                        total=100,
                        dynamic_ncols=True,
                        desc="ComfyUI",
                        leave=False,
                        unit="step",
                    )

                    def _on_progress(v: int, mx: int) -> None:
                        assert pbar is not None
                        pbar.total = max(mx, 1)
                        pbar.n = min(v, int(pbar.total))
                        pbar.refresh()

                except ImportError:

                    def _on_progress(v: int, mx: int) -> None:
                        print(f"\rComfyUI 进度 {v}/{mx}", end="", flush=True)

                    text_progress = True

                def _ws_worker() -> None:
                    try:
                        prompt_test.ws_progress_loop(
                            client_id,
                            base_url=base,
                            stop_event=stop_ev,
                            on_progress=_on_progress,
                            ws_out=ws_holder,
                        )
                    except ImportError:
                        pass
                    except Exception as e:
                        print(f"\nWebSocket 进度线程异常（可忽略）: {e}", file=sys.stderr)

                ws_thread = threading.Thread(target=_ws_worker, daemon=True)
                ws_thread.start()
                time.sleep(0.2)
            except ImportError:
                print(
                    "提示: pip install websocket-client 后可在等待期间显示进度条",
                    file=sys.stderr,
                )
                show_progress = False

        print(f"等待任务完成（/history，最长 {args.wait_history}s）: {pid!r}")
        try:
            hist = prompt_test.wait_for_history_entry(
                pid, timeout=args.wait_history, **common
            )
        except TimeoutError as e:
            print(f"等待超时: {e}", file=sys.stderr)
            return 3
        finally:
            stop_ev.set()
            for w in ws_holder:
                try:
                    close_fn = getattr(w, "close", None)
                    if callable(close_fn):
                        close_fn()
                except Exception:
                    pass
            if ws_thread is not None:
                ws_thread.join(timeout=8.0)
            if pbar is not None:
                pbar.close()
            elif text_progress and show_progress:
                print()

        print("history 条目:", json.dumps(hist, ensure_ascii=False, indent=2)[:8000])
        status = hist.get("status") if isinstance(hist, dict) else {}
        completed = isinstance(status, dict) and status.get("completed") is True
        status_str = status.get("status_str") if isinstance(status, dict) else None
        if args.no_download:
            print("已跳过下载（--no-download）。")
        elif completed and status_str == "success":
            out_path = args.output.expanduser() if args.output is not None else _default_output_path(fbx)
            if not out_path.is_absolute():
                out_path = (Path.cwd() / out_path).resolve()
            ok = _download_bound_output(
                hist,
                dest_path=out_path,
                base_url=base,
                ndbox_api_prefix=args.ndbox_api_prefix,
                comfy_user=args.comfy_user,
                ndbox_test=ndbox_test,
            )
            if not ok:
                return 4
        else:
            print(f"任务未成功完成（status={status_str!r}），跳过自动下载。", file=sys.stderr)
            return 5
    elif args.no_wait:
        print("已跳过等待（--no-wait）；请自行轮询 /history 或 WebSocket 查看进度。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
