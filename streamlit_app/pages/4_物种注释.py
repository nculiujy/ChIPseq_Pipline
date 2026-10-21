"""
物种注释下载与配置页面
======================
- 从 Ensembl / NCBI / UCSC 等公共源下载基因组 FASTA + GFF/GTF
- 本地 bowtie2-build 建索引 + samtools faidx
- 实时动态进度条（下载速率、ETA、bowtie2 阶段）
- 一键把结果写入 config.yaml 的 projects 条目
- 支持自定义物种（用户手填 URL）
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import streamlit as st
import yaml

# 让 streamlit_app/utils 可导入
_THIS_DIR = Path(__file__).resolve().parent
_APP_DIR = _THIS_DIR.parent                       # streamlit_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from utils import annotation_manager as am  # noqa: E402
from utils.ui_theme import inject_theme, page_header, render_sidebar  # noqa: E402

st.set_page_config(page_title="物种注释", layout="wide")
inject_theme()
render_sidebar("annotation")
page_header("物种注释", "从公共数据库下载基因组 FASTA + GFF/GTF，本地构建 bowtie2 索引，一键写入 config.yaml")

# ---------- 路径 ----------
BASE_DIR = _APP_DIR.parent                                   # 项目根
ANNO_DIR = BASE_DIR / "workflow" / "anno"
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
CATALOG_PATH = _APP_DIR / "annotations_catalog.yaml"

ANNO_DIR.mkdir(parents=True, exist_ok=True)


# ---------- 加载 catalog ----------
@st.cache_data(show_spinner=False)
def load_catalog() -> dict:
    if not CATALOG_PATH.is_file():
        return {"species": {}}
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"species": {}}


CATALOG = load_catalog()
SPECIES_DB: dict = CATALOG.get("species", {}) or {}


# ---------- 顶部：依赖 & 环境 ----------
def render_header() -> None:
    deps = am.check_dependencies()
    total_gb = shutil_disk_free(ANNO_DIR)

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    c1.metric("注释目录", str(ANNO_DIR).replace(str(BASE_DIR) + "/", ""))
    c2.metric("可用空间", f"{total_gb:.1f} GB")
    c3.metric("Catalog 物种", len(SPECIES_DB))

    missing = [k for k, v in deps.items() if not v]
    with c4:
        st.caption(f"依赖检测环境 bin：`{am.conda_env_bin()}`")
        if missing:
            st.error(f"缺少命令: {', '.join(missing)}（未在上述环境 bin 及 PATH 中找到）")
        else:
            st.success("依赖齐全: " + " · ".join(deps.keys()))


def shutil_disk_free(path: Path) -> float:
    try:
        s = os.statvfs(path)
        return (s.f_bavail * s.f_frsize) / (1024 ** 3)
    except (OSError, AttributeError):
        return 0.0


# ---------- 物种卡片 ----------
def species_badge(status: am.SpeciesStatus) -> tuple[str, str]:
    """返回 (emoji+文本, streamlit 颜色类型)。"""
    if not status.exists:
        return ("未配置", "secondary")
    if status.complete:
        return ("完整", "success")
    if status.missing == ["gff"]:
        return ("缺 GFF", "warning")
    if status.missing and "bt2_index" not in status.missing:
        return ("部分", "warning")
    return ("不完整", "error")


def render_catalog_tab() -> None:
    if not SPECIES_DB:
        st.warning(f"catalog 为空或未找到: {CATALOG_PATH}")
        return

    st.subheader("内置物种")
    st.caption("点击展开选择下载源；下载 + 索引构建全部在本机后台运行，可切到「任务面板」查看进度")

    # 跨境外链单连接受 RTT 限制很慢，有 aria2 才能走多连接分段
    if shutil.which("aria2c"):
        st.caption("下载器：aria2c（16 连接分段，支持断点续传）")
    else:
        st.caption("下载器：单连接 urllib（未检测到 aria2c，速度慢很多）"
                   "；装一下可提速：`conda install -p <analysis> -c conda-forge aria2`")

    # 已存在的物种优先展示
    all_status = {sid: am.scan_species(ANNO_DIR, sid) for sid in SPECIES_DB}
    order = sorted(SPECIES_DB.keys(), key=lambda s: (all_status[s].complete, s))

    for sid in order:
        entry = SPECIES_DB[sid]
        status = all_status[sid]
        badge, color = species_badge(status)
        genome_mb = entry.get("genome_size_mb") or 0
        sources = entry.get("sources", {}) or {}
        recommended = entry.get("recommended_source") or (next(iter(sources)) if sources else "")

        header = f"{badge}  **{entry.get('display_name', sid)}**  ·  `{sid}`  ·  ~{genome_mb} MB"
        with st.expander(header, expanded=False):
            # 已存在信息
            if status.exists:
                st.markdown(f"**目录**: `{ANNO_DIR / sid}`")
                if status.bowtie2_dir:
                    st.markdown(f"**索引目录**: `{status.bowtie2_dir}/`  ·  **前缀**: `{status.index_prefix}`  ·  **大小**: {am.human_size(status.size_bytes)}")
                if status.fasta_path:
                    st.markdown(f"**FASTA**: `{Path(status.fasta_path).name}`")
                if status.gff_path:
                    st.markdown(f"**注释**: `{Path(status.gff_path).name}`")
                if status.missing:
                    st.warning(f"缺失: {', '.join(status.missing)}")
                if status.complete:
                    _render_apply_button(sid, entry, status)
                    st.divider()

            # 源选择
            if not sources:
                st.info("catalog 中无下载源，请用「自定义物种」标签页手填 URL")
                continue

            src_ids = list(sources.keys())
            default_idx = src_ids.index(recommended) if recommended in src_ids else 0
            src_id = st.selectbox(
                "下载源",
                src_ids,
                index=default_idx,
                format_func=lambda x: f"{sources[x].get('label', x)}  ({x})",
                key=f"src_{sid}",
            )
            src = sources[src_id]
            fasta_size = (src.get("fasta") or {}).get("size_mb", 0)
            gff_size = (src.get("gff") or {}).get("size_mb", 0)

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**基因组 FASTA**")
                st.code((src.get("fasta") or {}).get("url", ""), language="text")
                st.caption(f"约 {fasta_size} MB · 保存为 `{(src.get('fasta') or {}).get('filename', '')}`")
            with c2:
                st.markdown("**注释 GFF/GTF**")
                st.code((src.get("gff") or {}).get("url", ""), language="text")
                st.caption(f"约 {gff_size} MB · 保存为 `{(src.get('gff') or {}).get('filename', '')}`")

            total_mb = fasta_size + gff_size
            st.info(f"总下载量约 **{total_mb} MB**；bowtie2-build 需额外 **{genome_mb * 3} MB** 磁盘（索引+中间文件）。构建耗时与基因组大小成正比：酵母 ~1 分钟，人类 ~30-60 分钟。")

            threads = st.slider("bowtie2-build 线程数", 1, min(32, (os.cpu_count() or 4)), value=min(8, os.cpu_count() or 4), key=f"thr_{sid}")

            if status.complete:
                label = "重新下载并覆盖"
                confirm = st.checkbox("我确认要覆盖已有索引", key=f"ovr_{sid}")
            else:
                label = "开始下载并构建索引"
                confirm = True

            if st.button(label, key=f"go_{sid}", type="primary", disabled=not confirm):
                spec = _build_spec(sid, entry, src_id, src, threads)
                try:
                    st_info = am.start_task(spec)
                    st.session_state["active_task"] = st_info["task_id"]
                    st.toast(f"任务已启动: {sid}", icon="")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"启动失败: {e}")


def _build_spec(sid: str, entry: dict, src_id: str, src: dict, threads: int) -> am.TaskSpec:
    fasta = src.get("fasta") or {}
    gff = src.get("gff") or {}
    return am.TaskSpec(
        task_id=am.new_task_id(),
        species_id=sid,
        display_name=entry.get("display_name", sid),
        source_id=src_id,
        anno_root=str(ANNO_DIR),
        fasta_url=fasta.get("url", ""),
        fasta_filename=fasta.get("filename", "genome.fa.gz"),
        gff_url=gff.get("url", ""),
        gff_filename=gff.get("filename", "annotation.gff3.gz"),
        fasta_sha256=fasta.get("sha256"),
        gff_sha256=gff.get("sha256"),
        bowtie2_threads=threads,
        keep_gz=True,
        default_txdb_pkg=entry.get("default_txdb_pkg"),
        default_orgdb_pkg=entry.get("default_orgdb_pkg") or "",
        genome_size_mb=entry.get("genome_size_mb"),
    )


def _render_apply_button(sid: str, entry: dict, status: am.SpeciesStatus, scope: str = "cat") -> None:
    """完整状态下的「应用到 config.yaml」按钮 + 表单。
    scope 区分不同 tab（cat=物种 catalog / exist=已配置），避免同物种在两处同时出现时 key 冲突。
    """
    key_prefix = f"apply_{scope}_{sid}"
    if st.button(f"应用到 config.yaml ({sid})", key=f"{key_prefix}_btn"):
        st.session_state[f"{key_prefix}_open"] = True

    if st.session_state.get(f"{key_prefix}_open"):
        with st.form(f"{key_prefix}_form"):
            experiment = st.text_input("experiment 名称", value=sid.lower(), key=f"{key_prefix}_exp")
            rawdata = st.text_input("rawdata_dir (FASTQ 目录，可稍后填)", value="", key=f"{key_prefix}_raw")

            # index_dir：相对项目根
            rel_target = Path(status.bowtie2_dir or "")
            index_dir_rel = f"workflow/anno/{sid}/{rel_target}/{status.index_prefix}" if status.index_prefix else ""
            st.text_input("index_dir (相对项目根)", value=index_dir_rel, disabled=True, key=f"{key_prefix}_idx")

            # txdb 选择
            pkg = entry.get("default_txdb_pkg")
            gff_rel = ""
            if status.gff_path:
                gff_rel = os.path.relpath(status.gff_path, BASE_DIR)
            txdb_options = []
            if pkg:
                txdb_options.append(f"Bioconductor 包: {pkg}")
            if gff_rel:
                txdb_options.append(f"本地 GFF: {gff_rel}")
            txdb_choice = st.selectbox("txdb 来源", txdb_options or ["(无)"], key=f"{key_prefix}_txc")
            txdb_value = pkg if (pkg and txdb_choice.startswith("Bioconductor")) else gff_rel

            orgdb_default = entry.get("default_orgdb_pkg") or ""
            orgdb = st.text_input("orgdb (Bioconductor OrgDb 包名，可空)", value=orgdb_default, key=f"{key_prefix}_org")

            submitted = st.form_submit_button("写入 config.yaml", type="primary")
            if submitted:
                if not experiment.strip():
                    st.error("experiment 不能为空")
                else:
                    try:
                        am.apply_to_config(
                            CONFIG_PATH,
                            species_id=sid,
                            experiment=experiment.strip(),
                            index_dir=index_dir_rel,
                            txdb=txdb_value,
                            orgdb=orgdb.strip(),
                            rawdata_dir=rawdata.strip(),
                        )
                        st.success(f"已写入 config.yaml: {sid}/{experiment}")
                        st.session_state[f"{key_prefix}_open"] = False
                    except Exception as e:  # noqa: BLE001
                        st.error(f"写入失败: {e}")


# ---------- 自定义物种 ----------
def render_custom_tab() -> None:
    st.subheader("自定义物种")
    st.caption("catalog 里没有的物种，或想用自己服务器上的镜像时填这里")

    with st.form("custom_species_form"):
        c1, c2 = st.columns(2)
        with c1:
            sid = st.text_input("物种 ID（用作目录名，例: Solanum_lycopersicum_SL4）", placeholder="Species_name", key="cus_sid")
            display = st.text_input("显示名", placeholder="番茄 Solanum lycopersicum (SL4.0)", key="cus_display")
        with c2:
            genome_mb = st.number_input("基因组大小 (MB, 用于估算)", min_value=1, max_value=200000, value=500, key="cus_genome_mb")
            threads = st.slider("bowtie2-build 线程", 1, min(32, os.cpu_count() or 4), value=min(8, os.cpu_count() or 4), key="cus_threads")

        st.markdown("**基因组 FASTA**")
        fasta_url = st.text_input("FASTA URL (http/https/ftp，或 file:// 本地路径)", placeholder="https://...", key="cus_fasta_url")
        fasta_fn = st.text_input("FASTA 保存文件名", placeholder="genome.fa.gz", key="cus_fasta_fn")
        fasta_sha = st.text_input("FASTA SHA256 (可选)", placeholder="留空跳过校验", key="cus_fasta_sha")

        st.markdown("**注释 GFF/GTF**")
        gff_url = st.text_input("GFF/GTF URL", placeholder="https://...", key="cus_gff_url")
        gff_fn = st.text_input("GFF 保存文件名", placeholder="annotation.gff3.gz", key="cus_gff_fn")
        gff_sha = st.text_input("GFF SHA256 (可选)", placeholder="留空跳过校验", key="cus_gff_sha")

        st.markdown("**Bioconductor 包 (可选)**")
        pkg_c1, pkg_c2 = st.columns(2)
        with pkg_c1:
            txdb_pkg = st.text_input("txdb 包名（如 TxDb.Hsapiens.UCSC.hg38.knownGene；留空则用本地 GFF）", key="cus_txdb_pkg")
        with pkg_c2:
            orgdb_pkg = st.text_input("orgdb 包名（如 org.Hs.eg.db；留空则 GSEA 跳过）", key="cus_orgdb_pkg")

        submitted = st.form_submit_button("启动下载并构建", type="primary")

        if submitted:
            if not sid or not fasta_url or not gff_url:
                st.error("物种 ID / FASTA URL / GFF URL 均不能为空")
            else:
                spec = am.TaskSpec(
                    task_id=am.new_task_id(),
                    species_id=sid,
                    display_name=display or sid,
                    source_id="custom",
                    anno_root=str(ANNO_DIR),
                    fasta_url=fasta_url,
                    fasta_filename=fasta_fn or "genome.fa.gz",
                    gff_url=gff_url,
                    gff_filename=gff_fn or "annotation.gff3.gz",
                    fasta_sha256=fasta_sha.strip() or None,
                    gff_sha256=gff_sha.strip() or None,
                    bowtie2_threads=threads,
                    keep_gz=True,
                    default_txdb_pkg=txdb_pkg.strip() or None,
                    default_orgdb_pkg=orgdb_pkg.strip() or "",
                    genome_size_mb=int(genome_mb),
                )
                try:
                    st_info = am.start_task(spec)
                    st.session_state["active_task"] = st_info["task_id"]
                    st.toast(f"任务已启动: {sid}", icon="")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"启动失败: {e}")


# ---------- 任务面板 ----------
def render_tasks_tab() -> None:
    st.subheader("任务面板")

    tasks = am.list_tasks(ANNO_DIR)
    if not tasks:
        st.info("还没有任务。到「物种 catalog」或「自定义物种」标签页启动一个吧。")
        return

    # 当前活动任务
    active_id = st.session_state.get("active_task")
    running = [t for t in tasks if t.get("status") in ("pending", "running")]
    if running:
        st.markdown("#### 运行中")
        for t in running:
            _render_task_progress(t, auto_refresh=True)
        st.caption("页面每 1 秒自动刷新；可以切到其他标签页，任务在后台继续")
        # 自动刷新
        time.sleep(1.0)
        st.rerun()

    st.markdown("#### 历史")
    for t in tasks:
        if t.get("status") in ("pending", "running"):
            continue
        _render_task_history(t)


def _render_task_progress(t: dict, auto_refresh: bool = False) -> None:  # noqa: ARG001
    tid = t["task_id"]
    sid = t.get("species_id", "?")
    stage = t.get("stage", "?")
    stage_idx = t.get("stage_index", 0)
    stage_total = t.get("stage_total", len(am.STAGES))
    progress = float(t.get("progress") or 0.0)
    msg = t.get("message") or ""
    speed = t.get("speed_bps") or 0
    eta = t.get("eta_sec") or 0

    st.markdown(f"**{sid}** · task `{tid}` · 阶段 {stage_idx+1}/{stage_total} `{stage}`")

    # 综合进度：阶段权重 + 阶段内进度
    overall = (stage_idx + min(1.0, max(0.0, progress))) / max(1, stage_total)
    st.progress(min(1.0, max(0.0, overall)), text=f"{overall*100:.1f}%  ·  {msg}")

    if stage.startswith("download") and speed > 0:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("已下载", am.human_size(t.get("downloaded_bytes") or 0))
        c2.metric("总大小", am.human_size(t.get("total_bytes") or 0))
        c3.metric("速率", f"{am.human_size(speed)}/s")
        c4.metric("ETA", f"{eta}s" if eta else "-")

    c1, c2 = st.columns([1, 5])
    if c1.button("取消任务", key=f"cancel_{tid}"):
        ok = am.cancel_task(ANNO_DIR, tid)
        st.toast("已取消" if ok else "任务已结束", icon="")
        time.sleep(0.5)
        st.rerun()
    if c2.toggle("显示日志尾部", key=f"logtog_{tid}", value=False):
        log = am.tail_log(ANNO_DIR, tid, 40)
        st.code(log or "(暂无日志)", language="text")


def _render_task_history(t: dict) -> None:
    tid = t["task_id"]
    sid = t.get("species_id", "?")
    status = t.get("status", "?")
    icon = {"done": "", "error": "", "cancelled": ""}.get(status, "")
    finished = t.get("finished_at")
    started = t.get("started_at") or 0
    dur = int((finished or time.time()) - started)
    label = f"{icon} `{sid}` · {status} · 用时 {dur}s · {time.strftime('%m-%d %H:%M', time.localtime(started))}"

    with st.expander(label, expanded=False):
        if t.get("error"):
            st.error(f"错误: {t['error']}")
        if t.get("message"):
            st.caption(f"最后消息: {t['message']}")
        if t.get("target_dir"):
            st.caption(f"目标目录: `{t['target_dir']}`")

        c1, c2, c3 = st.columns([1, 1, 3])
        if c1.button("清除记录", key=f"clr_{tid}"):
            am.clear_task(ANNO_DIR, tid)
            st.rerun()
        if status == "done" and sid in SPECIES_DB:
            if c2.button("应用到 config", key=f"apply2_{tid}"):
                # 触发 catalog tab 里对应物种的 apply 表单展开
                st.session_state[f"apply_cat_{sid}_open"] = True
                st.rerun()
        if c3.toggle("显示日志", key=f"logtog2_{tid}", value=False):
            st.code(am.tail_log(ANNO_DIR, tid, 60) or "(无日志)", language="text")


# ---------- 已配置物种扫描 ----------
def render_existing_tab() -> None:
    st.subheader("已存在的物种目录")
    st.caption(f"扫描 `{ANNO_DIR}/` 下的目录（catalog 之外的也会显示，但只能手动应用）")

    if not ANNO_DIR.is_dir():
        st.info("注释目录尚未创建")
        return

    subdirs = sorted([d for d in ANNO_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")])
    if not subdirs:
        st.info("注释目录为空")
        return

    for d in subdirs:
        sid = d.name
        status = am.scan_species(ANNO_DIR, sid)
        badge, _ = species_badge(status)
        entry = SPECIES_DB.get(sid, {})

        header = f"{badge}  **{entry.get('display_name', sid)}**  ·  `{sid}`  ·  {am.human_size(status.size_bytes)}"
        with st.expander(header, expanded=False):
            if status.bowtie2_dir:
                st.markdown(f"**索引目录**: `{status.bowtie2_dir}/`")
                st.markdown(f"**索引前缀**: `{status.index_prefix}`")
            if status.fasta_path:
                st.markdown(f"**FASTA**: `{Path(status.fasta_path).name}`")
            if status.gff_path:
                st.markdown(f"**注释**: `{Path(status.gff_path).name}`")
            if status.bt2_files:
                st.caption(f"bt2 文件: {', '.join(status.bt2_files)}")
            if status.missing:
                st.warning(f"缺失: {', '.join(status.missing)}")

            if status.complete:
                _render_apply_button(sid, entry, status, scope="exist")


# ---------- 主布局 ----------
render_header()
st.divider()

# 如果有活动任务，顶部显示 mini 进度条
active_id = st.session_state.get("active_task")
if active_id:
    t = am.get_task(ANNO_DIR, active_id)
    if t and t.get("status") in ("pending", "running"):
        with st.container():
            stage_idx = t.get("stage_index", 0)
            stage_total = t.get("stage_total", len(am.STAGES))
            overall = (stage_idx + float(t.get("progress") or 0.0)) / max(1, stage_total)
            st.progress(min(1.0, overall),
                        text=f"{t.get('species_id')} · {t.get('stage')} · {overall*100:.1f}% · {t.get('message', '')[:80]}")
        st.divider()

tab_cat, tab_custom, tab_tasks, tab_existing = st.tabs([
    f"物种 catalog ({len(SPECIES_DB)})",
    "自定义物种",
    f"任务面板",
    f"已配置",
])

with tab_cat:
    render_catalog_tab()
with tab_custom:
    render_custom_tab()
with tab_tasks:
    render_tasks_tab()
with tab_existing:
    render_existing_tab()
