#!/usr/bin/env python3
"""
annotation_worker.py — 后台注释下载/构建 worker

由 annotation_manager.start_task() 通过 subprocess 启动。
用法：
    python annotation_worker.py --state /path/to/state.json < spec.json

从 stdin 读 TaskSpec JSON；执行过程中不断更新 state.json（原子写）。
支持 SIGTERM 取消；下载段支持断点续传（HTTP Range + .part 文件）。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

STAGES = [
    "init",
    "download_gff",
    "download_fasta",
    "decompress_fasta",
    "samtools_faidx",
    "bowtie2_build",
    "verify",
    "done",
]

# 与 annotation_manager.STAGES_BY_KIND 保持一致：三种任务各自的阶段表。
# 阶段名不同、阶段数不同，_set_stage 必须按当前 kind 取表，否则进度条会错位。
_STAGES_BY_KIND = {
    "genome": STAGES,
    "orgdb": ["init", "check_env", "build_orgdb", "install_orgdb", "verify", "done"],
    "term2gene": ["init", "download_gene2go", "parse", "write", "verify", "done"],
}

# NCBI gene2go 的 Category 列 → clusterProfiler 的 ont 代号
_GO_CATEGORY_MAP = {"process": "bp", "function": "mf", "component": "cc"}

# ---------- 全局状态 ----------
_STATE_FILE: Optional[Path] = None
_STATE: Dict[str, Any] = {}
_CANCELLED = False


def _atomic_write_state() -> None:
    if not _STATE_FILE:
        return
    tmp = _STATE_FILE.with_suffix(_STATE_FILE.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_FILE)
    except OSError as e:
        print(f"[worker] 写状态失败: {e}", file=sys.stderr)


def _update(**kw: Any) -> None:
    _STATE.update(kw)
    _STATE["updated_at"] = time.time()
    _atomic_write_state()


def _active_stages() -> list:
    """当前任务的阶段表（kind 在校验阶段已写进 state）。"""
    kind = (_STATE.get("kind") or "genome") if _STATE else "genome"
    return _STAGES_BY_KIND.get(kind, STAGES)


def _set_stage(name: str, message: Optional[str] = None, progress: Optional[float] = None) -> None:
    stages = _active_stages()
    idx = stages.index(name) if name in stages else 0
    fields: Dict[str, Any] = {"stage": name, "stage_index": idx, "stage_total": len(stages)}
    if message is not None:
        fields["message"] = message
    if progress is not None:
        fields["progress"] = float(progress)
    _update(**fields)


def _sigterm_handler(signum, frame):  # noqa: ARG001
    global _CANCELLED
    _CANCELLED = True
    _update(status="cancelled", message="已取消", finished_at=time.time())
    sys.exit(0)


def _check_cancelled() -> None:
    if _CANCELLED:
        raise RuntimeError("cancelled")


# ---------- 下载 ----------
def _human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class _Aria2Unavailable(RuntimeError):
    """aria2c 存在但完全跑不动（0 字节 + 非 0 退出码）→ 回退单连接 urllib。"""
class _CurlUnavailable(RuntimeError):
    """curl 存在但完全跑不动（0 字节 + 非 0 退出码）→ 回退单连接 urllib。"""


def _find_aria2() -> Optional[str]:
    """找 aria2c：先看 PATH，再试常见安装位置。

    conda -p 环境里的 aria2 不一定在启动器的 PATH 上（PATH 只是被前置，不是重置），
    所以额外按 CHIPSEQ_CONDA_ENV / CONDA_PREFIX / 当前解释器前缀去找一遍。
    """
    found = shutil.which("aria2c")
    if found:
        return found
    cands = []
    for env in (os.environ.get("CHIPSEQ_CONDA_ENV"), os.environ.get("CONDA_PREFIX"),
                sys.prefix, os.path.dirname(os.path.dirname(sys.executable))):
        if env:
            cands.append(Path(env) / "bin" / "aria2c")
    cands += [
        Path("/usr/bin/aria2c"),
        Path("/usr/local/bin/aria2c"),
        Path.home() / "miniconda3" / "bin" / "aria2c",
        Path.home() / "anaconda3" / "bin" / "aria2c",
    ]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _download(url: str, dest: Path, sha256_expected: Optional[str] = None,
              chunk_size: int = 1024 * 512, attempts: int = 3) -> None:
    """下载分发：aria2c → curl → urllib，逐级回退。

    为什么要三级：跨境外链（Ensembl/NCBI/UCSC）上，单 TCP 连接受高延迟 RTT 限制
    （urllib 实测 0.3MB/s），且不少网络还会干扰 Python 的 TLS 握手，让 urllib 直接
    报 `_ssl.c:999 handshake operation timed out`——而 curl/aria2 用系统 OpenSSL
    握手正常。同一份 54MB 的 GTF 实测：aria2c 4.8s / curl 6.0s / urllib 174.6s。
    网络类错误会带 .part 重试 attempts 次（断点续传）。
    dest 是最终文件名；下载中写 dest.with_suffix(dest.suffix + '.part')。
    若最终文件已存在且 sha256 匹配（或未提供 sha256），跳过下载。
    """
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 已完成？
    if dest.exists() and dest.stat().st_size > 0:
        if not sha256_expected or _sha256(dest) == sha256_expected.lower():
            _update(message=f"{dest.name} 已存在，跳过下载")
            return
        dest.unlink()   # 校验失败，重新下载

    aria2 = _find_aria2()
    curl = shutil.which("curl")
    last: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            if aria2:
                try:
                    _download_aria2(aria2, url, part, dest, sha256_expected)
                    return
                except _Aria2Unavailable as e:
                    _update(message=f"aria2c 不可用（{e}）→ 改用 curl")
                    aria2 = None
            if curl:
                try:
                    _download_curl(curl, url, part, dest, sha256_expected)
                    return
                except _CurlUnavailable as e:
                    _update(message=f"curl 不可用（{e}）→ 回退单连接 urllib")
                    curl = None
            _download_urllib(url, part, dest, sha256_expected, chunk_size)
            return
        except Exception as e:      # noqa: BLE001 —— 下面按类型决定要不要重试
            last = e
            if not _looks_network(e) or attempt >= attempts:
                break
            wait = 5 * attempt
            _update(message=f"下载中断（{e}），{wait}s 后重试 {attempt}/{attempts - 1}"
                            f"（.part 保留，断点续传）")
            time.sleep(wait)

    if last is None:                       # 理论不可达
        raise RuntimeError(f"下载失败: {url}")
    if _looks_network(last):
        raise RuntimeError(f"{last}{_network_hint(url)}") from last
    raise last


def _looks_network(e: Exception) -> bool:
    """是不是值得重试的网络类错误。取消信号和 sha256 不匹配都不重试。"""
    msg = str(e)
    if msg.strip() == "cancelled":
        return False
    return any(k in msg for k in (
        "网络错误", "handshake", "timed out", "timeout", "Connection", "连接",
        "SSL", "下载中断", "reset", "Reset", "EOF", "Incomplete",
    ))


def _network_hint(url: str) -> str:
    """给用户看的排障建议，避免只甩一个 TLS 报错出去。"""
    name = url.rsplit("/", 1)[-1] or "download.gz"
    return (
        "\n可能原因与对策：\n"
        "  1) 该网络会干扰 Python 的 TLS 握手（实测 curl/aria2 能通、urllib 直接超时）"
        "→ 装 aria2 走多连接：conda install -p <analysis> -c conda-forge aria2\n"
        "  2) 有代理的话：export https_proxy=http://host:port 后重启应用（aria2/urllib 都认）\n"
        f"  3) 或手动补下：curl -L -C - -o {name} {url}\n"
        "     （放到目标目录下即可，重跑任务会自动跳过已存在文件）"
    )


def _http_status(url: str, timeout: float = 20.0) -> int:
    """取 URL 的 HTTP 状态码（跟随重定向）；拿不到返 0。

    curl 优先的理由同 _remote_size：该网络下 Python 的 TLS 握手会被拦。
    """
    curl = shutil.which("curl")
    if curl:
        try:
            out = subprocess.run(
                [curl, "-sIL", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", str(int(timeout)), url],
                capture_output=True, text=True, timeout=timeout + 3,
            ).stdout.strip()
            if out.isdigit():
                return int(out)
        except Exception:  # noqa: BLE001 —— 探测失败不影响下载
            pass
    return 0


def _remote_size(url: str, timeout: float = 6.0) -> int:
    """探测远端文件大小；拿不到返 0（进度退化为只显示已下载量）。

    优先用 curl：跨国镜像站对 Python 的 TLS 指纹不友好，urllib 的 HEAD 会一路挂到
    超时（实测 Ensembl/NCBI/UCSC 三个站点均握手失败，curl 约 0.9s 成功）。
    无论走哪条路都只给 6s，且调用方把它放在后台线程里，不允许拖慢真正要下的数据。
    """
    deadline = timeout
    curl = shutil.which("curl")
    if curl:
        try:
            out = subprocess.run(
                [curl, "-sIL", "--max-time", str(int(deadline)), url],
                capture_output=True, text=True, timeout=deadline + 3,
            ).stdout
            # 跟随重定向后有多段响应头，取最后一个 Content-Length
            sizes = re.findall(r"(?im)^content-length:\s*(\d+)\s*$", out)
            if sizes:
                return int(sizes[-1])
        except Exception:  # noqa: BLE001 —— 探测失败不影响下载
            pass
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "ChIPseq-Pipeline-AnnotationDownloader/1.0")
        with urllib.request.urlopen(req, timeout=deadline) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else 0
    except Exception:      # noqa: BLE001 —— 拿不到大小不影响下载
        return 0


def _download_aria2(aria2: str, url: str, part: Path, dest: Path,
                    sha256_expected: Optional[str]) -> None:
    """aria2c 多连接分段下载；-c 断点续传，进度靠轮询 part 大小计算（不解析终端输出）。"""
    cmd = [
        aria2, "-c", "-x16", "-s16", "-k1M",
        "--file-allocation=none", "--auto-file-renaming=false",
        "--allow-overwrite=true", "--summary-interval=0",
        "--console-log-level=error",
        # 握手/连接受阻时自己重试，比外层重试更细粒度
        "--max-tries=5", "--retry-wait=3", "--timeout=60", "--connect-timeout=30",
        "-d", str(part.parent), "-o", part.name, url,
    ]
    # 大小探测放后台线程：探测超时绝不能挡在下载前面（曾因 HEAD 挂满 30s，
    # 让 9MB 的文件从 7s 变成 48s）。没拿到之前进度就先只显示已下载量。
    size_box: Dict[str, int] = {}

    def _probe() -> None:
        size_box["n"] = _remote_size(url)

    threading.Thread(target=_probe, daemon=True, name="remote-size").start()

    _update(message=f"下载 {dest.name}（aria2c 16 连接）",
            downloaded_bytes=part.stat().st_size if part.exists() else 0,
            total_bytes=0, speed_bps=0, eta_sec=0)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    total_bytes = _watch_download_proc(proc, part, dest, size_box)

    got = part.stat().st_size if part.exists() else 0
    if proc.returncode != 0:
        if got == 0:
            raise _Aria2Unavailable(f"退出码 {proc.returncode}")
        raise RuntimeError(
            f"aria2c 下载中断（退出码 {proc.returncode}，已下载 {_human(got)}）: {url}")

    _finalize_download(part, dest, sha256_expected, got, total_bytes)


def _watch_download_proc(proc: "subprocess.Popen[Any]", part: Path, dest: Path,
                         size_box: Dict[str, int]) -> int:
    """轮询下载子进程写出的 part 大小来刷进度；返回探测到的总大小（可能为 0）。

    子进程退出、出错或被取消（_check_cancelled 抛异常）时都会走到 finally 终止子进程，
    不会留下孤儿。
    """
    window: list[tuple[float, int]] = []
    last_ui = 0.0
    total_bytes = 0
    try:
        while True:
            _check_cancelled()
            now = time.time()
            if not total_bytes:
                total_bytes = size_box.get("n", 0)
            cur = part.stat().st_size if part.exists() else 0
            window.append((now, cur))
            while window and window[0][0] < now - 5.0:
                window.pop(0)
            if proc.poll() is not None:
                break
            if now - last_ui > 0.3:
                span = window[-1][0] - window[0][0]
                speed = (window[-1][1] - window[0][1]) / span if span > 0.5 else 0.0
                prog = (cur / total_bytes) if total_bytes else 0.0
                eta = int((total_bytes - cur) / speed) if speed > 1 and total_bytes else 0
                msg = f"下载 {dest.name}: {_human(cur)}"
                if total_bytes:
                    msg += f" / {_human(total_bytes)} ({prog*100:.1f}%)"
                if speed > 1:
                    msg += f" @ {_human(speed)}/s"
                if eta > 0:
                    msg += f" ETA {eta}s"
                _update(downloaded_bytes=cur, total_bytes=total_bytes,
                        speed_bps=int(speed), eta_sec=eta, progress=prog, message=msg)
                last_ui = now
            time.sleep(0.2)
    finally:
        if proc.poll() is None:          # 取消/异常 → 别留孤儿进程
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return total_bytes


def _finalize_download(part: Path, dest: Path, sha256_expected: Optional[str],
                       got: int, total_bytes: int) -> None:
    """校验（可选）→ 清 aria2 控制文件 → part 改名成最终文件。"""
    if sha256_expected:
        _update(message=f"校验 sha256: {dest.name}", progress=1.0)
        actual = _sha256(part)
        if actual != sha256_expected.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"sha256 不匹配 (期望 {sha256_expected[:12]}..., 实际 {actual[:12]}...)")

    Path(str(part) + ".aria2").unlink(missing_ok=True)   # aria2 控制文件
    os.replace(part, dest)
    _update(downloaded_bytes=got, total_bytes=got or total_bytes, progress=1.0,
            message=f"{dest.name} 下载完成 ({_human(dest.stat().st_size)})")


def _download_curl(curl: str, url: str, part: Path, dest: Path,
                   sha256_expected: Optional[str]) -> None:
    """curl 单连接下载：aria2 装不上时的二级回退。

    单独存在这条路径的理由——有些网络会干扰 Python 的 TLS 握手（urllib 直接超时，
    报 _ssl.c:999 handshake operation timed out），而 curl 用系统 OpenSSL 握手正常。
    速度不如 16 连接的 aria2，但保证「不额外装东西也能把文件下下来」。
    """
    cmd = [curl, "-L", "--fail", "-sS", "-o", str(part),
           "--retry", "5", "--retry-delay", "3", "--connect-timeout", "30",
           "--speed-time", "180", "--speed-limit", "1024"]
    if part.exists() and part.stat().st_size > 0:
        cmd += ["-C", "-"]              # 断点续传
    cmd.append(url)

    size_box: Dict[str, int] = {}

    def _probe() -> None:
        size_box["n"] = _remote_size(url)

    threading.Thread(target=_probe, daemon=True, name="remote-size").start()

    _update(message=f"下载 {dest.name}（curl）",
            downloaded_bytes=part.stat().st_size if part.exists() else 0,
            total_bytes=0, speed_bps=0, eta_sec=0)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    total_bytes = _watch_download_proc(proc, part, dest, size_box)

    got = part.stat().st_size if part.exists() else 0
    if proc.returncode != 0:
        err = (proc.stderr.read() if proc.stderr else "").strip().splitlines()
        detail = err[-1] if err else f"退出码 {proc.returncode}"
        if got == 0:
            raise _CurlUnavailable(detail)
        raise RuntimeError(f"curl 下载中断（{detail}，已下载 {_human(got)}）: {url}")

    _finalize_download(part, dest, sha256_expected, got, total_bytes)


def _download_urllib(url: str, part: Path, dest: Path,
                     sha256_expected: Optional[str],
                     chunk_size: int = 1024 * 512) -> None:
    """单连接流式下载（aria2c 缺失时的回退路径，逻辑与旧版一致）。"""
    # 断点续传：part 文件已存在则从末尾继续
    resume_from = part.stat().st_size if part.exists() else 0

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ChIPseq-Pipeline-AnnotationDownloader/1.0")
    if resume_from > 0:
        req.add_header("Range", f"bytes={resume_from}-")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = getattr(resp, "status", 200)
            content_length_hdr = resp.headers.get("Content-Length")
            total_remote = int(content_length_hdr) if content_length_hdr else 0
            accept_ranges = (resp.headers.get("Accept-Ranges") or "").lower() == "bytes"

            # 206 表示服务器接受了 Range；200 表示忽略 Range 重传
            if status == 206 and resume_from > 0:
                total_bytes = resume_from + total_remote
                downloaded = resume_from
                mode = "ab"
            else:
                # 服务器不支持续传，从头开始
                resume_from = 0
                total_bytes = total_remote
                downloaded = 0
                mode = "wb"
                if part.exists():
                    part.unlink()

            _update(downloaded_bytes=downloaded, total_bytes=total_bytes, speed_bps=0, eta_sec=0)

            # 速度用滚动窗口计算
            window: list[tuple[float, int]] = []
            last_ui_update = 0.0

            with open(part, mode) as f:
                while True:
                    _check_cancelled()
                    buf = resp.read(chunk_size)
                    if not buf:
                        break
                    f.write(buf)
                    now = time.time()
                    downloaded += len(buf)
                    window.append((now, len(buf)))
                    # 保留最近 5s 窗口
                    while window and window[0][0] < now - 5.0:
                        window.pop(0)

                    # 每 300ms 更新一次状态文件
                    if now - last_ui_update > 0.3:
                        speed = sum(b for _, b in window) / max(1e-6, window[-1][0] - window[0][0]) if len(window) > 1 else 0
                        eta = int((total_bytes - downloaded) / speed) if speed > 1 and total_bytes else 0
                        prog = (downloaded / total_bytes) if total_bytes else 0.0
                        msg = f"下载 {dest.name}: {_human(downloaded)}"
                        if total_bytes:
                            msg += f" / {_human(total_bytes)} ({prog*100:.1f}%)"
                        if speed > 1:
                            msg += f" @ {_human(speed)}/s"
                        if eta > 0:
                            msg += f" ETA {eta}s"
                        _update(downloaded_bytes=downloaded, total_bytes=total_bytes,
                                speed_bps=int(speed), eta_sec=eta, progress=prog, message=msg)
                        last_ui_update = now

            # 下完后校验
            if sha256_expected:
                _update(message=f"校验 sha256: {dest.name}", progress=1.0)
                actual = _sha256(part)
                if actual != sha256_expected.lower():
                    part.unlink(missing_ok=True)
                    raise RuntimeError(f"sha256 不匹配 (期望 {sha256_expected[:12]}..., 实际 {actual[:12]}...)")

            os.replace(part, dest)
            _update(downloaded_bytes=total_bytes or downloaded, progress=1.0,
                    message=f"{dest.name} 下载完成 ({_human(dest.stat().st_size)})")

    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}: {url}") from e


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# ---------- 解压 ----------
def _decompress_gz(src: Path, dst: Path) -> None:
    """gunzip；带进度（按输入文件大小估算）。"""
    total = src.stat().st_size
    _update(progress=0.0, message=f"解压 {src.name} → {dst.name}")
    last_ui = 0.0
    read_bytes = 0
    with gzip.open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            _check_cancelled()
            buf = fi.read(4 * 1024 * 1024)
            if not buf:
                break
            fo.write(buf)
            # gzip 无原始偏移 API，用 dst 大小 / 估算压缩比近似进度
            read_bytes += len(buf)
            now = time.time()
            if now - last_ui > 0.3:
                # 假设压缩比 3x（DNA 序列典型值）
                est_total = total * 3
                prog = min(0.99, dst.stat().st_size / est_total if est_total else 0.5)
                _update(progress=prog,
                        message=f"解压 {src.name}: 已输出 {_human(dst.stat().st_size)}")
                last_ui = now
    _update(progress=1.0, message=f"解压完成: {dst.name} ({_human(dst.stat().st_size)})")


# ---------- 子进程执行 ----------
def _run(cmd: list[str], stage_name: str, progress_markers: Optional[Dict[str, float]] = None,
         env: Optional[Dict[str, str]] = None) -> None:
    """
    执行外部命令，实时读 stdout/stderr。
    若提供 progress_markers（子串 → 进度值），匹配到就更新 progress；否则用不确定进度条。
    env 只用于给 R 类命令定向库路径（R_LIBS），不覆盖整个运行环境。
    """
    _update(message=f"执行: {' '.join(cmd)}")
    run_env = None
    if env:
        run_env = dict(os.environ)
        run_env.update({k: str(v) for k, v in env.items()})
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace", env=run_env,
    )
    start = time.time()
    last_ui = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _check_cancelled()
            line = line.rstrip()
            if not line:
                continue
            print(line, flush=True)
            now = time.time()
            if progress_markers:
                for marker, prog in progress_markers.items():
                    if marker in line:
                        _update(progress=prog, message=f"[{stage_name}] {line[:200]}")
                        break
            if now - last_ui > 2.0:
                elapsed = int(now - start)
                _update(message=f"[{stage_name}] {line[:200]}",
                        progress=max(_STATE.get("progress", 0.0), min(0.99, elapsed / 600)))
                last_ui = now
    finally:
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"命令失败 (exit={proc.returncode}): {' '.join(cmd)}")


# ---------- 主流程 ----------
# ---------- 任务类型：OrgDb 自建 / gene2go 基因集 ----------
def _which_or_raise(cmd: str, hint: str) -> str:
    p = shutil.which(cmd)
    if not p:
        raise RuntimeError(f"未找到 {cmd}；{hint}")
    return p


def _r_lib_dir() -> str:
    """分析环境的 R 库目录：由解析到的 Rscript 反推 <prefix>/lib/R/library。

    R 脚本（5_1_gsea.R）就是靠 CONDA_PREFIX/lib/R/library 找包；worker 里
    CONDA_PREFIX 可能是启动器环境，所以不能直接信它，必须按 Rscript 实际位置推。
    """
    rs = shutil.which("Rscript")
    if rs:
        lib = Path(rs).resolve().parents[1] / "lib" / "R" / "library"
        if lib.is_dir():
            return str(lib)
    env_prefix = os.environ.get("CONDA_PREFIX") or ""
    if env_prefix:
        lib = Path(env_prefix) / "lib" / "R" / "library"
        if lib.is_dir():
            return str(lib)
    return ""


def _run_r(code: str, stage_name: str, message: str,
           r_lib: Optional[str] = None) -> str:
    """跑一段 Rscript。R 输出全进任务日志，失败时把尾部 30 行抛给 UI。"""
    rscript = _which_or_raise("Rscript", "确认分析环境已装 R（Rscript 应在 PATH 里）")
    _set_stage(stage_name, message=message, progress=0.1)
    lib = r_lib if r_lib is not None else _r_lib_dir()
    run_env = dict(os.environ)
    if lib:
        # R_LIBS 是追加语义：优先让人看到/装到分析环境那个库
        run_env["R_LIBS"] = lib + (os.pathsep + run_env["R_LIBS"] if run_env.get("R_LIBS") else "")
    # 不用 --vanilla：它会连 .Renviron 一起忽略，装包时拿不到代理/镜像设置。
    # --no-init-file 只跳过 .Rprofile，足够避开项目根 renv 的启动钩子（那会让每次启动去联网）。
    proc = subprocess.run(
        [rscript, "--no-init-file", "-e", code],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=run_env,
    )
    out = proc.stdout or ""
    print(out, flush=True)
    _check_cancelled()
    if proc.returncode != 0:
        tail = "\n".join(out.splitlines()[-30:])
        raise RuntimeError(f"Rscript 失败（exit {proc.returncode}）：\n{tail}")
    return out


def _anno_outdir(spec: Dict[str, Any]) -> Path:
    """OrgDb / gene2go 产物默认目录：<ANNO_DIR>/<物种>/orgdb。"""
    raw = str(spec.get("term2gene_outdir") or "").strip()
    p = Path(raw).expanduser() if raw else \
        Path(spec["anno_root"]) / str(spec["species_id"]) / "orgdb"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _run_orgdb_build(spec: Dict[str, Any]) -> None:
    """从 NCBI 数据自建 OrgDb 包并装进分析环境 R 库。

    AnnotationForge 自己下载 gene_info / gene2go / refseqmRNA；首建常 30–90 分钟，
    NCBIFilesDir 保留缓存，同物种重建是分钟级。
    """
    tax = str(spec.get("tax_id") or "").strip()
    genus = str(spec.get("ncbi_genus") or "").strip()
    sp = str(spec.get("ncbi_species") or "").strip()
    if not (tax and genus and sp):
        raise RuntimeError("OrgDb 构建需要 tax_id、genus、species（例：3847 / Glycine / max）")

    outdir = _anno_outdir(spec)
    # AnnotationForge 包名规则：org.<属首字母大写><种名前 3 字母小写>.eg.db
    pkg = str(spec.get("orgdb_pkg") or "") or f"org.{genus[0].upper()}{sp[:3].lower()}.eg.db"
    ncbi_cache = outdir / "NCBI_files"
    ncbi_cache.mkdir(parents=True, exist_ok=True)
    _update(target_dir=str(outdir), orgdb_pkg=pkg)

    _set_stage("check_env", message="检查 AnnotationForge 是否可用")
    probe = _run_r(
        'cat(if (requireNamespace("AnnotationForge", quietly=TRUE)) "HAS_FORGE" else "NO_FORGE", "\\n")',
        "check_env", "检查 AnnotationForge")
    if "NO_FORGE" in probe:
        raise RuntimeError(
            "分析环境缺少 AnnotationForge。安装后重试：\n"
            "  BiocManager::install(c('AnnotationForge','AnnotationDbi','GO.db'))\n"
            "或改用本页「gene2go 基因集」模式（不需要装包）")

    _set_stage("build_orgdb",
               message=f"构建 {pkg}（tax_id={tax}；NCBI 下载可能较久）", progress=0.05)
    _run_r(
        "AnnotationForge::makeOrgPackageFromNCBI("
        'version="0.1", '
        'maintainer="ChIPseq Pipeline <noreply@localhost>", '
        'author="ChIPseq Pipeline", '
        f'outputDir="{outdir}", tax_id="{tax}", genus="{genus}", species="{sp}", '
        f'NCBIFilesDir="{ncbi_cache}", rebuildCache=TRUE)',
        "build_orgdb", f"构建 {pkg}")

    # 产物目录：优先推导包名，找不到就扫 org.*.db（不同版本命名略有差异）
    pkg_dir = outdir / pkg
    if not pkg_dir.is_dir():
        cands = sorted(p for p in outdir.glob("org.*.db") if p.is_dir())
        if not cands:
            raise RuntimeError(f"未找到构建产物（{outdir} 下没有 org.*.db）")
        pkg_dir = cands[-1]
        pkg = pkg_dir.name
        _update(orgdb_pkg=pkg)

    _set_stage("install_orgdb", message=f"安装 {pkg} 到 R 库")
    r_bin = _which_or_raise("R", "确认分析环境已装 R")
    # 不指定库时就装进分析环境自己的库（5_1_gsea.R 只认 CONDA_PREFIX/lib/R/library）
    lib = str(spec.get("orgdb_lib") or "").strip() or _r_lib_dir()
    if not lib:
        raise RuntimeError("无法推导分析环境的 R 库路径，请在 spec.orgdb_lib 显式指定")
    cmd = [r_bin, "CMD", "INSTALL", "--no-multiarch"]
    Path(lib).mkdir(parents=True, exist_ok=True)
    cmd.append(f"--library={lib}")
    cmd.append(str(pkg_dir))
    _run(cmd, "install_orgdb", progress_markers=None, env={"R_LIBS": lib})

    _set_stage("verify", message=f"验证 {pkg} 可加载、可查询")
    out = _run_r(
        f'suppressPackageStartupMessages(library({pkg})); '
        f'cat("ORGDB_OK", length(AnnotationDbi::keys({pkg})), "\\n")',
        "verify", f"验证 {pkg}", r_lib=lib)
    m = re.search(r"ORGDB_OK\s+(\d+)", out)
    if not m:
        raise RuntimeError(f"{pkg} 已安装但 load/keys 失败，请看任务日志")
    n_keys = int(m.group(1))

    _set_stage("done", message=f"完成：{pkg}（{n_keys} 个 key）", progress=1.0)
    _update(status="done", error=None, finished_at=time.time(), progress=1.0,
            result_files=[str(pkg_dir)], orgdb_pkg=pkg, orgdb_lib=lib,
            orgdb_n_keys=n_keys)


def _run_term2gene(spec: Dict[str, Any]) -> None:
    """NCBI gene2go → clusterProfiler 的 TERM2GENE/TERM2NAME（不装 OrgDb）。

    gene2go 自带 term 名与 Category(Process/Function/Component)，一个文件即可产出
    BP/MF/CC 三套映射 + 全基因背景集，适合大豆这类无官方 OrgDb 的物种。
    """
    tax = str(spec.get("tax_id") or "").strip()
    if not tax:
        raise RuntimeError("生成 gene2go 基因集需要 tax_id（例：大豆 3847）")

    outdir = _anno_outdir(spec)
    prefix = str(spec.get("term2gene_prefix") or "") or f"tax{tax}"
    url = str(spec.get("gene2go_url") or "").strip() or \
        "https://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2go.gz"
    gz = outdir / "gene2go.gz"
    _update(target_dir=str(outdir))

    _set_stage("download_gene2go", message=f"下载 {url}", progress=0.0)
    if gz.is_file() and gz.stat().st_size > 1024 * 1024:
        # 同目录已有完整下载就复用；想强制刷新删掉 gene2go.gz 重跑
        _update(message=f"{gz.name} 已存在（{_human(gz.stat().st_size)}），跳过下载", progress=1.0)
    else:
        if gz.is_file():
            # 半截文件（上次断流）直接丢掉，否则会解析出空注释
            gz.unlink()
        _download(url, gz, None)
    _check_cancelled()

    _set_stage("parse", message=f"解析 tax_id={tax} 的 GO 注释", progress=0.0)
    terms: Dict[str, Dict[str, list]] = {"bp": {}, "mf": {}, "cc": {}}
    all_genes: set = set()
    n_hit = n_line = 0
    with gzip.open(gz, "rt", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("#"):
                continue
            n_line += 1
            f = ln.rstrip("\n").split("\t")
            if len(f) < 8 or f[0] != tax:
                if n_line % 200000 == 0:
                    _update(progress=min(0.95, n_line / 800000.0),
                            message=f"扫描 {n_line} 行，命中 {n_hit} 条")
                continue
            gene, go, qual, term, cat = f[1], f[2], f[4], f[5], f[7]
            if qual.upper().startswith("NOT"):     # NOT 限定不能当正注释用
                continue
            ont = _GO_CATEGORY_MAP.get(cat.strip().lower())
            if not ont:
                continue
            ent = terms[ont].get(go)
            if ent is None:
                terms[ont][go] = [term, {gene}]
            else:
                ent[1].add(gene)
            all_genes.add(gene)
            n_hit += 1
            if n_hit % 50000 == 0:
                _update(progress=min(0.95, n_line / 800000.0),
                        message=f"已收集 {n_hit} 条（{len(all_genes)} 基因）")
            _check_cancelled()
    if not all_genes:
        raise RuntimeError(f"gene2go 中没有 tax_id={tax} 的记录：确认 taxid 是否正确")

    _set_stage("write", message="写出 TERM2GENE / TERM2NAME / 背景基因集", progress=0.3)
    made = []
    for ont, d in terms.items():
        if not d:
            continue
        t2g = outdir / f"{prefix}_go_{ont}_term2gene.tsv"
        t2n = outdir / f"{prefix}_go_{ont}_term2name.tsv"
        with open(t2g, "w", encoding="utf-8") as a, open(t2n, "w", encoding="utf-8") as b:
            for go in sorted(d):
                name, genes = d[go]
                for g in sorted(genes):
                    a.write(f"{go}\t{g}\n")
                b.write(f"{go}\t{name}\n")
        made += [str(t2g), str(t2n)]
    universe = outdir / f"{prefix}_all_genes.txt"
    universe.write_text("\n".join(sorted(all_genes)) + "\n", encoding="utf-8")
    made.append(str(universe))

    _set_stage("verify", message="校验产物", progress=0.6)
    manifest = {
        "tax_id": tax, "prefix": prefix, "source": url,
        "n_go_terms": {k: len(v) for k, v in terms.items()},
        "n_genes": len(all_genes), "n_annotations": n_hit,
        "files": made, "built_at": time.strftime("%F %T"),
    }
    with open(outdir / f"{prefix}_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    for p in made:
        if not Path(p).is_file() or Path(p).stat().st_size == 0:
            raise RuntimeError(f"产物为空：{p}")

    _set_stage("done",
               message=f"完成：{len(all_genes)} 基因 / {sum(len(v) for v in terms.values())} GO term",
               progress=1.0)
    _update(status="done", error=None, finished_at=time.time(), progress=1.0,
            result_files=made, term2gene_prefix=prefix,
            term2gene_go={k: str(outdir / f"{prefix}_go_{k}_term2gene.tsv") for k in terms},
            term2gene_name={k: str(outdir / f"{prefix}_go_{k}_term2name.tsv") for k in terms},
            term2gene_universe=str(universe), n_genes=len(all_genes))


def _run_rdeps(spec: Dict[str, Any]) -> None:
    """在分析环境里装 R 依赖（默认 AnnotationForge 三件套）。

    AnnotationForge 是建 OrgDb 的前置；不走 conda、只用 install.packages /
    BiocManager，装到 Rscript 所属环境的库里，与 5_1_gsea.R 的 .libPaths 一致。
    """
    pkgs = [p.strip() for p in str(spec.get("r_packages") or "AnnotationForge").split(",") if p.strip()]
    if not pkgs:
        raise RuntimeError("未指定要安装的 R 包")
    lib = str(spec.get("orgdb_lib") or "").strip() or _r_lib_dir()
    if not lib:
        raise RuntimeError("无法推导分析环境的 R 库路径，请在 spec.orgdb_lib 显式指定")
    Path(lib).mkdir(parents=True, exist_ok=True)
    _update(target_dir=lib, r_packages=",".join(pkgs))

    mirror = str(spec.get("cran_mirror") or "").strip() or \
        "https://mirrors.tuna.tsinghua.edu.cn/CRAN"

    _set_stage("check_env", message="检查 BiocManager 与目标包状态")
    r_lib_r = lib.replace("\\", "/")
    quote_pkgs = ", ".join(f'"{p}"' for p in pkgs)
    probe = _run_r(
        f'.libPaths(c("{r_lib_r}", .libPaths())); '
        f'pk <- c({quote_pkgs}); '
        f'have <- vapply(pk, function(p) requireNamespace(p, quietly=TRUE), logical(1)); '
        'cat("DEPS_STATUS", paste(pk, have, sep=":", collapse=";"), "\\n"); '
        'cat("HAS_BIOC", requireNamespace("BiocManager", quietly=TRUE), "\\n")',
        "check_env", "检查 R 依赖", r_lib=lib)
    miss = [p for p in pkgs if f"{p}:FALSE" in probe]
    if not miss:
        _set_stage("verify", message="依赖已齐，无需安装", progress=0.9)
        _run_r(
            f'.libPaths(c("{r_lib_r}", .libPaths())); '
            f'cat("DEPS_OK", paste(vapply(c({quote_pkgs}), function(p) as.character(packageVersion(p)), character(1)), collapse=","), "\\n")',
            "verify", "确认版本", r_lib=lib)
        _set_stage("done", message="依赖已满足", progress=1.0)
        _update(status="done", error=None, finished_at=time.time(), progress=1.0,
                result_files=[lib], r_packages=",".join(pkgs))
        return

    _set_stage("install", message=f"安装 {', '.join(miss)}（含 Bioconductor 依赖，可能较久）",
               progress=0.05)
    quote_miss = ", ".join(f'"{p}"' for p in miss)
    _run_r(
        f'.libPaths(c("{r_lib_r}", .libPaths())); '
        f'options(repos = c(CRAN = "{mirror}")); '
        'if (!requireNamespace("BiocManager", quietly=TRUE)) '
        f'  install.packages("BiocManager", lib="{r_lib_r}", repos="{mirror}"); '
        f'BiocManager::install(c({quote_miss}), lib="{r_lib_r}", update=FALSE, ask=FALSE)',
        "install", f"安装 {', '.join(miss)}", r_lib=lib)

    _set_stage("verify", message="验证依赖可加载")
    out = _run_r(
        f'.libPaths(c("{r_lib_r}", .libPaths())); '
        f'ok <- vapply(c({quote_miss}), function(p) requireNamespace(p, quietly=TRUE), logical(1)); '
        'cat("DEPS_OK", paste(names(ok), ok, sep=":", collapse=";"), "\\n")',
        "verify", "验证依赖", r_lib=lib)
    if "FALSE" in out.split("DEPS_OK")[-1]:
        raise RuntimeError("安装后仍有包无法加载，请看任务日志")

    _set_stage("done", message=f"完成：{', '.join(pkgs)}", progress=1.0)
    _update(status="done", error=None, finished_at=time.time(), progress=1.0,
            result_files=[lib], r_packages=",".join(pkgs))


def run_task(spec: Dict[str, Any]) -> None:
    kind = str(spec.get("kind") or "genome")
    if kind == "rdeps":
        _run_rdeps(spec)
        return
    if kind == "orgdb":
        _run_orgdb_build(spec)
        return
    if kind == "term2gene":
        _run_term2gene(spec)
        return

    species_id = spec["species_id"]
    anno_root = Path(spec["anno_root"])
    bowtie2_dir_name = f"bowtie2_index_{species_id}"
    target_dir = anno_root / species_id / bowtie2_dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    _update(target_dir=str(target_dir))

    threads = int(spec.get("bowtie2_threads") or 4)
    genome_prefix = species_id       # 索引文件前缀 = 物种名（简洁、可预测）

    fasta_final = target_dir / f"{genome_prefix}.fa"
    fai_final = target_dir / f"{genome_prefix}.fa.fai"
    gff_final = target_dir / spec["gff_filename"]

    gff_only = bool(spec.get("gff_only"))

    # ---------- Stage 1: init ----------
    _set_stage("init", message=f"目标目录: {target_dir}", progress=0.0)
    if not gff_only:
        # 先探两个源：注释 URL 失效（404）必须在下载 950MB 基因组之前就报出来
        for label, u in (("FASTA", spec["fasta_url"]), ("注释", spec["gff_url"])):
            code = _http_status(u)
            if code in (403, 404, 410):
                raise RuntimeError(
                    f"{label}下载源不可用（HTTP {code}）: {u}\n"
                    f"请换个下载源，或修正 annotations_catalog.yaml 里的地址")
            if code == 0:
                _update(message=f"{label}源预检失败（网络/证书），继续尝试下载")
    _check_cancelled()

    # ---------- Stage 2: download_gff ----------
    # 注释文件最小（几十 MB），排在基因组之前下：源失效几秒内就暴露，
    # 而不是等 bowtie2 把索引建完（人类约 1 小时）才发现缺 GFF。
    _set_stage("download_gff", message=f"下载注释: {spec['gff_url']}")
    _download(spec["gff_url"], gff_final, spec.get("gff_sha256"))
    _check_cancelled()

    if not gff_only:
        # ---------- Stage 3: download_fasta ----------
        _set_stage("download_fasta", message=f"下载 FASTA: {spec['fasta_url']}")
        fasta_download = target_dir / spec["fasta_filename"]
        _download(spec["fasta_url"], fasta_download, spec.get("fasta_sha256"))
        _check_cancelled()

        # ---------- Stage 4: decompress_fasta ----------
        _set_stage("decompress_fasta", message="准备 FASTA")
        if fasta_download.name.endswith(".gz"):
            if not fasta_final.exists() or fasta_final.stat().st_size == 0:
                _decompress_gz(fasta_download, fasta_final)
            else:
                _update(progress=1.0, message=f"{fasta_final.name} 已存在，跳过解压")
            # 删掉 .gz 节省空间（保留可选：磁盘紧张时删）
            if not spec.get("keep_gz", True):
                fasta_download.unlink(missing_ok=True)
        else:
            # 非 gz 直接改名/软链
            if fasta_download.resolve() != fasta_final.resolve():
                shutil.copyfile(fasta_download, fasta_final)
        _check_cancelled()

        # ---------- Stage 5: samtools faidx ----------
        _set_stage("samtools_faidx", message="建立 FASTA 索引 (.fai)")
        if fai_final.exists():
            _update(progress=1.0, message=f"{fai_final.name} 已存在，跳过")
        else:
            if shutil.which("samtools"):
                _run(["samtools", "faidx", str(fasta_final)], "samtools_faidx",
                     progress_markers=None)
            else:
                # 兜底：python 自建 fai（简化实现，仅用于 bowtie2 无需精确）
                _update(message="samtools 不可用，跳过 faidx（不影响 bowtie2 比对）", progress=1.0)
        _check_cancelled()

        # ---------- Stage 6: bowtie2_build ----------
        _set_stage("bowtie2_build", message=f"bowtie2-build (threads={threads})，大基因组可能需要几十分钟")
        bt2_expected = target_dir / f"{genome_prefix}.1.bt2"
        if bt2_expected.exists():
            _update(progress=1.0, message="bowtie2 索引已存在，跳过")
        else:
            if not shutil.which("bowtie2-build"):
                raise RuntimeError("bowtie2-build 命令未找到；请确认 conda 环境已激活")
            index_prefix_path = str(target_dir / genome_prefix)
            # bowtie2-build 会打印阶段信息，我们抓关键字更新进度
            markers = {
                "Settings": 0.05,
                "Output will be": 0.10,
                "Reading": 0.15,
                "  1/": 0.20, "  2/": 0.35, "  3/": 0.50, "  4/": 0.65,
                "Building": 0.70,
                "Done": 0.99,
            }
            _run(
                ["bowtie2-build", "--threads", str(threads), str(fasta_final), index_prefix_path],
                "bowtie2_build", progress_markers=markers,
            )
            _update(progress=1.0, message="bowtie2 索引构建完成")
        _check_cancelled()

    # ---------- Stage 7: verify ----------
    _set_stage("verify", message="校验产物完整性")
    required = [
        target_dir / f"{genome_prefix}.1.bt2",
        target_dir / f"{genome_prefix}.2.bt2",
        target_dir / f"{genome_prefix}.rev.1.bt2",
        target_dir / f"{genome_prefix}.rev.2.bt2",
        fasta_final, gff_final,
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError(f"缺少产物: {', '.join(missing)}")
    _update(progress=1.0, message="全部产物齐全")

    # ---------- Stage 8: done ----------
    _set_stage("done", message="完成", progress=1.0)
    _update(
        status="done",
        finished_at=time.time(),
        index_prefix=str(target_dir / genome_prefix),
        fasta_path=str(fasta_final),
        gff_path=str(gff_final),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="状态 JSON 文件路径")
    args = ap.parse_args()

    global _STATE_FILE, _STATE
    _STATE_FILE = Path(args.state)

    # 读现有状态并接管
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            _STATE = json.load(f)
    except (OSError, json.JSONDecodeError):
        _STATE = {}

    # 读 spec（stdin JSON）
    try:
        spec = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        _update(status="error", error=f"stdin 不是合法 JSON: {e}", finished_at=time.time())
        return 2
    if not spec:
        _update(status="error", error="空 spec", finished_at=time.time())
        return 2

    # 安装 SIGTERM/SIGINT 处理器
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    _STATE["status"] = "running"
    _STATE["pid"] = os.getpid()
    _STATE["spec"] = spec
    _update(message=f"worker 启动 (PID {os.getpid()})")

    t0 = time.time()
    try:
        run_task(spec)
        print(f"[worker] done in {int(time.time() - t0)}s", flush=True)
        return 0
    except Exception as e:   # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr, flush=True)
        _update(
            status="cancelled" if _CANCELLED else "error",
            error=str(e),
            message=f"失败: {e}",
            finished_at=time.time(),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
