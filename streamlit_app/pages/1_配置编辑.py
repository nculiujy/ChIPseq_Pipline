"""
配置编辑页面 - 可视化编辑 config.yaml 和 metadata.csv
"""

import streamlit as st
import os
import re
import html as _html
import glob
import yaml
import pandas as pd
import copy
from datetime import datetime

import sys

# 让 streamlit_app/utils 可导入（控制台主题）
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from utils.ui_theme import (  # noqa: E402
    inject_theme,
    page_header,
    render_sidebar,
    section_title,
    steps_bar,
    render_issues,
    code_html,
    list_row,
    render_html_list,
)
from utils import runtime as rt  # noqa: E402

from utils.version import APP_FULL_NAME  # noqa: E402

st.set_page_config(page_title=f"配置编辑 · {APP_FULL_NAME}", layout="wide")
inject_theme()
render_sidebar("config")
page_header("配置编辑", "可视化编辑 config.yaml 与 metadata.csv")

# 路径定义
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
METADATA_PATH = os.path.join(BASE_DIR, "config", "metadata.csv")
ANNO_DIR = os.path.join(BASE_DIR, "workflow", "anno")
RESOURCES_DIR = os.path.join(BASE_DIR, "workflow", "resources")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config_data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # 记录“刚刚保存”时刻：校验面板据此显示“保存后需重新校验”
    st.session_state["cfg_last_saved_ts"] = datetime.now().timestamp()


def load_metadata():
    return pd.read_csv(METADATA_PATH)


def save_metadata(df):
    df.to_csv(METADATA_PATH, index=False)


# ------------------------------------------------------------
# 项目目录预建
# ------------------------------------------------------------
def _ensure_project_dirs_or_stop(species, experiment, rawdata_dir=""):
    """建好原始数据 / 结果 / 日志目录，返回要写进 config 的 rawdata 路径。

    rawdata_dir 留空则回落到 workflow/resources/<物种>/<实验>/，让用户直接把 fastq
    拷进去；建目录失败就当场拦住——路径建不出来时写进 config 也只是个死链。
    """
    try:
        info = rt.ensure_project_dirs(species, experiment, (rawdata_dir or "").strip())
    except Exception as e:  # noqa: BLE001
        st.error(f"目录创建失败：{e}")
        st.stop()
    return str(info["rawdata_dir"])


# ------------------------------------------------------------
# 样本名识别（供「样本配对表」按项目自动填表）
# ------------------------------------------------------------
# 规则与 workflow/scripts/3_1_ChIPseq.pl 保持一致，顺序不能乱：
# _1.clean.fq 必须排在 _1.fq 前面，否则 ".clean" 会被当成样本名的一部分。
_SAMPLE_SUFFIX_RULES = (
    (re.compile(r"^(.+)_1_val_1\.fq(?:\.gz)?$"), "_1_val_1.fq", "_2_val_2.fq"),
    (re.compile(r"^(.+)_1_trimmed\.fq(?:\.gz)?$"), "_1_trimmed.fq", "_2_trimmed.fq"),
    (re.compile(r"^(.+)_1\.clean\.fq(?:\.gz)?$"), "_1.clean.fq", "_2.clean.fq"),
    (re.compile(r"^(.+)_1\.fastq(?:\.gz)?$"), "_1.fastq", "_2.fastq"),
    (re.compile(r"^(.+)_1\.fq(?:\.gz)?$"), "_1.fq", "_2.fq"),
)

# 名字里带这些词的样本视为对照（Input / IgG / CK 等）
_INPUT_HINTS = ("input", "inp", "ctrl", "control", "mock", "igg", "ck")


def _is_input_sample(name: str) -> bool:
    low = str(name).lower()
    return any(h in low for h in _INPUT_HINTS)


def _project_fastq_dirs(project: dict) -> list:
    """项目可能存放 fastq 的目录，QC 产物优先于原始数据。"""
    dirs = []
    species = project.get("species", "")
    experiment = project.get("experiment", "")
    qc_dir = os.path.join(BASE_DIR, "result", species, experiment, "2_QC")
    if os.path.isdir(qc_dir):
        dirs.append(qc_dir)
    raw = (project.get("rawdata_dir") or "").strip()
    if raw:
        p = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def detect_fastq_samples(project: dict) -> dict:
    """扫描项目的 fastq，返回 {样本名: "PE"/"SE"}。

    只认 _1 结尾的 R1，剥掉配对后缀得到样本名（如 IP2_1.clean.fq.gz → IP2），
    再看同目录下有没有对应的 _2 判断是否双端。
    """
    found = {}
    patterns = ("*/*_1*.fq.gz", "*_1*.fq.gz", "*/*_1*.fq", "*_1*.fq",
                "*/*_1*.fastq", "*_1*.fastq")
    for d in _project_fastq_dirs(project):
        for pat in patterns:
            for path in sorted(glob.glob(os.path.join(d, pat))):
                filename = os.path.basename(path)
                for rx, r1_tail, r2_tail in _SAMPLE_SUFFIX_RULES:
                    m = rx.match(filename)
                    if not m:
                        continue
                    sample = m.group(1)
                    mate = os.path.join(os.path.dirname(path), filename.replace(r1_tail, r2_tail))
                    paired = os.path.exists(mate)
                    # 同一批次里双端优先：先按 SE 记下，后面见到 PE 再覆盖
                    if found.get(sample) != "PE":
                        found[sample] = "PE" if paired else "SE"
                    break
        if found:
            break  # 已找到就不再往下探第二个目录
    return found


def build_pairs_from_samples(samples, existing=None) -> list:
    """把识别到的样本名转成 IP/Input 配对建议。

    只在拿得准的时候自动填 Input：
      1. 恰好一个对照样本 → 所有非对照样本都配它（最常见的多 IP 共用一 Input）；
      2. 多个对照 → 按数字后缀对齐（IP2 ↔ INPUT2），对不上就留空。
    Input 拿不准一律留空，避免张冠李戴。

    existing 传入当前已配置的行时，同名 IP 的人工分组（group）原样保留：
    重复分组只能靠人判断，重新识别时不应把它冲掉。
    """
    names = list(samples)
    controls = [s for s in names if _is_input_sample(s)]
    ips = [s for s in names if not _is_input_sample(s)]

    old_group = {}
    if existing is not None and len(existing):
        for _, _r in existing.iterrows():
            _ip = str(_r.get("IP sample", "") or "").strip()
            _g = str(_r.get("group", "") or "").strip()
            if _ip and _g:
                old_group[_ip] = _g

    rows = []
    for ip in ips:
        ctrl = ""
        if len(controls) == 1:
            ctrl = controls[0]
        elif len(controls) > 1:
            digits = re.sub(r"\D", "", ip)
            if digits:
                for cand in controls:
                    if re.sub(r"\D", "", cand) == digits:
                        ctrl = cand
                        break
        rows.append({"IP sample": ip, "Input": ctrl, "group": old_group.get(ip, "")})
    return rows


def infer_row_projects(df_all, detected_by_project) -> list:
    """老 metadata 没有 project 列时，按识别到的样本名反推每行属于哪个项目。

    detected_by_project 是 {项目 ID: {样本名: "PE"/"SE"}}。只在一个项目的数据里
    出现过的样本名才算数；同时命中多个项目说明名字撞了（各项目都用 IP2 很常见），
    宁可留在「未分配」让人工确认，也不能张冠李戴。
    """
    names_by_pid = {pid: set(names) for pid, names in detected_by_project.items() if names}
    owners = []
    for _, row in df_all.iterrows():
        ip = str(row.get("IP sample", "") or "").strip()
        ctrl = str(row.get("Input", "") or "").strip()
        # IP 名优先；对不上再用 Input 兜一次。多个项目共用同一个 Input 时会同时
        # 命中，正好落到下面的多命中分支，不归任何一个。
        hits = [pid for pid, names in names_by_pid.items() if ip and ip in names]
        if not hits:
            hits = [pid for pid, names in names_by_pid.items() if ctrl and ctrl in names]
        owners.append(hits[0] if len(hits) == 1 else "")
    return owners


def get_species_dirs():
    """获取 workflow/anno/ 下的物种目录

    跳过隐藏目录：workflow/anno/.download_tasks 是下载任务的暂存区，
    不跳会把一个点开头的目录当成可选物种，新建项目直接落到那里去。
    """
    if not os.path.isdir(ANNO_DIR):
        return []
    return sorted([
        d for d in os.listdir(ANNO_DIR)
        if os.path.isdir(os.path.join(ANNO_DIR, d)) and not d.startswith(".")
    ])


def species_label(name):
    """下拉框展示名：变体目录显示成「物种 · 来源」，让人一眼看出用的是哪套参考。

    值仍然是目录名（TAIR10__Ensembl_Plants_63），只改显示。
    """
    sp, src = rt.split_variant(name)
    return f"{sp} · {src}" if src else name


def get_bowtie2_dirs(species):
    """获取指定物种目录下 bowtie2 开头的文件夹"""
    species_dir = os.path.join(ANNO_DIR, species)
    if not os.path.isdir(species_dir):
        return []
    return sorted([
        d for d in os.listdir(species_dir)
        if os.path.isdir(os.path.join(species_dir, d)) and d.lower().startswith("bowtie2")
    ])


FASTQ_EXTS = (".fastq", ".fq", ".fastq.gz", ".fq.gz")


def get_rawdata_candidates(species):
    """列出 workflow/resources/<物种>/ 下可作为 rawdata_dir 的实验目录。

    约定：原始 fastq 直接放在 workflow/resources/<物种>/<实验目录>/ 这一层，
    由用户自行放入（参考 .../TAIR10/需要合并分析的314Com5 ChIP-seq数据_20260427/）。
    因此只列一级子目录，并统计本层 fastq 数，用于提示数据是否已就位。

    传进来的是参考变体（TAIR10__Ensembl_Plants_63）时按物种本名找：fastq 是
    跟物种走的，同一批数据换套注释重跑不该再复制一份。

    返回 [(相对仓库根的路径, fastq 数), ...]，按路径排序。
    """
    species = rt.variant_species(species) or species
    base = os.path.join(RESOURCES_DIR, species)
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        folder = os.path.join(base, name)
        if name.startswith(".") or not os.path.isdir(folder):
            continue
        try:
            n = sum(1 for f in os.listdir(folder) if f.endswith(FASTQ_EXTS))
        except OSError:
            n = 0
        out.append((f"workflow/resources/{species}/{name}", n))
    return out


def get_bowtie2_index_prefix(species, bowtie2_folder):
    """
    从 bowtie2 文件夹中提取索引前缀。
    Bowtie2 索引文件格式: prefix.1.bt2, prefix.2.bt2, ... prefix.rev.1.bt2, prefix.rev.2.bt2
    需要截取 .1.bt2 / .rev.1.bt2 之前的部分作为前缀路径。
    返回相对于项目根目录的完整路径前缀。
    """
    folder_path = os.path.join(ANNO_DIR, species, bowtie2_folder)
    bt2_files = glob.glob(os.path.join(folder_path, "*.bt2"))
    if not bt2_files:
        return None

    # 取第一个 .bt2 文件，截取前缀
    # 匹配模式: prefix.1.bt2 或 prefix.rev.1.bt2
    prefixes = set()
    for f in bt2_files:
        basename = os.path.basename(f)
        # 去掉 .rev.1.bt2 / .rev.2.bt2 / .1.bt2 / .2.bt2 / .3.bt2 / .4.bt2
        prefix = re.sub(r'\.(rev\.)?\d+\.bt2$', '', basename)
        prefixes.add(prefix)

    if len(prefixes) == 1:
        prefix = prefixes.pop()
        # 返回相对于 BASE_DIR 的路径
        rel_folder = os.path.relpath(folder_path, BASE_DIR)
        return os.path.join(rel_folder, prefix)
    elif len(prefixes) > 1:
        # 多个索引前缀，返回列表让用户选择
        return sorted(prefixes)
    return None


def get_bed_files(species, bowtie2_folder):
    """从 bowtie2 目录中检索 .bed 文件"""
    folder_path = os.path.join(ANNO_DIR, species, bowtie2_folder)
    bed_files = glob.glob(os.path.join(folder_path, "*.bed"))
    return sorted([os.path.basename(f) for f in bed_files])


def get_gff_files(species, bowtie2_folder):
    """从 bowtie2 目录中检索 .gff3/.gff/.gtf 文件"""
    folder_path = os.path.join(ANNO_DIR, species, bowtie2_folder)
    gff_files = []
    for ext in ["*.gff3", "*.gff", "*.gtf"]:
        gff_files.extend(glob.glob(os.path.join(folder_path, ext)))
    return sorted([os.path.basename(f) for f in gff_files])


# ============================================================
# 工作流配置进度 + 全局校验入口
# ============================================================
_cfg = load_config()
_issues = rt.config_issues(_cfg)
_nerr = sum(1 for i in _issues if i["level"] == "error")
_nwarn = sum(1 for i in _issues if i["level"] == "warn")
_projs = _cfg.get("projects", []) or []


def _match(iss, needles):
    f = ((iss.get("field") or "") + " " + (iss.get("msg") or "")).lower()
    return any(n in f for n in needles)


def _step_status(needles, base="done"):
    if any(i["level"] == "error" and _match(i, needles) for i in _issues):
        return "err"
    if any(i["level"] == "warn" and _match(i, needles) for i in _issues):
        return "todo"
    return base


# ——校验结果关联配置版本与保存态（前移计算，供步骤条复用）——
try:
    _cfg_mtime_ts = os.path.getmtime(CONFIG_PATH)
    _cfg_mtime_txt = datetime.fromtimestamp(_cfg_mtime_ts).strftime("%Y-%m-%d %H:%M:%S")
except Exception:  # noqa: BLE001
    _cfg_mtime_ts, _cfg_mtime_txt = 0.0, "未知"
_validate_key = "cfg_validate_" + str(round(_cfg_mtime_ts, 2))
_validated = bool(st.session_state.get("cfg_validate_ts")) and \
    st.session_state.get("cfg_validate_for") == _validate_key
if _validated:
    _validated_at = st.session_state["cfg_validate_ts"]
else:
    _validated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

_saved_ts = st.session_state.get("cfg_last_saved_ts", 0)
_just_saved_pending = bool(_saved_ts) and _saved_ts >= _cfg_mtime_ts - 1 and not _validated

# 步骤状态：done/err/todo/dirty（dirty=刚保存还没重新校验）
_step_last = "err" if _nerr else ("dirty" if _just_saved_pending else "done")
_steps = [
    {"label": "项目与样本", "status": ("err" if not _projs else _step_status(["projects", "metadata"]))},
    {"label": "ChIP 注释", "status": _step_status(["index_dir", "anno", "物种"])},
    {"label": "基础分析参数", "status": "done" if _cfg.get("deeptools") else "todo"},
    {"label": "GSEA 与 Motif", "status": _step_status(["gsea", "motif", "txdb", "orgdb"])},
    {"label": "校验并保存", "status": _step_last},
]
# 当前步骤唯一强调态：第一个有问题的环节
_current_step = next((i for i, s in enumerate(_steps) if s["status"] != "done"), len(_steps) - 1)
for _i, _s in enumerate(_steps):
    _s["current"] = _i == _current_step

section_title("工作流配置进度")
# 保存时若有字段被自动规范化，先把提示暂存到 session_state，rerun 后在这里补发一次。
# 元素可以是纯文本（默认 warning），也可以是 (level, text) 指定级别。
_flash = st.session_state.pop("_cfg_flash", None)
if _flash:
    _lvl, _txt = _flash if isinstance(_flash, tuple) else ("warning", _flash)
    {"success": st.success, "info": st.info}.get(_lvl, st.warning)(_txt)
steps_bar(_steps)
st.caption("分工说明：步骤条只反映各工作流环节的完成度/问题状态（已完成 · 有问题 · 需重校验）；具体修改仍请在下方对应 Tab 内完成并保存。")

with st.container(border=True):
    _sumc, _btnc = st.columns([3, 1])
    with _sumc:
        if _nerr:
            st.markdown(f"**全局校验：** 🔴 {_nerr} 个错误 · 🟡 {_nwarn} 个警告（建议修正后再运行）")
        elif _nwarn:
            st.markdown(f"**全局校验：** 🟡 {_nwarn} 个警告（无阻断性错误）")
        else:
            st.markdown("**全局校验：** ✅ 通过，未发现配置问题")
        _proj_names = "、".join(f"{p.get('species')}/{p.get('experiment')}" for p in _projs) or "无项目"
        st.caption(f"校验时间：{_validated_at} · 配置版本：修改于 {_cfg_mtime_txt}")
        st.markdown(
            '<div class="ux-metric-label">覆盖项目：' + _html.escape(_proj_names)
            + " · 文件：" + code_html(os.path.relpath(CONFIG_PATH, BASE_DIR)) + "</div>",
            unsafe_allow_html=True,
        )
    with _btnc:
        st.write("")
        if st.button("🔍 重新校验", key="validate_cfg", type="primary"):
            st.session_state["cfg_validate_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state["cfg_validate_for"] = _validate_key
            st.rerun()
    # 保存/校验状态结论（单一状态模型：已保存并校验 / 刚保存待校验 / 自动校验中）
    if _validated and _nerr == 0:
        st.success("✅ 已保存并通过校验：当前配置版本于 " + st.session_state["cfg_validate_ts"] + " 手动校验通过")
    elif _validated and _nerr:
        st.error(f"❌ 手动校验未通过（{st.session_state['cfg_validate_ts']}）：存在 {_nerr} 个错误")
    elif _just_saved_pending:
        st.warning("⚠️ 配置已修改并保存，需重新校验：上方结果为页面加载时的自动校验，请点击右侧「重新校验」确认保存后的版本")
    if _issues:
        render_issues(_issues)
    else:
        st.success("✅ 未发现：样本名不匹配、物种目录缺失、路径无效、参数组合冲突等常见问题")

st.markdown("---")

# ============================================================
# Tab 布局
# ============================================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "全局参数",
    "项目管理",
    "样本配对表",
    "ChIP 注释",
    "GSEA 参数",
    "Motif 参数",
])

# ============================================================
# Tab 1: 全局参数
# ============================================================
with tab1:
    config = load_config()

    st.subheader("基本参数")
    col1, col2 = st.columns(2)
    with col1:
        threads = st.number_input("并行线程数", min_value=1, max_value=64, value=config.get("threads", 8))
    with col2:
        norm_method = st.selectbox(
            "标准化方法",
            ["BPM", "RPKM", "CPM", "RPGC", "None"],
            index=["BPM", "RPKM", "CPM", "RPGC", "None"].index(config.get("normalization_method", "BPM")),
        )

    st.subheader("MACS2 Peak Calling 参数")
    col1, col2, col3 = st.columns(3)
    with col1:
        peak_type = st.selectbox("Peak 类型", ["broad", "narrow"], index=0 if config.get("macs2_peak_type") == "broad" else 1)
    with col2:
        cutoff_type = st.selectbox("阈值类型", ["pvalue", "qvalue"], index=0 if config.get("macs2_cutoff_type") == "pvalue" else 1)
    with col3:
        cutoff_value = st.number_input("阈值", min_value=0.0001, max_value=1.0, value=float(config.get("macs2_cutoff_value", 0.05)), format="%.4f")

    col1, col2 = st.columns(2)
    with col1:
        # Broad cutoff 仅在 broad 模式下有意义
        if peak_type == "broad":
            broad_cutoff = st.number_input("Broad cutoff", min_value=0.0001, max_value=1.0, value=float(config.get("macs2_broad_cutoff", 0.1)), format="%.4f")
        else:
            broad_cutoff = None
            st.caption("Narrow 模式下无需 Broad cutoff 参数")
    
    with col2:
        # MACS2 基因组大小参数
        genome_options = {
            "自动（根据项目物种）": "auto",
            "拟南芥 (1.2e8)": "1.2e8",
            "大豆 (1.0e9)": "1.0e9",
            "人类 (hs)": "hs",
            "小鼠 (mm)": "mm",
            "自定义": "custom"
        }
        current_genome = config.get("macs2_genome_size", "auto")
        if current_genome in genome_options.values():
            genome_idx = list(genome_options.values()).index(current_genome)
        else:
            genome_idx = list(genome_options.keys()).index("自定义")
        
        genome_select = st.selectbox(
            "MACS2 基因组大小",
            list(genome_options.keys()),
            index=genome_idx,
            help="设置为'自动'时从项目配置的 species 推断；否则使用固定值"
        )
        genome_size = genome_options[genome_select]
        
        if genome_size == "custom":
            genome_size = st.text_input(
                "自定义基因组大小",
                value=current_genome if current_genome not in genome_options.values() else "2.7e9",
                placeholder="如: 2.7e9 或 dm"
            )

    st.subheader("DeepTools 参数")
    dt = config.get("deeptools", {})

    col1, col2 = st.columns(2)
    with col1:
        matrix_mode = st.selectbox(
            "computeMatrix 模式",
            ["reference-point", "scale-regions"],
            index=0 if dt.get("matrix_mode") == "reference-point" else 1,
        )
        bin_size = st.number_input("Bin 大小 (bp)", min_value=1, max_value=500, value=dt.get("bin_size", 10))
        missing_zero = st.checkbox("缺失数据当作0", value=dt.get("missing_data_as_zero", True))
        per_group = st.checkbox(
            "按组拆分绘图 (--perGroup)",
            value=dt.get("plot", {}).get("per_group", True),
            help="勾选时热图/profile 按 group 分面展示（推荐多组比较时使用）；取消则所有样本合并在一张图中",
        )
        input_mode_options = ["none", "include_input", "log2_ratio"]
        input_mode_labels = {
            "none": "仅 IP 信号（默认）",
            "include_input": "IP + Input 并排展示",
            "log2_ratio": "IP/Input 比值 (bamCompare)",
        }
        current_input_mode = dt.get("input_mode", "none")
        # 向后兼容旧配置
        if current_input_mode == "none" and dt.get("subtract_input", False):
            current_input_mode = "log2_ratio"
        input_mode = st.selectbox(
            "Input 对照模式",
            input_mode_options,
            index=input_mode_options.index(current_input_mode) if current_input_mode in input_mode_options else 0,
            format_func=lambda x: input_mode_labels.get(x, x),
            help=(
                "• **仅 IP 信号**: 只展示 IP 样本的绝对信号热图\n"
                "• **IP + Input 并排**: IP 和 Input 的 bigwig 同时展示在热图中，直接视觉对比\n"
                "• **IP/Input 比值**: 用 bamCompare 计算 IP 与 Input 的比值，展示扣除背景后的富集信号"
            ),
        )
        if input_mode == "log2_ratio":
            subtract_op_options = ["log2", "ratio", "subtract", "mean", "reciprocal_ratio"]
            subtract_operation = st.selectbox(
                "比值运算方式",
                subtract_op_options,
                index=subtract_op_options.index(dt.get("subtract_input_operation", "log2")),
                help="log2: log2(IP/Input)（推荐）；ratio: IP/Input；subtract: IP - Input",
            )
        else:
            subtract_operation = dt.get("subtract_input_operation", "log2")

    with col2:
        bed_source = st.selectbox(
            "BED 文件来源",
            ["macs2_peaks", "union_peaks", "consensus_peaks", "custom", "genes"],
            index=["macs2_peaks", "union_peaks", "consensus_peaks", "custom", "genes"].index(dt.get("bed_source", "union_peaks"))
                if dt.get("bed_source", "union_peaks") in ["macs2_peaks", "union_peaks", "consensus_peaks", "custom", "genes"] else 1,
            help=(
                "决定 computeMatrix 使用哪些基因组区域：\n"
                "- **macs2_peaks**: 单组用 consensus peaks，多组取涉及组 peaks 的并集\n"
                "- **union_peaks**: 合并涉及组所有样本的 macs2 peaks 为并集区域\n"
                "- **consensus_peaks**: 直接使用各组的 consensus peaks（单组直接用，多组合并）\n"
                "- **custom / genes**: 使用自定义 BED 或 GTF 注释文件"
            ),
        )

        # ---- 热图配色选择（含软色调分组 + 色带预览）----
        COLOR_MAP_GROUPS = {
            "经典对比": {
                "RdYlBu_r":   ["#313695","#4575b4","#74add1","#abd9e9","#ffffbf","#fdae61","#f46d43","#d73027"],
                "coolwarm":   ["#3b4cc0","#6f92f3","#b8d0f9","#f7f7f7","#f5c4ac","#e0735a","#b40426"],
                "RdBu_r":     ["#053061","#2166ac","#92c5de","#f7f7f7","#f4a582","#d6604d","#67001f"],
            },
            "渐变单色": {
                "viridis":    ["#440154","#414487","#2a788e","#22a884","#7ad151","#fde725"],
                "plasma":     ["#0d0887","#7e03a8","#cc4778","#f89441","#f0f921"],
                "magma":      ["#000004","#3b0f70","#8c2981","#de4968","#fea16e","#fcfdbf"],
                "inferno":    ["#000004","#420a68","#932667","#dd513a","#fca50a","#fcffa4"],
                "Blues":      ["#f7fbff","#c6dbef","#6baed6","#2171b5","#08306b"],
                "Greens":     ["#f7fcf5","#c7e9c0","#74c476","#238b45","#00441b"],
                "Reds":       ["#fff5f0","#fcbba1","#fb6a4a","#cb181d","#67000d"],
            },
            "软色调": {
                "PuBuGn":     ["#fff7fb","#ece2f0","#a6bddb","#67a9cf","#1c9099","#016c59"],
                "YlOrBr":     ["#ffffe5","#fff7bc","#fec44f","#d95f0e","#662506"],
                "PiYG":       ["#8e0152","#de77ae","#f7f7f7","#b8e186","#4d9221"],
                "BrBG":       ["#543005","#bf812d","#f5f5f5","#80cdc1","#003c30"],
                "PRGn":       ["#40004b","#9970ab","#f7f7f7","#5aae61","#00441b"],
                "RdYlGn":     ["#a50026","#f46d43","#fee08b","#d9ef8b","#1a9850","#006837"],
                "Pastel1":    ["#fbb4ae","#b3cde3","#ccebc5","#decbe4","#fed9a6","#ffffcc"],
            },
        }
        ALL_COLOR_MAPS = {k: v for grp in COLOR_MAP_GROUPS.values() for k, v in grp.items()}

        current_cmap = dt.get("plot", {}).get("color_map", "RdYlBu_r")
        cmap_names = list(ALL_COLOR_MAPS.keys())
        cmap_idx = cmap_names.index(current_cmap) if current_cmap in cmap_names else 0

        color_map = st.selectbox(
            "热图配色",
            cmap_names,
            index=cmap_idx,
            format_func=lambda x: (
                "" + x if x in COLOR_MAP_GROUPS.get("软色调", {}) else
                "" + x if x in COLOR_MAP_GROUPS.get("渐变单色", {}) else
                "" + x
            ),
            help="软色调配色适合发表级图片，对比度柔和；经典对比色适合强调信号差异",
        )
        # 色带预览
        if color_map in ALL_COLOR_MAPS:
            stops = ALL_COLOR_MAPS[color_map]
            gradient = ", ".join(stops)
            st.markdown(
                f'<div style="height:18px;border-radius:4px;margin-top:4px;'
                f'background:linear-gradient(to right,{gradient});'
                f'border:1px solid #ddd"></div>'
                f'<div style="font-size:11px;color:#888;margin-top:2px">'
                f'{color_map} — {len(stops)} 色节点</div>',
                unsafe_allow_html=True,
            )

    # BED/GFF 文件选择（当 bed_source 为 custom 或 genes 时）
    if bed_source in ("custom", "genes"):
        st.markdown("---")
        st.markdown(f"**{'自定义 BED 文件' if bed_source == 'custom' else 'GFF/GTF 基因注释文件'} 选择**")

        # 选择物种目录
        species_list = get_species_dirs()
        if species_list:
            dt_species = st.selectbox(
                "选择物种目录（用于检索文件）",
                species_list,
                key="dt_species_select",
            )
            # 获取该物种下的 bowtie2 目录
            bowtie2_dirs = get_bowtie2_dirs(dt_species)
            if bowtie2_dirs:
                dt_bowtie2_dir = st.selectbox(
                    "选择 Bowtie2 目录",
                    bowtie2_dirs,
                    key="dt_bowtie2_select",
                )
                # 根据模式检索文件
                if bed_source == "custom":
                    available_files = get_bed_files(dt_species, dt_bowtie2_dir)
                    file_label = "BED 文件"
                else:
                    available_files = get_gff_files(dt_species, dt_bowtie2_dir)
                    file_label = "GFF3/GFF/GTF 文件"

                if available_files:
                    selected_file = st.selectbox(f"选择 {file_label}", available_files, key="dt_file_select")
                    # 构造完整相对路径
                    dt_file_path = os.path.join("workflow", "anno", dt_species, dt_bowtie2_dir, selected_file)
                    st.caption(f"路径: `{dt_file_path}`")
                else:
                    st.warning(f"在 `workflow/anno/{dt_species}/{dt_bowtie2_dir}/` 下未找到 {file_label}")
                    dt_file_path = st.text_input(f"手动输入 {file_label} 路径", value=dt.get("custom_bed", "") if bed_source == "custom" else dt.get("gtf_file", ""))
            else:
                st.warning(f"在 `workflow/anno/{dt_species}/` 下未找到 bowtie2 开头的目录")
                dt_file_path = ""
        else:
            st.warning("未找到 `workflow/anno/` 下的物种目录")
            dt_file_path = ""
    else:
        dt_file_path = ""

    if matrix_mode == "reference-point":
        st.markdown("**Reference-point 模式参数**")
        rp = dt.get("reference_point", {})
        col1, col2, col3 = st.columns(3)
        with col1:
            ref_point = st.selectbox("参考点", ["center", "TSS", "TES"], index=["center", "TSS", "TES"].index(rp.get("point", "center")))
        with col2:
            rp_upstream = st.number_input("上游距离 (bp)", min_value=100, max_value=50000, value=rp.get("upstream", 2000), step=500)
        with col3:
            rp_downstream = st.number_input("下游距离 (bp)", min_value=100, max_value=50000, value=rp.get("downstream", 2000), step=500)
    else:
        st.markdown("**Scale-regions 模式参数**")
        sr = dt.get("scale_regions", {})
        col1, col2, col3 = st.columns(3)
        with col1:
            sr_length = st.number_input("区域长度 (bp)", min_value=500, max_value=50000, value=sr.get("region_length", 5000), step=500)
        with col2:
            sr_upstream = st.number_input("上游延伸 (bp)", min_value=0, max_value=50000, value=sr.get("upstream", 2000), step=500)
        with col3:
            sr_downstream = st.number_input("下游延伸 (bp)", min_value=0, max_value=50000, value=sr.get("downstream", 2000), step=500)

    # 保存按钮
    if st.button("保存全局配置", type="primary", key="save_global"):
        # 重新读取最新 config，避免覆盖其他 Tab 的修改
        new_config = load_config()
        new_config["threads"] = int(threads)
        new_config["normalization_method"] = norm_method
        new_config["macs2_peak_type"] = peak_type
        new_config["macs2_cutoff_type"] = cutoff_type
        new_config["macs2_cutoff_value"] = cutoff_value
        new_config["macs2_genome_size"] = genome_size
        # broad_cutoff 仅 broad 模式有意义，但保留 key 以兼容 snakemake 读取
        if peak_type == "broad" and broad_cutoff is not None:
            new_config["macs2_broad_cutoff"] = broad_cutoff
        else:
            new_config["macs2_broad_cutoff"] = new_config.get("macs2_broad_cutoff", 0.1)

        # DeepTools — 保留已有的 comparisons 等子字段
        existing_dt = new_config.get("deeptools", {})
        existing_dt["matrix_mode"] = matrix_mode
        existing_dt["bin_size"] = int(bin_size)
        existing_dt["missing_data_as_zero"] = missing_zero
        existing_dt["bed_source"] = bed_source
        existing_dt["subtract_input"] = (input_mode == "log2_ratio")
        existing_dt["input_mode"] = input_mode
        existing_dt["subtract_input_operation"] = subtract_operation
        if "plot" not in existing_dt:
            existing_dt["plot"] = {}
        existing_dt["plot"]["color_map"] = color_map
        existing_dt["plot"]["per_group"] = per_group

        # 保存 BED/GFF 路径
        if bed_source == "custom" and dt_file_path:
            existing_dt["custom_bed"] = dt_file_path
        elif bed_source == "genes" and dt_file_path:
            existing_dt["gtf_file"] = dt_file_path

        if matrix_mode == "reference-point":
            existing_dt["reference_point"] = {
                "point": ref_point,
                "upstream": int(rp_upstream),
                "downstream": int(rp_downstream),
            }
        else:
            existing_dt["scale_regions"] = {
                "region_length": int(sr_length),
                "upstream": int(sr_upstream),
                "downstream": int(sr_downstream),
                "unscaled_5prime": 0,
                "unscaled_3prime": 0,
            }
        new_config["deeptools"] = existing_dt

        save_config(new_config)
        st.success("全局配置已保存！")
        st.info("提示：由于使用了 `ancient()` 包装，修改 config.yaml 不会触发已完成模块的重跑。")

# ============================================================
# Tab 2: 项目管理
# ============================================================
with tab2:
    config = load_config()
    projects = config.get("projects", [])

    st.subheader("现有项目")

    # —— 项目顺序：排在第一位的项目即总览/运行页默认的「当前项目」 ——
    if projects:
        st.caption("调整顺序：排在**第一位**的项目作为总览页的「当前项目」（运行管理、结果预览也默认定位到它）。用右侧 ↑ / ↓ 移动，顺序会写回 config.yaml。")
        for _i, _p in enumerate(projects):
            _mods = _p.get("modules", {}) or {}
            _n_en = sum(1 for m in rt.MODULE_ORDER if _mods.get(m))
            _c_tag, _c_name, _c_up, _c_dn = st.columns([1.3, 4.7, 0.5, 0.5])
            with _c_tag:
                st.markdown("**★ 当前项目**" if _i == 0 else "&nbsp;", unsafe_allow_html=True)
            with _c_name:
                render_html_list([list_row(
                    main=f"{_p['species']} / {_p['experiment']}",
                    sub_html=(code_html(f"result/{_p['species']}/{_p['experiment']}")
                              + f" · {_n_en} 个启用模块"),
                )])
            with _c_up:
                if st.button("↑", key=f"ord_up_{_i}", disabled=(_i == 0),
                             use_container_width=True, help="上移（移到第一位即成为当前项目）"):
                    _fresh = load_config()
                    _fp = _fresh.get("projects", [])
                    _fp[_i - 1], _fp[_i] = _fp[_i], _fp[_i - 1]
                    _fresh["projects"] = _fp
                    _ok, _msg = rt.save_project_order(_fresh)
                    if not _ok:
                        st.error(_msg)
                    st.rerun()
            with _c_dn:
                if st.button("↓", key=f"ord_dn_{_i}", disabled=(_i == len(projects) - 1),
                             use_container_width=True, help="下移"):
                    _fresh = load_config()
                    _fp = _fresh.get("projects", [])
                    _fp[_i + 1], _fp[_i] = _fp[_i], _fp[_i + 1]
                    _fresh["projects"] = _fp
                    _ok, _msg = rt.save_project_order(_fresh)
                    if not _ok:
                        st.error(_msg)
                    st.rerun()
        st.markdown("---")

    for i, proj in enumerate(projects):
        with st.expander(f"{proj['species']}/{proj['experiment']}", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                # 物种选择（基于 workflow/anno 下的实际目录）
                available_species = get_species_dirs()
                if available_species and proj["species"] in available_species:
                    sp_idx = available_species.index(proj["species"])
                else:
                    available_species = [proj["species"]] + get_species_dirs()
                    sp_idx = 0
                selected_species = st.selectbox(
                    "物种",
                    available_species,
                    index=sp_idx,
                    key=f"sp_{i}",
                    format_func=species_label,
                )

                st.text_input(
                    "实验名称",
                    value=proj["experiment"],
                    key=f"exp_{i}",
                    help="会拼进 result/<物种>/<实验名> 与 logs/<物种>/<实验名>，"
                         "因此保存时自动把空格转成下划线（含空格会让 shell 拆词，规则跑到 touch 就挂）",
                )
                st.text_input("原始数据目录", value=proj.get("rawdata_dir", ""), key=f"raw_{i}")

                # Bowtie2 索引选择
                st.markdown("**Bowtie2 索引**")
                bowtie2_dirs_i = get_bowtie2_dirs(selected_species)
                if bowtie2_dirs_i:
                    # 尝试从现有 index_dir 推断当前选中的 bowtie2 目录
                    current_index = proj.get("index_dir", "")
                    current_bt2_dir = None
                    for d in bowtie2_dirs_i:
                        if d in current_index:
                            current_bt2_dir = d
                            break
                    bt2_idx = bowtie2_dirs_i.index(current_bt2_dir) if current_bt2_dir else 0

                    selected_bt2_dir = st.selectbox(
                        "Bowtie2 索引目录",
                        bowtie2_dirs_i,
                        index=bt2_idx,
                        key=f"bt2dir_{i}",
                    )

                    # 提取索引前缀
                    index_prefix = get_bowtie2_index_prefix(selected_species, selected_bt2_dir)
                    if isinstance(index_prefix, list):
                        # 多个前缀，让用户选择
                        selected_prefix = st.selectbox(
                            "索引前缀",
                            index_prefix,
                            key=f"bt2prefix_{i}",
                        )
                        final_index_dir = os.path.join(
                            "workflow", "anno", selected_species, selected_bt2_dir, selected_prefix
                        )
                    elif index_prefix:
                        final_index_dir = index_prefix
                        st.caption(f"索引前缀: `{index_prefix}`")
                    else:
                        st.warning("未在该目录下找到 .bt2 文件")
                        final_index_dir = current_index
                else:
                    st.warning(f"`workflow/anno/{selected_species}/` 下无 bowtie2 开头的目录")
                    final_index_dir = st.text_input(
                        "手动输入 Bowtie2 索引路径",
                        value=proj.get("index_dir", ""),
                        key=f"idx_manual_{i}",
                    )

            with col2:
                st.text_input(
                    "TxDb（R 包名或本地 GFF3/GTF 路径）",
                    value=proj.get("txdb", ""), key=f"txdb_{i}",
                    help="填 Bioconductor TxDb 包名，或直接填 GFF3/GTF 文件路径（脚本自动识别）",
                )
                st.text_input(
                    "OrgDb（R 包名，可留空）",
                    value=proj.get("orgdb", ""), key=f"orgdb_{i}",
                    help="基因符号注释与富集分析用；大豆等无官方 OrgDb 的物种可留空（注释仍可用，GO/KEGG 富集将跳过）",
                )

                st.markdown("**模块开关：**")
                modules = proj.get("modules", {})
                m1 = st.checkbox("1_download", value=modules.get("1_download", False), key=f"m1_{i}")
                m2 = st.checkbox("2_QC", value=modules.get("2_QC", False), key=f"m2_{i}")
                m3 = st.checkbox("3_ChIPseq", value=modules.get("3_ChIPseq", False), key=f"m3_{i}")
                m4 = st.checkbox("4_analyse", value=modules.get("4_analyse", False), key=f"m4_{i}")
                m5 = st.checkbox("5_gsea", value=modules.get("5_gsea", False), key=f"m5_{i}")
                m6 = st.checkbox("6_motif", value=modules.get("6_motif", False), key=f"m6_{i}")

            btn_col1, btn_col2 = st.columns([3, 1])
            with btn_col1:
                if st.button(f"保存项目", key=f"save_proj_{i}"):
                    # 重新读取最新 config 以避免被其他 Tab 的旧缓存覆盖
                    fresh_config = load_config()
                    fresh_projects = fresh_config.get("projects", [])
                    if i < len(fresh_projects):
                        # 物种/实验名会展开进 result、logs 的路径，再经 shell 传给下游工具。
                        # 含空格时 `> {log}` 会被 bash 拆词（日志跑进同名文件、touch 失败），
                        # 所以保存前统一规范化，并把改名结果写回输入框。
                        _exp_raw = st.session_state[f"exp_{i}"]
                        _exp = rt.sanitize_name(_exp_raw)
                        _sp = rt.sanitize_name(selected_species)
                        if not _exp:
                            st.error("实验名称不能为空")
                            st.stop()
                        fresh_projects[i]["species"] = _sp
                        fresh_projects[i]["experiment"] = _exp
                        fresh_projects[i]["rawdata_dir"] = _ensure_project_dirs_or_stop(
                            _sp, _exp, st.session_state[f"raw_{i}"])
                        fresh_projects[i]["index_dir"] = final_index_dir
                        fresh_projects[i]["txdb"] = st.session_state[f"txdb_{i}"]
                        fresh_projects[i]["orgdb"] = st.session_state[f"orgdb_{i}"]
                        fresh_projects[i]["modules"] = {
                            "1_download": m1,
                            "2_QC": m2,
                            "3_ChIPseq": m3,
                            "4_analyse": m4,
                            "5_gsea": m5,
                            "6_motif": m6,
                        }
                        fresh_config["projects"] = fresh_projects
                        save_config(fresh_config)
                        if _exp != _exp_raw:
                            st.session_state[f"exp_{i}"] = _exp
                            st.session_state["_cfg_flash"] = (
                                f"实验名称已自动规范化：`{_exp_raw}` → `{_exp}`。"
                                "路径里带空格会让 shell 把一条命令拆成多个词，"
                                "表现为日志写进一个同名文件、touch 找不到目录而报规则失败。"
                                "若该名字已有 result/logs 目录，请一并改名，否则旧进度不会被识别。")
                        st.rerun()
            with btn_col2:
                if st.button(f"删除", key=f"del_proj_{i}", type="secondary"):
                    fresh_config = load_config()
                    fresh_config["projects"].pop(i)
                    save_config(fresh_config)
                    st.success(f"项目 {proj['species']}/{proj['experiment']} 已删除")
                    st.rerun()

    st.markdown("---")
    st.subheader("添加新项目")

    # 新项目：物种选择
    available_species_new = get_species_dirs()
    if not available_species_new:
        available_species_new = ["TAIR10", "Glycine_max_v4", "homo", "mouse"]

    new_species = st.selectbox(
        "物种", available_species_new, key="new_sp", format_func=species_label
    )

    # 新项目：Bowtie2 索引目录选择
    new_bt2_dirs = get_bowtie2_dirs(new_species)
    if new_bt2_dirs:
        new_bt2_dir = st.selectbox("Bowtie2 索引目录", new_bt2_dirs, key="new_bt2dir")
        new_index_prefix = get_bowtie2_index_prefix(new_species, new_bt2_dir)
        if isinstance(new_index_prefix, list):
            new_selected_prefix = st.selectbox("索引前缀", new_index_prefix, key="new_bt2prefix")
            new_index_value = os.path.join("workflow", "anno", new_species, new_bt2_dir, new_selected_prefix)
        elif new_index_prefix:
            new_index_value = new_index_prefix
            st.caption(f"索引前缀: `{new_index_prefix}`")
        else:
            new_index_value = ""
            st.warning("未在该目录下找到 .bt2 文件")
    else:
        st.warning(f"`workflow/anno/{new_species}/` 下无 bowtie2 开头的目录")
        new_index_value = st.text_input("手动输入 Bowtie2 索引路径", key="new_idx_manual")

    # 新项目：原始数据目录跟随物种联动。rawdata_dir 必须指向真正放 fastq 的那一层，
    # 所以列出 workflow/resources/<物种>/ 的子目录，并把下载流程产出的 fastqfile/ 也单列。
    raw_cands = get_rawdata_candidates(new_species)
    _MANUAL = "手动填写其他路径…"
    _raw_labels = [
        f"{rel}   （{n} 个 fastq）" if n
        else f"{rel}   （本层未发现 fastq，请把 fq 放入该目录）"
        for rel, n in raw_cands
    ]

    def _sync_rawdata():
        pick = st.session_state.get("new_raw_pick", "")
        if pick and pick != _MANUAL and pick in _raw_labels:
            st.session_state["new_raw_val"] = raw_cands[_raw_labels.index(pick)][0]

    # 切换物种后旧路径已失效，重置为新物种的第一个候选（没有候选则清空）
    if st.session_state.get("_raw_sp_prev") != new_species:
        st.session_state["_raw_sp_prev"] = new_species
        # 默认选第一个已放好 fastq 的目录，没有则选第一项
        st.session_state["new_raw_val"] = next(
            (rel for rel, n in raw_cands if n),
            raw_cands[0][0] if raw_cands else "",
        )

    if raw_cands:
        st.selectbox(
            "原始数据目录",
            _raw_labels + [_MANUAL],
            key="new_raw_pick",
            on_change=_sync_rawdata,
            help="列出 workflow/resources/<物种>/ 下含 fastq 的目录；样本各自成子目录时，选其父目录可一次覆盖全部样本",
        )
    new_rawdata = st.text_input(
        "原始数据目录（可手动修改）",
        key="new_raw_val",
        placeholder=f"workflow/resources/{rt.variant_species(new_species) or new_species}/<实验目录>",
        help="数据不在项目内时可填绝对路径",
    )

    with st.form("new_project_form"):
        col1, col2 = st.columns(2)
        with col1:
            new_experiment = st.text_input(
                "实验名称",
                key="new_exp",
                help="会拼进 result/<物种>/<实验名> 与 logs/<物种>/<实验名>，"
                     "带空格会被 shell 拆词，因此保存时自动转成下划线",
            )
            st.caption(f"原始数据目录: `{new_rawdata or '（未填写）'}`")
            st.caption(f"Bowtie2 索引: `{new_index_value}`")
        with col2:
            new_txdb = st.text_input(
                "TxDb（R 包名或本地 GFF3/GTF 路径）",
                help="填 Bioconductor TxDb 包名，或直接填 GFF3/GTF 文件路径（脚本自动识别）",
            )
            new_orgdb = st.text_input(
                "OrgDb（R 包名，可留空）",
                help="基因符号注释与富集分析用；大豆等无官方 OrgDb 的物种可留空（注释仍可用，GO/KEGG 富集将跳过）",
            )

        submitted = st.form_submit_button("添加项目")
        if submitted and new_experiment:
            _sp_new = rt.sanitize_name(new_species)
            _exp_new = rt.sanitize_name(new_experiment)
            # 目录先建好再写 config：rawdata 留空也落到 workflow/resources/<物种>/<实验>/，
            # 用户拿到的是一条能直接拷 fastq 进去的路径，而不是一个空值。
            _raw_new = _ensure_project_dirs_or_stop(_sp_new, _exp_new, new_rawdata)
            new_proj = {
                "species": _sp_new,
                "experiment": _exp_new,
                "rawdata_dir": _raw_new,
                "index_dir": new_index_value,
                "txdb": new_txdb,
                "orgdb": new_orgdb,
                "modules": {
                    "1_download": False,
                    "2_QC": False,
                    "3_ChIPseq": False,
                    "4_analyse": False,
                    "5_gsea": False,
                    "6_motif": False,
                },
            }
            # 重新读取最新 config，避免覆盖其他 Tab 的修改
            fresh_config = load_config()
            if "projects" not in fresh_config:
                fresh_config["projects"] = []
            fresh_config["projects"].append(new_proj)
            save_config(fresh_config)
            if _exp_new != new_experiment:
                st.session_state["_cfg_flash"] = (
                    f"实验名称已自动规范化：`{new_experiment}` → `{_exp_new}`。"
                    "路径里带空格会让 shell 把一条命令拆成多个词，规则会在 touch 阶段失败。")
            else:
                st.session_state["_cfg_flash"] = (
                    f"项目 {new_proj['species']}/{_exp_new} 已添加；"
                    f"fastq 放到 `{_raw_new}/` 即可")
            st.rerun()

# ============================================================
# Tab 3: 样本配对表（按项目分抽屉）
# ============================================================
with tab3:
    st.subheader("IP vs Input 样本配对表")
    st.caption(f"文件路径: `{METADATA_PATH}`")

    # 保存按钮在页面底部，st.success 会被随后的 rerun 冲掉，
    # 先存一下提示，下一轮在顶部补发。
    _t3_flash = st.session_state.pop("_tab3_flash", None)
    if _t3_flash:
        st.success(_t3_flash)

    st.info(
        "**group 列说明：** 同一 group 名称的样本被视为生物学重复。"
        "Pipeline 会自动对同组样本取 Consensus Peaks（重叠区域），"
        "并用 deeptools 做重复间相关性分析。留空则每对样本独立分析。"
    )

    if os.path.exists(METADATA_PATH):
        config_for_tab3 = load_config()
        projects_tab3 = config_for_tab3.get("projects", [])

        # 每个项目实际有哪些 fastq，抽屉展示和归属推断都要用，先算一次
        detected_by_proj = {
            f"{p.get('species', '')}/{p.get('experiment', '')}": detect_fastq_samples(p)
            for p in projects_tab3
        }

        df_all = load_metadata()
        # 兼容旧格式：缺列就补齐，不能直接丢掉老数据
        if "group" not in df_all.columns:
            df_all["group"] = ""
        if "project" not in df_all.columns:
            # 老文件没有项目归属：先拿各项目实际有的 fastq 样本名去对，只对得上
            # 一个项目的自动归位；剩下对不上的再看是不是只有唯一项目可无脑认领。
            df_all["project"] = infer_row_projects(df_all, detected_by_proj)
            if len(projects_tab3) == 1:
                _only = projects_tab3[0]
                _pid_only = f"{_only['species']}/{_only['experiment']}"
                df_all["project"] = df_all["project"].replace("", _pid_only)
        df_all["project"] = df_all["project"].fillna("").astype(str).str.strip()
        df_all["group"] = df_all["group"].fillna("").astype(str).str.strip()

        col_cfg = {
            "IP sample": st.column_config.TextColumn("IP 样本", help="ChIP/实验组样本名"),
            "Input": st.column_config.TextColumn("Input 对照", help="对照组样本名"),
            "group": st.column_config.TextColumn(
                "重复组",
                help="相同组名的样本为生物学重复，留空表示独立分析",
            ),
        }
        meta_cols = ["IP sample", "Input", "group"]

        if not projects_tab3:
            st.warning("还没有配置任何项目。请先到「项目管理」页添加项目，本节会按项目自动分抽屉。")
            edited_df = df_all[meta_cols].copy()
            edited_df["project"] = ""
        else:
            edited_frames = []

            for _proj in projects_tab3:
                pid = f"{_proj.get('species', '')}/{_proj.get('experiment', '')}"
                sub = df_all[df_all["project"] == pid][meta_cols].copy()
                n_filled = int((sub["IP sample"].astype(str).str.strip() != "").sum())
                grp_n = sub["group"].replace("", pd.NA).dropna().nunique()
                status = f"{n_filled} 对" + (f" · {grp_n} 组" if grp_n else "")
                label = f"**{pid}** · {status}" if n_filled else f"**{pid}** · 未配置"

                with st.expander(label, expanded=(n_filled > 0)):
                    # ---- 自动识别 fastq 样本名 ----
                    detected = detected_by_proj.get(pid) or {}
                    if detected:
                        n_pe = sum(1 for v in detected.values() if v == "PE")
                        st.caption(
                            f"从 fastq 识别到 {len(detected)} 个样本（双端 {n_pe} 个）："
                            + "、".join(f"`{s}`" for s in detected)
                        )
                        if st.button("按识别结果生成配对表", key=f"auto_{pid}"):
                            rows = build_pairs_from_samples(detected, sub)
                            if rows:
                                # data_editor 不接受用 session_state 回填，只能换 key：
                                # 存下识别结果 + 版本号 +1，下一轮渲染成一个全新控件，
                                # 再把结果作为它的初始数据。
                                st.session_state[f"_md_pending_{pid}"] = pd.DataFrame(rows, columns=meta_cols)
                                st.session_state[f"md_ver_{pid}"] = st.session_state.get(f"md_ver_{pid}", 0) + 1
                                st.rerun()
                            else:
                                st.warning("只识别到对照样本，没有可配对的 IP 样本")
                    else:
                        st.caption("未在项目数据目录中找到 fastq，无法自动识别样本名。")

                    # 自动识别的结果要一直当成编辑器基线：data_editor 每次 rerun 都是
                    # 「基线 + 增量修改」，基线要是换回 csv 里的旧行，回填就丢了。
                    _ver = st.session_state.get(f"md_ver_{pid}", 0)
                    _pending = st.session_state.pop(f"_md_pending_{pid}", None)
                    if _pending is not None:
                        st.session_state[f"_md_base_{pid}"] = _pending
                    _base = st.session_state.get(f"_md_base_{pid}")
                    if _base is None:
                        _base = sub

                    edf = st.data_editor(
                        _base,
                        num_rows="dynamic",
                        use_container_width=True,
                        column_config=col_cfg,
                        key=f"md_editor_{pid}_{_ver}",
                    )

                    # 重复组统计
                    _g = edf["group"].fillna("").astype(str).str.strip()
                    _g = _g[_g != ""]
                    if len(_g):
                        st.caption("重复组：" + "、".join(
                            f"`{k}`({v})" for k, v in _g.value_counts().items()))
                    else:
                        st.caption("当前未设置重复组，所有样本对将独立分析")

                _d = edf.copy()
                _d["project"] = pid
                edited_frames.append(_d)

            # ---- 未分配的行（老数据/多项目时）----
            _un = df_all[df_all["project"] == ""][meta_cols].copy()
            if int((_un["IP sample"].astype(str).str.strip() != "").sum()) > 0:
                with st.expander(f"未分配 · {len(_un)} 行", expanded=True):
                    st.warning(
                        "这些行没有项目归属，目前对所有项目生效。"
                        "建议把它们在下方表里删除，再在对应项目的抽屉里重新录入。"
                    )
                    _un_edf = st.data_editor(
                        _un, num_rows="dynamic", use_container_width=True,
                        column_config=col_cfg, key="md_editor__unassigned",
                    )
                _u = _un_edf.copy()
                _u["project"] = ""
                edited_frames.append(_u)

            edited_df = (
                pd.concat(edited_frames, ignore_index=True)
                if edited_frames
                else pd.DataFrame(columns=meta_cols + ["project"])
            )
            edited_df = edited_df[meta_cols + ["project"]]

        # ---- Consensus Peaks 参数 ----
        st.markdown("---")
        st.markdown("#### Consensus Peaks 参数")
        st.caption("当同一 group 有多个重复时，取重叠 peaks 作为 consensus")
        st.caption("Consensus peaks 将自动进行 ChIPseeker 注释（饼图 + CSV），结果在「结果预览」→「Peak Annotation」→「Consensus」中查看")

        config_for_tab3 = load_config()
        consensus_cfg = config_for_tab3.get("consensus_peaks", {})

        col1, col2 = st.columns(2)
        with col1:
            consensus_min = st.number_input(
                "最小重叠数 (min_overlap)",
                min_value=1, max_value=10,
                value=int(consensus_cfg.get("min_overlap", 2)),
                help="一个 peak 至少在 N 个重复中出现才被保留。例如 3 个重复设 2 表示≥2/3重叠即保留",
            )
        with col2:
            consensus_frac = st.number_input(
                "最小重叠比例 (min_fraction)",
                min_value=0.0, max_value=1.0,
                value=float(consensus_cfg.get("min_fraction", 0.0)),
                step=0.1, format="%.1f",
                help="设为 >0 时使用比例模式（如 0.5 表示≥50%重复中出现）。设为 0 则使用上面的绝对数",
            )

        # ---- DeepTools 组间比较配置 ----
        st.markdown("---")
        st.markdown("#### DeepTools 组间比较 (Comparisons)")
        st.info(
            "**比较逻辑：** Peakcalling 使用 IP vs Input 配对，而 DeepTools 比较的是 **IP 之间**。\n\n"
            "在此配置哪些 group 需要做热图/profile 对比：\n"
            "- **单组 (1 group)**：展示该组所有重复的信号分布热图\n"
            "- **双组/多组 (≥2 groups)**：将多组 IP bigwig 放在一起做 computeMatrix + plotHeatmap 对比"
        )

        # 使用 session_state 管理 comparisons 列表（跨 rerun 持久化）
        if "comparisons_list" not in st.session_state:
            st.session_state.comparisons_list = copy.deepcopy(
                config_for_tab3.get("deeptools", {}).get("comparisons", [])
            )

        # 获取当前 metadata 中的 group 列表
        available_groups = []
        if "group" in edited_df.columns:
            available_groups = sorted(
                edited_df["group"].dropna().replace("", pd.NA).dropna().unique().tolist()
            )

        if not available_groups:
            st.warning("请先在上方配对表中设置 group 列，才能配置组间比较")
        else:
            st.caption(f"可用 group: {', '.join(f'`{g}`' for g in available_groups)}")

            # 显示现有比较项
            if st.session_state.comparisons_list:
                st.markdown("**现有比较项：**")
                to_delete = None
                for idx, comp in enumerate(st.session_state.comparisons_list):
                    comp_name = comp.get("name", f"comparison_{idx+1}")
                    comp_groups = comp.get("groups", [])
                    col1, col2, col3 = st.columns([2, 5, 1])
                    with col1:
                        st.text(f"{comp_name}")
                    with col2:
                        st.caption(f"groups: {' vs '.join(comp_groups)}")
                    with col3:
                        if st.button("", key=f"del_comp_{idx}"):
                            to_delete = idx

                # 执行删除（在循环外操作以避免 rerun 问题）
                if to_delete is not None:
                    removed = st.session_state.comparisons_list.pop(to_delete)
                    st.success(f"已删除比较: {removed.get('name', '')}")
                    st.rerun()
            else:
                st.caption("当前无比较项，请在下方添加")

            # 添加新比较
            st.markdown("**添加新比较：**")
            new_comp_col1, new_comp_col2 = st.columns([1, 3])
            with new_comp_col1:
                new_comp_name = st.text_input(
                    "比较名称",
                    placeholder="如: WT_vs_OE",
                    key="new_comp_name",
                )
            with new_comp_col2:
                new_comp_groups = st.multiselect(
                    "选择 group（≥1组）",
                    available_groups,
                    key="new_comp_groups",
                    help="选择 1 个 group 可查看该组重复信号分布；选择 ≥2 个 group 则做组间对比",
                )

            if st.button("添加比较", key="add_comparison"):
                if new_comp_name and len(new_comp_groups) >= 1:
                    st.session_state.comparisons_list.append({
                        "name": new_comp_name,
                        "groups": new_comp_groups,
                    })
                    label = ' vs '.join(new_comp_groups) if len(new_comp_groups) > 1 else new_comp_groups[0]
                    st.success(f"已添加比较: {new_comp_name} ({label})")
                    st.rerun()
                elif not new_comp_name:
                    st.error("请输入比较名称")
                else:
                    st.error("请至少选择 1 个 group")

        # ---- 统一保存按钮 ----
        st.markdown("---")
        if st.button("保存本页所有设置", type="primary", key="save_tab3_all"):
            # 1. 保存 metadata.csv（带上 project 列，每个项目只写自己的行）
            _out = edited_df.copy()
            for _c in meta_cols + ["project"]:
                if _c not in _out.columns:
                    _out[_c] = ""
                _out[_c] = _out[_c].fillna("").astype(str).str.strip()
            # 丢掉空行（data_editor 加了一行却没填）
            _out = _out[(_out["IP sample"] != "") | (_out["Input"] != "")]
            save_metadata(_out[meta_cols + ["project"]])

            # 2. 重新读取最新 config，避免覆盖其他 Tab 的修改
            fresh_cfg = load_config()

            # 3. 保存 consensus_peaks 参数到 config.yaml
            fresh_cfg["consensus_peaks"] = {
                "min_overlap": int(consensus_min),
                "min_fraction": float(consensus_frac),
            }

            # 4. 保存 comparisons 到 config.yaml（从 session_state 读取）
            if "deeptools" not in fresh_cfg:
                fresh_cfg["deeptools"] = {}
            fresh_cfg["deeptools"]["comparisons"] = (
                st.session_state.get("comparisons_list", []) if available_groups else []
            )

            save_config(fresh_cfg)

            # 保存后彻底重置编辑器：清基线 + 换 key 丢弃增量，
            # 下一轮直接从刚写好的 csv 重读，避免旧增量被二次施加
            # （删行增量叠到新表上会直接吃掉一行）。
            for _p in projects_tab3:
                _pid = f"{_p.get('species', '')}/{_p.get('experiment', '')}"
                st.session_state.pop(f"_md_base_{_pid}", None)
                st.session_state[f"md_ver_{_pid}"] = st.session_state.get(f"md_ver_{_pid}", 0) + 1
            st.session_state["_tab3_flash"] = "样本配对表、Consensus 参数、Comparisons 已全部保存！"
            st.rerun()
    else:
        st.warning("未找到 metadata.csv 文件")
        if st.button("创建空模板"):
            pd.DataFrame({"IP sample": [], "Input": [], "group": [], "project": []}).to_csv(
                METADATA_PATH, index=False)
            st.success("已创建空模板（含 group 与 project 列）")
            st.rerun()

# ============================================================
# Tab 4: ChIP 注释（ChIPseeker）
# ============================================================
with tab4:
    config = load_config()
    cs = config.get("chipseeker", {}) or {}

    # 兼容读取：同时接受 ChIPseeker 官方 camelCase（UI 保存时使用的命名）
    # 与旧版 snake_case（历史 config），按 keys 顺序优先匹配。
    def _cs_get(keys, default=None):
        for k in keys:
            if k in cs and cs[k] is not None:
                return cs[k]
        return default

    # tssRegion 支持两种写法：
    #   1) camelCase 一维数组: tssRegion: [-3000, 3000]
    #   2) snake_case 嵌套:    tss_region: {upstream: -3000, downstream: 3000}
    _tss_raw = _cs_get(["tssRegion", "tss_region"], None)
    if isinstance(_tss_raw, dict) and "upstream" in _tss_raw:
        tss_region_cfg = {"upstream": _tss_raw.get("upstream", -3000),
                          "downstream": _tss_raw.get("downstream", 3000)}
    elif isinstance(_tss_raw, (list, tuple)) and len(_tss_raw) >= 2:
        tss_region_cfg = {"upstream": _tss_raw[0], "downstream": _tss_raw[1]}
    else:
        tss_region_cfg = {"upstream": -3000, "downstream": 3000}

    st.subheader("ChIPseeker Peak 注释参数")

    st.markdown("#### 分析逻辑")
    st.info(
        "**基于 ChIPseeker 的 Peak 注释与可视化：**\n\n"
        "1. 读取 MACS2 peaks（或重复组 consensus peaks）\n"
        "2. 使用 TxDb 定义基因结构，将每个 peak 归类到 "
        "Promoter / 5'UTR / 3'UTR / Exon / Intron / Downstream / Intergenic\n"
        "3. 使用 OrgDb（可选）将 geneId 映射为 gene symbol\n"
        "4. 输出注释表 + 饼图 / 柱状图 / TSS 距离分布 / TSS 平均信号曲线 等可视化\n\n"
        "TxDb / OrgDb 在「项目管理」中为每个项目单独配置；本 Tab 配置**注释规则与可视化输出**。"
    )

    # 当前项目的注释库快览（仅展示，不可修改）
    _projects_for_hint = config.get("projects", []) or []
    if _projects_for_hint:
        _hint_lines = []
        for _p in _projects_for_hint:
            _hint_lines.append(
                f"  • `{_p.get('species','')}/{_p.get('experiment','')}` — "
                f"TxDb: `{_p.get('txdb','') or '未配置'}` | OrgDb: `{_p.get('orgdb','') or '未配置'}`"
            )
        st.caption("当前项目注释库：\n" + "\n".join(_hint_lines))

    # ---- 1. Promoter 区域定义 ----
    st.markdown("---")
    st.markdown("#### 1Promoter 区域定义 (tssRegion)")
    st.caption("以 TSS 为中心的区间被归类为 Promoter；ChIPseeker 默认 -3000 ~ +3000")
    col1, col2, col3 = st.columns(3)
    with col1:
        tss_upstream = st.number_input(
            "TSS 上游 (bp)",
            min_value=-50000, max_value=0, step=500,
            value=int(tss_region_cfg.get("upstream", -3000)),
            key="cs_tss_upstream",
            help="负值表示 TSS 上游，默认 -3000。拟南芥启动子常用 -1000 ~ -3000",
        )
    with col2:
        tss_downstream = st.number_input(
            "TSS 下游 (bp)",
            min_value=0, max_value=50000, step=500,
            value=int(tss_region_cfg.get("downstream", 3000)),
            key="cs_tss_downstream",
            help="正值表示 TSS 下游，默认 +3000",
        )
    with col3:
        flank_distance = st.number_input(
            "Flank Distance (Downstream 定义, bp)",
            min_value=0, max_value=50000, step=500,
            value=int(_cs_get(["flankDistance", "flank_distance"], 3000)),
            key="cs_flank_distance",
            help="基因 TES 下游 N bp 内的 peak 归类为 Downstream（而非 Intergenic）",
        )

    # ---- 2. 注释优先级 ----
    st.markdown("---")
    st.markdown("#### 2Genomic Annotation Priority")
    st.caption("当一个 peak 同时落在多个 feature（如 Exon 与 Intron）时，按此优先级归类。ChIPseeker 默认: Promoter > 5UTR > 3UTR > Exon > Intron > Downstream > Intergenic")

    DEFAULT_PRIORITY = ["Promoter", "5UTR", "3UTR", "Exon", "Intron", "Downstream", "Intergenic"]
    current_priority = _cs_get(["genomicAnnotationPriority", "annotation_priority"], DEFAULT_PRIORITY) or DEFAULT_PRIORITY
    # 过滤非法项，保留合法项的相对顺序
    current_priority = [x for x in current_priority if x in DEFAULT_PRIORITY]
    if not current_priority:
        current_priority = DEFAULT_PRIORITY

    annotation_priority = st.multiselect(
        "注释优先级（选择项顺序 = 优先度，高 → 低）",
        DEFAULT_PRIORITY,
        default=current_priority,
        key="cs_annotation_priority",
        help="列表中越靠前的类别优先级越高。若未选中某类别，该类别的 peak 将被归到下游低优先项或 Intergenic",
    )
    if not annotation_priority:
        st.warning("至少选择一个类别，否则保存时将回退到默认优先级")
        annotation_priority = DEFAULT_PRIORITY
    st.caption(f"当前优先级: {' > '.join(annotation_priority)}")

    # 关键提示：5'UTR / 3'UTR 独立分类的可见性依赖 priority 顺序
    st.info(
        "**想看到独立的 5'UTR / 3'UTR 分类？** 把 `5UTR` / `3UTR` 拖到 `Promoter` 前面。\n\n"
        "ChIPseeker 按此列表顺序“先到先得”：Promoter 区域（TSS ±3kb）与 5'UTR 高度重叠，"
        "若 Promoter 在前，大量 5'UTR peak 会被优先归为 Promoter，导致饼图里 5'UTR 计数为 0 或很少。\n\n"
        "推荐顺序（想突出 UTR）：`5UTR > 3UTR > Promoter > Exon > Intron > Downstream > Intergenic`"
    )

    # ---- 3. 注释粒度 ----
    st.markdown("---")
    st.markdown("#### 3注释粒度")
    col1, col2, col3 = st.columns(3)
    with col1:
        level_options = ["gene", "transcript"]
        _level_cur = cs.get("level", "gene")
        level = st.selectbox(
            "Level（注释级别）",
            level_options,
            index=level_options.index(_level_cur) if _level_cur in level_options else 0,
            key="cs_level",
            help="gene: 每个 peak 只关联一个基因（默认）；transcript: 保留所有转录本层面的注释",
        )
    with col2:
        add_flank = st.checkbox(
            "addFlankGeneInfo",
            value=bool(cs.get("add_flank_gene_info", False)),
            key="cs_add_flank",
            help="为落在基因附近但未直接命中的 peak 额外附加 flank 基因信息（ChIPseeker v1.42+ 支持）",
        )
    with col3:
        verbose = st.checkbox(
            "Verbose（打印进度）",
            value=bool(cs.get("verbose", True)),
            key="cs_verbose",
            help="运行时打印每个染色体的注释进度到日志",
        )

    # ---- 4. ChIPseeker 高级 options ----
    st.markdown("---")
    st.markdown("#### 4高级选项 (options 与 annotatePeak 参数)")
    st.caption(
        "仅列出 ChIPseeker v1.42 确实支持的选项："
        "`ignore_1st_exon` / `ignore_1st_intron` / `ignore_downstream` / `ignore_promoter_subcategory` / `downstreamDistance` 与 `annotatePeak(ignoreOverlap=...)`。"
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        ign_exon = st.checkbox(
            "ignore_1st_exon",
            value=bool(cs.get("ignore_1st_exon", True)),
            key="cs_ign_exon",
            help="忽略第一个外显子，使其仅归类为 Promoter（推荐）",
        )
        ign_intron = st.checkbox(
            "ignore_1st_intron",
            value=bool(cs.get("ignore_1st_intron", True)),
            key="cs_ign_intron",
            help="忽略第一个内含子（推荐）",
        )
    with col2:
        ign_down = st.checkbox(
            "ignore_downstream",
            value=bool(cs.get("ignore_downstream", False)),
            key="cs_ign_down",
            help="完全跳过 Downstream 分类，基因下游的 peak 将归为 Intergenic",
        )
        ign_promo_sub = st.checkbox(
            "ignore_promoter_subcategory",
            value=bool(cs.get("ignore_promoter_subcategory", False)),
            key="cs_ign_promo_sub",
            help="不细分 Promoter 子类别（如 Promoter (1-2kb)），统一归为 Promoter",
        )
    with col3:
        downstream_distance = st.number_input(
            "downstreamDistance (bp)",
            min_value=0, max_value=50000, step=500,
            value=int(cs.get("downstream_distance", 3000)),
            key="cs_downstream_distance",
            help="ChIPseeker 全局下游距离（options 级），影响 Downstream 分类的判定范围",
        )
        ignore_overlap = st.checkbox(
            "ignoreOverlap",
            value=bool(_cs_get(["ignoreOverlap", "ignore_overlap"], False)),
            key="cs_ignore_overlap",
            help="annotatePeak() 参数：开启后忽略与 feature 仅部分重叠的 peak（严格模式）",
        )

    # ---- 5. 可视化输出 ----
    st.markdown("---")
    st.markdown("#### 5输出可视化")
    st.caption("所有输出位于 `result/<species>/<experiment>/4_analyse/annoChIPPeaks/<pair>/`；结果预览页会自动扫描")
    outputs_cfg = cs.get("outputs", {}) or {}
    col1, col2, col3 = st.columns(3)
    with col1:
        out_pie = st.checkbox(
            "饼图 (plotAnnoPie) ",
            value=True,
            disabled=True,
            key="cs_out_pie",
            help="【必需】Snakemake 规则与结果预览页硬性依赖此产物，不可关闭",
        )
        out_bar = st.checkbox(
            "柱状图 (plotAnnoBar)",
            value=bool(outputs_cfg.get("bar_chart", True)),
            key="cs_out_bar",
            help="各注释类别计数柱状图",
        )
        out_dist = st.checkbox(
            "TSS 距离分布 (plotDistToTSS)",
            value=bool(outputs_cfg.get("dist_to_tss", True)),
            key="cs_out_dist",
            help="peak 到最近 TSS 的距离分布直方图 + 密度曲线",
        )
    with col2:
        out_avg = st.checkbox(
            "TSS 平均信号曲线 (plotAvgProf)",
            value=bool(outputs_cfg.get("avg_profile", True)),
            key="cs_out_avg",
            help="peak 在 TSS ±tssRegion 上的平均密度曲线",
        )
        out_cov = st.checkbox(
            "染色体覆盖图 (covplot)",
            value=bool(outputs_cfg.get("covplot", False)),
            key="cs_out_cov",
            help="peaks 在各染色体上的位置覆盖图（大数据量时较慢）",
        )
        out_promoter_csv = st.checkbox(
            "Promoter peaks CSV",
            value=bool(outputs_cfg.get("promoter_csv", True)),
            key="cs_out_promoter_csv",
            help="将落在 Promoter 区域的 peak 单独导出为 CSV",
        )
    with col3:
        out_anno_csv = st.checkbox(
            "完整注释表 CSV ",
            value=True,
            disabled=True,
            key="cs_out_anno_csv",
            help="【必需】GSEA 模块与结果预览页硬性依赖此产物，不可关闭",
        )
        out_venn = st.checkbox(
            "多样本 Venn 图 (vennpie)",
            value=bool(outputs_cfg.get("venn", False)),
            key="cs_out_venn",
            help="仅当同一项目有 ≥ 2 个 peak 文件时，绘制注释基因 Venn 图（需后续实现）",
        )
        st.caption("")

    # ---- 6. 当前配置预览 ----
    st.markdown("---")
    st.markdown("#### 保存前预览")
    _preview = {
        "tssRegion": [int(tss_upstream), int(tss_downstream)],
        "flankDistance": int(flank_distance),
        "level": level,
        "add_flank_gene_info": bool(add_flank),
        "genomicAnnotationPriority": annotation_priority,
        "ignore_1st_exon": bool(ign_exon),
        "ignore_1st_intron": bool(ign_intron),
        "ignore_downstream": bool(ign_down),
        "ignore_promoter_subcategory": bool(ign_promo_sub),
        "downstream_distance": int(downstream_distance),
        "ignoreOverlap": bool(ignore_overlap),
        "verbose": bool(verbose),
        "outputs": {
            "anno_csv": bool(out_anno_csv),
            "pie_chart": bool(out_pie),
            "bar_chart": bool(out_bar),
            "dist_to_tss": bool(out_dist),
            "avg_profile": bool(out_avg),
            "covplot": bool(out_cov),
            "promoter_csv": bool(out_promoter_csv),
            "venn": bool(out_venn),
        },
    }
    with st.expander("config.yaml 中的 `chipseeker:` 节内容", expanded=False):
        st.code(yaml.dump({"chipseeker": _preview}, allow_unicode=True, sort_keys=False), language="yaml")

    # ---- 7. 保存按钮 ----
    if st.button("保存 ChIP 注释参数", type="primary", key="save_chipseeker"):
        fresh_cfg = load_config()
        fresh_cfg["chipseeker"] = _preview
        save_config(fresh_cfg)
        st.success("ChIPseeker 注释参数已保存到 `config/config.yaml`！")
        st.caption(
            f"tssRegion: [{tss_upstream}, {tss_downstream}] | flankDistance: {flank_distance} | "
            f"level: {level} | priority: {' > '.join(annotation_priority)}"
        )
        st.info(
            "提示：修改后需重跑模块 4（annoChIPPeaks / anno_consensus）才能生效。\n"
            "若已完成的注释结果使用了 `ancient()` 包装的 config，Snakemake 不会自动重跑，需手动删除对应的输出文件或使用 `--forceall`。"
        )

# ============================================================
# Tab 5: GSEA 参数
# ============================================================
with tab5:
    config = load_config()
    gsea = config.get("gsea", {})

    st.subheader("模块5：GSEA 富集分析参数")

    # ---- 数据来源说明 ----
    st.markdown("#### 分析逻辑")
    st.info(
        "**基于重复组 Consensus Peaks 的富集分析：**\n\n"
        "1. 读取 metadata.csv 中的 **group** 列，识别各重复组\n"
        "2. 每个 group 的 consensus peaks（模块4生成）进行 ChIPseeker 注释\n"
        "3. 提取注释到的基因列表，做 GO 和 KEGG 富集分析\n"
        "4. 每个 group 独立输出富集结果（柱状图 + 点图 + CSV）\n\n"
        "需要先运行模块4（含 consensus peaks 步骤）才能执行 GSEA"
    )

    # 显示当前 metadata 中的重复组
    if os.path.exists(METADATA_PATH):
        meta_df = load_metadata()
        if "group" in meta_df.columns:
            groups_series = meta_df["group"].dropna().replace("", pd.NA).dropna()
            if len(groups_series) > 0:
                group_counts = groups_series.value_counts()
                st.markdown("**当前重复组（将各自独立做 GSEA）：**")
                for grp, cnt in group_counts.items():
                    st.caption(f"  • `{grp}`: {cnt} 个样本对 → consensus peaks → 注释基因 → 富集分析")
            else:
                st.warning("metadata.csv 中未设置 group 列，请在「样本配对表」中配置重复组")
        else:
            st.warning("metadata.csv 中缺少 group 列，请在「样本配对表」中添加")
    else:
        st.warning("未找到 metadata.csv")

    st.markdown("---")
    st.markdown("#### 分析内容开关")
    col1, col2 = st.columns(2)
    with col1:
        run_go   = st.checkbox("运行 GO 富集分析",   value=gsea.get("run_go",   True))
        run_kegg = st.checkbox("运行 KEGG 通路分析", value=gsea.get("run_kegg", True))
        promoter_only = st.checkbox(
            "仅使用 Promoter 区域的基因",
            value=gsea.get("promoter_only", False),
            help="勾选后只取 annotation 中 Promoter 类别的 gene_id 进行富集分析",
        )

    st.markdown("---")
    st.markdown("#### GO 分析参数")
    if run_go:
        current_go_types = gsea.get("go_types", ["BP", "MF", "CC"])
        col1, col2, col3 = st.columns(3)
        with col1:
            go_bp = st.checkbox("BP（生物学过程）", value="BP" in current_go_types)
        with col2:
            go_mf = st.checkbox("MF（分子功能）",   value="MF" in current_go_types)
        with col3:
            go_cc = st.checkbox("CC（细胞组分）",   value="CC" in current_go_types)
        go_types_selected = [t for t, v in [("BP", go_bp), ("MF", go_mf), ("CC", go_cc)] if v]
        if not go_types_selected:
            st.warning("请至少选择一种 GO 类型")
    else:
        go_types_selected = gsea.get("go_types", ["BP", "MF", "CC"])
        st.caption("GO 分析已关闭")

    st.markdown("---")
    st.markdown("#### 显著性阈值")
    col1, col2 = st.columns(2)
    with col1:
        pvalue_cutoff = st.number_input(
            "p-value 阈值",
            min_value=0.001, max_value=1.0,
            value=float(gsea.get("pvalue_cutoff", 0.05)),
            step=0.01, format="%.3f",
            help="富集分析的 p-value 截断值（BH 多重校正前）",
        )
    with col2:
        qvalue_cutoff = st.number_input(
            "q-value 阈值（BH 校正后）",
            min_value=0.01, max_value=1.0,
            value=float(gsea.get("qvalue_cutoff", 0.2)),
            step=0.05, format="%.2f",
            help="BH 校正后的 FDR 阈值，通常设为 0.2",
        )

    st.markdown("---")
    st.markdown("#### 基因集过滤")
    col1, col2 = st.columns(2)
    with col1:
        min_gs_size = st.number_input(
            "最小基因集大小",
            min_value=1, max_value=100,
            value=int(gsea.get("min_gs_size", 10)),
            help="富集通路/GO条目至少包含的基因数",
        )
    with col2:
        max_gs_size = st.number_input(
            "最大基因集大小",
            min_value=50, max_value=5000,
            value=int(gsea.get("max_gs_size", 500)),
            help="富集通路/GO条目最多包含的基因数（过大的条目通常不够具体）",
        )

    top_n = st.slider(
        "图表展示 Top N 条目",
        min_value=5, max_value=50,
        value=int(gsea.get("top_n", 20)),
        help="柱状图和点图各展示最显著的前 N 个富集条目",
    )

    st.markdown("---")
    if st.button("保存 GSEA 参数", type="primary", key="save_gsea"):
        # 重新读取最新 config，避免覆盖其他 Tab 的修改
        fresh_cfg = load_config()
        fresh_cfg["gsea"] = {
            "run_go":         run_go,
            "run_kegg":       run_kegg,
            "go_types":       go_types_selected,
            "pvalue_cutoff":  float(pvalue_cutoff),
            "qvalue_cutoff":  float(qvalue_cutoff),
            "min_gs_size":    int(min_gs_size),
            "max_gs_size":    int(max_gs_size),
            "top_n":          int(top_n),
            "promoter_only":  promoter_only,
        }
        save_config(fresh_cfg)
        st.success("GSEA 参数已保存到 config/config.yaml！")
        st.caption(
            f"GO 类型: {', '.join(go_types_selected)} | "
            f"p < {pvalue_cutoff} | q < {qvalue_cutoff} | "
            f"基因集大小: {min_gs_size}–{max_gs_size} | Top {top_n}"
        )

# ============================================================
# Motif 数据库：物种映射与一键获取
# （与 workflow/scripts/6_1_motif.py 的 SPECIES_DB_MAP 保持同步）
# ============================================================
MEME_DB_BASE = os.path.join(os.path.expanduser("~"), ".meme_db")
MOTIF_DB_URL = "https://meme-suite.org/meme/meme-software/Databases/motifs/motif_databases.12.25.tgz"
MOTIF_SPECIES_OPTIONS = [
    ("拟南芥 Arabidopsis (ArabidopsisDAPv1)", "motif_databases/ARABD/ArabidopsisDAPv1.meme"),
    ("大豆 Glycine max (CIS-BP)",            "motif_databases/CIS-BP_2.00/Glycine_max.meme"),
    ("人/脊椎动物 JASPAR2022 (vertebrates)", "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme"),
    ("果蝇 Drosophila (FLY factor survey)",  "motif_databases/FLY/fly_factor_survey.meme"),
    ("线虫 C. elegans (WORM uniprobe)",       "motif_databases/WORM/uniprobe_worm.meme"),
    ("酵母 S. cerevisiae (SwissRegulon)",     "motif_databases/YEAST/SwissRegulon_s_cer.meme"),
]


def motif_db_downloaded():
    """本地是否已下载并解压 motif 数据库"""
    return os.path.isdir(os.path.join(MEME_DB_BASE, "motif_databases"))


def motif_db_download():
    """下载并解压 MEME motif 数据库到 ~/.meme_db，返回 (ok, msg)"""
    import subprocess
    os.makedirs(MEME_DB_BASE, exist_ok=True)
    tmp = "/tmp/motif_databases.tgz"
    try:
        r = subprocess.run(
            f"wget -q -O {tmp} '{MOTIF_DB_URL}'", shell=True, capture_output=True, text=True
        )
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
            return False, f"下载失败：{r.stderr[:300]}"
        r2 = subprocess.run(
            f"tar -xzf {tmp} -C {MEME_DB_BASE}", shell=True, capture_output=True, text=True
        )
        if os.path.exists(tmp):
            os.remove(tmp)
        if r2.returncode != 0:
            return False, f"解压失败：{r2.stderr[:300]}"
        return True, "下载并解压完成"
    except Exception as e:
        return False, f"异常：{e}"


def motif_db_path_for(rel_path):
    return os.path.join(MEME_DB_BASE, rel_path)


# ============================================================
# Tab 6: Motif 参数
# ============================================================
with tab6:
    config = load_config()
    motif_cfg = config.get("motif", {})

    st.subheader("模块6：MEME-ChIP Motif 富集分析参数")

    st.markdown("#### 分析逻辑")
    st.info(
        "**基于 Peaks 的 Motif 发现与富集分析：**\n\n"
        "1. 从 consensus peaks（或单样本 peaks）中心提取 ±flank_size bp 的基因组序列\n"
        "2. 调用 MEME-ChIP 进行从头 motif 发现 (MEME/STREME)\n"
        "3. 与已知 motif 数据库比对 (TOMTOM)\n"
        "4. 分析 motif 在 peaks 中的位置富集 (CentriMo)\n\n"
        "需要先运行模块4（含 consensus peaks 步骤）才能执行 Motif 分析\n\n"
        "首次运行时会自动下载 MEME motif 数据库（~200MB），后续复用"
    )

    st.markdown("---")

    st.markdown("#### Motif 数据库 · 物种选择与一键获取")
    # 初始化数据库输入框状态（若未点过『一键获取』，沿用已保存配置）
    if "meme_db_field" not in st.session_state:
        st.session_state["meme_db_field"] = motif_cfg.get("meme_db", "")

    _labels = [lab for lab, _ in MOTIF_SPECIES_OPTIONS]
    pick_label = st.selectbox(
        "选择 motif 数据库物种",
        _labels,
        help="决定『一键获取』解析哪个物种的 .meme 数据库；若下方路径留空，运行流程中各项目仍会按其物种自动选库",
    )
    _rel = dict(MOTIF_SPECIES_OPTIONS)[pick_label]
    _target_path = motif_db_path_for(_rel)

    if motif_db_downloaded():
        _exists = os.path.exists(_target_path)
        st.caption(f"📦 本地数据库已就绪：`{os.path.join(MEME_DB_BASE, 'motif_databases')}`")
        st.caption(
            f"➜ 该物种文件：`{_target_path}` "
            + ("✅ 存在" if _exists else "⚠️ 未找到（版本可能不同，可手动指定）")
        )
    else:
        st.caption(f"📦 本地尚未下载数据库（`{MEME_DB_BASE}` 为空），获取约需下载 ~200MB")

    fc1, fc2 = st.columns([1, 3])
    with fc1:
        do_fetch = st.button("🔽 一键获取", type="primary", key="fetch_motif_db")
    with fc2:
        st.caption(
            "获取后会自动将该物种 .meme 绝对路径填入下方『Motif 数据库路径』，"
            "记得点击底部『保存 Motif 参数』写回配置（该路径对所有项目生效）"
        )

    if do_fetch:
        if not motif_db_downloaded():
            with st.spinner("正在下载并解压 MEME motif 数据库（~200MB，请耐心等待）..."):
                _ok, _msg = motif_db_download()
            if _ok:
                st.success(_msg)
            else:
                st.error(f"下载失败：{_msg}")
        if os.path.exists(_target_path):
            st.session_state["meme_db_field"] = _target_path
            st.success(f"已填充路径：`{_target_path}`，请下拉确认并点击『保存 Motif 参数』")
        elif motif_db_downloaded():
            _cand = sorted(
                glob.glob(os.path.join(MEME_DB_BASE, "motif_databases", "**", "*.meme"), recursive=True)
            )
            st.warning(f"未找到目标文件，本地共有 {len(_cand)} 个 .meme 数据库，可从中手动指定：")
            st.code("\n".join(_cand[:30]), language="text")

    st.markdown("---")
    st.markdown("#### 基本设置")

    col1, col2 = st.columns(2)
    with col1:
        use_consensus = st.checkbox(
            "使用 Consensus Peaks",
            value=motif_cfg.get("use_consensus_peaks", True),
            help="勾选：对每个重复组的 consensus peaks 做 motif 分析；取消：对每个样本对独立分析",
        )
        flank_size = st.slider(
            "序列窗口大小 (±bp)",
            min_value=50, max_value=500, step=50,
            value=int(motif_cfg.get("flank_size", 100)),
            help="从 peak 中心向两侧扩展的 bp 数。总提取长度 = 2 × flank_size",
        )
        max_peaks = st.number_input(
            "最大 Peaks 数量",
            min_value=100, max_value=10000, step=100,
            value=int(motif_cfg.get("max_peaks", 1000)),
            help="对 peaks 数量做上限截断，避免 MEME 运行过久。推荐 500-2000",
        )

    with col2:
        genome_fasta = st.text_input(
            "基因组 FASTA 路径（留空自动推断）",
            value=motif_cfg.get("genome_fasta", ""),
            help="留空时从项目配置的 index_dir 推断 .fa 文件路径",
        )
        meme_db = st.text_input(
            "Motif 数据库路径（留空 = 按项目物种自动选库）",
            key="meme_db_field",
            help="指定 .meme 格式数据库文件绝对路径。可用上方『一键获取』自动填充。留空则各项目按其物种自动选择默认数据库（首次运行自动下载）",
        )
        st.caption("数据库下载地址: https://meme-suite.org/meme/doc/download.html")

    st.markdown("---")
    st.markdown("#### MEME 参数")

    col1, col2, col3 = st.columns(3)
    with col1:
        meme_nmotifs = st.number_input(
            "发现 Motif 数量",
            min_value=1, max_value=20,
            value=int(motif_cfg.get("meme_nmotifs", 5)),
            help="MEME 从头发现的 motif 数量上限",
        )
    with col2:
        meme_minw = st.number_input(
            "最小 Motif 宽度",
            min_value=4, max_value=30,
            value=int(motif_cfg.get("meme_minw", 6)),
        )
    with col3:
        meme_maxw = st.number_input(
            "最大 Motif 宽度",
            min_value=6, max_value=50,
            value=int(motif_cfg.get("meme_maxw", 15)),
        )

    col1, col2 = st.columns(2)
    with col1:
        meme_mod_options = ["zoops", "oops", "anr"]
        meme_mod = st.selectbox(
            "MEME 模型",
            meme_mod_options,
            index=meme_mod_options.index(motif_cfg.get("meme_mod", "zoops")),
            help="zoops: 每条序列 0 或 1 个 motif（推荐）；oops: 每条恰好 1 个；anr: 任意数量",
        )
    with col2:
        streme_pvt = st.number_input(
            "STREME p-value 阈值",
            min_value=0.001, max_value=1.0,
            value=float(motif_cfg.get("streme_pvt", 0.05)),
            step=0.01, format="%.3f",
            help="STREME 快速 motif 发现的显著性阈值",
        )

    st.markdown("---")
    if st.button("保存 Motif 参数", type="primary", key="save_motif"):
        fresh_cfg = load_config()
        fresh_cfg["motif"] = {
            "use_consensus_peaks": use_consensus,
            "flank_size":    int(flank_size),
            "max_peaks":     int(max_peaks),
            "genome_fasta":  genome_fasta,
            "meme_db":       meme_db,
            "meme_nmotifs":  int(meme_nmotifs),
            "meme_minw":     int(meme_minw),
            "meme_maxw":     int(meme_maxw),
            "meme_mod":      meme_mod,
            "streme_pvt":    float(streme_pvt),
        }
        save_config(fresh_cfg)
        st.success("Motif 参数已保存到 config/config.yaml！")
