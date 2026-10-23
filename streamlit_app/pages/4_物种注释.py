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
import re
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
from utils import orgdb_helper as oh  # noqa: E402
from utils import runtime as rt  # noqa: E402
from utils.ui_theme import inject_theme, page_header, render_sidebar  # noqa: E402

from utils.version import APP_FULL_NAME  # noqa: E402

st.set_page_config(page_title=f"物种注释 · {APP_FULL_NAME}", layout="wide")
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


# ---------- 物种 → 组装版本 → 参考源 ----------
# catalog 里的条目是「一个组装」（Glycine_max_v4），但用户心里的一层是「一个物种」
# （大豆）：大豆有三个组装，各自下面又挂着若干数据库源。界面先按物种归堆，
# 抽屉里再分组装、再列源，免得 10 个条目平铺在页面上、同物种的散落各处。
_VER_RE = re.compile(r"^(?P<group>.*?)\s*[（(]\s*(?P<ver>.*?)\s*[)）]\s*$")


def split_display(name: str) -> tuple[str, str]:
    """`大豆 Glycine max (Wm82.a4.v1，2019)` → (`大豆 Glycine max`, `Wm82.a4.v1，2019`)。

    catalog 的 display_name 统一写成「物种 (版本)」，从它拆比再维护一套字段省事。
    格式对不上就整串当组名——最多是不分组，不会崩。
    """
    m = _VER_RE.match(str(name or "").strip())
    if m:
        return m.group("group").strip(), m.group("ver").strip()
    return str(name or "").strip(), ""


def group_of(sid: str, entry: dict) -> tuple[str, str]:
    """(物种组名, 版本标签)。catalog 里显式写了 group / version 就优先用。"""
    g_default, v_default = split_display(entry.get("display_name", sid))
    return (
        str(entry.get("group") or g_default or sid),
        str(entry.get("version") or v_default),
    )


def species_groups() -> dict:
    """{物种组名: [(sid, 版本标签), ...]}，组内保持 catalog 原有顺序。"""
    out: dict = {}
    for sid, entry in SPECIES_DB.items():
        g, v = group_of(sid, entry)
        out.setdefault(g, []).append((sid, v))
    return out


# ---------- 顶部：依赖 & 环境 ----------
def render_header() -> None:
    deps = am.check_dependencies()
    total_gb = shutil_disk_free(ANNO_DIR)

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    c1.metric("注释目录", str(ANNO_DIR).replace(str(BASE_DIR) + "/", ""))
    c2.metric("可用空间", f"{total_gb:.1f} GB")
    n_sources = sum(len((e.get("sources") or {})) for e in SPECIES_DB.values())
    # 「物种」按生物学物种数（大豆 1 个），组装与参考源分别计数；
    # 直接拿条目数当物种数会把大豆算成 3 个
    c3.metric(
        "Catalog",
        f"{len(species_groups())} 物种",
        f"{len(SPECIES_DB)} 个组装 · {n_sources} 套参考",
        delta_color="off",
    )

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


def _render_legacy_merge(sid: str, entry: dict) -> None:
    """把老结构里挤在物种目录下的索引挂到具体来源上。

    早期版本没有变体概念，拟南芥的几套索引都建在 workflow/anno/TAIR10/ 下，
    目录名手起（bowtie2_index_TAIR / _Ensembl / _ncbi …）。这些索引几十 GB，
    重下不划算，所以给个入口把它们搬到 workflow/anno/<物种>__<源>/ 下。
    """
    legacy = am.list_legacy_index_dirs(ANNO_DIR, sid)
    sources = entry.get("sources") or {}
    if not legacy or not sources:
        return

    with st.container(border=True):
        st.markdown(f"**旧结构索引 {len(legacy)} 个**（未归入任何来源）")
        st.caption(
            "这些目录建在 `workflow/anno/" + sid + "/` 下，没有来源标记，所以无法区分结果。"
            "选好对应的数据库源后搬过去，搬完就能在下方按来源直接应用；移动不改名、不复制。"
        )
        src_ids = list(sources.keys())
        for d in legacy:
            guess = am.guess_source_for_dir(sources, d)
            c1, c2, c3 = st.columns([3, 3, 1])
            with c1:
                st.markdown(f"`{d}`")
                # 目录名里带 tair 但没有对应源的老目录，靠猜必然错，留空让人选
                if not guess:
                    st.caption("看不出对应哪个源，请手动选")
            with c2:
                target = st.selectbox(
                    "归到哪个源", src_ids,
                    index=src_ids.index(guess) if guess in src_ids else 0,
                    format_func=lambda x, _s=sources: _s[x].get("label", x),
                    key=f"mig_src_{sid}_{d}",
                )
            with c3:
                if st.button("迁移", key=f"mig_go_{sid}_{d}"):
                    try:
                        info = am.migrate_legacy_index(ANNO_DIR, sid, d, target)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"迁移失败: {e}")
                    else:
                        # 旧 index_dir 指向已搬走的目录，不同步改就会到比对阶段才报错
                        changed = am.repoint_index_dir(
                            CONFIG_PATH,
                            f"workflow/anno/{sid}/{d}",
                            f"workflow/anno/{info['variant_id']}/{Path(info['to']).name}",
                        )
                        msg = f"`{d}` → `{info['variant_id']}`"
                        if changed:
                            msg += "；已同步更新项目 " + "、".join(f"`{c}`" for c in changed)
                        st.session_state["_anno_flash"] = msg
                        st.rerun()


def _render_source_block(
    sid: str, entry: dict, src_id: str, src: dict, status: am.SpeciesStatus
) -> None:
    """一个数据库来源 = 一个参考变体，各自一套索引、各自一套结果目录。"""
    vid = rt.variant_id(sid, src_id)
    badge, _ = species_badge(status)

    with st.container(border=True):
        st.markdown(f"**{src.get('label', src_id)}**  ·  {badge}")
        # 变体 ID 就是 anno 下的目录名，放小字里够用了，不必占标题行
        st.caption(f"`{vid}`")

        if status.exists:
            if status.bowtie2_dir:
                st.caption(
                    f"索引 `{status.bowtie2_dir}/` · 前缀 `{status.index_prefix}` · "
                    f"{am.human_size(status.size_bytes)}"
                )
            if status.fasta_path:
                st.caption(f"FASTA `{Path(status.fasta_path).name}`")
            if status.gff_path:
                st.caption(f"注释 `{Path(status.gff_path).name}`")
            if status.missing:
                st.warning(f"缺失: {', '.join(status.missing)}")

        if status.complete:
            _render_apply_button(vid, entry, status, base_species=sid)
            # 重建表单默认收起：URL 很长，已经就绪的变体多半只是来应用的
            if st.checkbox("重建这套索引（会覆盖现有）", key=f"rebuild_{vid}"):
                _render_download_form(sid, entry, src_id, src, status)
            return

        # 不完整：补 GFF 能救的就只补注释，不要重下基因组
        if status.missing == ["gff"]:
            _render_gff_patch(vid, entry, src_id=src_id)
            st.divider()
        _render_download_form(sid, entry, src_id, src, status)


def _render_download_form(
    sid: str, entry: dict, src_id: str, src: dict, status: am.SpeciesStatus
) -> None:
    """下载 / 建索引表单：URL、估算大小、线程数、启动按钮。"""
    vid = rt.variant_id(sid, src_id)
    fasta = src.get("fasta") or {}
    gff = src.get("gff") or {}
    fasta_size = fasta.get("size_mb", 0)
    gff_size = gff.get("size_mb", 0)
    genome_mb = entry.get("genome_size_mb") or 0

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**基因组 FASTA**")
        st.code(fasta.get("url", ""), language="text")
        st.caption(f"约 {fasta_size} MB · 保存为 `{fasta.get('filename', '')}`")
    with c2:
        st.markdown("**注释 GFF/GTF**")
        if gff.get("url"):
            st.code(gff.get("url", ""), language="text")
            st.caption(
                f"约 {gff_size} MB · 保存为 `{gff.get('filename', '')}` · "
                "与上面的 FASTA 同源同版本，peak 注释直接用这份建 TxDb"
            )
        else:
            st.warning("catalog 里这套源没配注释文件，建好索引后配不了 TxDb")

    total_mb = fasta_size + gff_size
    st.info(
        f"总下载量约 **{total_mb} MB**；bowtie2-build 需额外 **{genome_mb * 3} MB** 磁盘。"
        "构建耗时与基因组大小成正比：酵母 ~1 分钟，人类 ~30-60 分钟。"
    )

    threads = st.slider(
        "bowtie2-build 线程数", 1, min(32, (os.cpu_count() or 4)),
        value=min(8, os.cpu_count() or 4), key=f"thr_{vid}",
    )

    # 能走到这里说明上面已经勾过「重建这套索引」，不再叠一层确认
    label = "重新下载并覆盖" if status.complete else "开始下载并构建索引"
    raw_sp = rt.variant_species(sid)
    # 只有真的挂了多个组装时才能说「共用」，单组装物种不用说这句
    n_sib = sum(1 for k in SPECIES_DB if rt.variant_species(k) == raw_sp)
    st.caption(
        f"构建开始时一并建好 `workflow/resources/{raw_sp}/`；"
        f"fastq 放在其下的 `{raw_sp}/<实验名>/` 里，实验名与写入 config.yaml 时填的一致。"
        + (f"该物种下的 {n_sib} 个组装共用这一个数据目录。" if n_sib > 1 else "")
    )
    if st.button(label, key=f"go_{vid}", type="primary"):
        spec = _build_spec(vid, entry, src_id, src, threads)
        # 索引要建几十分钟，期间用户就可以把 fastq 拷进去；建目录失败不拦住索引
        try:
            rt.ensure_species_rawdata_dir(sid)
        except (OSError, ValueError) as e:  # noqa: BLE001
            st.warning(f"原始数据目录创建失败（不影响索引构建）：{e}")
        try:
            st_info = am.start_task(spec)
            st.session_state["active_task"] = st_info["task_id"]
            st.toast(f"任务已启动: {vid}", icon="")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"启动失败: {e}")


def render_catalog_tab() -> None:
    if not SPECIES_DB:
        st.warning(f"catalog 为空或未找到: {CATALOG_PATH}")
        return

    flash = st.session_state.pop("_anno_flash", None)
    if flash:
        st.success(flash)

    st.subheader("内置物种")
    st.caption(
        "同一物种可以并存多套数据库 / 版本，每套各建一份索引。"
        "应用到 config.yaml 后项目的 species 会写成 `<物种>__<来源>`，"
        "结果落在 `result/<物种>__<来源>/<实验>/`，一眼能看出用的是哪一套。"
    )

    # 跨境外链单连接受 RTT 限制很慢，有 aria2 才能走多连接分段
    if shutil.which("aria2c"):
        st.caption("下载器：aria2c（16 连接分段，支持断点续传）")
    else:
        st.caption("下载器：单连接 urllib（未检测到 aria2c，速度慢很多）"
                   "；装一下可提速：`conda install -p <analysis> -c conda-forge aria2`")

    # 已就绪的物种 / 变体优先展示
    ready: dict = {}
    for sid, entry in SPECIES_DB.items():
        for src_id in (entry.get("sources") or {}):
            ready[(sid, src_id)] = am.scan_variant(ANNO_DIR, sid, src_id)

    # 老结构（workflow/anno/<物种>/bowtie2_index_*）不算进就绪，但得提前算：
    # 否则用户明明装了，抽屉头上却写「未装」，看起来像丢了。
    legacy: dict = {sid: am.list_legacy_index_dirs(ANNO_DIR, sid) for sid in SPECIES_DB}

    groups = species_groups()

    def _srcs(sid: str) -> dict:
        return SPECIES_DB[sid].get("sources") or {}

    # 组间排序：已装的、有旧索引待归位的排前面，其余按名字
    g_order = sorted(
        groups,
        key=lambda g: (
            -sum(1 for s, _v in groups[g] for x in _srcs(s) if ready[(s, x)].complete),
            -sum(len(legacy.get(s) or []) for s, _v in groups[g]),
            g,
        ),
    )

    for gname in g_order:
        members = groups[gname]
        n_ver = len(members)
        n_src = sum(len(_srcs(s)) for s, _v in members)
        n_done = sum(1 for s, _v in members for x in _srcs(s) if ready[(s, x)].complete)
        n_legacy = sum(len(legacy.get(s) or []) for s, _v in members)

        # 抽屉头只写物种：版本、来源、状态都往下沉，不然一行摆四样东西看着累
        parts = [f"{n_src} 套参考"]
        if n_ver > 1:
            parts.append(f"{n_ver} 个组装版本")
        else:
            genome_mb = SPECIES_DB[members[0][0]].get("genome_size_mb") or 0
            if genome_mb:
                parts.append(f"~{genome_mb} MB")
        if n_done and n_done == n_src:
            parts.append("✓ 已装")
        elif n_done:
            parts.append(f"已装 {n_done}/{n_src}")
        else:
            parts.append("未装")
        # 有一批索引建在老结构里，没归到任何源，不提示就会以为没装过
        if n_legacy:
            parts.append(f"另有 {n_legacy} 个旧索引待归位")

        header = f"**{gname}**  ·  " + "  ·  ".join(parts)
        with st.expander(header, expanded=False):
            for idx, (sid, ver) in enumerate(members):
                entry = SPECIES_DB[sid]
                sources = _srcs(sid)
                # 一个物种能有好几个组装（大豆 a2/a4/a6），多个时才分节标版本；
                # 单组装的物种（拟南芥）再挂个小标题纯属噪声
                if n_ver > 1:
                    genome_mb = entry.get("genome_size_mb") or 0
                    st.markdown(f"#### {ver or sid}  ·  ~{genome_mb} MB")
                _render_legacy_merge(sid, entry)
                if not sources:
                    st.info("catalog 中无下载源，请用「自定义物种」标签页手填 URL")
                    continue
                for src_id, src in sources.items():
                    _render_source_block(sid, entry, src_id, src, ready[(sid, src_id)])
                if n_ver > 1 and idx < n_ver - 1:
                    st.divider()


def _render_gff_patch(vid: str, entry: dict, src_id: str = "") -> None:
    """只缺注释文件时的补救入口：不重下基因组、不重建索引。

    src_id 给定时锁定同一个源（catalog 里每个变体就是单一源）；
    「已配置」页扫到的不带源标记的老目录才需要手动选。
    """
    sources = entry.get("sources") or {}
    if not sources:
        return
    st.markdown("**补下注释文件**")
    st.caption("只补 GFF/GTF，不重下基因组、不重建索引（human 重建一次要 1 小时）。"
               "注意选与现有 FASTA **同一来源**的注释，否则染色体名对不上（chr1 vs 1）。")
    if src_id and src_id in sources:
        choice = src_id
        st.caption(f"沿用本变体的源：`{src_id}`")
    else:
        src_ids = list(sources)
        rec = entry.get("recommended_source")
        choice = st.selectbox(
            "注释来源", src_ids,
            index=src_ids.index(rec) if rec in src_ids else 0,
            key=f"gffpatch_{vid}",
        )
    gff = (sources[choice] or {}).get("gff") or {}
    gff_url = gff.get("url", "")
    if not gff_url:
        st.warning("该下载源没有配置注释地址")
        return
    st.code(gff_url, language="text")
    st.caption(f"约 {gff.get('size_mb', '?')} MB · 保存为 `{gff.get('filename', '')}`")
    if st.button("补下这个注释文件", key=f"gffpatch_btn_{vid}", type="primary"):
        spec = _build_spec(vid, entry, choice, sources[choice], 1)
        spec.gff_only = True
        try:
            st_info = am.start_task(spec)
            st.session_state["active_task"] = st_info["task_id"]
            st.toast(f"已启动补下任务: {vid}", icon="")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"启动失败: {e}")


def _build_spec(vid: str, entry: dict, src_id: str, src: dict, threads: int) -> am.TaskSpec:
    """变体 ID 就是下载目录名：worker 建 workflow/anno/<变体>/{bowtie2_index_<变体>}/。"""
    fasta = src.get("fasta") or {}
    gff = src.get("gff") or {}
    return am.TaskSpec(
        task_id=am.new_task_id(),
        species_id=vid,
        display_name=entry.get("display_name", rt.variant_species(vid)),
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


def _render_apply_button(
    vid: str,
    entry: dict,
    status: am.SpeciesStatus,
    base_species: str = "",
    scope: str = "cat",
) -> None:
    """完整状态下的「应用到 config.yaml」按钮 + 表单。

    vid 是参考变体（TAIR10__Ensembl_Plants_63），会直接写进 config 的 species：
    结果目录自然分家。base_species 是物种本名，原始数据仍按物种存放。
    scope 区分不同 tab（cat=物种 catalog / exist=已配置），避免同一变体在两处同时
    出现时 key 冲突。
    """
    base = base_species or rt.variant_species(vid)
    key_prefix = f"apply_{scope}_{vid}"
    if st.button(f"应用到 config.yaml ({vid})", key=f"{key_prefix}_btn"):
        st.session_state[f"{key_prefix}_open"] = True

    # 刚写入的项目：把三个目录摆出来。配置完下一步就是往原始数据目录里放 fastq，
    # 路径必须当场能看见，否则用户还得自己去猜目录建在哪。
    done = st.session_state.get(f"{key_prefix}_done")
    if done:
        st.success(f"已写入 config.yaml：`{done['species']}/{done['experiment']}`")
        st.markdown(
            f"- **原始数据**(放 fastq) `{done['dirs']['rawdata']}/`\n"
            f"- **结果** `{done['dirs']['result']}/`\n"
            f"- **日志** `{done['dirs']['logs']}/`"
        )
        st.caption("放好 fastq 后到「配置编辑 → 样本配对表」，在对应项目的抽屉里点「按识别结果生成配对表」。")
        if st.button("知道了", key=f"{key_prefix}_done_ok"):
            st.session_state.pop(f"{key_prefix}_done", None)
            st.rerun()

    if st.session_state.get(f"{key_prefix}_open"):
        with st.form(f"{key_prefix}_form"):
            experiment = st.text_input(
                "experiment 名称", value=base.lower(), key=f"{key_prefix}_exp",
                help=f"会拼进 result/{vid}/<实验> 与 logs/{vid}/<实验>；带空格会自动转成下划线",
            )
            # 表单内改实验名不会触发 rerun，所以这里只给模板不填具体值；
            # 真正建目录一律以提交时的实验名为准。
            rawdata = st.text_input(
                "rawdata_dir (FASTQ 目录，留空自动创建)",
                value="",
                placeholder=f"workflow/resources/{base}/<实验名>",
                key=f"{key_prefix}_raw",
                help=f"留空则按上面的实验名建 workflow/resources/{base}/<实验名>/；"
                     "同一物种的几套参考共用一份 fastq，不用重复拷",
            )
            st.caption(
                "提交时按上面填的实验名建好："
                f"`workflow/resources/{base}/<实验名>/`(放 fastq) · "
                f"`result/{vid}/<实验名>/` · `logs/{vid}/<实验名>/`"
            )

            # index_dir：相对项目根
            rel_target = Path(status.bowtie2_dir or "")
            index_dir_rel = (
                f"workflow/anno/{vid}/{rel_target}/{status.index_prefix}"
                if status.index_prefix else ""
            )
            st.text_input("index_dir (相对项目根)", value=index_dir_rel, disabled=True, key=f"{key_prefix}_idx")

            # txdb 来源：优先用下载下来的 GFF。它和 FASTA 是同一批下载、同一套
            # seqid，由它现场构建的 TxDb 坐标才能和比对结果直接对上；Bioconductor
            # 包常是另一套染色体命名（拟南芥 plantsmart28 用 1/2/3，TAIR 下载的索引
            # 用 Chr1/Chr2），下游得先做一道改名才能注释。
            pkg = entry.get("default_txdb_pkg") or ""
            gff_rel = os.path.relpath(status.gff_path, BASE_DIR) if status.gff_path else ""
            _txdb_opts = []  # [(写入 config 的值, 下拉显示的文案)]
            if gff_rel:
                _txdb_opts.append((gff_rel, f"本地 GFF · {os.path.basename(gff_rel)}"))
            if pkg:
                _txdb_opts.append((pkg, f"Bioconductor 包 · {pkg}"))
            if _txdb_opts:
                _txdb_label = dict(_txdb_opts)
                txdb_value = st.selectbox(
                    "txdb 来源",
                    options=[v for v, _ in _txdb_opts],
                    format_func=lambda v: _txdb_label.get(v, v),
                    key=f"{key_prefix}_txc",
                    help="GFF 与索引同源，坐标体系一致；Bioconductor 包的染色体命名"
                         "可能与索引不同，需要下游先转换",
                )
            else:
                st.warning("这套索引目录下没找到 GFF/GTF，catalog 里也没有默认 TxDb 包；"
                           "注释模块需要两者之一")
                txdb_value = st.text_input("txdb（包名或 GFF 路径）", key=f"{key_prefix}_txc_manual")

            # orgdb：catalog 里跟着物种写好的，用户不用自己记包名
            orgdb_default = entry.get("default_orgdb_pkg") or ""
            if orgdb_default:
                orgdb = st.text_input(
                    "orgdb（Bioconductor OrgDb 包名）", value=orgdb_default,
                    key=f"{key_prefix}_org",
                    help="随物种自动绑定；要换别的包直接改这里",
                )
            else:
                st.warning(
                    "该物种在 Bioconductor 没有官方 OrgDb 包，GO/KEGG 富集将跳过；"
                    "peak 到基因的注释不受影响。自己建了包可以填在这里"
                )
                orgdb = st.text_input("orgdb（自建包名，可留空）", key=f"{key_prefix}_org")

            submitted = st.form_submit_button("写入 config.yaml", type="primary")
            if submitted:
                exp = rt.sanitize_name(experiment)
                if not exp:
                    st.error("experiment 不能为空")
                else:
                    try:
                        # 先把目录建好，再把路径写进 config：rawdata_dir 不再是空字符串，
                        # 用户在配置编辑页能看到数据该放哪，4_1 之前的规则也不用猜。
                        # 结果/日志按变体分（vid），原始数据仍按物种走（base）。
                        info = rt.ensure_project_dirs(
                            vid, exp, rawdata.strip(), base_species=base
                        )
                        am.apply_to_config(
                            CONFIG_PATH,
                            species_id=vid,
                            experiment=exp,
                            index_dir=index_dir_rel,
                            txdb=txdb_value,
                            orgdb=orgdb.strip(),
                            rawdata_dir=str(info["rawdata_dir"]),
                        )
                        info["species"] = vid
                        info["experiment"] = exp
                        st.session_state[f"{key_prefix}_done"] = info
                        st.session_state[f"{key_prefix}_open"] = False
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"写入失败: {e}")


# ---------- 自定义物种 ----------
def render_custom_tab() -> None:
    st.subheader("自定义物种")
    st.caption("catalog 里没有的物种，或想用自己服务器上的镜像时填这里")
    st.info(
        "同一物种要多套版本时，把来源/版本写进 ID（如 `Solanum_SL4_ensembl`、`Solanum_SL4_ncbi`），"
        "结果目录会按 ID 自动分开。ID 里不要用双下划线 `__`：那是内置物种「物种 + 来源」的分隔符。"
    )

    with st.form("custom_species_form"):
        c1, c2 = st.columns(2)
        with c1:
            sid = st.text_input("物种 ID（用作目录名，例: Solanum_lycopersicum_SL4）", placeholder="Species_name", key="cus_sid", help="会同时作为 workflow/anno/<ID>/ 与 result/<ID>/<实验>/ 的名字；不要包含 `__`")
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
                    # 与内置物种一致：建索引的同时备好放数据的物种目录
                    rt.ensure_species_rawdata_dir(sid)
                except (OSError, ValueError) as e:  # noqa: BLE001
                    st.warning(f"原始数据目录创建失败（不影响索引构建）：{e}")
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

    # 先扫一遍再分组：老目录（TAIR10）与它的变体目录（TAIR10__Ensembl_Plants_63）
    # 要归到同一个物种抽屉里，否则同一个物种在页面上散成好几块
    rows = []
    for d in subdirs:
        vid = d.name
        base, src_id = rt.split_variant(vid)
        entry = SPECIES_DB.get(base, {})
        status = am.scan_species(ANNO_DIR, vid, source_id=src_id, species_base=base)
        gname = group_of(base, entry)[0] if entry else base
        rows.append((gname, vid, base, src_id, entry, status))

    groups: dict = {}
    for r in rows:
        groups.setdefault(r[0], []).append(r)

    for gname, items in groups.items():
        n_ok = sum(1 for _g, _v, _b, _s, _e, stt in items if stt.complete)
        size = sum(stt.size_bytes for *_x, stt in items)
        head = (
            f"**{gname}**  ·  {len(items)} 个目录  ·  "
            + ("✓ 已装" if n_ok == len(items) else f"已装 {n_ok}/{len(items)}")
            + f"  ·  {am.human_size(size)}"
        )
        with st.expander(head, expanded=False):
            for _g, vid, base, src_id, entry, status in items:
                _render_existing_one(vid, base, src_id, entry, status)


def _render_existing_one(vid, base, src_id, entry, status) -> None:
    """一个具体目录的详情卡片。"""
    badge, _ = species_badge(status)
    # 变体目录名里已经含了源，但源 ID 不好看，catalog 里有 label 就用 label
    src_label = ((entry.get("sources") or {}).get(src_id) or {}).get("label", src_id)

    with st.container(border=True):
        st.markdown(f"`{vid}`  ·  {badge}" + (f"  ·  {src_label}" if src_id else ""))

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
        if not src_id and status.exists:
            st.caption(
                "这个目录名不带数据库来源，所以结果目录也只能叫 `" + vid + "`。"
                "想按来源区分的话，到「物种 catalog」里把它迁移到某个源上。"
            )

        if status.complete:
            _render_apply_button(vid, entry, status, base_species=base, scope="exist")
        elif status.missing == ["gff"]:
            _render_gff_patch(vid, entry, src_id=src_id)


# ---------- OrgDb 构建 / ID 映射 ----------
def _project_choices() -> list[dict]:
    return [p for p in rt.get_projects() if p.get("species") and p.get("experiment")]


@st.cache_data(show_spinner=False, ttl=600)
def _scan_ids_cached(paths: tuple, promoter_only: bool) -> tuple:
    ids, col, n_rows = oh.read_gene_ids(paths, promoter_only=promoter_only)
    return ids, col, n_rows


@st.cache_data(show_spinner=False, ttl=600)
def _preview_cached(ids: tuple, t2g_items: tuple, id_strip: str) -> dict:
    return oh.preview_mapping(ids, dict(t2g_items), id_strip)


def _render_orgdb_status() -> None:
    """每个项目一行：orgdb / term2gene / 5_gsea 开关，一眼看出谁还缺东西。"""
    rows = []
    for p in _project_choices():
        t2g = p.get("term2gene_go") or {}
        orgdb = str(p.get("orgdb") or "")
        mods = p.get("modules") or {}
        if orgdb:
            src = f"OrgDb: `{orgdb}`"
        elif t2g:
            src = f"TERM2GENE: {len(t2g)} 套"
        else:
            src = "**缺（富集会被跳过）**"
        rows.append({
            "项目": f"{p['species']}/{p['experiment']}",
            "基因集来源": src,
            "id_strip": p.get("id_strip") or "",
            "5_gsea": "开" if mods.get("5_gsea") else "关",
        })
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("config.yaml 里还没有项目，先到配置页保存一个项目再回来")


def render_orgdb_tab() -> None:
    st.subheader("OrgDb 自建 / ID 映射")
    st.caption(
        "非模式物种没有官方 OrgDb 包时，用这里把「基因 ↔ 基因集」的映射补齐。"
        "两条路二选一：自建 OrgDb 包（走 GO 官方注释），或直接生成 TERM2GENE 基因集（更快，不装包）。"
    )

    projects = _project_choices()

    # ---- 任务：orgdb / term2gene / rdeps ----
    my_kinds = ("orgdb", "term2gene", "rdeps")
    my_tasks = [t for t in am.list_tasks(ANNO_DIR) if (t.get("kind") or "genome") in my_kinds]
    running = [t for t in my_tasks if t.get("status") in ("pending", "running")]
    if running:
        st.markdown("#### 运行中的构建任务")
        for t in running:
            _render_task_progress(t)
        if st.toggle("自动刷新（任务运行中每 1 秒）", value=True, key="orgdb_auto"):
            time.sleep(1.0)
            st.rerun()
        st.divider()

    # ---- 已配置项目一览 ----
    with st.expander("当前项目的基因集来源状态", expanded=True):
        _render_orgdb_status()

    st.divider()

    # ---- 选项目 ----
    labels = [f"{p['species']}/{p['experiment']}" for p in projects]
    default_idx = 0
    for i, p in enumerate(projects):
        if (p.get("modules") or {}).get("5_gsea"):
            default_idx = i
            break
    MANUAL = "手动指定（config 里还没有这一条）"
    sel = st.selectbox("作用于哪个项目", labels + [MANUAL], index=default_idx, key="orgdb_proj")
    if sel == MANUAL:
        cm1, cm2 = st.columns(2)
        sp = cm1.text_input("species（与 result/ 下的目录同名）", key="orgdb_manual_sp",
                            placeholder="例：Glycine_max_v4__NCBI_RefSeq_v4")
        ex = cm2.text_input("experiment", key="orgdb_manual_ex",
                            placeholder="例：NAC35andNAC27")
        st.caption("这一条还不在 config.yaml 里：写入时会自动新建，之后回「配置编辑」补 txdb 与结果目录")
        proj = {"species": sp, "experiment": ex, "modules": {}}
    else:
        proj = projects[labels.index(sel)]
        sp, ex = proj["species"], proj["experiment"]
    if not (sp and ex):
        st.info("填上 species 与 experiment，下面才会出现环境体检和构建表单")
        return
    # 控件 key 里带物种名：切项目时默认值才会跟着变（否则会留着上一个物种的 taxid）
    wkey = re.sub(r"[^0-9A-Za-z_]+", "_", sp)
    outdir = oh.species_orgdb_dir(ANNO_DIR, sp)
    orgdb_lib = oh.r_lib_dir()

    # ---- 环境体检 ----
    st.markdown("#### ① 环境体检")
    cfg_orgdb = str(proj.get("orgdb") or "").strip()
    check_pkgs = ["AnnotationForge", "AnnotationDbi", "clusterProfiler", "enrichplot", "GO.db"]
    if cfg_orgdb and cfg_orgdb not in check_pkgs:
        check_pkgs.append(cfg_orgdb)
    with st.spinner("查询 R 环境…"):
        env = oh.r_packages_status(check_pkgs, lib=orgdb_lib)
    c1, c2 = st.columns(2)
    c1.code(f"Rscript: {env['rscript'] or '未找到'}\nR 库:    {env['lib'] or '未找到'}", language="text")
    missing = [k for k, v in env["packages"].items() if not v["ok"]]
    with c2:
        for name, stt in env["packages"].items():
            st.markdown(f"{'✓' if stt['ok'] else '✗'} `{name}` {stt['version']}")

    if "AnnotationForge" in missing:
        st.warning(
            "分析环境缺 **AnnotationForge**，无法自建 OrgDb。"
            "两种选择：一键安装（需联网，几分钟），或改走下面的 gene2go 基因集（不装包，立刻可用）。"
        )
        cc1, cc2 = st.columns([1, 2])
        mirror = cc2.selectbox("CRAN 镜像", oh.CRAN_MIRRORS, index=0, key="orgdb_mirror")
        if cc1.button("安装 AnnotationForge", type="secondary", key="orgdb_install_deps"):
            spec = am.rdeps_task(sp, f"R 依赖 / {sp}", str(ANNO_DIR),
                                 packages=oh.RDEPS_DEFAULT, orgdb_lib=orgdb_lib,
                                 cran_mirror=mirror)
            am.start_task(spec)
            st.session_state["active_task"] = spec.task_id
            st.toast("已开始安装 AnnotationForge")
            time.sleep(0.5)
            st.rerun()

    installed = oh.list_orgdb_packages(orgdb_lib)
    if installed:
        st.caption("已装的 OrgDb 包：" + "、".join(
            f"{d['pkg']} {d['version']}" for d in installed))

    # 项目里已经填了 orgdb，但当前 R 库加载不到 —— 模块 5 的 enrichGO 会直接失败
    if cfg_orgdb and not (env["packages"].get(cfg_orgdb) or {}).get("ok"):
        st.warning(f"项目配置的 OrgDb `{cfg_orgdb}` 在这个 R 库里加载不到，模块 5 的 GO 富集跑不起来")
        if st.button(f"安装 {cfg_orgdb}", key=f"orgdb_install_cfg_{wkey}"):
            spec = am.rdeps_task(sp, f"R 依赖 / {sp}", str(ANNO_DIR), packages=cfg_orgdb,
                                 orgdb_lib=orgdb_lib, cran_mirror=oh.CRAN_MIRRORS[0])
            am.start_task(spec)
            st.session_state["active_task"] = spec.task_id
            st.toast(f"已开始安装 {cfg_orgdb}")
            time.sleep(0.5)
            st.rerun()

    st.divider()

    # ---- 路线 A：自建 OrgDb ----
    st.markdown("#### ② 路线 A：自建 OrgDb 包（AnnotationForge × NCBI）")
    st.caption("首次构建会在 NCBI 下载 gene_info / gene2go，常 30–90 分钟；同物种重建有缓存，分钟级。")
    g0, s0 = oh.guess_genus_species(sp, proj.get("display_name") or "")
    ca, cb, cc = st.columns(3)
    tax_default = oh.guess_taxid(sp, proj.get("display_name") or "")
    tax_id = ca.text_input("NCBI taxid", value=tax_default, key=f"orgdb_tax_{wkey}",
                           help="大豆 3847、玉米 4577、小麦 4565、番茄 4081")
    genus = cb.text_input("属（genus）", value=g0, key=f"orgdb_genus_{wkey}")
    species_latin = cc.text_input("种（species）", value=s0, key=f"orgdb_species_{wkey}")
    pkg_guess = oh.suggest_orgdb_pkg(genus, species_latin)
    pkg_name = st.text_input("包名", value=pkg_guess, key=f"orgdb_pkg_{wkey}",
                             help="留空按 org.<属首字母大写><种前 3 字母>.eg.db 生成") or pkg_guess
    ca, cb = st.columns([2, 3])
    ca.code(f"输出: {outdir}", language="text")
    if cb.button("开始构建 OrgDb 包", type="primary", key="orgdb_build",
                 disabled=("AnnotationForge" in missing) or not (tax_id and genus and species_latin)):
        spec = am.orgdb_task(sp, f"OrgDb / {sp}", str(ANNO_DIR), tax_id=tax_id,
                             genus=genus, species=species_latin,
                             orgdb_pkg=pkg_name, orgdb_lib=orgdb_lib)
        am.start_task(spec)
        st.session_state["active_task"] = spec.task_id
        st.toast("已开始构建 OrgDb")
        time.sleep(0.5)
        st.rerun()
    if "AnnotationForge" in missing:
        st.caption("（按钮置灰：先装 AnnotationForge）")

    built = oh.scan_orgdb_outdir(outdir)
    if built:
        st.markdown("已构建：" + "、".join(f"`{d['pkg']}`（{d['size_mb']} MB）" for d in built))

    st.divider()

    # ---- 路线 B：gene2go 基因集 ----
    st.markdown("#### ③ 路线 B：gene2go 基因集（不装包，推荐先用这条）")
    st.caption(
        "从 NCBI gene2go.gz 抽当前物种的 GO 注释，生成 clusterProfiler 的 TERM2GENE / TERM2NAME；"
        "GO 富集走 `enricher()`，输出列名与 `enrichGO()` 一致，图表代码完全复用。"
    )
    cb1, cb2, cb3 = st.columns([1, 1, 2])
    tax_b = cb1.text_input("NCBI taxid", value=tax_default, key=f"t2g_tax_{wkey}")
    prefix_b = cb2.text_input("文件前缀", value=(oh.guess_genus_species(sp)[0][:1].lower()
                                                + oh.guess_genus_species(sp)[1][:3].lower()) or "gene2go",
                              key=f"t2g_prefix_{wkey}", help="例：gmax → gmax_go_bp_term2gene.tsv")
    cb3.code(f"输出目录: {outdir}", language="text")
    if st.button("生成 / 更新基因集", type="primary", key="t2g_build", disabled=not tax_b):
        spec = am.term2gene_task(sp, f"gene2go / {sp}", str(ANNO_DIR), tax_id=tax_b,
                                 prefix=prefix_b, outdir=str(outdir))
        am.start_task(spec)
        st.session_state["active_task"] = spec.task_id
        st.toast("已开始生成 gene2go 基因集")
        time.sleep(0.5)
        st.rerun()

    manifest = oh.scan_term2gene(outdir)
    if manifest:
        st.success(
            f"已有基因集：{manifest.get('n_genes', 0)} 个基因 / "
            f"{sum((manifest.get('n_go_terms') or {}).values())} 个 GO term / "
            f"{manifest.get('n_annotations', 0)} 条注释（{manifest.get('built_at', '?')}）"
        )
        st.code("\n".join(str(x) for x in (manifest.get("files") or [])), language="text")
    else:
        st.info("该物种还没有生成过基因集")

    st.divider()

    # ---- ID 体检 + 映射预览 ----
    st.markdown("#### ④ ID 体检与映射预览（决定 id_strip）")
    st.caption(
        "ChIPseeker 输出的 geneId 与 gene2go/KEGG 的 ID 不是一套字串："
        "`LOC100808170`、`gene-LOC100808170`、`100808170` 三者必须靠 strip 规则对齐。"
    )
    anno_files = oh.find_peak_anno(BASE_DIR / "result", sp, ex, limit=5)
    ids_key = f"orgdb_ids_{wkey}"
    meta_key = f"orgdb_ids_meta_{wkey}"
    pv_key = f"orgdb_preview_{wkey}"
    if anno_files:
        st.caption("扫描文件：" + "、".join(f"`{Path(f).name}`" for f in anno_files))
        promoter_only = st.checkbox("只看 Promoter 区域（与 GSEA 的 promoter_only 对齐）",
                                   value=False, key=f"orgdb_prom_only_{wkey}")
        if st.button("扫描 geneId", key=f"orgdb_scan_ids_{wkey}"):
            ids, col, n_rows = _scan_ids_cached(tuple(str(f) for f in anno_files), promoter_only)
            st.session_state[ids_key] = ids
            st.session_state[meta_key] = {"col": col, "rows": n_rows}
            st.session_state.pop(pv_key, None)
    else:
        st.warning("还没找到 4_analyse/annoChIPPeaks 下的 *_peak_anno.csv（模块 4 还没跑完？）")
        st.caption("也可以先把任意来源的 geneId 粘进来，提前确认 strip 规则对不对：")
        pasted = st.text_area("粘贴 geneId（换行 / 逗号 / 斜杠分隔均可）", height=110,
                              key=f"orgdb_paste_{wkey}",
                              placeholder="LOC100808170\ngene-LOC100808171\n100808172")
        if st.button("用这些 ID 体检", key=f"orgdb_paste_btn_{wkey}"):
            got = oh.parse_id_text(pasted)
            if got:
                st.session_state[ids_key] = got
                st.session_state[meta_key] = {"col": "手动粘贴", "rows": len(got)}
                st.session_state.pop(pv_key, None)
            else:
                st.toast("没解析出任何 ID")
    ids = st.session_state.get(ids_key) or []
    if ids:
        meta = st.session_state.get(meta_key) or {}
        summary = oh.summarize_ids(ids)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("唯一 geneId", summary["n_total"])
        c2.metric("纯数字", summary["n_numeric"])
        c3.metric("LOC 前缀", summary["n_loc"] + summary["n_gene_loc"])
        c4.metric("带版本号", summary["n_with_version"])
        st.caption(f"列名 `{meta.get('col', '?')}` · 扫描 {meta.get('rows', 0)} 行 · 示例："
                   + "、".join(f"`{x}`" for x in summary["samples"]))
        if summary["prefix_top"]:
            st.caption("前缀分布：" + "、".join(f"{p} × {n}" for p, n in summary["prefix_top"]))

        suggest = oh.suggest_id_strip(ids)
        id_strip = st.text_input(
            "id_strip（正则，去掉这个前缀即得到 gene2go/KEGG 的 ID）",
            value=proj.get("id_strip") or suggest, key=f"orgdb_strip_{wkey}",
            help="例：^(gene-)?LOC → LOC100808170 / gene-LOC100808170 都变成 100808170",
        )
        st.caption(f"推荐值：`{suggest or '（无）'}`")

        if manifest and manifest.get("term2gene_go"):
            if st.button("预览映射命中率", key=f"orgdb_preview_btn_{wkey}"):
                pv = _preview_cached(tuple(ids), tuple(sorted(manifest["term2gene_go"].items())), id_strip)
                st.session_state[pv_key] = pv
            pv = st.session_state.get(pv_key)
            if pv and pv.get("id_strip") == id_strip:
                rows = []
                for ont, d in pv["per_ont"].items():
                    rows.append({
                        "GO 类别": ont,
                        "term 数": d["n_terms"],
                        "库内基因": d["n_genes_in_db"],
                        "命中": d["n_hit"],
                        "命中率": f"{d['hit_rate']*100:.1f}%",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
                worst = min((d["hit_rate"] for d in pv["per_ont"].values()), default=0.0)
                if worst < 0.2:
                    st.error("命中率过低：strip 规则大概写错了。先看下面的未命中示例对齐一下 ID 形状。")
                elif worst < 0.6:
                    st.warning("命中率偏低：确认 GFF 与 GTF 是否混用、LOC 前缀是否漏掉")
                else:
                    st.success("命中率正常，可以写入 config 并跑模块 5")
                miss = next(iter(pv["per_ont"].values()), {}).get("samples_miss") or []
                if miss:
                    st.caption("未命中示例：" + "、".join(f"`{x}`" for x in miss))

        st.markdown("---")
        cw1, cw2 = st.columns([1, 2])
        if cw1.button("写入 config", type="primary", key=f"orgdb_write_{wkey}"):
            fields: dict = {"id_strip": id_strip}
            if manifest and manifest.get("term2gene_go"):
                fields.update({
                    "term2gene_go": manifest["term2gene_go"],
                    "term2name_go": manifest.get("term2name_go") or {},
                    "universe": manifest.get("universe") or "",
                })
            if built:
                fields["orgdb"] = built[-1]["pkg"]
            elif cfg_orgdb:
                fields["orgdb"] = cfg_orgdb
            template = None
            if sel == MANUAL:
                template = {
                    "display_name": sp,
                    "modules": {"1_download": False, "2_QC": False, "3_ChIPseq": True,
                                "4_analyse": True, "5_gsea": True, "6_motif": False},
                }
            ok, msg = oh.update_project(CONFIG_PATH, sp, ex, fields,
                                        create_missing=(sel == MANUAL), template=template)
            if ok:
                ok2, msg2 = oh.enable_gsea_module(CONFIG_PATH, sp, ex, True)
                st.success(f"{msg}；{msg2}。到「配置编辑 → GSEA 参数」确认后即可跑模块 5")
                _scan_ids_cached.clear()
                _preview_cached.clear()
                time.sleep(0.6)
                st.rerun()
            else:
                st.error(msg)
        cw2.caption(
            "写入内容：项目的 `orgdb`（若已建包）+ `term2gene_go`/`term2name_go`/`universe` + `id_strip`，"
            "并顺手打开 `5_gsea` 开关；config 里没有这一条时会自动新建。"
        )


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

tab_cat, tab_custom, tab_orgdb, tab_tasks, tab_existing = st.tabs([
    f"物种 catalog ({len(SPECIES_DB)})",
    "自定义物种",
    "OrgDb 构建 / ID 映射",
    f"任务面板",
    f"已配置",
])

with tab_cat:
    render_catalog_tab()
with tab_custom:
    render_custom_tab()
with tab_orgdb:
    render_orgdb_tab()
with tab_tasks:
    render_tasks_tab()
with tab_existing:
    render_existing_tab()
