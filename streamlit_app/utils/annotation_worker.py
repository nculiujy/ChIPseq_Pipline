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


def _set_stage(name: str, message: Optional[str] = None, progress: Optional[float] = None) -> None:
    idx = STAGES.index(name) if name in STAGES else 0
    fields: Dict[str, Any] = {"stage": name, "stage_index": idx, "stage_total": len(STAGES)}
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
def _run(cmd: list[str], stage_name: str, progress_markers: Optional[Dict[str, float]] = None) -> None:
    """
    执行外部命令，实时读 stdout/stderr。
    若提供 progress_markers（子串 → 进度值），匹配到就更新 progress；否则用不确定进度条。
    """
    _update(message=f"执行: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace",
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
def run_task(spec: Dict[str, Any]) -> None:
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
