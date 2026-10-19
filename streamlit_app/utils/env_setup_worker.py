#!/usr/bin/env python3
"""
env_setup_worker.py — 环境配置后台 worker

由 env_setup_manager.start_task() 通过 subprocess 启动。
用法：
    python env_setup_worker.py --state /path/to/state.json < spec.json

从 stdin 读 SetupSpec JSON；按阶段执行环境配置流程，
每个阶段实时更新 state.json（原子写）并把命令与输出打印到 stdout（被 manager 重定向进日志）。
支持 SIGTERM 取消。阶段顺序与含义对齐 workflow/env/portable-distribution.md。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STAGES = ["precheck", "conda_lock", "conda_env", "r_packages", "verify_tools", "selfcheck", "done"]

VERIFY_TOOLS = ["bowtie2", "bowtie2-build", "samtools", "macs2", "snakemake",
                "deeptools", "picard", "fastqc", "trim_galore", "bedtools"]

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


def _set_stage(key: str, index: int, total: int, status: str,
               msg: str, progress: float) -> None:
    ss = dict(_STATE.get("stage_status") or {})
    ss[key] = status
    _update(stage=key, stage_index=index, stage_total=total,
            status="running", progress=round(progress, 3),
            message=msg, stage_status=ss)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _run(cmd: List[str], cwd: Optional[str] = None, dry_run: bool = False,
         timeout: Optional[int] = None) -> int:
    """执行命令并把输出实时透传（进入日志）。返回返回码；dry_run 只打印。"""
    _log("$ " + " ".join(cmd))
    if dry_run:
        _log("  (dry-run：未实际执行)")
        return 0
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        _log(f"  ✗ 命令不存在：{cmd[0]}")
        return 127
    assert proc.stdout is not None
    start = time.time()
    for line in proc.stdout:
        _log(line.rstrip("\n"))
        if _CANCELLED:
            proc.terminate()
            break
        if timeout and time.time() - start > timeout:
            _log("  ✗ 超时，终止")
            proc.terminate()
            break
    proc.wait()
    return proc.returncode


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd)


# ---------- 各阶段实现 ----------
def stage_precheck(spec: Dict[str, Any]) -> str:
    _log("=== ① 安装前自检 ===")
    problems: List[str] = []
    info: Dict[str, str] = {}

    arch = os.uname().machine
    sysname = os.uname().sysname
    info["arch"] = arch
    info["sysname"] = sysname
    _log(f"  架构={arch}  系统={sysname}")
    if arch != "x86_64":
        problems.append(f"架构非 x86_64（当前 {arch}），发行包按 linux-64 构建")

    # glibc 版本
    try:
        r = subprocess.run(["getconf", "GNU_LIBC_VERSION"], capture_output=True, text=True, timeout=10)
        gl = (r.stdout or "").strip()  # 形如 "glibc 2.34"
        info["glibc"] = gl
        _log(f"  glibc={gl or '未知'}")
    except Exception as e:  # noqa: BLE001
        info["glibc"] = "未知"
        _log(f"  glibc 检测失败：{e}")

    # 磁盘空间
    probe = spec.get("project_root") or os.path.expanduser("~")
    try:
        s = os.statvfs(probe)
        free_gb = s.f_bavail * s.f_frsize / (1024 ** 3)
        info["disk_free_gb"] = f"{free_gb:.1f}"
        _log(f"  磁盘可用（{probe}）：{free_gb:.1f} GB")
        if free_gb < 20:
            problems.append(f"磁盘可用仅 {free_gb:.1f} GB（建议 ≥20 GB 供 conda 环境）")
    except OSError as e:  # noqa: BLE001
        _log(f"  磁盘检测失败：{e}")

    # 写权限（用户目录约定）
    for d in (os.path.expanduser("~/.config"), os.path.expanduser("~/.local/share")):
        try:
            os.makedirs(d, exist_ok=True)
            tf = os.path.join(d, ".chipseq_wtest")
            with open(tf, "w") as f:
                f.write("ok")
            os.remove(tf)
            _log(f"  可写：{d}")
        except OSError as e:  # noqa: BLE001
            problems.append(f"目录不可写：{d}（{e}）")

    # conda 是否可用（conda_env 阶段依赖）
    if "conda_env" in spec.get("steps", []) and not _which("conda"):
        problems.append("未找到 conda 命令（③ 环境安装阶段将无法执行）")

    _STATE.setdefault("result", {})["precheck"] = info
    if problems:
        raise RuntimeError("； ".join(problems))
    _log("  ✓ 安装前自检通过")
    return "通过"


def stage_conda_lock(spec: Dict[str, Any]) -> str:
    _log("=== ② 依赖锁定（conda-lock）===")
    if not _which("conda-lock"):
        _log("  » 未安装 conda-lock，跳过锁定（可用 environment.yml 直接建环境）")
        return "跳过：conda-lock 不可用"
    yml = spec["environment_yml"]
    out = os.path.join(spec["env_dir"], "conda-lock.yml")
    rc = _run(["conda-lock", "lock", "-f", yml, "-p", "linux-64", "--lockfile", out],
              cwd=spec["project_root"], dry_run=spec.get("dry_run", False))
    if rc != 0:
        raise RuntimeError(f"conda-lock 失败（返回码 {rc}）")
    _log(f"  ✓ 锁定文件：{out}")
    return f"生成 {os.path.basename(out)}"


def _conda_env_exists(name: str) -> bool:
    try:
        r = subprocess.run(["conda", "env", "list"], capture_output=True, text=True, timeout=30)
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if parts and parts[0] == name:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def stage_conda_env(spec: Dict[str, Any]) -> str:
    _log("=== ③ 环境安装（conda env）===")
    env = spec["conda_env"]
    yml = spec["environment_yml"]
    dry = spec.get("dry_run", False)
    if not _which("conda"):
        raise RuntimeError("未找到 conda 命令")
    if not os.path.isfile(yml):
        raise RuntimeError(f"未找到 environment.yml：{yml}")

    if _conda_env_exists(env) or dry:
        cmd = ["conda", "env", "update", "-n", env, "-f", yml]
        if spec.get("prune"):
            cmd.append("--prune")
        action = "update"
    else:
        cmd = ["conda", "env", "create", "-n", env, "-f", yml]
        action = "create"
    _log(f"  环境 {env} {'已存在' if action == 'update' else '不存在'} → 执行 env {action}")
    rc = _run(cmd, cwd=spec["project_root"], dry_run=dry)
    if rc != 0:
        raise RuntimeError(f"conda env {action} 失败（返回码 {rc}）")
    return f"env {action} 完成"


def stage_r_packages(spec: Dict[str, Any]) -> str:
    _log("=== ④ R 包补齐 ===")
    script = spec["install_r_script"]
    dry = spec.get("dry_run", False)
    if not os.path.isfile(script):
        _log(f"  » 未找到 {script}，跳过")
        return "跳过：install_R_packages.R 不存在"
    cmd = ["conda", "run", "-n", spec["conda_env"], "Rscript", script]
    rc = _run(cmd, cwd=spec["project_root"], dry_run=dry)
    if rc != 0:
        raise RuntimeError(f"R 包安装失败（返回码 {rc}）")
    return "R 包安装完成"


def stage_verify_tools(spec: Dict[str, Any]) -> str:
    _log("=== ⑤ 核心工具校验 ===")
    dry = spec.get("dry_run", False)
    if dry:
        _log("  (dry-run：仅列出将校验的工具)")
        _STATE.setdefault("result", {})["verify_tools"] = {t: "?" for t in VERIFY_TOOLS}
        return "dry-run 跳过"
    found: Dict[str, Optional[str]] = {}
    missing: List[str] = []
    for tool in VERIFY_TOOLS:
        r = subprocess.run(["conda", "run", "-n", spec["conda_env"], "which", tool],
                           capture_output=True, text=True)
        ok = r.returncode == 0 and r.stdout.strip()
        found[tool] = r.stdout.strip().splitlines()[-1] if ok else None
        _log(f"  {'✓' if ok else '✗'} {tool}" + (f" → {found[tool]}" if ok else " — 未找到"))
        if not ok:
            missing.append(tool)
    _STATE.setdefault("result", {})["verify_tools"] = found
    if missing:
        raise RuntimeError("缺失工具：" + ", ".join(missing))
    return f"{len(VERIFY_TOOLS)} 个工具全部就位"


def stage_selfcheck(spec: Dict[str, Any]) -> str:
    _log("=== ⑥ 运行时自检 ===")
    dry = spec.get("dry_run", False)
    info: Dict[str, str] = {}
    if dry:
        _log("  (dry-run：跳过实际探测)")
        _STATE.setdefault("result", {})["selfcheck"] = {"dry_run": "true"}
        return "dry-run 跳过"

    env = spec["conda_env"]

    def _r(expr: str) -> str:
        # --vanilla 跳过 .Rprofile（避免 renv 启动横幅污染取值），只取最后一行非空输出
        r = subprocess.run(["conda", "run", "-n", env, "Rscript", "--vanilla", "-e", expr],
                           capture_output=True, text=True)
        lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    # R 版本
    rv = _r('cat(R.version$major, ".", R.version$minor, sep="")')
    info["R"] = rv
    _log(f"  R 版本：{rv or '未知'}")

    # txdbmaker（makeTxDbFromGFF 迁移的修复依赖）
    tx = _r('cat(requireNamespace("txdbmaker", quietly=TRUE))')
    info["txdbmaker"] = tx
    _log(f"  txdbmaker 可用：{tx}（true=已就绪；false=请 BiocManager::install('txdbmaker')）")

    # GenomicFeatures 版本（>=1.61.1 需 txdbmaker）
    gf = _r('tryCatch(cat(as.character(packageVersion("GenomicFeatures"))), error=function(e) cat("NA"))')
    info["GenomicFeatures"] = gf
    _log(f"  GenomicFeatures：{gf}")

    # ChIPseeker 版本
    cs = _r('tryCatch(cat(as.character(packageVersion("ChIPseeker"))), error=function(e) cat("NA"))')
    info["ChIPseeker"] = cs
    _log(f"  ChIPseeker：{cs}")

    # picard：命令是否在 PATH
    r = subprocess.run(["conda", "run", "-n", env, "which", "picard"],
                       capture_output=True, text=True)
    info["picard"] = r.stdout.strip().splitlines()[-1] if r.returncode == 0 else "未找到（走 jar 或需安装）"
    _log(f"  picard：{info['picard']}")

    _STATE.setdefault("result", {})["selfcheck"] = info
    if tx != "TRUE":
        _log("  ⚠ txdbmaker 未就绪：新版 GenomicFeatures 下 makeTxDbFromGFF 会报 defunct")
    return "版本快照已采集"


STAGE_FUNCS = {
    "precheck": stage_precheck,
    "conda_lock": stage_conda_lock,
    "conda_env": stage_conda_env,
    "r_packages": stage_r_packages,
    "verify_tools": stage_verify_tools,
    "selfcheck": stage_selfcheck,
}


def _on_sigterm(signum, frame):  # noqa: ARG001
    global _CANCELLED
    _CANCELLED = True
    _log("\n[worker] 收到 SIGTERM，准备退出")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()

    global _STATE_FILE, _STATE
    _STATE_FILE = Path(args.state)
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            _STATE = json.load(f)
    except (OSError, json.JSONDecodeError) as e:  # noqa: BLE001
        print(f"[worker] 无法读取状态文件: {e}", file=sys.stderr)
        return 2

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        spec = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as e:  # noqa: BLE001
        _update(status="error", error=f"读取 spec 失败: {e}")
        return 2

    steps = [s for s in STAGES if s in (spec.get("steps") or []) and s in STAGE_FUNCS]
    total = len(steps)
    _log(f"[worker] 计划执行 {total} 个阶段：{', '.join(steps)}")
    _log(f"[worker] conda_env={spec.get('conda_env')} dry_run={spec.get('dry_run')}")

    for i, key in enumerate(steps):
        if _CANCELLED:
            _update(status="cancelled", message="已取消", finished_at=time.time())
            return 130
        _set_stage(key, i, total, "running", f"正在执行：{key}", i / max(1, total))
        try:
            detail = STAGE_FUNCS[key](spec)
            if _CANCELLED:
                _update(status="cancelled", message="已取消", finished_at=time.time())
                return 130
            ss = dict(_STATE.get("stage_status") or {})
            ss[key] = "done"
            _update(stage_status=ss, message=f"完成：{key}（{detail}）",
                    progress=round((i + 1) / max(1, total), 3))
            _log(f"  ✓ 阶段 {key} 完成：{detail}")
        except Exception as e:  # noqa: BLE001
            ss = dict(_STATE.get("stage_status") or {})
            ss[key] = "error"
            _update(status="error", stage_status=ss, error=f"{key}: {e}",
                    message=f"阶段 {key} 失败", finished_at=time.time())
            _log(f"  ✗ 阶段 {key} 失败：{e}")
            return 1

    _update(status="done", stage="done", progress=1.0,
            message="环境配置全部完成", finished_at=time.time())
    _log("[worker] ✅ 全部阶段完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
