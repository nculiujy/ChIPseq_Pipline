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
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

STAGES = [
    "init",
    "download_fasta",
    "decompress_fasta",
    "samtools_faidx",
    "bowtie2_build",
    "download_gff",
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


def _download(url: str, dest: Path, sha256_expected: Optional[str] = None,
              chunk_size: int = 1024 * 512) -> None:
    """
    带断点续传的流式下载，同时更新全局状态的 progress / speed / eta。
    dest 是最终文件名；下载中会写到 dest.with_suffix(dest.suffix + '.part')。
    如果最终文件已存在且 sha256 匹配（或 sha256 为空），跳过下载。
    """
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 已完成？
    if dest.exists() and dest.stat().st_size > 0:
        if not sha256_expected or _sha256(dest) == sha256_expected.lower():
            _update(message=f"{dest.name} 已存在，跳过下载")
            return
        dest.unlink()   # 校验失败，重新下载

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

    # ---------- Stage 1: init ----------
    _set_stage("init", message=f"目标目录: {target_dir}", progress=0.0)
    _check_cancelled()

    # ---------- Stage 2: download_fasta ----------
    _set_stage("download_fasta", message=f"下载 FASTA: {spec['fasta_url']}")
    fasta_download = target_dir / spec["fasta_filename"]
    _download(spec["fasta_url"], fasta_download, spec.get("fasta_sha256"))
    _check_cancelled()

    # ---------- Stage 3: decompress_fasta ----------
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

    # ---------- Stage 4: samtools faidx ----------
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

    # ---------- Stage 5: bowtie2_build ----------
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

    # ---------- Stage 6: download_gff ----------
    _set_stage("download_gff", message=f"下载注释: {spec['gff_url']}")
    _download(spec["gff_url"], gff_final, spec.get("gff_sha256"))
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
