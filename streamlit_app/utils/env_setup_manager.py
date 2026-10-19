"""
env_setup_manager.py — 环境配置（可搬运发行包）后台任务管理

设计目标（对齐 workflow/env/portable-distribution.md）：
  - 环境配置是长耗时任务（conda env create/update、R 包安装可能十几分钟），
    不能阻塞 Streamlit 页面；因此 fork 到独立 subprocess，通过 JSON 状态文件通信。
  - UI 侧提供 start / status / cancel / tail_log / list / clear 接口，
    与 annotation_manager 完全同构，便于页面复用同一套轮询/进度渲染代码。

状态目录：  workflow/env/.setup_tasks/
状态文件：  workflow/env/.setup_tasks/<task_id>.json
Worker：    streamlit_app/utils/env_setup_worker.py（python 直接可执行）
日志文件：  workflow/env/.setup_tasks/<task_id>.log
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------- 阶段定义（与 env_setup_worker.py 保持一致）----------
# key -> 中文标签，用于 UI 的 stepper 展示
STAGE_LABELS: Dict[str, str] = {
    "precheck":     "① 安装前自检（架构 / glibc / 磁盘 / 写权限）",
    "conda_lock":   "② 依赖锁定（conda-lock 生成 conda-lock.yml）",
    "conda_env":    "③ 环境安装（conda env create / update）",
    "r_packages":   "④ R 包补齐（install_R_packages.R）",
    "verify_tools": "⑤ 核心工具校验（bowtie2 / samtools / macs2 …）",
    "selfcheck":    "⑥ 运行时自检（txdbmaker / R 库 / picard / ldd）",
}

# 一次配置的完整阶段顺序（done 为终止标记，不参与展示）
STAGES: List[str] = ["precheck", "conda_lock", "conda_env", "r_packages", "verify_tools", "selfcheck", "done"]

# 默认执行的步骤（conda_lock 需要额外工具，默认不勾选）
DEFAULT_STEPS: List[str] = ["precheck", "conda_env", "r_packages", "verify_tools", "selfcheck"]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_SKIPPED = "skipped"


@dataclass
class SetupSpec:
    """启动一次环境配置所需的全部参数（序列化成 JSON 传给 worker）。"""
    task_id: str
    project_root: str                 # 项目根绝对路径
    env_dir: str                      # workflow/env 绝对路径
    conda_env: str                    # conda 环境名，如 ChIPseq_Pipline
    environment_yml: str              # environment.yml 绝对路径
    install_r_script: str             # install_R_packages.R 绝对路径
    steps: List[str] = field(default_factory=lambda: list(DEFAULT_STEPS))
    prune: bool = False               # conda env update 是否 --prune
    threads: int = 4                  # 传给底层工具的线程上限
    dry_run: bool = False             # True 时只打印将要执行的命令，不实际执行


def _tasks_dir(env_dir: str | Path) -> Path:
    p = Path(env_dir) / ".setup_tasks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(env_dir: str | Path, task_id: str) -> Path:
    return _tasks_dir(env_dir) / f"{task_id}.json"


def _log_path(env_dir: str | Path, task_id: str) -> Path:
    return _tasks_dir(env_dir) / f"{task_id}.log"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def new_task_id() -> str:
    return "env-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def start_task(spec: SetupSpec) -> Dict[str, Any]:
    """启动后台 worker 进程，返回初始状态字典。"""
    env_dir = Path(spec.env_dir)
    env_dir.mkdir(parents=True, exist_ok=True)

    state_file = _state_path(env_dir, spec.task_id)
    log_file = _log_path(env_dir, spec.task_id)

    # 每个阶段的初始状态
    stage_status = {}
    for s in STAGES[:-1]:
        stage_status[s] = STATUS_PENDING if s in spec.steps else STATUS_SKIPPED

    initial_state = {
        "task_id": spec.task_id,
        "kind": "env_setup",
        "conda_env": spec.conda_env,
        "status": STATUS_PENDING,
        "stage": None,
        "stage_index": 0,
        "stage_total": len([s for s in spec.steps if s in STAGES]),
        "progress": 0.0,
        "message": "任务已创建，等待 worker 启动",
        "stage_status": stage_status,
        "started_at": time.time(),
        "updated_at": time.time(),
        "finished_at": None,
        "error": None,
        "spec": asdict(spec),
        "pid": None,
        "result": {},
    }
    _atomic_write_json(state_file, initial_state)

    worker_path = Path(__file__).resolve().parent / "env_setup_worker.py"
    if not worker_path.is_file():
        initial_state["status"] = STATUS_ERROR
        initial_state["error"] = f"worker 脚本未找到: {worker_path}"
        _atomic_write_json(state_file, initial_state)
        return initial_state

    worker_env = dict(os.environ)
    worker_env["PATH"] = _tool_search_path()
    with open(log_file, "ab", buffering=0) as logf:
        logf.write(
            f"\n==== env task {spec.task_id} started at {time.strftime('%F %T')} ====\n".encode()
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", str(worker_path), "--state", str(state_file)],
            stdin=subprocess.PIPE,
            stdout=logf,
            stderr=logf,
            start_new_session=True,
            close_fds=True,
            env=worker_env,
        )
        proc.stdin.write(json.dumps(asdict(spec), ensure_ascii=False).encode("utf-8"))
        proc.stdin.close()

    initial_state["pid"] = proc.pid
    initial_state["status"] = STATUS_RUNNING
    initial_state["message"] = f"worker 已启动 (PID {proc.pid})"
    initial_state["updated_at"] = time.time()
    _atomic_write_json(state_file, initial_state)
    return initial_state


def get_task(env_dir: str | Path, task_id: str) -> Optional[Dict[str, Any]]:
    p = _state_path(env_dir, task_id)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_tasks(env_dir: str | Path) -> List[Dict[str, Any]]:
    tdir = _tasks_dir(env_dir)
    out: List[Dict[str, Any]] = []
    for p in tdir.glob("env-*.json"):
        st = get_task(env_dir, p.stem)
        if st:
            out.append(st)
    out.sort(key=lambda s: s.get("started_at") or 0, reverse=True)
    return out


def cancel_task(env_dir: str | Path, task_id: str) -> bool:
    st = get_task(env_dir, task_id)
    if not st:
        return False
    if st.get("status") in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED):
        return False
    pid = st.get("pid")
    if pid:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    st["status"] = STATUS_CANCELLED
    st["message"] = "已取消"
    st["finished_at"] = time.time()
    st["updated_at"] = time.time()
    _atomic_write_json(_state_path(env_dir, task_id), st)
    return True


def clear_task(env_dir: str | Path, task_id: str) -> bool:
    s = _state_path(env_dir, task_id)
    l = _log_path(env_dir, task_id)
    ok = False
    if s.exists():
        s.unlink()
        ok = True
    if l.exists():
        l.unlink()
        ok = True
    return ok


def tail_log(env_dir: str | Path, task_id: str, n_lines: int = 60) -> str:
    p = _log_path(env_dir, task_id)
    if not p.is_file():
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= n_lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n_lines:])
    except OSError:
        return ""


# ---------- 依赖 / 路径辅助（UI 页顶部展示用）----------
def conda_env_bin() -> str:
    return os.path.join(sys.prefix, "bin")


def _tool_search_path() -> str:
    b = conda_env_bin()
    cur = os.environ.get("PATH", "")
    parts = [b] + [p for p in cur.split(os.pathsep) if p and p != b]
    return os.pathsep.join(parts)


def resolved_tool_path(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd, path=_tool_search_path())


def check_prereqs() -> Dict[str, Optional[str]]:
    """检测配置流程依赖的外部命令，返回 {命令: 绝对路径或 None}。"""
    out: Dict[str, Optional[str]] = {}
    for cmd in ("conda", "mamba", "Rscript", "conda-lock"):
        out[cmd] = resolved_tool_path(cmd)
    return out
