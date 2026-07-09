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
tab1, tab2, tab3, tab4 = st.tabs(["🔧 全局参数", "🧬 项目管理", "📋 样本配对表", "🔬 GSEA 参数"])

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
            "人类 (hs)": "hs",
            "小鼠 (mm)": "mm",
            "自定义": "custom"
        }
        current_genome = config.get("macs2_genome_size", "auto")
        if current_genome in genome_options.values():
            genome_idx = list(genome_options.values()).index(current_genome)
        else:
            genome_idx = 4  # 自定义
        
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

    with col2:
        bed_source = st.selectbox(
            "BED 文件来源",
            ["macs2_peaks", "union_peaks", "custom", "genes"],
            index=["macs2_peaks", "union_peaks", "custom", "genes"].index(dt.get("bed_source", "union_peaks")),
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
        new_config = copy.deepcopy(config)
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
            new_config["macs2_broad_cutoff"] = config.get("macs2_broad_cutoff", 0.1)

        # DeepTools
        new_config["deeptools"] = dt.copy() if dt else {}
        new_config["deeptools"]["matrix_mode"] = matrix_mode
        new_config["deeptools"]["bin_size"] = int(bin_size)
        new_config["deeptools"]["missing_data_as_zero"] = missing_zero
        new_config["deeptools"]["bed_source"] = bed_source
        if "plot" not in new_config["deeptools"]:
            new_config["deeptools"]["plot"] = {}
        new_config["deeptools"]["plot"]["color_map"] = color_map

        # 保存 BED/GFF 路径
        if bed_source == "custom" and dt_file_path:
            new_config["deeptools"]["custom_bed"] = dt_file_path
        elif bed_source == "genes" and dt_file_path:
            new_config["deeptools"]["gtf_file"] = dt_file_path

        if matrix_mode == "reference-point":
            new_config["deeptools"]["reference_point"] = {
                "point": ref_point,
                "upstream": int(rp_upstream),
                "downstream": int(rp_downstream),
            }
        else:
            new_config["deeptools"]["scale_regions"] = {
                "region_length": int(sr_length),
                "upstream": int(sr_upstream),
                "downstream": int(sr_downstream),
                "unscaled_5prime": 0,
                "unscaled_3prime": 0,
            }

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
                st.text_input("TxDb", value=proj.get("txdb", ""), key=f"txdb_{i}")
                st.text_input("OrgDb", value=proj.get("orgdb", ""), key=f"orgdb_{i}")

                st.markdown("**模块开关：**")
                modules = proj.get("modules", {})
                m1 = st.checkbox("1_download", value=modules.get("1_download", False), key=f"m1_{i}")
                m2 = st.checkbox("2_QC", value=modules.get("2_QC", False), key=f"m2_{i}")
                m3 = st.checkbox("3_ChIPseq", value=modules.get("3_ChIPseq", False), key=f"m3_{i}")
                m4 = st.checkbox("4_analyse", value=modules.get("4_analyse", False), key=f"m4_{i}")
                m5 = st.checkbox("5_gsea", value=modules.get("5_gsea", False), key=f"m5_{i}")

            if st.button(f"保存项目 {proj['species']}/{proj['experiment']}", key=f"save_proj_{i}"):
                projects[i]["species"] = selected_species
                projects[i]["experiment"] = st.session_state[f"exp_{i}"]
                projects[i]["rawdata_dir"] = st.session_state[f"raw_{i}"]
                projects[i]["index_dir"] = final_index_dir
                projects[i]["txdb"] = st.session_state[f"txdb_{i}"]
                projects[i]["orgdb"] = st.session_state[f"orgdb_{i}"]
                projects[i]["modules"] = {
                    "1_download": m1,
                    "2_QC": m2,
                    "3_ChIPseq": m3,
                    "4_analyse": m4,
                    "5_gsea": m5,
                }
                config["projects"] = projects
                save_config(config)
                st.success(f"✅ 项目 {selected_species}/{st.session_state[f'exp_{i}']} 已保存")

    st.markdown("---")
    st.subheader("添加新项目")

    # 新项目：物种选择
    available_species_new = get_species_dirs()
    if not available_species_new:
        available_species_new = ["TAIR10", "homo", "mouse"]

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
            new_txdb = st.text_input("TxDb 包名")
            new_orgdb = st.text_input("OrgDb 包名")

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
                },
            }
            config["projects"].append(new_proj)
            save_config(config)
            st.success(f"✅ 项目 {new_species}/{new_experiment} 已添加！")
            st.rerun()

# ============================================================
# Tab 3: 样本配对表
# ============================================================
with tab3:
    st.subheader("IP vs Input 样本配对表")
    st.caption(f"文件路径: `{METADATA_PATH}`")

    if os.path.exists(METADATA_PATH):
        df = load_metadata()
        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key="metadata_editor",
        )

        if st.button("💾 保存样本配对表", type="primary", key="save_metadata"):
            save_metadata(edited_df)
            st.success("✅ 样本配对表已保存！")
    else:
        st.warning("未找到 metadata.csv 文件")
        if st.button("创建空模板"):
            pd.DataFrame({"IP sample": [], "Input": []}).to_csv(METADATA_PATH, index=False)
            st.success("已创建空模板")
            st.rerun()

# ============================================================
# Tab 4: GSEA 参数
# ============================================================
with tab4:
    config = load_config()
    gsea = config.get("gsea", {})

    st.subheader("🔬 模块5：GSEA 富集分析全局参数")

    # ---- 数据来源：基于已激活项目的样本选择 ----
    st.markdown("#### 数据来源")
    projects = config.get("projects", [])
    # 筛选出模块开关中 5_gsea 已开启的项目（即"已激活"项目）
    active_projects = [
        p for p in projects
        if p.get("modules", {}).get("5_gsea", False)
    ]

    if active_projects:
        st.success(f"当前已激活 GSEA 的项目共 {len(active_projects)} 个")
        # 列出激活的项目供选择
        proj_labels = [f"{p['species']}/{p['experiment']}" for p in active_projects]
        selected_proj_labels = st.multiselect(
            "选择参与 GSEA 分析的项目",
            proj_labels,
            default=proj_labels,
            help="从已激活 5_gsea 的项目中选择要纳入分析的项目",
        )
        # 进一步显示各项目下的样本对
        for label in selected_proj_labels:
            sp, exp = label.split("/", 1)
            anno_dir = os.path.join(BASE_DIR, "result", sp, exp, "4_analyse", "peakcalling")
            if os.path.isdir(anno_dir):
                # 扫描可用的 annotation 结果
                anno_csvs = glob.glob(os.path.join(anno_dir, "*", "*_peak_anno.csv"))
                sample_pairs = sorted(set(
                    os.path.basename(os.path.dirname(f)) for f in anno_csvs
                ))
                if sample_pairs:
                    st.caption(f"📁 `{label}` 下可用样本对: {', '.join(sample_pairs)}")
                else:
                    st.caption(f"📁 `{label}` 下暂无 annotation 结果（需先运行模块4）")
            else:
                st.caption(f"📁 `{label}` 结果目录尚不存在")
    else:
        st.warning("⚠️ 当前无项目激活了 5_gsea 模块。请在「项目管理」中开启。")
        selected_proj_labels = []

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
        config["gsea"] = {
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
        save_config(config)
        st.success("✅ GSEA 参数已保存到 config/config.yaml！")
        st.caption(
            f"GO 类型: {', '.join(go_types_selected)} | "
            f"p < {pvalue_cutoff} | q < {qvalue_cutoff} | "
            f"基因集大小: {min_gs_size}–{max_gs_size} | Top {top_n}"
        )
