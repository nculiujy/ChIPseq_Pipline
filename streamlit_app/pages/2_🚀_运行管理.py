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

st.set_page_config(page_title="运行管理", page_icon="🚀", layout="wide")
st.title("🚀 运行管理")

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


def run_snakemake_dryrun():
    """执行 dry-run 并返回输出"""
    try:
        result = subprocess.run(
            ["snakemake", "-n", "--quiet"],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=30,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "⚠️ Dry-run 超时"
    except FileNotFoundError:
        return "⚠️ 未找到 snakemake 命令，请确认已激活 conda 环境"


# ============================================================
# 状态总览
# ============================================================
st.subheader("📊 流程状态总览")

# 自动刷新控制
col_refresh1, col_refresh2 = st.columns([1, 3])
with col_refresh1:
    if st.button("🔄 刷新状态"):
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
    f"👤 当前识别用户：`{CURRENT_USER}` (UID={CURRENT_UID}) | "
    f"📁 项目目录：`{BASE_DIR_REAL}`"
)

if snakemake_active:
    _proc_lines = "\n".join(
        f"  • PID `{p['pid']}` — cwd=`{p['cwd']}` — `{p['cmdline'][:120]}{'…' if len(p['cmdline']) > 120 else ''}`"
        for p in snakemake_procs
    )
    st.info(
        "🔄 **Snakemake 进程正在运行中...**  状态会随着任务完成自动更新（刷新页面可查看最新进度）\n\n"
        f"检测到 {len(snakemake_procs)} 个属于当前用户且在项目目录下的进程：\n{_proc_lines}"
    )
else:
    st.caption(
        "✅ 未检测到当前用户在本项目目录下的 snakemake 进程（其他用户的进程不会误报）"
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

for proj in projects:
    species = proj["species"]
    experiment = proj["experiment"]
    modules = proj.get("modules", {})

    with st.expander(f"📁 {species}/{experiment}", expanded=True):
        cols = st.columns(len(MARKERS))
        for idx, (name, marker) in enumerate(MARKERS.items()):
            with cols[idx]:
                exists = check_marker(species, experiment, marker)
                mtime = get_marker_mtime(species, experiment, marker)

                if exists:
                    st.markdown(f"✅ **{name}**")
                    if mtime:
                        st.caption(mtime)
                else:
                    # 判断是否启用了该模块
                    is_enabled = False
                    if name.startswith("4_"):
                        is_enabled = modules.get("4_analyse", False)
                    elif name.startswith("5_") or "gsea" in name:
                        is_enabled = modules.get("5_gsea", False)
                    elif name.startswith("6_") or "motif" in name:
                        is_enabled = modules.get("6_motif", False)
                    elif "download" in name:
                        is_enabled = modules.get("1_download", False)
                    elif "QC" in name:
                        is_enabled = modules.get("2_QC", False)
                    elif "ChIPseq" in name:
                        is_enabled = modules.get("3_ChIPseq", False)

                    if is_enabled and snakemake_active:
                        st.markdown(f"🔄 **{name}**")
                        st.caption("运行中...")
                    elif is_enabled:
                        st.markdown(f"⏳ **{name}**")
                        st.caption("待运行")
                    else:
                        st.markdown(f"⬜ **{name}**")
                        st.caption("未启用")
        
        # 重跑模块功能
        st.markdown("---")
        st.markdown("**🔄 重跑已完成的模块**")
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
            
            if st.button(f"🗑️ 删除选中模块（标记文件{' + 输出目录' if clean_outputs else ''}）", key=f"delete_markers_{species}_{experiment}"):
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
                        st.success(f"✅ 已{'，'.join(msg_parts)}。下次运行 Snakemake 时将完全重新执行这些模块")
                        if cleaned_dirs:
                            with st.expander("已清理的目录", expanded=False):
                                for d in cleaned_dirs:
                                    st.text(f"  🗑️ {d}")
                        st.rerun()
                else:
                    st.warning("⚠️ 请先选择要重跑的模块")
        else:
            st.info("📌 当前无已完成的模块")
        
        # 清理未完成任务功能
        st.markdown("---")
        st.markdown("**🧹 清理未完成任务（断点续行）**")
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
            st.warning(f"⚠️ 检测到 {len(incomplete_modules)} 个未完成的模块")
            
            for mod_name, mod_dir, full_path in incomplete_modules:
                with st.expander(f"🔍 {mod_name} ({mod_dir})", expanded=False):
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
                            
                            if st.button(f"🗑️ 清空 {mod_name} 目录", key=f"clean_{species}_{experiment}_{mod_name}"):
                                try:
                                    import shutil
                                    shutil.rmtree(full_path)
                                    st.success(f"✅ 已清空 {mod_dir}，下次运行时将从头开始")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"清空目录失败: {e}")
                        else:
                            st.info("目录为空")
                    except Exception as e:
                        st.error(f"读取目录失败: {e}")
        else:
            st.success("✅ 无未完成的任务，所有启用的模块都已正常完成或尚未开始")

# 自动刷新实现
if auto_refresh:
    time.sleep(15)
    st.rerun()

# ============================================================
# 运行控制
# ============================================================
st.markdown("---")
st.subheader("🎮 运行控制")

# 检测是否有未完成的任务（提示可以续行）
has_incomplete = False
for proj in projects:
    species = proj["species"]
    experiment = proj["experiment"]
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
            marker_exists = check_marker(species, experiment, marker)
            dir_path = os.path.join(BASE_DIR, "result", species, experiment, module_dir)
            dir_exists = os.path.isdir(dir_path)
            if dir_exists and not marker_exists:
                has_incomplete = True
                break
    if has_incomplete:
        break

if has_incomplete:
    st.info("💡 **检测到未完成的任务** — 可以直接启动继续运行，Snakemake 会自动从断点续行")

col1, col2, col3 = st.columns(3)

with col1:
    cores = st.number_input("CPU 核心数", min_value=1, max_value=64, value=config.get("threads", 8))

with col2:
    run_mode = st.selectbox("运行模式", ["正常运行", "Dry-run（仅预览）", "强制全部重跑"])

with col3:
    extra_args = st.text_input("额外参数", placeholder="例如: --until analyse_deeptools")

# Dry-run 预览
if st.button("🔍 预览将执行的任务 (dry-run)", key="dryrun"):
    with st.spinner("正在分析 DAG..."):
        output = run_snakemake_dryrun()
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

# 启动/恢复按钮
button_label = "▶️ 恢复运行（从断点继续）" if has_incomplete else "▶️ 启动 Snakemake"
if st.button(button_label, type="primary", disabled=st.session_state.snakemake_running):
    cmd = ["snakemake", "-c", str(int(cores))]

    if run_mode == "Dry-run（仅预览）":
        cmd.append("-n")
    elif run_mode == "强制全部重跑":
        cmd.append("--forceall")

    if extra_args.strip():
        cmd.extend(extra_args.strip().split())

    st.session_state.snakemake_running = True
    
    if has_incomplete:
        st.info(f"🔄 正在恢复运行: `{' '.join(cmd)}`")
        st.caption("Snakemake 会检查已完成的任务，跳过它们，只执行未完成的部分")
    else:
        st.info(f"� 正在运行: `{' '.join(cmd)}`")
        st.caption("流程在后台运行中。请查看日志了解进度")

    # 使用 subprocess 异步启动
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
        st.success(f"✅ 已启动 (PID: {proc.pid})，日志写入: `{log_file}`")
    except Exception as e:
        st.error(f"❌ 启动失败: {e}")
        st.session_state.snakemake_running = False

if st.session_state.snakemake_running and st.session_state.snakemake_pid:
    st.warning(f"⚠️ Snakemake 正在运行 (PID: {st.session_state.snakemake_pid})")
    col_pause1, col_pause2 = st.columns([1, 3])
    with col_pause1:
        if st.button("⏸️ 暂停"):
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
                st.success("✅ 已暂停，下次启动时会从断点继续")
                st.rerun()
            except ProcessLookupError:
                st.session_state.snakemake_running = False
                st.session_state.snakemake_pid = None
                st.info("进程已结束")
                st.rerun()
            except PermissionError:
                st.error("❌ 无权限终止该进程。请在终端手动执行: `kill " + str(st.session_state.snakemake_pid) + "`")
    with col_pause2:
        st.caption("💡 暂停后可以点击上面的「恢复运行」按钮继续执行")

# ============================================================
# 常用命令速查
# ============================================================
st.markdown("---")
st.subheader("📝 常用命令")

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
# 日志查看
# ============================================================
st.markdown("---")
st.subheader("📄 日志查看")

# 列出所有可用的日志文件
log_files = []
for root, dirs, files in os.walk(LOGS_DIR):
    for f in files:
        if f.endswith(".log"):
            log_files.append(os.path.relpath(os.path.join(root, f), BASE_DIR))

if log_files:
    log_files.sort(key=lambda x: os.path.getmtime(os.path.join(BASE_DIR, x)), reverse=True)
    selected_log = st.selectbox("选择日志文件", log_files[:20])

    if selected_log:
        log_path = os.path.join(BASE_DIR, selected_log)
        tail_lines = st.slider("显示最后 N 行", min_value=20, max_value=500, value=100)

        if st.button("🔄 刷新日志"):
            st.rerun()

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                content = "".join(lines[-tail_lines:])
            st.code(content, language="text")
        except Exception as e:
            st.error(f"读取日志失败: {e}")
else:
    st.info("暂无日志文件")
