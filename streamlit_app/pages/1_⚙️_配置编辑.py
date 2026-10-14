"""
配置编辑页面 - 可视化编辑 config.yaml 和 metadata.csv
"""

import streamlit as st
import os
import re
import glob
import yaml
import pandas as pd
import copy

st.set_page_config(page_title="配置编辑", page_icon="⚙️", layout="wide")
st.title("⚙️ 配置编辑")

# 路径定义
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
METADATA_PATH = os.path.join(BASE_DIR, "config", "metadata.csv")
ANNO_DIR = os.path.join(BASE_DIR, "workflow", "anno")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config_data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_metadata():
    return pd.read_csv(METADATA_PATH)


def save_metadata(df):
    df.to_csv(METADATA_PATH, index=False)


def get_species_dirs():
    """获取 workflow/anno/ 下的物种目录"""
    if not os.path.isdir(ANNO_DIR):
        return []
    return sorted([
        d for d in os.listdir(ANNO_DIR)
        if os.path.isdir(os.path.join(ANNO_DIR, d))
    ])


def get_bowtie2_dirs(species):
    """获取指定物种目录下 bowtie2 开头的文件夹"""
    species_dir = os.path.join(ANNO_DIR, species)
    if not os.path.isdir(species_dir):
        return []
    return sorted([
        d for d in os.listdir(species_dir)
        if os.path.isdir(os.path.join(species_dir, d)) and d.lower().startswith("bowtie2")
    ])


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
# Tab 布局
# ============================================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🔧 全局参数",
    "🧬 项目管理",
    "📋 样本配对表",
    "📍 ChIP 注释",
    "🔬 GSEA 参数",
    "🧬 Motif 参数",
])

# ============================================================
# Tab 1: 全局参数
# ============================================================
with tab1:
    config = load_config()

    st.subheader("基本参数")
    col1, col2 = st.columns(2)
    with col1:
        picard_dir = st.text_input("Picard 路径", value=config.get("picard_dir", ""))
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
            st.caption("ℹ️ Narrow 模式下无需 Broad cutoff 参数")
    
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
            "🔵 经典对比": {
                "RdYlBu_r":   ["#313695","#4575b4","#74add1","#abd9e9","#ffffbf","#fdae61","#f46d43","#d73027"],
                "coolwarm":   ["#3b4cc0","#6f92f3","#b8d0f9","#f7f7f7","#f5c4ac","#e0735a","#b40426"],
                "RdBu_r":     ["#053061","#2166ac","#92c5de","#f7f7f7","#f4a582","#d6604d","#67001f"],
            },
            "🌈 渐变单色": {
                "viridis":    ["#440154","#414487","#2a788e","#22a884","#7ad151","#fde725"],
                "plasma":     ["#0d0887","#7e03a8","#cc4778","#f89441","#f0f921"],
                "magma":      ["#000004","#3b0f70","#8c2981","#de4968","#fea16e","#fcfdbf"],
                "inferno":    ["#000004","#420a68","#932667","#dd513a","#fca50a","#fcffa4"],
                "Blues":      ["#f7fbff","#c6dbef","#6baed6","#2171b5","#08306b"],
                "Greens":     ["#f7fcf5","#c7e9c0","#74c476","#238b45","#00441b"],
                "Reds":       ["#fff5f0","#fcbba1","#fb6a4a","#cb181d","#67000d"],
            },
            "🌸 软色调": {
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
                "🌸 " + x if x in COLOR_MAP_GROUPS.get("🌸 软色调", {}) else
                "🌈 " + x if x in COLOR_MAP_GROUPS.get("🌈 渐变单色", {}) else
                "🔵 " + x
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
                    st.caption(f"📄 路径: `{dt_file_path}`")
                else:
                    st.warning(f"⚠️ 在 `workflow/anno/{dt_species}/{dt_bowtie2_dir}/` 下未找到 {file_label}")
                    dt_file_path = st.text_input(f"手动输入 {file_label} 路径", value=dt.get("custom_bed", "") if bed_source == "custom" else dt.get("gtf_file", ""))
            else:
                st.warning(f"⚠️ 在 `workflow/anno/{dt_species}/` 下未找到 bowtie2 开头的目录")
                dt_file_path = ""
        else:
            st.warning("⚠️ 未找到 `workflow/anno/` 下的物种目录")
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
    if st.button("💾 保存全局配置", type="primary", key="save_global"):
        # 重新读取最新 config，避免覆盖其他 Tab 的修改
        new_config = load_config()
        new_config["picard_dir"] = picard_dir
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
        st.success("✅ 全局配置已保存！")
        st.info("💡 提示：由于使用了 `ancient()` 包装，修改 config.yaml 不会触发已完成模块的重跑。")

# ============================================================
# Tab 2: 项目管理
# ============================================================
with tab2:
    config = load_config()
    projects = config.get("projects", [])

    st.subheader("现有项目")

    for i, proj in enumerate(projects):
        with st.expander(f"📁 {proj['species']}/{proj['experiment']}", expanded=False):
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
                )

                st.text_input("实验名称", value=proj["experiment"], key=f"exp_{i}")
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
                        st.caption(f"✅ 索引前缀: `{index_prefix}`")
                    else:
                        st.warning("⚠️ 未在该目录下找到 .bt2 文件")
                        final_index_dir = current_index
                else:
                    st.warning(f"⚠️ `workflow/anno/{selected_species}/` 下无 bowtie2 开头的目录")
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
                if st.button(f"💾 保存项目", key=f"save_proj_{i}"):
                    # 重新读取最新 config 以避免被其他 Tab 的旧缓存覆盖
                    fresh_config = load_config()
                    fresh_projects = fresh_config.get("projects", [])
                    if i < len(fresh_projects):
                        fresh_projects[i]["species"] = selected_species
                        fresh_projects[i]["experiment"] = st.session_state[f"exp_{i}"]
                        fresh_projects[i]["rawdata_dir"] = st.session_state[f"raw_{i}"]
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
                        st.success(f"✅ 项目 {selected_species}/{st.session_state[f'exp_{i}']} 已保存")
                        st.rerun()
            with btn_col2:
                if st.button(f"🗑️ 删除", key=f"del_proj_{i}", type="secondary"):
                    fresh_config = load_config()
                    fresh_config["projects"].pop(i)
                    save_config(fresh_config)
                    st.success(f"✅ 项目 {proj['species']}/{proj['experiment']} 已删除")
                    st.rerun()

    st.markdown("---")
    st.subheader("添加新项目")

    # 新项目：物种选择
    available_species_new = get_species_dirs()
    if not available_species_new:
        available_species_new = ["TAIR10", "Glycine_max_v4", "homo", "mouse"]

    new_species = st.selectbox("物种", available_species_new, key="new_sp")

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
            st.caption(f"✅ 索引前缀: `{new_index_prefix}`")
        else:
            new_index_value = ""
            st.warning("⚠️ 未在该目录下找到 .bt2 文件")
    else:
        st.warning(f"⚠️ `workflow/anno/{new_species}/` 下无 bowtie2 开头的目录")
        new_index_value = st.text_input("手动输入 Bowtie2 索引路径", key="new_idx_manual")

    with st.form("new_project_form"):
        col1, col2 = st.columns(2)
        with col1:
            new_experiment = st.text_input("实验名称")
            new_rawdata = st.text_input("原始数据目录")
            st.caption(f"📄 Bowtie2 索引: `{new_index_value}`")
        with col2:
            new_txdb = st.text_input(
                "TxDb（R 包名或本地 GFF3/GTF 路径）",
                help="填 Bioconductor TxDb 包名，或直接填 GFF3/GTF 文件路径（脚本自动识别）",
            )
            new_orgdb = st.text_input(
                "OrgDb（R 包名，可留空）",
                help="基因符号注释与富集分析用；大豆等无官方 OrgDb 的物种可留空（注释仍可用，GO/KEGG 富集将跳过）",
            )

        submitted = st.form_submit_button("➕ 添加项目")
        if submitted and new_experiment:
            new_proj = {
                "species": new_species,
                "experiment": new_experiment,
                "rawdata_dir": new_rawdata,
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
            st.success(f"✅ 项目 {new_species}/{new_experiment} 已添加！")
            st.rerun()

# ============================================================
# Tab 3: 样本配对表
# ============================================================
with tab3:
    st.subheader("IP vs Input 样本配对表（含重复组）")
    st.caption(f"文件路径: `{METADATA_PATH}`")

    st.info(
        "**group 列说明：** 同一 group 名称的样本被视为生物学重复。"
        "Pipeline 会自动对同组样本取 Consensus Peaks（重叠区域），"
        "并用 deeptools 做重复间相关性分析。留空则每对样本独立分析。"
    )

    if os.path.exists(METADATA_PATH):
        df = load_metadata()
        # 兼容旧格式：如果没有 group 列，自动添加
        if "group" not in df.columns:
            df["group"] = ""

        # 配置列顺序
        col_order = ["IP sample", "Input", "group"]
        df = df[[c for c in col_order if c in df.columns]]

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "IP sample": st.column_config.TextColumn("IP 样本", help="ChIP/实验组样本名"),
                "Input": st.column_config.TextColumn("Input 对照", help="对照组样本名"),
                "group": st.column_config.TextColumn(
                    "重复组",
                    help="相同组名的样本为生物学重复，留空表示独立分析",
                ),
            },
            key="metadata_editor",
        )

        # 显示重复组统计
        if "group" in edited_df.columns:
            groups = edited_df["group"].dropna().replace("", pd.NA).dropna()
            if len(groups) > 0:
                group_counts = groups.value_counts()
                st.markdown("**重复组统计：**")
                for grp, cnt in group_counts.items():
                    st.caption(f"  • `{grp}`: {cnt} 个样本对")
            else:
                st.caption("📌 当前未设置重复组，所有样本对将独立分析")

        # ---- Consensus Peaks 参数 ----
        st.markdown("---")
        st.markdown("#### Consensus Peaks 参数")
        st.caption("当同一 group 有多个重复时，取重叠 peaks 作为 consensus")
        st.caption("✅ Consensus peaks 将自动进行 ChIPseeker 注释（饼图 + CSV），结果在「结果预览」→「Peak Annotation」→「🔗 Consensus」中查看")

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
        st.markdown("#### 🔬 DeepTools 组间比较 (Comparisons)")
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
            st.warning("⚠️ 请先在上方配对表中设置 group 列，才能配置组间比较")
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
                        st.text(f"📊 {comp_name}")
                    with col2:
                        st.caption(f"groups: {' vs '.join(comp_groups)}")
                    with col3:
                        if st.button("🗑️", key=f"del_comp_{idx}"):
                            to_delete = idx

                # 执行删除（在循环外操作以避免 rerun 问题）
                if to_delete is not None:
                    removed = st.session_state.comparisons_list.pop(to_delete)
                    st.success(f"✅ 已删除比较: {removed.get('name', '')}")
                    st.rerun()
            else:
                st.caption("📌 当前无比较项，请在下方添加")

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

            if st.button("➕ 添加比较", key="add_comparison"):
                if new_comp_name and len(new_comp_groups) >= 1:
                    st.session_state.comparisons_list.append({
                        "name": new_comp_name,
                        "groups": new_comp_groups,
                    })
                    label = ' vs '.join(new_comp_groups) if len(new_comp_groups) > 1 else new_comp_groups[0]
                    st.success(f"✅ 已添加比较: {new_comp_name} ({label})")
                    st.rerun()
                elif not new_comp_name:
                    st.error("❌ 请输入比较名称")
                else:
                    st.error("❌ 请至少选择 1 个 group")

        # ---- 统一保存按钮 ----
        st.markdown("---")
        if st.button("💾 保存本页所有设置", type="primary", key="save_tab3_all"):
            # 1. 保存 metadata.csv
            save_metadata(edited_df)

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
            st.success("✅ 样本配对表、Consensus 参数、Comparisons 已全部保存！")
    else:
        st.warning("未找到 metadata.csv 文件")
        if st.button("创建空模板"):
            pd.DataFrame({"IP sample": [], "Input": [], "group": []}).to_csv(METADATA_PATH, index=False)
            st.success("已创建空模板（含 group 列）")
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

    st.subheader("📍 ChIPseeker Peak 注释参数")

    st.markdown("#### 分析逻辑")
    st.info(
        "**基于 ChIPseeker 的 Peak 注释与可视化：**\n\n"
        "1. 读取 MACS2 peaks（或重复组 consensus peaks）\n"
        "2. 使用 TxDb 定义基因结构，将每个 peak 归类到 "
        "Promoter / 5'UTR / 3'UTR / Exon / Intron / Downstream / Intergenic\n"
        "3. 使用 OrgDb（可选）将 geneId 映射为 gene symbol\n"
        "4. 输出注释表 + 饼图 / 柱状图 / TSS 距离分布 / TSS 平均信号曲线 等可视化\n\n"
        "🔗 TxDb / OrgDb 在「项目管理」中为每个项目单独配置；本 Tab 配置**注释规则与可视化输出**。"
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
    st.markdown("#### 1️⃣ Promoter 区域定义 (tssRegion)")
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
    st.markdown("#### 2️⃣ Genomic Annotation Priority")
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
        st.warning("⚠️ 至少选择一个类别，否则保存时将回退到默认优先级")
        annotation_priority = DEFAULT_PRIORITY
    st.caption(f"当前优先级: {' > '.join(annotation_priority)}")

    # 关键提示：5'UTR / 3'UTR 独立分类的可见性依赖 priority 顺序
    st.info(
        "💡 **想看到独立的 5'UTR / 3'UTR 分类？** 把 `5UTR` / `3UTR` 拖到 `Promoter` 前面。\n\n"
        "ChIPseeker 按此列表顺序“先到先得”：Promoter 区域（TSS ±3kb）与 5'UTR 高度重叠，"
        "若 Promoter 在前，大量 5'UTR peak 会被优先归为 Promoter，导致饼图里 5'UTR 计数为 0 或很少。\n\n"
        "推荐顺序（想突出 UTR）：`5UTR > 3UTR > Promoter > Exon > Intron > Downstream > Intergenic`"
    )

    # ---- 3. 注释粒度 ----
    st.markdown("---")
    st.markdown("#### 3️⃣ 注释粒度")
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
    st.markdown("#### 4️⃣ 高级选项 (options 与 annotatePeak 参数)")
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
    st.markdown("#### 5️⃣ 输出可视化")
    st.caption("所有输出位于 `result/<species>/<experiment>/4_analyse/annoChIPPeaks/<pair>/`；结果预览页会自动扫描")
    outputs_cfg = cs.get("outputs", {}) or {}
    col1, col2, col3 = st.columns(3)
    with col1:
        out_pie = st.checkbox(
            "饼图 (plotAnnoPie) 🔒",
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
            "完整注释表 CSV 🔒",
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
    st.markdown("#### 🔍 保存前预览")
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
    with st.expander("📄 config.yaml 中的 `chipseeker:` 节内容", expanded=False):
        st.code(yaml.dump({"chipseeker": _preview}, allow_unicode=True, sort_keys=False), language="yaml")

    # ---- 7. 保存按钮 ----
    if st.button("💾 保存 ChIP 注释参数", type="primary", key="save_chipseeker"):
        fresh_cfg = load_config()
        fresh_cfg["chipseeker"] = _preview
        save_config(fresh_cfg)
        st.success("✅ ChIPseeker 注释参数已保存到 `config/config.yaml`！")
        st.caption(
            f"tssRegion: [{tss_upstream}, {tss_downstream}] | flankDistance: {flank_distance} | "
            f"level: {level} | priority: {' > '.join(annotation_priority)}"
        )
        st.info(
            "💡 提示：修改后需重跑模块 4（annoChIPPeaks / anno_consensus）才能生效。\n"
            "若已完成的注释结果使用了 `ancient()` 包装的 config，Snakemake 不会自动重跑，需手动删除对应的输出文件或使用 `--forceall`。"
        )

# ============================================================
# Tab 5: GSEA 参数
# ============================================================
with tab5:
    config = load_config()
    gsea = config.get("gsea", {})

    st.subheader("🔬 模块5：GSEA 富集分析参数")

    # ---- 数据来源说明 ----
    st.markdown("#### 分析逻辑")
    st.info(
        "**基于重复组 Consensus Peaks 的富集分析：**\n\n"
        "1. 读取 metadata.csv 中的 **group** 列，识别各重复组\n"
        "2. 每个 group 的 consensus peaks（模块4生成）进行 ChIPseeker 注释\n"
        "3. 提取注释到的基因列表，做 GO 和 KEGG 富集分析\n"
        "4. 每个 group 独立输出富集结果（柱状图 + 点图 + CSV）\n\n"
        "⚠️ 需要先运行模块4（含 consensus peaks 步骤）才能执行 GSEA"
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
                st.warning("⚠️ metadata.csv 中未设置 group 列，请在「样本配对表」中配置重复组")
        else:
            st.warning("⚠️ metadata.csv 中缺少 group 列，请在「样本配对表」中添加")
    else:
        st.warning("⚠️ 未找到 metadata.csv")

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
            st.warning("⚠️ 请至少选择一种 GO 类型")
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
    if st.button("💾 保存 GSEA 参数", type="primary", key="save_gsea"):
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
        st.success("✅ GSEA 参数已保存到 config/config.yaml！")
        st.caption(
            f"GO 类型: {', '.join(go_types_selected)} | "
            f"p < {pvalue_cutoff} | q < {qvalue_cutoff} | "
            f"基因集大小: {min_gs_size}–{max_gs_size} | Top {top_n}"
        )

# ============================================================
# Tab 6: Motif 参数
# ============================================================
with tab6:
    config = load_config()
    motif_cfg = config.get("motif", {})

    st.subheader("🧬 模块6：MEME-ChIP Motif 富集分析参数")

    st.markdown("#### 分析逻辑")
    st.info(
        "**基于 Peaks 的 Motif 发现与富集分析：**\n\n"
        "1. 从 consensus peaks（或单样本 peaks）中心提取 ±flank_size bp 的基因组序列\n"
        "2. 调用 MEME-ChIP 进行从头 motif 发现 (MEME/STREME)\n"
        "3. 与已知 motif 数据库比对 (TOMTOM)\n"
        "4. 分析 motif 在 peaks 中的位置富集 (CentriMo)\n\n"
        "⚠️ 需要先运行模块4（含 consensus peaks 步骤）才能执行 Motif 分析\n\n"
        "💡 首次运行时会自动下载 MEME motif 数据库（~200MB），后续复用"
    )

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
            "Motif 数据库路径（留空自动下载）",
            value=motif_cfg.get("meme_db", ""),
            help="指定 .meme 格式的 motif 数据库文件。留空则根据物种自动选择默认数据库（首次运行时自动下载）",
        )
        st.caption("📥 数据库下载地址: https://meme-suite.org/meme/doc/download.html")

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
    if st.button("💾 保存 Motif 参数", type="primary", key="save_motif"):
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
        st.success("✅ Motif 参数已保存到 config/config.yaml！")
