"""
结果预览页面 - 查看分析产出的图表和数据表格
"""

import streamlit as st
import os
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime

st.set_page_config(page_title="结果预览", page_icon="📊", layout="wide")
st.title("📊 结果预览")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


config = load_config()
projects = config.get("projects", [])

# 项目选择
project_names = [f"{p['species']}/{p['experiment']}" for p in projects]
selected_proj = st.selectbox("选择项目", project_names)

if selected_proj:
    species, experiment = selected_proj.split("/")
    result_dir = os.path.join(BASE_DIR, "result", species, experiment)

    if not os.path.isdir(result_dir):
        st.warning(f"结果目录不存在: `result/{species}/{experiment}/`")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["🔬 Peak Annotation", "📈 DeepTools Heatmap", "📁 所有输出文件"])

    # ============================================================
    # Tab 1: Peak Annotation 结果
    # ============================================================
    with tab1:
        anno_dir = os.path.join(result_dir, "4_analyse", "annoChIPPeaks")
        if os.path.isdir(anno_dir):
            # 列出所有 pair 目录
            pairs = sorted([d for d in os.listdir(anno_dir) if os.path.isdir(os.path.join(anno_dir, d))])

            if pairs:
                selected_pair = st.selectbox("选择 IP vs INPUT 对", pairs)
                pair_dir = os.path.join(anno_dir, selected_pair)

                col1, col2 = st.columns(2)

                # 查找 PDF 文件
                with col1:
                    st.markdown("**📊 Peak 注释饼图**")
                    pdf_files = [f for f in os.listdir(pair_dir) if f.endswith(".pdf")]
                    if pdf_files:
                        for pdf in pdf_files:
                            pdf_path = os.path.join(pair_dir, pdf)
                            st.caption(pdf)
                            # PDF 不能直接显示，提供下载
                            with open(pdf_path, "rb") as f:
                                st.download_button(
                                    f"⬇️ 下载 {pdf}",
                                    data=f.read(),
                                    file_name=pdf,
                                    mime="application/pdf",
                                    key=f"dl_{pdf}",
                                )

                    # 查找 PNG 图片
                    png_files = [f for f in os.listdir(pair_dir) if f.endswith((".png", ".jpg", ".jpeg"))]
                    for png in png_files:
                        st.image(os.path.join(pair_dir, png), caption=png)

                # 查找 CSV 数据
                with col2:
                    st.markdown("**📋 Peak 注释数据**")
                    csv_files = [f for f in os.listdir(pair_dir) if f.endswith(".csv")]
                    if csv_files:
                        for csv_file in csv_files:
                            csv_path = os.path.join(pair_dir, csv_file)
                            try:
                                df = pd.read_csv(csv_path)
                                st.caption(f"{csv_file} ({len(df)} rows)")
                                st.dataframe(df.head(50), use_container_width=True)

                                # 下载完整数据
                                st.download_button(
                                    f"⬇️ 下载完整 CSV",
                                    data=df.to_csv(index=False),
                                    file_name=csv_file,
                                    mime="text/csv",
                                    key=f"csv_{csv_file}",
                                )
                            except Exception as e:
                                st.error(f"读取 {csv_file} 失败: {e}")
                    else:
                        st.info("暂无 CSV 数据")

                # 合并结果
                st.markdown("---")
                st.markdown("**📊 所有样本汇总**")
                all_csv_path = os.path.join(anno_dir, "all_peaks_annotation.csv")
                if os.path.exists(all_csv_path):
                    try:
                        df_all = pd.read_csv(all_csv_path)
                        st.dataframe(df_all.head(100), use_container_width=True)
                    except Exception as e:
                        st.error(f"读取汇总数据失败: {e}")
                else:
                    st.info("汇总文件尚未生成")
            else:
                st.info("暂无 Peak Annotation 结果")
        else:
            st.info(f"目录不存在: `4_analyse/annoChIPPeaks/`")

    # ============================================================
    # Tab 2: DeepTools 热图
    # ============================================================
    with tab2:
        deeptools_dir = os.path.join(result_dir, "4_analyse", "deeptools")
        if os.path.isdir(deeptools_dir):
            # 查找所有图片文件
            image_files = []
            for root, dirs, files in os.walk(deeptools_dir):
                for f in files:
                    if f.endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf")):
                        image_files.append(os.path.relpath(os.path.join(root, f), deeptools_dir))

            if image_files:
                image_files.sort()
                st.markdown(f"共找到 **{len(image_files)}** 个图表文件")

                for img_file in image_files:
                    img_path = os.path.join(deeptools_dir, img_file)
                    st.markdown(f"---\n**{img_file}**")

                    if img_file.endswith((".png", ".jpg", ".jpeg")):
                        st.image(img_path, use_container_width=True)
                    elif img_file.endswith(".svg"):
                        with open(img_path, "r") as f:
                            svg_content = f.read()
                        st.markdown(svg_content, unsafe_allow_html=True)
                    elif img_file.endswith(".pdf"):
                        with open(img_path, "rb") as f:
                            st.download_button(
                                f"⬇️ 下载 {img_file}",
                                data=f.read(),
                                file_name=os.path.basename(img_file),
                                mime="application/pdf",
                                key=f"dt_{img_file}",
                            )

                # 查找 matrix 文件信息
                st.markdown("---")
                st.markdown("**📁 Matrix 文件**")
                matrix_files = []
                for root, dirs, files in os.walk(deeptools_dir):
                    for f in files:
                        if f.endswith((".gz", ".mat", ".npz")):
                            fpath = os.path.join(root, f)
                            size_mb = os.path.getsize(fpath) / (1024 * 1024)
                            mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M")
                            matrix_files.append({
                                "文件": os.path.relpath(fpath, deeptools_dir),
                                "大小 (MB)": f"{size_mb:.1f}",
                                "修改时间": mtime,
                            })
                if matrix_files:
                    st.dataframe(pd.DataFrame(matrix_files), use_container_width=True)
            else:
                st.info("暂无 DeepTools 图表输出")
        else:
            st.info(f"目录不存在: `4_analyse/deeptools/`")

    # ============================================================
    # Tab 3: 所有输出文件
    # ============================================================
    with tab3:
        st.markdown("**结果目录结构**")

        if os.path.isdir(result_dir):
            all_files = []
            for root, dirs, files in os.walk(result_dir):
                # 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    fpath = os.path.join(root, f)
                    rel_path = os.path.relpath(fpath, result_dir)
                    size_kb = os.path.getsize(fpath) / 1024
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M")
                    all_files.append({
                        "路径": rel_path,
                        "大小": f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                        "修改时间": mtime,
                    })

            if all_files:
                # 分类显示
                df_files = pd.DataFrame(all_files)
                df_files = df_files.sort_values("路径")

                # 过滤器
                filter_text = st.text_input("🔍 过滤文件路径", placeholder="输入关键词，例如: .pdf, deeptools, peak")
                if filter_text:
                    df_files = df_files[df_files["路径"].str.contains(filter_text, case=False)]

                st.dataframe(df_files, use_container_width=True, height=500)
                st.caption(f"共 {len(df_files)} 个文件")
            else:
                st.info("结果目录为空")
        else:
            st.warning("结果目录不存在")
