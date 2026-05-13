"""
测试 ComfyUI userdata 上传（POST /userdata/{path}）。

文档：https://docs.comfy.org/development/comfyui-server/comms_routes
若实例挂在反向代理下，按需传入 api_prefix（例如 "/api"）。
"""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, NamedTuple

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


# 直连 ComfyUI 一般为 ""；若访问地址是 .../api/prompt 则常为 "/api"
DEFAULT_API_PREFIX = "/api"


class DownloadNdBoxToFileResult(NamedTuple):
    """NDBox 下载落盘结果（``download_ndbox_api_to_file`` / ``download_ndbox_api_by_filepath_to_file``）；失败时不抛异常，请检查 ``ok``。"""

    ok: bool
    path: Path | None
    status_code: int
    url: str
    error: str
    body_preview: str


def _filename_from_content_disposition(value: str | None) -> str | None:
    """从 ``Content-Disposition`` 解析文件名（支持 ``filename*`` RFC 5987 与 ``filename``）。"""
    if not value:
        return None
    # filename*=UTF-8''foo%20bar.bin
    m = re.search(r"filename\*\s*=\s*(?:UTF-8''|)([^;\r\n]+)", value, flags=re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    # filename="..." or filename=...
    m = re.search(r'filename\s*=\s*"([^"]*)"', value, flags=re.IGNORECASE)
    if m:
        q = m.group(1)
        return q if q else None
    m = re.search(r"filename\s*=\s*([^;\r\n]+)", value, flags=re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    return None


def download_ndbox_api(
    download_id: str,
    *,
    base_url: str | None = None,
    api_prefix: str = "",
    timeout: float = 120,
    comfy_user: str | None = None,
    stream: bool = False,
    encode_path: bool = True,
) -> requests.Response:
    """
    GET ``/NDBox/download_file/{download_id}``（Sundaybox / NDBox 插件）。

    ``download_id`` 一般为服务端识别的文件标识或相对路径（如 ``output/3d/xxx.npz``）。
    默认将整段路径 ``quote(..., safe=\"\")`` 作为 URL 中单个路径段，避免含 ``/`` 时被路由拆错。

    若你的服务端路由是按字面多级路径匹配的，可设 ``encode_path=False``。

    若路由未挂 ``/api`` 前缀，请传 ``api_prefix=\"\"``。
    """
    bu = resolve_base_url(base_url)
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    rel = download_id.replace("\\", "/").lstrip("/")
    segment = urllib.parse.quote(rel, safe="") if encode_path else rel
    url = f"{bu}{prefix}/NDBox/download_file/{segment}"

    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user

    return requests.get(
        url,
        headers=headers or None,
        timeout=timeout,
        stream=stream,
    )


def download_ndbox_api_by_filepath(
    file_path: str,
    *,
    target_type: str = "output",
    base_url: str | None = None,
    api_prefix: str = "",
    timeout: float = 120,
    comfy_user: str | None = None,
    stream: bool = True,
) -> requests.Response:
    """
    GET ``/NDBox/download_file_by_filepath``：通过查询参数按相对路径下载。

    对应服务端形如：
    ``?target_type=output&file_path=3d/demo.npz``

    ``file_path`` 为相对 ``target_type`` 根目录的路径（使用 ``/``）。

    ``stream=True`` 时请在用完后关闭响应（建议使用 ``with``）。
    """
    bu = resolve_base_url(base_url)
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    url = f"{bu}{prefix}/NDBox/download_file_by_filepath"
    params = {
        "target_type": target_type,
        "file_path": file_path.replace("\\", "/").lstrip("/"),
    }
    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user
    return requests.get(
        url,
        params=params,
        headers=headers or None,
        timeout=timeout,
        stream=stream,
    )


def _response_binary_preview(r: requests.Response, limit: int) -> str:
    try:
        return (r.content or b"")[:limit].decode("utf-8", errors="replace")
    except Exception:
        return ""


def download_ndbox_api_by_filepath_to_file(
    file_path: str,
    dest_path: str | Path | None = None,
    *,
    target_type: str = "output",
    fallback_filename: str | None = None,
    chunk_size: int = 256 * 1024,
    body_preview_limit: int = 4000,
    **kwargs: Any,
) -> DownloadNdBoxToFileResult:
    """
    按 ``target_type`` + ``file_path`` 下载并写入本地（默认分块流式写入）。

    行为与 ``download_ndbox_api_to_file`` 一致：**HTTP 错误不抛异常**，返回 ``DownloadNdBoxToFileResult``。
    成功时路径规则相同；若无 ``Content-Disposition``，会用 ``fallback_filename`` 或 ``file_path`` 的最后一段作为文件名。
    """
    kw = dict(kwargs)
    kw.pop("chunk_size", None)
    kw.pop("body_preview_limit", None)
    kw.setdefault("stream", True)
    try:
        with download_ndbox_api_by_filepath(file_path, target_type=target_type, **kw) as r:
            if not r.ok:
                return DownloadNdBoxToFileResult(
                    ok=False,
                    path=None,
                    status_code=r.status_code,
                    url=r.url,
                    error=f"HTTP {r.status_code} {r.reason}",
                    body_preview=_response_binary_preview(r, body_preview_limit),
                )

            cd_name = _filename_from_content_disposition(r.headers.get("Content-Disposition"))
            path_norm = file_path.replace("\\", "/")
            basename = cd_name or fallback_filename or Path(path_norm).name
            if not basename:
                return DownloadNdBoxToFileResult(
                    ok=False,
                    path=None,
                    status_code=r.status_code,
                    url=r.url,
                    error="无法确定保存文件名",
                    body_preview=_response_binary_preview(r, body_preview_limit),
                )

            if dest_path is None:
                out = Path.cwd() / Path(basename).name
            else:
                dp = Path(dest_path)
                if dp.exists() and dp.is_dir():
                    out = dp / Path(basename).name
                else:
                    out = dp

            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
            except OSError as e:
                return DownloadNdBoxToFileResult(
                    ok=False,
                    path=None,
                    status_code=r.status_code,
                    url=r.url,
                    error=f"写入磁盘失败: {e}",
                    body_preview="",
                )

            return DownloadNdBoxToFileResult(
                ok=True,
                path=out,
                status_code=r.status_code,
                url=r.url,
                error="",
                body_preview="",
            )
    except requests.RequestException as e:
        return DownloadNdBoxToFileResult(
            ok=False,
            path=None,
            status_code=0,
            url="",
            error=f"请求异常: {e}",
            body_preview="",
        )


def download_ndbox_api_to_file(
    download_id: str,
    dest_path: str | Path | None = None,
    *,
    fallback_filename: str | None = None,
    body_preview_limit: int = 4000,
    **kwargs: Any,
) -> DownloadNdBoxToFileResult:
    """
    下载并写入本地。**HTTP 错误（如 404）不抛异常**，返回 ``DownloadNdBoxToFileResult``，请查看 ``ok``、``error``、``body_preview``。

    成功时 ``ok=True`` 且 ``path`` 为写入路径。

    默认使用响应头 ``Content-Disposition`` 中的文件名作为保存名：

    - ``dest_path`` 为 ``None``：保存到当前工作目录 ``Path.cwd()`` / 解析出的文件名。
    - ``dest_path`` 为已存在的目录：保存为 ``该目录 / 解析出的文件名``。
    - ``dest_path`` 为文件路径（非已存在目录）：完整路径仍以 ``dest_path`` 为准（不使用 CD 文件名）。

    若无 ``Content-Disposition`` 或解析不到文件名，可使用 ``fallback_filename``；
    二者皆无时 ``ok=False``，不写入文件。
    """
    kw = dict(kwargs)
    kw.pop("stream", None)
    r = download_ndbox_api(download_id, stream=False, **kw)

    def _preview() -> str:
        return (r.text or "")[:body_preview_limit]

    if not r.ok:
        return DownloadNdBoxToFileResult(
            ok=False,
            path=None,
            status_code=r.status_code,
            url=r.url,
            error=f"HTTP {r.status_code} {r.reason}",
            body_preview=_preview(),
        )

    cd_name = _filename_from_content_disposition(r.headers.get("Content-Disposition"))
    basename = cd_name or fallback_filename
    if basename is None:
        return DownloadNdBoxToFileResult(
            ok=False,
            path=None,
            status_code=r.status_code,
            url=r.url,
            error="无可用文件名：缺少 Content-Disposition 中的 filename，且未传入 fallback_filename",
            body_preview=_preview(),
        )

    if dest_path is None:
        out = Path.cwd() / Path(basename).name
    else:
        dp = Path(dest_path)
        if dp.exists() and dp.is_dir():
            out = dp / Path(basename).name
        else:
            out = dp

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
    except OSError as e:
        return DownloadNdBoxToFileResult(
            ok=False,
            path=None,
            status_code=r.status_code,
            url=r.url,
            error=f"写入磁盘失败: {e}",
            body_preview=_preview(),
        )

    return DownloadNdBoxToFileResult(
        ok=True,
        path=out,
        status_code=r.status_code,
        url=r.url,
        error="",
        body_preview="",
    )


def upload_ndbox_api(
    local_path: str | Path,
    *,
    file_type: str = "any",
    upload_target: str = "input",
    upload_subdir: str = "3d",
    base_url: str | None = None,
    api_prefix: str = "",
    timeout: float = 120,
    comfy_user: str | None = None,
) -> requests.Response:
    """
    使用 ComfyUI-Sundaybox 插件的上传接口，支持将文件上传到 input、output 目录下。
    这个接口兼容性更好，上传的文件直接就在 input ,output 路径下，其他节点能够方便的获取到文件。

    POST ``/NDBox/upload_files``：与服务端 ``multipart()`` 一致。

    表单字段：``file``（文件正文 + filename）、``file_type``、``upload_target``、
    ``upload_subdir``。成功时响应 JSON 含 ``ok``、``file_path``（如 ``output/3d/xxx.npz``）。

    若路由未挂 ``/api`` 前缀，请传 ``api_prefix=\"\"``。
    """
    bu = resolve_base_url(base_url)
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    url = f"{bu}{prefix}/NDBox/upload_files"

    local = Path(local_path)
    headers: dict[str, str] = {}
    if comfy_user:
        headers["Comfy-User"] = comfy_user

    data = {
        "file_type": file_type,
        "upload_target": upload_target,
        "upload_subdir": upload_subdir,
    }

    with local.open("rb") as f:
        files = {"file": (local.name, f, "application/octet-stream")}
        return requests.post(
            url,
            files=files,
            data=data,
            headers=headers or None,
            timeout=timeout,
        )


def upload_ndbox_api_json(
    local_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    r = upload_ndbox_api(local_path, **kwargs)
    r.raise_for_status()
    out = r.json()
    if isinstance(out, dict) and out.get("ok") is False:
        raise RuntimeError(out.get("error", out))
    return out

def upload_userdata(
    local_path: str | Path,
    remote_path: str,
    *,
    base_url: str | None = None,
    api_prefix: str = DEFAULT_API_PREFIX,
    overwrite: bool = True,
    full_info: bool = True,
    timeout: float = 120,
    comfy_user: str | None = None,
) -> requests.Response:
    """
    ComfyUI自己的上传接口，只能上传到 userdata 目录下。 默认路径是 user/default/input
    
    将本地文件以原始二进制流 POST 到 ComfyUI userdata。

    remote_path 可含子目录（用 / 分隔）；整段会被 URL 编码，
    否则 aiohttp 单段 {file} 路由会得到 405。
    """
    bu = resolve_base_url(base_url)
    remote = remote_path.replace("\\", "/").lstrip("/")
    path_part = urllib.parse.quote(remote, safe="")
    prefix = api_prefix.rstrip("/") if api_prefix else ""
    url = f"{bu}{prefix}/userdata/{path_part}"

    params: dict[str, str] = {}
    if overwrite:
        params["overwrite"] = "true"
    if full_info:
        params["full_info"] = "true"

    headers = {"Content-Type": "application/octet-stream"}
    if comfy_user:
        headers["Comfy-User"] = comfy_user

    local = Path(local_path)
    with local.open("rb") as f:
        return requests.post(
            url,
            data=f,
            headers=headers,
            params=params or None,
            timeout=timeout,
        )


def upload_userdata_json(
    local_path: str | Path,
    remote_path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """上传成功后解析 JSON 响应；失败则 raise_for_status。"""
    r = upload_userdata(local_path, remote_path, **kwargs)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    fbx_file = r"D:\Downloads\Slow Run.fbx"
    sample = Path(fbx_file).resolve().parent / fbx_file
    # resp = upload_userdata(sample, "input/3d/011111.fbx")
    # # 上传之后的 路径是 user\default\input\3d\011111.fbx
    # print(resp.json())

    #resp = upload_ndbox_api(sample)
    #print(resp.json())
    
    
    # res = download_ndbox_api_to_file("95acc89196d44fd396ea3bb41a2bcc92")
    # print(res)
    # if not res.ok:
    #     print("error:", res.error)
    #     print("body:", res.body_preview)
    
    res = download_ndbox_api_by_filepath_to_file("rigged_mia_20260511_150537.fbx", target_type="output")
    print(res)
    if not res.ok:
        print("error:", res.error)
        print("body:", res.body_preview)