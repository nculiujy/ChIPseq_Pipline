"""
配置编辑页面 - 可视化编辑 config.yaml 和 metadata.csv
"""

import streamlit as st
import os
import yaml
import pandas as pd
import copy

st.set_page_config(page_title="配置编辑", page_icon="⚙️", layout="wide")
st.title("⚙️ 配置编辑")

# 路径定义
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
METADATA_PATH = os.path.join(BASE_DIR, "config", "metadata.csv")


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


# ============================================================
# Tab 布局
# ============================================================
tab1, tab2, tab3 = st.tabs(["🔧 全局参数", "🧬 项目管理", "📋 样本配对表"])

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

    # Broad cutoff 仅在 broad 模式下有意义
    broad_cutoff = None
    if peak_type == "broad":
        broad_cutoff = st.number_input("Broad cutoff", min_value=0.0001, max_value=1.0, value=float(config.get("macs2_broad_cutoff", 0.1)), format="%.4f")
    else:
        st.caption("ℹ️ Narrow 模式下无需 Broad cutoff 参数")

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
        color_map = st.selectbox(
            "热图配色",
            ["RdYlBu_r", "viridis", "plasma", "inferno", "magma", "coolwarm", "Blues", "Reds"],
            index=0,
        )

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
                st.text_input("物种", value=proj["species"], key=f"sp_{i}", disabled=True)
                st.text_input("实验名称", value=proj["experiment"], key=f"exp_{i}", disabled=True)
                st.text_input("原始数据目录", value=proj.get("rawdata_dir", ""), key=f"raw_{i}")
                st.text_input("Bowtie2 索引路径", value=proj.get("index_dir", ""), key=f"idx_{i}")
            with col2:
                st.text_input("TxDb", value=proj.get("txdb", ""), key=f"txdb_{i}")
                st.text_input("OrgDb", value=proj.get("orgdb", ""), key=f"orgdb_{i}")

                st.markdown("**模块开关：**")
                modules = proj.get("modules", {})
                m1 = st.checkbox("1_download", value=modules.get("1_download", False), key=f"m1_{i}")
                m2 = st.checkbox("2_QC", value=modules.get("2_QC", False), key=f"m2_{i}")
                m3 = st.checkbox("3_ChIPseq", value=modules.get("3_ChIPseq", False), key=f"m3_{i}")
                m4 = st.checkbox("4_analyse", value=modules.get("4_analyse", False), key=f"m4_{i}")

            if st.button(f"保存项目 {proj['species']}/{proj['experiment']}", key=f"save_proj_{i}"):
                projects[i]["rawdata_dir"] = st.session_state[f"raw_{i}"]
                projects[i]["index_dir"] = st.session_state[f"idx_{i}"]
                projects[i]["txdb"] = st.session_state[f"txdb_{i}"]
                projects[i]["orgdb"] = st.session_state[f"orgdb_{i}"]
                projects[i]["modules"] = {
                    "1_download": m1,
                    "2_QC": m2,
                    "3_ChIPseq": m3,
                    "4_analyse": m4,
                }
                config["projects"] = projects
                save_config(config)
                st.success(f"✅ 项目 {proj['species']}/{proj['experiment']} 已保存")

    st.markdown("---")
    st.subheader("添加新项目")
    with st.form("new_project_form"):
        col1, col2 = st.columns(2)
        with col1:
            new_species = st.selectbox("物种", ["TAIR", "homo", "mm"])
            new_experiment = st.text_input("实验名称")
            new_rawdata = st.text_input("原始数据目录")
            new_index = st.text_input("Bowtie2 索引路径")
        with col2:
            new_txdb = st.text_input("TxDb 包名")
            new_orgdb = st.text_input("OrgDb 包名")

        submitted = st.form_submit_button("➕ 添加项目")
        if submitted and new_experiment:
            new_proj = {
                "species": new_species,
                "experiment": new_experiment,
                "rawdata_dir": new_rawdata,
                "index_dir": new_index,
                "txdb": new_txdb,
                "orgdb": new_orgdb,
                "modules": {
                    "1_download": False,
                    "2_QC": False,
                    "3_ChIPseq": False,
                    "4_analyse": False,
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
