"""
utils.runtime — 只读运行时数据助手
==================================
为首页仪表盘（状态条 / 管线轨道 / 最近运行）提供真实数据。全部只读、
防御式：任何缺失或异常都安全降级，不影响页面渲染。

不触发任何写操作或流程执行；进程检测逻辑与「运行管理」页保持一致
（UID + CWD 双重过滤，避免共享服务器上其他用户的 snakemake 误报）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

# 项目根目录（runtime.py 位于 streamlit_app/utils/）
BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
LOGS_DIR = BASE_DIR / "logs"
RESULT_DIR = BASE_DIR / "result"
ANNO_DIR = BASE_DIR / "workflow" / "anno"
RESOURCES_DIR = BASE_DIR / "workflow" / "resources"

CURRENT_UID = os.getuid()

# 分析管线的阶段定义（用于首页管线轨道）
PIPELINE_STAGES: list[dict] = [
    {"key": "1_download", "label": "下载", "marker": "1_download/finished.txt", "module": "1_download"},
    {"key": "2_QC", "label": "QC", "marker": "2_QC/QC_finished.txt", "module": "2_QC"},
    {"key": "3_ChIPseq", "label": "比对", "marker": "3_ChIPseq/step2_finished.txt", "module": "3_ChIPseq"},
    {"key": "4_peakcalling", "label": "Peak Calling", "marker": "4_analyse/peakcalling_finished.txt", "module": "4_analyse"},
    {"key": "4_annotation", "label": "功能注释", "marker": "4_analyse/annoChIPPeaks_finished.txt", "module": "4_analyse"},
    {"key": "4_deeptools", "label": "DeepTools", "marker": "4_analyse/deeptools_finished.txt", "module": "4_analyse"},
    {"key": "5_gsea", "label": "富集分析", "marker": "5_gsea/gsea_finished.txt", "module": "5_gsea"},
    {"key": "6_motif", "label": "Motif", "marker": "6_motif/motif_finished.txt", "module": "6_motif"},
]


def load_config() -> dict:
    """读取 config.yaml，失败返回空 dict。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_projects(config: dict | None = None) -> list[dict]:
    cfg = config if config is not None else load_config()
    return cfg.get("projects", []) or []


def save_project_order(config: dict) -> tuple[bool, str]:
    """把（可能已重排的）config["projects"] 顺序写回 config.yaml。

    只改列表顺序不改内容；与配置编辑页使用相同的 dump 参数，
    保证 yaml 风格一致（不排序键、保留 Unicode）。返回 (成功?, 提示)。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True, "已保存项目顺序"
    except OSError as e:
        return False, f"保存失败：{e}"


# 项目名会展开进 result/{species}/{experiment}、logs/{species}/{experiment}，再经 snakemake
# 的 shell 块、perl 的 system()、python 的 shell=True 一路传给下游工具。名字里只要有空格，
# `> {log}` / `touch {output}` 就会被 bash 按空格拆词 —— 实测日志会跑进一个叫
# `logs/TAIR10/314COM_ChIP` 的文件里，touch 则报 `9/3_ChIPseq/step1_finished.txt: 没有那个文件`。
# 所以物种/实验名统一在这个函数里收敛成路径安全的形式。
_UNSAFE_NAME_CHARS = re.compile(r"[\s~!$&'\"()*;<>?\[\]{}|\\`#]")


def sanitize_name(name: str) -> str:
    """把物种/实验名规范成路径安全形式：连续空白压成下划线，其余 shell 元字符丢弃。

    例：`314COM_ChIP 3 and 9` → `314COM_ChIP_3_and_9`。已经是安全名的原样返回。
    """
    s = re.sub(r"\s+", "_", (name or "").strip())
    return _UNSAFE_NAME_CHARS.sub("", s)


def is_safe_name(name: str) -> bool:
    """名字本身是否已经能安全地当路径用（无需 sanitize 改写）。"""
    return bool(name) and sanitize_name(name) == name


# 原始数据目录里留一张便签：空目录在文件管理器里看不出用途，
# 有了它用户就知道 fastq 往哪放、放完下一步该去哪个页面。
_RESOURCES_NOTE = """\
这里是项目 {species} / {experiment} 的原始测序数据目录，
由「物种注释」页或「配置编辑」页配置项目时自动创建。

把 fastq 直接放在本目录下，支持这些命名：
  IP2_1.clean.fq.gz    IP2_2.clean.fq.gz    双端，_1/_2 成对
  IP2_1_val_1.fq.gz    IP2_2_val_2.fq.gz    Trimmomatic 产出
  IP2_1.fq.gz          IP2_2.fq.gz
  INPUT2_1.fastq.gz    INPUT2_2.fastq.gz    单端也行

文件名里 _1 前面的部分就是样本名（上面几行分别是 IP2 和 INPUT2）。
带 input / ctrl / control / mock / igg / ck 字样的样本会被当成对照。

放好之后：
  1. 打开「配置编辑 → 样本配对表」
  2. 在 {species} 项目的抽屉里点「按识别结果生成配对表」
  3. 核对一遍配对和重复组，保存

这个说明文件可以直接删，不影响流程。
"""


def project_dirs(species: str, experiment: str) -> dict[str, Path]:
    """一个项目会用到的三个目录：原始数据 / 结果 / 日志。"""
    return {
        "rawdata": RESOURCES_DIR / species / experiment,
        "result": RESULT_DIR / species / experiment,
        "logs": LOGS_DIR / species / experiment,
    }


def _rel_path(p: Path) -> str:
    """相对仓库根的路径；数据在别的盘时退回绝对路径。"""
    try:
        return str(p.relative_to(BASE_DIR))
    except ValueError:
        return str(p)


def ensure_project_dirs(
    species: str, experiment: str, rawdata_dir: str = ""
) -> dict[str, object]:
    """按 species/experiment 预建原始数据、结果、日志目录。

    rawdata_dir 留空时按约定建 workflow/resources/<物种>/<实验>/，让用户把 fastq
    直接拖进去；显式给了别的路径（比如数据在另一块盘）就只建那一处，
    不再多建一个空目录。物种层与实验层两级都建：同一物种跑第二个实验时
    两份原始数据互不干扰。

    返回 {"dirs": {key: 路径}, "created": [...], "existed": [...]}，
    其中 created/existed 是相对仓库根的路径，供 UI 直接展示。
    """
    sp = sanitize_name(species)
    exp = sanitize_name(experiment)
    if not sp or not exp:
        raise ValueError("物种名与实验名都不能为空")

    custom_raw = (rawdata_dir or "").strip()
    dirs = project_dirs(sp, exp)
    if custom_raw:
        p = Path(custom_raw)
        dirs["rawdata"] = p if p.is_absolute() else BASE_DIR / p

    created: list[str] = []
    existed: list[str] = []
    for path in dirs.values():
        rel = _rel_path(path)
        if path.is_dir():
            existed.append(rel)
        else:
            path.mkdir(parents=True, exist_ok=True)
            created.append(rel)

    # 便签只放在默认的原始数据目录里；用户自定义目录不往里塞东西
    if not custom_raw and dirs["rawdata"].is_dir():
        note = dirs["rawdata"] / "README.txt"
        if not note.exists():
            note.write_text(_RESOURCES_NOTE.format(species=sp, experiment=exp), encoding="utf-8")

    return {
        "dirs": {k: _rel_path(v) for k, v in dirs.items()},
        "created": created,
        "existed": existed,
        "rawdata_dir": _rel_path(dirs["rawdata"]),
    }


def check_marker(species: str, experiment: str, marker_path: str) -> bool:
    return (RESULT_DIR / species / experiment / marker_path).exists()


def get_marker_mtime(species: str, experiment: str, marker_path: str) -> str | None:
    p = RESULT_DIR / species / experiment / marker_path
    try:
        if p.exists():
            return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None
    return None


def get_snakemake_processes() -> list[dict]:
    """检测当前用户在本项目目录下运行的 snakemake 进程（UID + CWD 双重过滤）。"""
    procs: list[dict] = []
    base_real = os.path.realpath(str(BASE_DIR))
    pids: list[str] = []
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(CURRENT_UID), "-f", "snakemake"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip().isdigit()]
    except Exception:
        pids = []

    if not pids:
        try:
            pids = [e for e in os.listdir("/proc") if e.isdigit()]
        except Exception:
            return procs

    for pid in pids:
        proc_dir = f"/proc/{pid}"
        try:
            if os.stat(proc_dir).st_uid != CURRENT_UID:
                continue
            with open(f"{proc_dir}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if "snakemake" not in cmdline or "streamlit" in cmdline or "pgrep" in cmdline:
                continue
            try:
                cwd_real = os.path.realpath(os.readlink(f"{proc_dir}/cwd"))
                if not (cwd_real == base_real or cwd_real.startswith(base_real + os.sep)):
                    continue
            except (FileNotFoundError, PermissionError, OSError):
                cwd_real = "<unknown>"
            procs.append({"pid": int(pid), "cwd": cwd_real, "cmdline": cmdline})
        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
            continue
    return procs


def is_snakemake_running() -> bool:
    return len(get_snakemake_processes()) > 0


def stage_status(species: str, experiment: str, modules: dict, running: bool) -> list[dict]:
    """返回某项目各管线阶段的状态列表，供 stage_rail 使用。"""
    out: list[dict] = []
    for st_def in PIPELINE_STAGES:
        marker = st_def["marker"]
        module = st_def["module"]
        enabled = bool(modules.get(module, False))
        if check_marker(species, experiment, marker):
            mtime = get_marker_mtime(species, experiment, marker)
            out.append({"name": st_def["label"], "status": "done", "meta": mtime or "已完成"})
        elif enabled and running:
            out.append({"name": st_def["label"], "status": "running", "meta": "运行中"})
        elif enabled:
            out.append({"name": st_def["label"], "status": "pending", "meta": "待运行"})
        else:
            out.append({"name": st_def["label"], "status": "disabled", "meta": "未启用"})
    return out


def project_progress(species: str, experiment: str, modules: dict) -> tuple[int, int]:
    """返回 (已完成阶段数, 启用阶段数)。"""
    done = enabled = 0
    for st_def in PIPELINE_STAGES:
        if modules.get(st_def["module"], False):
            enabled += 1
            if check_marker(species, experiment, st_def["marker"]):
                done += 1
    return done, enabled


def list_recent_logs(limit: int = 6) -> list[dict]:
    """最近的 snakemake 运行日志（按修改时间倒序）。"""
    logs: list[dict] = []
    if not LOGS_DIR.is_dir():
        return logs
    try:
        for p in LOGS_DIR.glob("snakemake_run_*.log"):
            try:
                stat = p.stat()
                logs.append({
                    "name": p.name,
                    "relpath": str(p.relative_to(BASE_DIR)),
                    "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime_ts": stat.st_mtime,
                    "size_kb": stat.st_size / 1024,
                })
            except Exception:
                continue
    except Exception:
        return logs
    logs.sort(key=lambda x: x["mtime_ts"], reverse=True)
    return logs[:limit]


def tail_log(relpath: str, n: int = 120) -> str:
    """读取日志末尾 n 行。"""
    try:
        with open(BASE_DIR / relpath, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def disk_free_gb(path: Path | None = None) -> float | None:
    try:
        target = str(path or BASE_DIR)
        return shutil.disk_usage(target).free / (1024 ** 3)
    except Exception:
        return None


def disk_usage(path: Path | None = None) -> dict | None:
    """磁盘使用情况：{free_gb, total_gb, used_frac}；失败返回 None。"""
    try:
        u = shutil.disk_usage(str(path or BASE_DIR))
        return {"free_gb": u.free / (1024 ** 3), "total_gb": u.total / (1024 ** 3),
                "used_frac": (u.total - u.free) / u.total if u.total else 0.0}
    except Exception:
        return None


def _fmt_size(nbytes: float) -> str:
    """把字节数格式化为人类可读（B/KB/MB/GB/TB）。"""
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def project_size(species: str, experiment: str) -> str:
    """当前项目在 result/ 下占用空间（人类可读）；目录不存在返回 '0B'。"""
    root = RESULT_DIR / str(species) / str(experiment)
    if not root.exists():
        return "0B"
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    except Exception:
        return "?"
    return _fmt_size(total)


def project_last_run(species: str, experiment: str) -> float | None:
    """项目最后活动时间：所有 marker 与该项目运行日志中的最大 mtime；无活动返回 None。"""
    latest = 0.0
    for s in PIPELINE_STAGES:
        mpath = RESULT_DIR / str(species) / str(experiment) / s["marker"]
        try:
            if mpath.exists():
                m = mpath.stat().st_mtime
                if m > latest:
                    latest = m
        except Exception:
            pass
    logd = LOGS_DIR / str(species) / str(experiment)
    if logd.exists():
        try:
            for f in logd.glob("*.log"):
                e = f.stat().st_mtime
                if e > latest:
                    latest = e
        except Exception:
            pass
    return latest if latest > 0 else None


def recent_runs(limit: int = 6) -> list[dict]:
    """最近运行的项目：按最后活动时间倒序，仅返回有活动记录的项目。

    每项：{project, species, experiment, last_run}。"""
    out = []
    for p in get_projects():
        lr = project_last_run(p["species"], p["experiment"])
        if lr:
            out.append({
                "project": f"{p['species']} / {p['experiment']}",
                "species": p["species"],
                "experiment": p["experiment"],
                "last_run": lr,
            })
    out.sort(key=lambda x: -x["last_run"])
    return out[:limit]


def list_species_dirs() -> list[str]:
    """workflow/anno 下已存在的物种目录名。"""
    if not ANNO_DIR.is_dir():
        return []
    try:
        return sorted(d.name for d in ANNO_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
    except Exception:
        return []


def enabled_module_count(modules: dict) -> int:
    return sum(1 for v in (modules or {}).values() if v)


# ===========================================================================
# 统一状态模型 + 阶段表 + 配置校验
# ===========================================================================
# 阶段 -> 操作链接（label, href），href 为 Streamlit MPA 页面 slug
STAGE_ACTION = {
    "unconfigured": ("去配置", "/配置编辑"),
    "pending":      ("运行", "/运行管理"),
    "running":      ("查看日志", "/运行管理"),
    "done":         ("查看结果", "/结果预览"),
    "failed":       ("查看错误", "/运行管理"),
    "skipped":      ("启用", "/配置编辑"),
}

# 状态 -> 上下文动作列表（主动作在前，表格操作列按此动态渲染）
STAGE_ACTIONS: dict[str, list[tuple[str, str]]] = {
    "unconfigured": [("去配置", "/配置编辑")],
    "pending":      [("运行", "/运行管理")],
    "running":      [("查看日志", "/运行管理"), ("停止运行", "/运行管理")],
    "done":         [("查看结果", "/结果预览"), ("重新运行", "/运行管理")],
    "failed":       [("查看错误", "/运行管理"), ("重试", "/运行管理")],
    "skipped":      [("启用模块", "/配置编辑")],
}

# 阶段 key -> 日志文件名包含的 token（用于定位该阶段日志并扫描失败）
STAGE_LOG_TOKENS = {
    "1_download":   ["1_download"],
    "2_QC":         ["2_QC", "2_1_QC", "QC"],
    "3_ChIPseq":    ["3_1_ChIPseq", "3_2_bamtobw", "ChIPseq"],
    "4_peakcalling": ["4_1_peakcalling", "peakcalling"],
    "4_annotation": ["4_2_annoChIPPeaks", "annoChIPPeaks"],
    "4_deeptools":  ["4_3_deeptools", "deeptools"],
    "5_gsea":       ["5_1_gsea", "gsea"],
    "6_motif":      ["6_1_motif", "motif"],
}

FAILURE_SIGNATURES = (
    "Exiting because a job execution failed",
    "Command failed",
    "Error in rule",
    "Traceback (most recent call",
    "WorkflowError",
    "SystemExit",
)


def _exists_any(p: str) -> bool:
    """路径可能是绝对或相对 BASE_DIR；判断是否存在。"""
    if not p:
        return False
    if os.path.isabs(p):
        return os.path.exists(p)
    return (BASE_DIR / p).exists()


def _stage_log_files(species: str, experiment: str, tokens: list[str]) -> list[Path]:
    logd = LOGS_DIR / str(species) / str(experiment)
    if not logd.is_dir():
        return []
    out: list[Path] = []
    for f in logd.glob("*.log"):
        low = f.name.lower()
        if any(tok.lower() in low for tok in tokens):
            out.append(f)
    return out


def _stage_failed(species: str, experiment: str, key: str) -> bool:
    """启用但未完成的阶段：若对应日志尾部出现失败标志，判为失败。"""
    for lf in _stage_log_files(species, experiment, STAGE_LOG_TOKENS.get(key, [])):
        try:
            with open(lf, "r", encoding="utf-8", errors="replace") as fh:
                tail = "".join(fh.readlines()[-40:])
            if any(sig in tail for sig in FAILURE_SIGNATURES):
                return True
        except Exception:
            continue
    return False


def _marker_ts(species: str, experiment: str, marker: str) -> float | None:
    p = RESULT_DIR / str(species) / str(experiment) / marker
    try:
        if p.exists():
            return p.stat().st_mtime
    except Exception:
        return None
    return None


def fmt_duration(seconds: float | None) -> str | None:
    """将秒数格式化为人性化耗时：<60s → '45s'；<1h → '12m'；<1d → '3.2h'；否则 '1.4d'。"""
    if seconds is None or seconds < 0:
        return None
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _infer_genome_fasta(config: dict, species: str, experiment: str) -> str | None:
    mcfg = config.get("motif", {}) or {}
    gf = mcfg.get("genome_fasta", "")
    if gf and _exists_any(gf):
        return gf
    for proj in config.get("projects", []) or []:
        if proj.get("species") == species and proj.get("experiment") == experiment:
            idx = proj.get("index_dir", "")
            if idx:
                for cand in (idx + ".fa", idx + ".fasta"):
                    if _exists_any(cand):
                        return cand
                parent = os.path.dirname(idx)
                pdir = Path(parent) if os.path.isabs(parent) else (BASE_DIR / parent)
                if parent and pdir.is_dir():
                    for f in os.listdir(pdir):
                        if f.endswith((".fa", ".fasta")):
                            return os.path.join(parent, f)
            break
    return None


def classify_stages(
    species: str, experiment: str, modules: dict, running: bool, config: dict | None = None
) -> list[dict]:
    """返回每个管线阶段的结构化状态，供首页阶段表使用。

    每项：{name, key, state, time, dur, action_label, action_href}。
    state ∈ done|running|pending|failed|skipped|unconfigured。
    耗时：相邻已完成阶段 mtime 差（近似顺序执行耗时）。
    """
    cfg = config or {}
    raw: list[dict] = []
    prev_ts: float | None = None
    for st_def in PIPELINE_STAGES:
        key = st_def["key"]
        marker = st_def["marker"]
        module = st_def["module"]
        enabled = bool(modules.get(module, False))
        ts = _marker_ts(species, experiment, marker)
        mtime = get_marker_mtime(species, experiment, marker)
        state = "pending"
        dur: str | None = None
        if ts is not None:
            state = "done"
            dur = fmt_duration(ts - prev_ts) if prev_ts is not None else None
            prev_ts = ts
        elif not enabled:
            state = "skipped"
        else:
            # 启用但未完成：失败 > 未配置 > 运行中 > 待运行
            if _stage_failed(species, experiment, key):
                state = "failed"
            elif _is_unconfigured(key, cfg, species, experiment):
                state = "unconfigured"
            elif running:
                state = "running"
            else:
                state = "pending"
        action = STAGE_ACTION.get(state)
        raw.append({
            "name": st_def["label"],
            "key": key,
            "module": module,
            "state": state,
            "time": mtime,
            "dur": dur,
            "action_label": action[0] if action else None,
            "action_href": action[1] if action else None,
            "actions": STAGE_ACTIONS.get(state, []),
        })
    return raw


def _is_unconfigured(key: str, config: dict, species: str, experiment: str) -> bool:
    """针对特定阶段判断“启用但缺少必要配置”。"""
    mods = _modules_for(config, species, experiment)
    if key == "6_motif":
        mcfg = config.get("motif", {}) or {}
        has_db = bool(mcfg.get("meme_db", ""))
        return (not _infer_genome_fasta(config, species, experiment)) and not has_db
    if key == "5_gsea":
        return not (mods.get("txdb") and mods.get("orgdb"))
    return False


def _modules_for(config: dict, species: str, experiment: str) -> dict:
    for proj in config.get("projects", []) or []:
        if proj.get("species") == species and proj.get("experiment") == experiment:
            return proj
    return {}


def running_summary(config: dict | None = None) -> dict | None:
    """若有 snakemake 在跑，返回 {stage, elapsed_text, pid}；否则 None。"""
    procs = get_snakemake_processes()
    if not procs:
        return None
    pid = procs[0]["pid"]
    secs = None
    try:
        out = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        s = out.stdout.strip()
        secs = int(s) if s.isdigit() else None
    except Exception:
        secs = None
    stage = _current_running_stage(config)
    return {
        "stage": stage,
        "elapsed_text": fmt_duration(secs) if secs is not None else None,
        "pid": pid,
    }


def _current_running_stage(config: dict | None = None) -> str | None:
    """估计当前正在执行的阶段：按管线顺序第一个“启用且未完成”的阶段。"""
    cfg = config or load_config()
    projects = cfg.get("projects", []) or []
    if not projects:
        return None
    p = projects[0]
    mods = p.get("modules", {})
    for st_def in PIPELINE_STAGES:
        if mods.get(st_def["module"], False) and not check_marker(
            p["species"], p["experiment"], st_def["marker"]
        ):
            return st_def["label"]
    return None


# =========================================================================
# 目标文件与进度口径（与 snakefile 的 TARGET_FILES 保持一致）
# =========================================================================
MODULE_TARGETS: dict[str, list[str]] = {
    "1_download": ["result/{s}/{e}/1_download/finished.txt"],
    "2_QC":       ["result/{s}/{e}/2_QC/QC_finished.txt"],
    "3_ChIPseq":  ["result/{s}/{e}/3_ChIPseq/step1_finished.txt",
                   "result/{s}/{e}/3_ChIPseq/step2_finished.txt"],
    "4_analyse":  ["result/{s}/{e}/4_analyse/peakcalling_finished.txt",
                   "result/{s}/{e}/4_analyse/annoChIPPeaks_finished.txt",
                   "result/{s}/{e}/4_analyse/deeptools_finished.txt",
                   "result/{s}/{e}/4_analyse/consensus_finished.txt",
                   "result/{s}/{e}/4_analyse/anno_consensus_finished.txt",
                   "result/{s}/{e}/4_analyse/replicate_qc_finished.txt"],
    "5_gsea":     ["result/{s}/{e}/5_gsea/gsea_finished.txt"],
    "6_motif":    ["result/{s}/{e}/6_motif/motif_finished.txt"],
}

MODULE_ORDER = ("1_download", "2_QC", "3_ChIPseq", "4_analyse", "5_gsea", "6_motif")

# 顶层模块的可读名称（用户界面展示用）
MODULE_LABELS = {
    "1_download": "下载", "2_QC": "质控", "3_ChIPseq": "比对",
    "4_analyse": "Peak/注释/DeepTools", "5_gsea": "GSEA", "6_motif": "Motif",
}


def project_targets(proj: dict) -> list[str]:
    """项目启用模块对应的全部 marker 目标文件（相对项目根）。"""
    s, e = proj["species"], proj["experiment"]
    mods = proj.get("modules", {}) or {}
    out: list[str] = []
    for m in MODULE_ORDER:
        if mods.get(m, False):
            out.extend(t.format(s=s, e=e) for t in MODULE_TARGETS[m])
    return out


def progress_summary(proj: dict) -> dict:
    """统一进度口径，避免“全流程完成”与“启用模块完成”混用。

    返回：{enabled_modules, done_modules, skipped_modules,
    targets_total, targets_done, targets_frac, pending_modules}。
    模块“完成”=该模块全部 marker 存在；百分比口径=目标文件。"""
    s, e = proj["species"], proj["experiment"]
    mods = proj.get("modules", {}) or {}
    enabled = skipped = done_mod = 0
    t_total = t_done = 0
    pending: list[str] = []
    for m in MODULE_ORDER:
        if not mods.get(m, False):
            skipped += 1
            continue
        enabled += 1
        markers = [t.format(s=s, e=e) for t in MODULE_TARGETS[m]]
        t_total += len(markers)
        md = sum(1 for mk in markers if _exists_any(mk))
        t_done += md
        if md == len(markers):
            done_mod += 1
        else:
            pending.append(m)
    frac = (t_done / t_total) if t_total else 0.0
    return {
        "enabled_modules": enabled, "done_modules": done_mod,
        "skipped_modules": skipped, "pending_modules": pending,
        "targets_total": t_total, "targets_done": t_done, "targets_frac": frac,
    }


def project_outputs_count(species: str, experiment: str) -> int | None:
    """项目输出文件数量（result/{sp}/{ex} 下）；不存在返回 0，异常返回 None。"""
    root = RESULT_DIR / str(species) / str(experiment)
    if not root.exists():
        return 0
    n = 0
    try:
        for _dirpath, _dirs, files in os.walk(root):
            n += len(files)
    except Exception:
        return None
    return n


def last_run_duration(species: str, experiment: str) -> str | None:
    """最近一次阶段运行时长（近似）：项目日志目录中最新 .log 的 mtime - ctime。"""
    logd = LOGS_DIR / str(species) / str(experiment)
    if not logd.is_dir():
        return None
    try:
        newest = max(logd.glob("*.log"), key=lambda p: p.stat().st_mtime, default=None)
        if newest is None:
            return None
        stt = newest.stat()
        secs = stt.st_mtime - stt.st_ctime
        return fmt_duration(secs) if secs > 0 else None
    except Exception:
        return None


def last_failed_stage(species: str, experiment: str, modules: dict) -> str | None:
    """按管线顺序返回第一个“启用、未完成且日志含失败标志”的阶段名。"""
    for st_def in PIPELINE_STAGES:
        if modules.get(st_def["module"], False) and not check_marker(species, experiment, st_def["marker"]):
            if _stage_failed(species, experiment, st_def["key"]):
                return st_def["label"]
    return None


# =========================================================================
# 日志分层解析（摘要 / 问题列表 / 原始文本）
# =========================================================================
_ERR_TOKENS = ("error", "Error in rule", "Command failed", "Traceback", "WorkflowError",
               "Exiting because", "system错误", "ERROR:")
_WARN_TOKENS = ("warning", "Warning", "WARNING", "deprecated", "In summary: There were")


def analyze_log_text(text: str, max_items: int = 40) -> dict:
    """把原始日志解析为分层结构，供日志查看区使用。

    返回：{n_lines, status(success|failed|unknown), n_error, n_warning,
    errors[list], warnings[list], last_err_idx, last_err_ctx}。"""
    lines = (text or "").splitlines()
    errors: list[tuple[int, str]] = []
    warnings: list[tuple[int, str]] = []
    last_err_idx = -1
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(tok.lower() in low for tok in _ERR_TOKENS):
            errors.append((i + 1, ln.strip()[:220]))
            last_err_idx = i
        elif any(tok.lower() in low for tok in _WARN_TOKENS):
            warnings.append((i + 1, ln.strip()[:220]))
    failed = any(sig in text for sig in FAILURE_SIGNATURES)
    finished = "Finished at" in text or "This was a dry-run" in text
    status = "failed" if failed else ("success" if finished and not errors else "unknown")
    ctx = ""
    if last_err_idx >= 0:
        ctx = "\n".join(lines[max(0, last_err_idx - 3): last_err_idx + 12])
    return {
        "n_lines": len(lines),
        "status": status,
        "n_error": len(errors),
        "n_warning": len(warnings),
        "errors": errors[:max_items],
        "warnings": warnings[:max_items],
        "last_err_idx": last_err_idx,
        "last_err_ctx": ctx,
    }


def config_issues(config: dict | None = None) -> list[dict]:
    """全局配置校验：返回可定位到具体字段的错误/警告列表。

    每项：{level: error|warn|info, field, msg}。"""
    cfg = config if config is not None else load_config()
    issues: list[dict] = []

    def add(level, field, msg):
        issues.append({"level": level, "field": field, "msg": msg})

    projects = cfg.get("projects", []) or []
    if not projects:
        add("error", "projects", "未配置任何项目（projects 为空）")

    # 读取 metadata 的 group 列（用于比较组校验）
    meta_groups: set[str] = set()
    meta_path = BASE_DIR / "config" / "metadata.csv"
    try:
        import csv as _csv
        with open(meta_path, newline="", encoding="utf-8") as fh:
            reader = _csv.DictReader(fh)
            cols = reader.fieldnames or []
            rows = list(reader)
        cols_lower = [(c or "").lower() for c in cols]
        if not any("sample" in c for c in cols_lower):
            add("warn", "metadata.csv", "未找到样本名列（含 'sample' 的列），请确认样本名与流程一致")
        if "group" in cols:
            meta_groups = {(r.get("group") or "").strip() for r in rows if (r.get("group") or "").strip()}
    except FileNotFoundError:
        add("error", "metadata.csv", "未找到 config/metadata.csv")
    except Exception as e:  # noqa: BLE001
        add("warn", "metadata.csv", f"metadata 解析失败：{e}")

    for i, proj in enumerate(projects):
        sp = proj.get("species", "")
        ex = proj.get("experiment", "")
        tag = f"{sp}/{ex}" if (sp and ex) else f"projects[{i}]"
        for req in ("species", "experiment", "index_dir"):
            if not proj.get(req):
                add("error", f"projects[{i}].{req}", f"项目 {tag} 缺少必填字段 {req}")
        for req in ("species", "experiment"):
            val = proj.get(req, "")
            if val and not is_safe_name(val):
                add("error", f"projects[{i}].{req}",
                    f"{req} '{val}' 含空格或 shell 特殊字符：该名字会展开进 result/logs 路径，"
                    f"被 shell 按空格拆词后规则会在 touch 阶段失败"
                    f"（日志会写进一个叫 '{val.split()[0]}' 的文件里）"
                    f"。请改成 {sanitize_name(val)}，并把已有的 result/logs 目录一并改名")
        idx = proj.get("index_dir", "")
        if idx and not any(_exists_any(f"{idx}.{n}.bt2") for n in (1, 2, 3, 4)):
            add("error", f"{tag}.index_dir", f"bowtie2 索引前缀不存在：{idx}（缺 *.bt2）")
        rd = proj.get("rawdata_dir", "")
        if rd and not _exists_any(rd):
            add("warn", f"{tag}.rawdata_dir", f"原始数据目录不存在：{rd}")
        mods = proj.get("modules", {}) or {}
        if not any(mods.values()):
            add("info", f"{tag}.modules", "该项目未启用任何模块")
        if mods.get("5_gsea") and not (proj.get("txdb") and proj.get("orgdb")):
            add("error", f"{tag}", "启用 GSEA 但缺少 txdb/orgdb")
        if mods.get("6_motif") and not _infer_genome_fasta(cfg, sp, ex):
            add("warn", f"{tag}", "启用 Motif 但未推断到基因组 FASTA（可在 motif.genome_fasta 指定）")

    # deeptools 比较组 vs metadata group
    for comp in (cfg.get("deeptools", {}) or {}).get("comparisons", []) or []:
        for g in comp.get("groups", []) or []:
            if meta_groups and g not in meta_groups:
                add("warn", f"deeptools.comparisons.{comp.get('name', '?')}",
                    f"比较组 '{g}' 在 metadata.csv 的 group 列中不存在")

    # motif 数据库/基因组路径
    mcfg = cfg.get("motif", {}) or {}
    for k in ("meme_db", "genome_fasta"):
        v = mcfg.get(k, "")
        if v and not _exists_any(v):
            add("error", f"motif.{k}", f"配置的路径不存在：{v}")

    return issues
