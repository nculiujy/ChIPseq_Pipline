"""
运行管理页面 - 监控流程状态、触发运行、查看日志
"""

import streamlit as st
import os
import subprocess
import signal
import time
import yaml
from datetime import datetime
from pathlib import Path

st.set_page_config(page_title="运行管理", page_icon="🚀", layout="wide")
st.title("🚀 运行管理")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
LOGS_DIR = os.path.join(BASE_DIR, "logs")


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


def is_snakemake_running():
    """检测系统中是否有 snakemake 进程在运行（排除 pgrep 自身和 streamlit）"""
    try:
        result = subprocess.run(
            ["bash", "-c", "pgrep -f 'snakemake' | grep -v $$ | head -5"],
            capture_output=True,
            text=True,
        )
        # 过滤掉 streamlit 和 pgrep 自身的 PID
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if not pids:
            return False
        # 验证这些 PID 是否真的是 snakemake 进程
        for pid in pids:
            try:
                cmdline_path = f"/proc/{pid}/cmdline"
                if os.path.exists(cmdline_path):
                    with open(cmdline_path, "r") as f:
                        cmdline = f.read()
                    # 排除 streamlit 和 pgrep 自身
                    if "snakemake" in cmdline and "streamlit" not in cmdline and "pgrep" not in cmdline:
                        return True
            except (PermissionError, FileNotFoundError):
                continue
        return False
    except Exception:
        return False


snakemake_active = is_snakemake_running()

if snakemake_active:
    st.info("🔄 **Snakemake 进程正在运行中...**  状态会随着任务完成自动更新（刷新页面可查看最新进度）")

# 定义各模块的 marker 文件
MARKERS = {
    "1_download": "1_download/finished.txt",
    "2_QC": "2_QC/QC_finished.txt",
    "3_ChIPseq_step1": "3_ChIPseq/step1_finished.txt",
    "3_ChIPseq_step2": "3_ChIPseq/step2_finished.txt",
    "4_peakcalling": "4_analyse/peakcalling_finished.txt",
    "4_annotation": "4_analyse/annoChIPPeaks_finished.txt",
    "4_deeptools": "4_analyse/deeptools_finished.txt",
    "5_gsea": "5_gsea/gsea_finished.txt",
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
                    if "download" in name:
                        is_enabled = modules.get("1_download", False)
                    elif "QC" in name:
                        is_enabled = modules.get("2_QC", False)
                    elif "ChIPseq" in name:
                        is_enabled = modules.get("3_ChIPseq", False)
                    elif "peakcalling" in name or "annotation" in name or "deeptools" in name:
                        is_enabled = modules.get("4_analyse", False)
                    elif "gsea" in name:
                        is_enabled = modules.get("5_gsea", False)

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
            
            if st.button(f"🗑️ 删除选中模块的标记文件", key=f"delete_markers_{species}_{experiment}"):
                if rerun_selected:
                    deleted_count = 0
                    for module_name in rerun_selected:
                        marker_path = MARKERS[module_name]
                        full_path = os.path.join(BASE_DIR, "result", species, experiment, marker_path)
                        try:
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                deleted_count += 1
                        except Exception as e:
                            st.error(f"删除 {marker_path} 失败: {e}")
                    
                    if deleted_count > 0:
                        st.success(f"✅ 已删除 {deleted_count} 个标记文件，下次运行时将重新执行这些模块")
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
            "4_annotation": "4_analyse/annoChIPPeaks",
            "4_deeptools": "4_analyse/deeptools",
            "5_gsea": "5_gsea",
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
            "4_annotation": "4_analyse/annoChIPPeaks",
            "4_deeptools": "4_analyse/deeptools",
            "5_gsea": "5_gsea",
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

st.session_state.snakemake_running = snakemake_active or (st.session_state.snakemake_pid is not None)

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
                st.session_state.snakemake_running = False
                st.session_state.snakemake_pid = None
                st.success("✅ 已暂停，下次启动时会从断点继续")
            except ProcessLookupError:
                st.session_state.snakemake_running = False
                st.session_state.snakemake_pid = None
                st.info("进程已结束")
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
