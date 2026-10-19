"""
运行管理页面 - 监控流程状态、触发运行、查看日志
"""

import streamlit as st
import os
import subprocess
import signal
import time
import yaml
import pwd
from datetime import datetime
from pathlib import Path

import sys

# 让 streamlit_app/utils 可导入（控制台主题）
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from utils.ui_theme import (  # noqa: E402
    inject_theme,
    page_header,
    section_title,
    pill,
    list_row,
    code_html,
    render_html_list,
    render_sidebar,
    status_bar,
    log_console,
    render_issues,
)
from utils import runtime as rt  # noqa: E402

st.set_page_config(page_title="运行管理", layout="wide")
inject_theme()
render_sidebar("run")
page_header("运行管理", "监控流程状态、触发运行、查看实时日志")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# 当前 Streamlit 服务进程的用户信息（用于过滤“属于自己”的 snakemake 进程）
CURRENT_UID = os.getuid()
try:
    CURRENT_USER = pwd.getpwuid(CURRENT_UID).pw_name
except KeyError:
    CURRENT_USER = os.environ.get("USER") or os.environ.get("LOGNAME") or str(CURRENT_UID)
# 预先把项目目录 realpath 一次（避免符号链接导致比较失败）
BASE_DIR_REAL = os.path.realpath(BASE_DIR)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_marker(species, experiment, marker_path):
    """检查 marker 文件是否存在"""
    full_path = os.path.join(BASE_DIR, "result", species, experiment, marker_path)
    return os.path.exists(full_path)


def get_marker_mtime(species, experiment, marker_path):
    """获取 marker 文件修改时间"""
    full_path = os.path.join(BASE_DIR, "result", species, experiment, marker_path)
    if os.path.exists(full_path):
        mtime = os.path.getmtime(full_path)
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    return None


# ============================================================
# 单管线目标：复用 utils.runtime（与 snakefile 的 TARGET_FILES 保持一致）
# ============================================================
MODULE_TARGETS = rt.MODULE_TARGETS


def build_project_targets(proj):
    return rt.project_targets(proj)


def run_snakemake_dryrun(targets):
    """对指定目标执行 dry-run 并返回输出（Snakemake 7.32：目标文件用位置参数）"""
    try:
        result = subprocess.run(
            ["snakemake", "-n", *targets],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=60,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Dry-run 超时"
    except FileNotFoundError:
        return "未找到 snakemake 命令，请确认已激活 conda 环境"


# ============================================================
# 状态总览
# ============================================================
st.subheader("流程状态总览")

# 自动刷新控制
col_refresh1, col_refresh2 = st.columns([1, 3])
with col_refresh1:
    if st.button("刷新状态"):
        st.rerun()
with col_refresh2:
    auto_refresh = st.checkbox("自动刷新（每15秒）", value=False)

config = load_config()
projects = config.get("projects", [])


def get_snakemake_processes():
    """检测当前用户在当前项目目录下运行的 snakemake 进程。

    双重过滤，避免共享服务器上其他用户的 snakemake 进程被误认为“已启动”：
      1. **UID 过滤**：仅保留 /proc/<pid> 的属主与当前 Streamlit 服务进程一致的 PID
      2. **CWD 过滤**：仅保留工作目录在 BASE_DIR 下的 PID（同一用户在另一个项目目录
         里跑的 snakemake 也不会影响本项目 UI）

    Returns:
        list[dict]: 每项包含 {"pid": int, "user": str, "cwd": str, "cmdline": str}
    """
    procs = []

    # 使用 pgrep -u <user> 先限定当前用户，减少后续循环开销；
    # 若 pgrep 不可用或用户名获取失败，回退到遍历 /proc
    pids = []
    try:
        result = subprocess.run(
            ["pgrep", "-u", str(CURRENT_UID), "-f", "snakemake"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip().isdigit()]
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pids = []

    if not pids:
        # 回退方案：直接扫 /proc（不依赖 pgrep）
        try:
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    pids.append(entry)
        except Exception:
            return procs

    for pid in pids:
        proc_dir = f"/proc/{pid}"
        try:
            # 1) UID 过滤：仅保留当前用户的进程
            st_info = os.stat(proc_dir)
            if st_info.st_uid != CURRENT_UID:
                continue

            # 2) cmdline 确认是 snakemake 且不是 streamlit/pgrep 自身
            with open(f"{proc_dir}/cmdline", "rb") as f:
                raw = f.read()
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if not cmdline or "snakemake" not in cmdline:
                continue
            if "streamlit" in cmdline or "pgrep" in cmdline:
                continue

            # 3) CWD 过滤：仅保留在本项目目录下的 snakemake
            try:
                cwd_link = os.readlink(f"{proc_dir}/cwd")
                cwd_real = os.path.realpath(cwd_link)
                in_project = (
                    cwd_real == BASE_DIR_REAL
                    or cwd_real.startswith(BASE_DIR_REAL + os.sep)
                )
                if not in_project:
                    continue
            except (FileNotFoundError, PermissionError, OSError):
                # 无法确定 CWD 时保守地保留（已经通过 UID 过滤，属于当前用户）
                cwd_real = "<unknown>"

            procs.append({
                "pid": int(pid),
                "user": CURRENT_USER,
                "cwd": cwd_real,
                "cmdline": cmdline,
            })
        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
            continue

    return procs


def is_snakemake_running():
    """当前用户在当前项目目录下是否有 snakemake 进程在运行。"""
    return len(get_snakemake_processes()) > 0


snakemake_procs = get_snakemake_processes()
snakemake_active = len(snakemake_procs) > 0

# 展示当前识别身份，方便确认多用户环境下 UI 看到的是否是自己
st.caption(
    f"当前识别用户：`{CURRENT_USER}` (UID={CURRENT_UID}) | "
    f"项目目录：`{BASE_DIR_REAL}`"
)

if snakemake_active:
    _proc_lines = "\n".join(
        f"  • PID `{p['pid']}` — cwd=`{p['cwd']}` — `{p['cmdline'][:120]}{'…' if len(p['cmdline']) > 120 else ''}`"
        for p in snakemake_procs
    )
    st.info(
        "**Snakemake 进程正在运行中...**  状态会随着任务完成自动更新（刷新页面可查看最新进度）\n\n"
        f"检测到 {len(snakemake_procs)} 个属于当前用户且在项目目录下的进程：\n{_proc_lines}"
    )
else:
    st.caption(
        "未检测到当前用户在本项目目录下的 snakemake 进程（其他用户的进程不会误报）"
    )

# 定义各模块的 marker 文件
MARKERS = {
    "1_download": "1_download/finished.txt",
    "2_QC": "2_QC/QC_finished.txt",
    "3_ChIPseq_step1": "3_ChIPseq/step1_finished.txt",
    "3_ChIPseq_step2": "3_ChIPseq/step2_finished.txt",
    "4_peakcalling": "4_analyse/peakcalling_finished.txt",
    "4_consensus": "4_analyse/consensus_finished.txt",
    "4_anno_consensus": "4_analyse/anno_consensus_finished.txt",
    "4_annotation": "4_analyse/annoChIPPeaks_finished.txt",
    "4_deeptools": "4_analyse/deeptools_finished.txt",
    "4_repQC": "4_analyse/replicate_qc_finished.txt",
    "5_gsea": "5_gsea/gsea_finished.txt",
    "6_motif": "6_motif/motif_finished.txt",
}

def _is_enabled(name, modules):
    """根据 marker 名推断其所属顶层模块是否启用（沿用原有前缀映射）。"""
    if name.startswith("4_"):
        return modules.get("4_analyse", False)
    if name.startswith("5_") or "gsea" in name:
        return modules.get("5_gsea", False)
    if name.startswith("6_") or "motif" in name:
        return modules.get("6_motif", False)
    if "download" in name:
        return modules.get("1_download", False)
    if "QC" in name:
        return modules.get("2_QC", False)
    if "ChIPseq" in name:
        return modules.get("3_ChIPseq", False)
    return False


_GROUP_LABEL = {
    "1": "阶段 1 · 下载 (1_download)",
    "2": "阶段 2 · 质控 (2_QC)",
    "3": "阶段 3 · 比对 (3_ChIPseq)",
    "4": "阶段 4 · 分析 (4_analyse)",
    "5": "阶段 5 · 富集 (5_gsea)",
    "6": "阶段 6 · Motif (6_motif)",
}

for proj in projects:
    species = proj["species"]
    experiment = proj["experiment"]
    modules = proj.get("modules", {})

    with st.expander(f"{species} / {experiment}", expanded=False):
        # 模块按顶层阶段分组，逐行梯次（缩进）排列，避免横向文字挤压
        groups = {}
        for name, marker in MARKERS.items():
            groups.setdefault(name.split("_", 1)[0], []).append((name, marker))

        for g in sorted(groups):
            # 该阶段汇总：已完成数 / 启用步骤
            g_done = sum(1 for _n, mk in groups[g] if check_marker(species, experiment, mk))
            g_enabled = sum(1 for n, _mk in groups[g] if _is_enabled(n, modules))
            agg_kind = "ok" if (g_enabled and g_done >= g_enabled) else ("accent" if g_done else "idle")
            agg_text = f"{g_done}/{g_enabled} 完成" if g_enabled else "未启用"
            st.markdown(
                '<div style="display:flex;align-items:baseline;justify-content:space-between;'
                'margin-top:.3rem">'
                + f'<span class="ux-section-title" style="margin:0">{_GROUP_LABEL.get(g, "阶段 " + g)}</span>'
                + pill(agg_text, agg_kind)
                + '</div>',
                unsafe_allow_html=True,
            )
            rows = []
            for name, marker in groups[g]:
                exists = check_marker(species, experiment, marker)
                mtime = get_marker_mtime(species, experiment, marker)
                is_enabled = _is_enabled(name, modules)
                if exists:
                    text, kind = "已完成", "ok"
                elif is_enabled and snakemake_active:
                    text, kind = "运行中", "run"
                elif is_enabled:
                    text, kind = "待运行", "accent"
                else:
                    text, kind = "未启用", "idle"
                short = name.split("_", 1)[1] if "_" in name else name
                rows.append(list_row(
                    main=f"└ {short}",
                    sub=(mtime if exists else ""),
                    right_html=pill(text, kind),
                ))
            render_html_list(rows)
        
        # 重跑模块功能
        st.markdown("---")
        st.markdown("**重跑已完成的模块**")
        st.caption("删除标记文件后，下次运行 Snakemake 会重新执行该模块")
        
        # 收集已完成的模块
        completed_modules = []
        for name, marker in MARKERS.items():
            if check_marker(species, experiment, marker):
                completed_modules.append(name)
        
        if completed_modules:
            rerun_selected = st.multiselect(
                "选择要重跑的模块",
                completed_modules,
                key=f"rerun_{species}_{experiment}",
            )

            # 模块名 -> 需要清理的输出目录映射
            MODULE_OUTPUT_DIRS = {
                "1_download": ["1_download"],
                "2_QC": ["2_QC"],
                "3_ChIPseq_step1": ["3_ChIPseq"],
                "3_ChIPseq_step2": ["3_ChIPseq"],
                "4_peakcalling": ["4_analyse/peakcalling"],
                "4_consensus": ["4_analyse/consensus"],
                "4_anno_consensus": ["4_analyse/annoChIPPeaks/consensus_*"],
                "4_annotation": ["4_analyse/annoChIPPeaks"],
                "4_deeptools": ["4_analyse/deeptools"],
                "4_repQC": ["4_analyse/replicate_qc"],
                "5_gsea": ["5_gsea"],
                "6_motif": ["6_motif"],
            }

            clean_outputs = st.checkbox(
                "同时清空对应模块的输出文件（推荐）",
                value=True,
                help="勾选后会删除 marker 文件和已有的输出目录内容，确保参数修改后能完全重新运行",
                key=f"clean_outputs_{species}_{experiment}",
            )
            
            if st.button(f"删除选中模块（标记文件{' + 输出目录' if clean_outputs else ''}）", key=f"delete_markers_{species}_{experiment}"):
                if rerun_selected:
                    import shutil as _shutil
                    import glob as _glob
                    deleted_count = 0
                    cleaned_dirs = []

                    for module_name in rerun_selected:
                        # 1. 删除 marker 文件
                        marker_path = MARKERS[module_name]
                        full_path = os.path.join(BASE_DIR, "result", species, experiment, marker_path)
                        try:
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                deleted_count += 1
                        except Exception as e:
                            st.error(f"删除 {marker_path} 失败: {e}")

                        # 2. 清空输出目录
                        if clean_outputs and module_name in MODULE_OUTPUT_DIRS:
                            for dir_pattern in MODULE_OUTPUT_DIRS[module_name]:
                                target = os.path.join(BASE_DIR, "result", species, experiment, dir_pattern)
                                # 支持 glob 通配符（如 consensus_*）
                                if "*" in target:
                                    matched = _glob.glob(target)
                                    for m in matched:
                                        if os.path.isdir(m):
                                            try:
                                                _shutil.rmtree(m)
                                                cleaned_dirs.append(os.path.relpath(m, os.path.join(BASE_DIR, "result", species, experiment)))
                                            except Exception as e:
                                                st.error(f"清空 {m} 失败: {e}")
                                elif os.path.isdir(target):
                                    try:
                                        _shutil.rmtree(target)
                                        cleaned_dirs.append(dir_pattern)
                                    except Exception as e:
                                        st.error(f"清空 {target} 失败: {e}")
                    
                    msg_parts = []
                    if deleted_count > 0:
                        msg_parts.append(f"删除 {deleted_count} 个标记文件")
                    if cleaned_dirs:
                        msg_parts.append(f"清空 {len(cleaned_dirs)} 个输出目录")
                    if msg_parts:
                        st.success(f"已{'，'.join(msg_parts)}。下次运行 Snakemake 时将完全重新执行这些模块")
                        if cleaned_dirs:
                            with st.expander("已清理的目录", expanded=False):
                                for d in cleaned_dirs:
                                    st.text(f"  {d}")
                        st.rerun()
                else:
                    st.warning("请先选择要重跑的模块")
        else:
            st.info("当前无已完成的模块")
        
        # 清理未完成任务功能
        st.markdown("---")
        st.markdown("**清理未完成任务（断点续行）**")
        st.caption("检测有输出目录但无完成标记的模块，清理中间文件后可重新运行")
        
        # 定义各模块的输出目录
        MODULE_DIRS = {
            "1_download": "1_download",
            "2_QC": "2_QC",
            "3_ChIPseq": "3_ChIPseq",
            "4_peakcalling": "4_analyse/peakcalling",
            "4_consensus": "4_analyse/consensus",
            "4_anno_consensus": "4_analyse/annoChIPPeaks",
            "4_annotation": "4_analyse/annoChIPPeaks",
            "4_deeptools": "4_analyse/deeptools",
            "4_repQC": "4_analyse/replicate_qc",
            "5_gsea": "5_gsea",
            "6_motif": "6_motif",
        }
        
        # 检测未完成的模块
        incomplete_modules = []
        for name, marker in MARKERS.items():
            # 跳过 step1/step2 子步骤，只看主模块
            if "step" in name:
                continue
            
            # 映射到模块目录
            if name in MODULE_DIRS:
                module_dir = MODULE_DIRS[name]
            else:
                continue
            
            marker_exists = check_marker(species, experiment, marker)
            dir_path = os.path.join(BASE_DIR, "result", species, experiment, module_dir)
            dir_exists = os.path.isdir(dir_path)
            
            # 如果目录存在但标记不存在，说明未完成
            if dir_exists and not marker_exists:
                incomplete_modules.append((name, module_dir, dir_path))
        
        if incomplete_modules:
            st.warning(f"检测到 {len(incomplete_modules)} 个未完成的模块")
            
            for mod_name, mod_dir, full_path in incomplete_modules:
                with st.expander(f"{mod_name} ({mod_dir})", expanded=False):
                    # 列出目录下的文件
                    try:
                        files = []
                        for root, dirs, filelist in os.walk(full_path):
                            for f in filelist:
                                fpath = os.path.join(root, f)
                                rel_path = os.path.relpath(fpath, full_path)
                                size_kb = os.path.getsize(fpath) / 1024
                                files.append({
                                    "文件": rel_path,
                                    "大小": f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
                                })
                        
                        if files:
                            import pandas as pd
                            st.dataframe(pd.DataFrame(files).head(20), use_container_width=True)
                            st.caption(f"共 {len(files)} 个文件（显示前 20 个）")
                            
                            if st.button(f"清空 {mod_name} 目录", key=f"clean_{species}_{experiment}_{mod_name}"):
                                try:
                                    import shutil
                                    shutil.rmtree(full_path)
                                    st.success(f"已清空 {mod_dir}，下次运行时将从头开始")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"清空目录失败: {e}")
                        else:
                            st.info("目录为空")
                    except Exception as e:
                        st.error(f"读取目录失败: {e}")
        else:
            st.success("无未完成的任务，所有启用的模块都已正常完成或尚未开始")

# 自动刷新实现
if auto_refresh:
    time.sleep(15)
    st.rerun()

# ============================================================
# 运行控制
# ============================================================
st.markdown("---")
st.subheader("运行控制")

# ——单管线模式：一次只能选择并运行一个项目的管线——
try:
    _cfg_mtime = datetime.fromtimestamp(os.path.getmtime(CONFIG_PATH)).strftime("%Y-%m-%d %H:%M")
except Exception:  # noqa: BLE001
    _cfg_mtime = "未知"

_proj_options = [f"{p['species']} / {p['experiment']}" for p in projects]
selected_idx = st.selectbox(
    "选择要运行的管线（单管线模式，一次仅运行一个项目）",
    range(len(projects)),
    format_func=lambda i: _proj_options[i],
    key="run_project_selector",
) if projects else None

if selected_idx is None:
    st.warning("config.yaml 中没有任何项目，请先在「配置编辑 → 项目管理」中添加。")
    st.stop()  # 无项目时不再渲染运行按钮

selected_proj = projects[selected_idx]
_sel_targets = build_project_targets(selected_proj)
_sel_enabled = [k for k, v in selected_proj.get("modules", {}).items() if v]
_other_projects = [f"{p['species']} / {p['experiment']}" for i, p in enumerate(projects) if i != selected_idx]

scope_title = "本次运行范围：" + _proj_options[selected_idx] + (
    f"（{len(_sel_enabled)} 个启用模块 / {len(_sel_targets)} 个目标）" if _sel_enabled else "（无启用模块）")
section_title(scope_title)
_scope_rows = [
    list_row(
        main=f"└ {rt.MODULE_LABELS.get(m, m)}（{m}）",
        sub_html=code_html("、".join(t.format(s=_p_s, e=_p_e).split("/")[-1] for t in MODULE_TARGETS[m]),
                           title="、".join(t.format(s=_p_s, e=_p_e) for t in MODULE_TARGETS[m])),
    )
    for m in _sel_enabled
    for _p_s, _p_e in [(selected_proj["species"], selected_proj["experiment"])]
]
if _scope_rows:
    render_html_list(_scope_rows)
else:
    st.caption("该项目未启用任何模块")
if _other_projects:
    st.caption("不会被触发：" + "、".join(f"`{p}`" for p in _other_projects))
st.caption(f"实际目标文件：`{' '.join(_sel_targets)}`" if len(_sel_targets) <= 6 else
           f"共 {len(_sel_targets)} 个目标文件（Snakemake 仅构建该项目的 DAG）")

if not _sel_enabled:
    st.error("所选项目没有启用任何模块，请先到「配置编辑」中启用需要运行的阶段。")
    _sel_blocked = True
else:
    _sel_blocked = False
st.info(
    "**单管线模式**：启动命令会把目标文件作为位置参数限定为所选项目的 marker，"
    "其他项目不会被连带运行；同一时刻只允许运行一条管线（运行中时启动按钮会禁用）。"
)
st.caption(f"当前配置版本：`{CONFIG_PATH}` · 修改于 {_cfg_mtime}")

# 检测所选项目是否有未完成的任务（提示可以续行，仅针对选中项目）
has_incomplete = False
_species_sel = selected_proj["species"]
_experiment_sel = selected_proj["experiment"]
for name, marker in MARKERS.items():
    if "step" in name:
        continue
    module_dir = {
        "1_download": "1_download",
        "2_QC": "2_QC",
        "3_ChIPseq": "3_ChIPseq",
        "4_peakcalling": "4_analyse/peakcalling",
        "4_consensus": "4_analyse/consensus",
        "4_anno_consensus": "4_analyse/annoChIPPeaks",
        "4_annotation": "4_analyse/annoChIPPeaks",
        "4_deeptools": "4_analyse/deeptools",
        "4_repQC": "4_analyse/replicate_qc",
        "5_gsea": "5_gsea",
        "6_motif": "6_motif",
    }.get(name)
    if module_dir:
        marker_exists = check_marker(_species_sel, _experiment_sel, marker)
        dir_path = os.path.join(BASE_DIR, "result", _species_sel, _experiment_sel, module_dir)
        if os.path.isdir(dir_path) and not marker_exists:
            has_incomplete = True
            break

if has_incomplete:
    st.info(f"**检测到「{_proj_options[selected_idx]}」有未完成的任务** — 直接启动即可从断点续行")

col1, col2, col3 = st.columns(3)

with col1:
    cores = st.number_input("CPU 核心数", min_value=1, max_value=64, value=30)

with col2:
    run_mode = st.selectbox("运行模式", ["正常运行", "Dry-run（仅预览）", "强制全部重跑"])

with col3:
    extra_args = st.text_input("额外参数", placeholder="例如: --until analyse_deeptools")

# Dry-run 预览（仅预览所选项目的 DAG）
if st.button("预览将执行的任务 (dry-run)", key="dryrun", disabled=_sel_blocked):
    with st.spinner("正在分析 DAG..."):
        output = run_snakemake_dryrun(_sel_targets)
    st.caption(f"snakemake -n {' '.join(_sel_targets)}")
    st.code(output, language="text")

# 正式运行
st.markdown("---")

if "snakemake_running" not in st.session_state:
    st.session_state.snakemake_running = snakemake_active
    st.session_state.snakemake_pid = None

# 同步实际进程状态
if st.session_state.snakemake_pid:
    try:
        os.kill(st.session_state.snakemake_pid, 0)  # 检查进程是否存在
    except ProcessLookupError:
        st.session_state.snakemake_running = False
        st.session_state.snakemake_pid = None
    except PermissionError:
        # 无权限检查该进程（可能是其他用户启动的），假设已结束
        st.session_state.snakemake_running = False
        st.session_state.snakemake_pid = None

# 只有当我们自己启动的进程还活着，或系统检测到 snakemake 在跑时，才认为 running
# 注意：暂停后 pid 已清空，此时即使 pgrep 还能短暂检测到进程（正在退出中），也不阻止恢复
if st.session_state.snakemake_pid is not None:
    st.session_state.snakemake_running = True
else:
    st.session_state.snakemake_running = snakemake_active

# 启动/恢复按钮（单管线：目标文件作为位置参数，仅构建所选项目的 DAG）
import re as _re  # noqa: E402  （解析 dry-run job 数）

_pending_modules = rt.progress_summary(selected_proj)["pending_modules"]

# ——运行前确认摘要：高影响操作点击前可快速核对范围——
with st.container(border=True):
    section_title("运行前确认摘要")
    render_html_list([
        list_row(main="项目",
                 sub_html=code_html(f"{selected_proj['species']} / {selected_proj['experiment']}")),
        list_row(main="配置最后修改", sub=_cfg_mtime),
        list_row(main="将执行的模块",
                 sub="、".join(f"{rt.MODULE_LABELS.get(m, m)}（{m}）" for m in _sel_enabled) or "无"),
        list_row(main="目标文件数量", sub=f"{len(_sel_targets)} 个 marker"),
        list_row(main="运行模式",
                 sub="Dry-run（仅预览，不会执行任务）" if run_mode == "Dry-run（仅预览）" else run_mode),
        list_row(main="待完成模块", sub="、".join(_pending_modules) or "无（启用模块均已完成）"),
    ])

dry_first = st.checkbox(
    "启动前先自动执行 dry-run 预览，核对确认摘要后再决定启动（推荐，防误运行）",
    value=True, key="dry_first",
)


def _build_cmd():
    cmd = ["snakemake", "-c", str(int(cores)), *_sel_targets]
    if run_mode == "Dry-run（仅预览）":
        cmd.insert(1, "-n")
    elif run_mode == "强制全部重跑":
        cmd.append("--forceall")
    if extra_args.strip():
        cmd.extend(extra_args.strip().split())
    return cmd


def _start_snakemake(cmd):
    """后台启动 Snakemake（带 loading / 成功 / 失败 / 可重试反馈）。"""
    st.session_state.snakemake_running = True
    st.session_state.snakemake_target_project = _proj_options[selected_idx]
    with st.spinner("正在后台启动 Snakemake…"):
        try:
            log_file = os.path.join(LOGS_DIR, f"snakemake_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
            with open(log_file, "w") as lf:
                proc = subprocess.Popen(
                    cmd,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    cwd=BASE_DIR,
                )
            st.session_state.snakemake_pid = proc.pid
            st.success(
                f"✅ 已启动 (PID: {proc.pid}) · 仅运行 `{_proj_options[selected_idx]}`（{len(_sel_enabled)} 个启用模块） · "
                f"日志：`{os.path.relpath(log_file, BASE_DIR)}`"
            )
            st.caption(f"命令：`{' '.join(cmd)}`  —  可在下方「日志查看」选择该 snakemake_run_*.log 实时跟进进度。")
        except Exception as e:
            st.error(f"❌ 启动失败：{e}")
            st.session_state.snakemake_running = False
            st.session_state.snakemake_pid = None
            st.info("可再次点击上方“运行所选项目管线”按钮重试（若之前有残留锁，先执行：snakemake --unlock）。")


_disable_launch = st.session_state.snakemake_running or _sel_blocked

# ——待确认状态：dry-run 预览已完成，等待用户核对后确认启动——
if st.session_state.get("pending_launch"):
    _pl = st.session_state["pending_launch"]
    st.info(
        f"**确认摘要**：Dry-run 预览完成，预计执行 **{_pl['jobs']} 个 job** · "
        f"项目 `{_proj_options[selected_idx]}` · 配置修改于 {_cfg_mtime}。核对无误后点击「确认并启动」。"
    )
    st.caption(f"启动命令：`{' '.join(_pl['cmd'])}`")
    _c_ok, _c_no = st.columns([1, 3])
    with _c_ok:
        if st.button("🚀 确认并启动", type="primary", disabled=_disable_launch):
            st.session_state.pop("pending_launch", None)
            _start_snakemake(_pl["cmd"])
    with _c_no:
        if st.button("取消，不启动", key="cancel_pending"):
            st.session_state.pop("pending_launch", None)
            st.rerun()
    st.code(_pl["preview"], language="text")

button_label = (
    "▶ 恢复所选项目管线（从断点继续）" if has_incomplete
    else "▶ 运行所选项目管线（仅 " + _proj_options[selected_idx] + "）"
)
if st.button(
    button_label, type="primary",
    disabled=_disable_launch or bool(st.session_state.get("pending_launch")),
):
    cmd = _build_cmd()
    if run_mode == "Dry-run（仅预览）":
        with st.spinner("正在分析 DAG..."):
            output = run_snakemake_dryrun(_sel_targets)
        st.code(output[-6000:], language="text")
    elif dry_first:
        # 两段式：先预览，写入 pending_launch 后重跑展示确认摘要
        with st.spinner("启动前先执行 dry-run 预览…"):
            output = run_snakemake_dryrun(_sel_targets)
        _m = _re.search(r"total\s+(\d+)", output)
        st.session_state.pending_launch = {
            "cmd": cmd, "preview": output[-4000:], "jobs": _m.group(1) if _m else "?",
        }
        st.rerun()
    else:
        _start_snakemake(cmd)

if st.session_state.snakemake_running and st.session_state.snakemake_pid:
    _tgt_label = st.session_state.get("snakemake_target_project") or "（由外部或其他会话启动）"
    st.warning(
        f"Snakemake 正在运行 (PID: {st.session_state.snakemake_pid}) · 管线：`{_tgt_label}` — "
        "单管线模式：运行中不能启动另一条管线，请先暂停或等待完成。"
    )
    col_pause1, col_pause2 = st.columns([1, 3])
    with col_pause1:
        if st.button("暂停"):
            try:
                os.kill(st.session_state.snakemake_pid, signal.SIGTERM)
                # 等待进程退出（最多 5 秒）
                for _ in range(10):
                    time.sleep(0.5)
                    try:
                        os.kill(st.session_state.snakemake_pid, 0)
                    except (ProcessLookupError, PermissionError):
                        break  # 进程已退出
                st.session_state.snakemake_running = False
                st.session_state.snakemake_pid = None
                st.success("已暂停，下次启动时会从断点继续")
                st.rerun()
            except ProcessLookupError:
                st.session_state.snakemake_running = False
                st.session_state.snakemake_pid = None
                st.info("进程已结束")
                st.rerun()
            except PermissionError:
                st.error("无权限终止该进程。请在终端手动执行: `kill " + str(st.session_state.snakemake_pid) + "`")
    with col_pause2:
        st.caption("暂停后可以点击上面的「恢复运行」按钮继续执行")

# ============================================================
# 常用命令速查
# ============================================================
st.markdown("---")
st.subheader("常用命令")

st.markdown("""
| 场景 | 命令 |
|------|------|
| 仅重跑 4 模块 | `snakemake -c 8 --forcerun analyse_peakcalling analyse_deeptools analyse_annoChIPPeaks_all` |
| 解锁目录 | `snakemake --unlock` |
| 清除元数据 | `snakemake --cleanup-metadata result/TAIR/314Com5/3_ChIPseq/step1_finished.txt` |
| 仅跑 deeptools | `snakemake -c 8 --until analyse_deeptools` |
| 查看 DAG 图 | `snakemake --dag \\| dot -Tpng > dag.png` |
""")

# ============================================================
# 日志查看（按项目区分）
# ============================================================
st.markdown("---")
st.subheader("日志查看")


def _collect_logs(scope):
    """scope: 'ALL' 或 (species, experiment)。返回按 mtime 倒序的日志列表。

    每项：{relpath, name, size, mtime}。"""
    items = []
    if scope == "ALL":
        walk = os.walk(LOGS_DIR)
    else:
        sp, ex = scope
        base = os.path.join(LOGS_DIR, sp, ex)
        walk = ([(base, [], os.listdir(base))] if os.path.isdir(base) else [])
    for root, _dirs, files in walk:
        for f in files:
            if not f.endswith(".log"):
                continue
            full = os.path.join(root, f)
            try:
                stt = os.stat(full)
            except OSError:
                continue
            items.append({
                "relpath": os.path.relpath(full, BASE_DIR),
                "name": f,
                "size": stt.st_size,
                "mtime": datetime.fromtimestamp(stt.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    items.sort(key=lambda x: os.path.getmtime(os.path.join(BASE_DIR, x["relpath"])), reverse=True)
    return items


_proj_scopes = ["全部日志"] + [f"{p['species']}/{p['experiment']}" for p in projects]
# 默认定位到第一个项目（而非“全部日志”）
_default_scope_idx = 1 if len(_proj_scopes) > 1 else 0
sel_scope = st.selectbox(
    "选择项目（区分运行记录）", _proj_scopes,
    index=_default_scope_idx, key="log_scope",
    help="选择具体项目只看它自己的运行日志；选“全部日志”则跨项目包括全局 snakemake_run 日志",
)
if sel_scope == "全部日志":
    _scope_arg = "ALL"
else:
    _sp, _ex = sel_scope.split("/", 1)
    _scope_arg = (_sp.strip(), _ex.strip())

_logs = _collect_logs(_scope_arg)
if not _logs:
    st.info("该范围暂无日志文件")
else:
    _labels = [
        f"{lg['name']}  ·  {lg['mtime']}  ·  {lg['size'] / 1024:.1f} KB"
        + ("  （空文件）" if lg["size"] == 0 else "")
        for lg in _logs
    ]
    # 默认选中第一个非空日志，避免打开时“显示为空”
    _first_nonempty = next((i for i, lg in enumerate(_logs) if lg["size"] > 0), 0)
    _sel_idx = st.selectbox(
        "选择日志文件", list(range(len(_labels))),
        index=_first_nonempty, format_func=lambda i: _labels[i], key="log_file",
    )
    _chosen = _logs[_sel_idx]
    tail_lines = st.slider("显示最后 N 行", min_value=20, max_value=1000, value=150)

    if _chosen["size"] == 0:
        st.info("该日志文件为空（任务可能尚未产生输出，或刚创建）")
    else:
        log_path = os.path.join(BASE_DIR, _chosen["relpath"])
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            st.error(f"读取日志失败: {e}")
            lines = []
        full_text = "".join(lines)
        if not full_text.strip():
            st.info("该日志文件为空")
        else:
            # ---------- 第一层：摘要（状态/错误/警告/行数/耗时） ----------
            parsed = rt.analyze_log_text(full_text)
            _st_map = {
                "success": ("✅ 已完成且无错误", "ok"),
                "failed": ("❌ 存在失败标志", "err"),
                "unknown": ("⏳ 进行中或无明确结束标志", "run"),
            }
            _s_txt, _s_kind = _st_map.get(parsed["status"], _st_map["unknown"])
            _dur = rt.last_run_duration(_sp, _ex) if _scope_arg != "ALL" else None
            st.markdown(
                " ".join([
                    pill(_s_txt, _s_kind),
                    pill(f"错误 {parsed['n_error']} 行", "err" if parsed["n_error"] else "idle"),
                    pill(f"警告 {parsed['n_warning']} 行", "warn" if parsed["n_warning"] else "idle"),
                    pill(f"共 {parsed['n_lines']} 行", "idle"),
                    pill(f"耗时 {_dur}", "idle") if _dur else "",
                ]).replace("  ", " "),
                unsafe_allow_html=True,
            )
            st.caption(f"文件：{_chosen['relpath']} · {_chosen['size'] / 1024:.1f} KB · 修改于 {_chosen['mtime']}")

            # ---------- 第二层：问题列表（按 error/warning 聚合） ----------
            _ctrl1, _ctrl2, _ctrl3 = st.columns([1, 1, 2])
            only_issue = _ctrl1.checkbox(
                "仅显示 warning/error", value=False, key="log_only_issue",
            )
            show_err_ctx = _ctrl2.checkbox(
                "最后错误上下文", value=bool(parsed["n_error"]) and parsed["status"] == "failed",
                key="log_err_ctx", disabled=parsed["n_error"] == 0,
                help="定位到文件中最后一条错误行及其前后若干行",
            )
            _ctrl3.download_button(
                "⬇ 下载完整日志", data=full_text.encode("utf-8"),
                file_name=_chosen["name"], key="log_download", mime="text/plain",
            )
            if parsed["n_error"]:
                with st.expander(f"❗ 错误行（{parsed['n_error']} 条，最多展示 40 条）", expanded=True):
                    render_issues([
                        {"level": "error", "field": f"L{n}", "msg": m} for n, m in parsed["errors"]
                    ])
            if parsed["n_warning"]:
                with st.expander(f"⚠️ 警告行（{parsed['n_warning']} 条，最多展示 40 条）", expanded=False):
                    render_issues([
                        {"level": "warn", "field": f"L{n}", "msg": m} for n, m in parsed["warnings"]
                    ])
            if show_err_ctx and parsed["last_err_ctx"]:
                st.markdown(f"**最后错误上下文（文件第 {parsed['last_err_idx'] + 1} 行附近）**")
                log_console(parsed["last_err_ctx"], title="last error context")

            # ---------- 第三层：原始日志（默认折叠，固定高度独立滚动） ----------
            tail_txt = "".join(lines[-tail_lines:])
            if only_issue:
                kept = [
                    ln for ln in lines[-3000:]
                    if any(tok in ln.lower() for tok in ("error", "warning", "traceback"))
                ]
                tail_txt = "".join(kept[-tail_lines:]) or "（未命中 warning/error 行）\n"
            with st.expander(f"原始日志（末尾 {tail_lines} 行）", expanded=False):
                log_console(tail_txt, title=_chosen["name"])
