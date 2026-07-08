"""
结果预览页面 - 查看分析产出的图表和数据表格
"""

import streamlit as st
import os
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime
from PIL import Image

st.set_page_config(page_title="结果预览", page_icon="📊", layout="wide")
st.title("📊 结果预览")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pdf_to_images(pdf_path):
    """尝试将 PDF 转换为图片以供预览（需要 pdf2image 库）"""
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, first_page=1, last_page=3, dpi=150)
        return images
    except ImportError:
        return None
    except Exception as e:
        st.warning(f"PDF 转换失败: {e}")
        return None


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

    tab1, tab2, tab3, tab4 = st.tabs(["📊 比对率统计", "🔬 Peak Annotation", "📈 DeepTools Heatmap", "📁 所有输出文件"])

    # ============================================================
    # Tab 1: 比对率统计
    # ============================================================
    with tab1:
        st.markdown("### Bowtie2 比对率统计")
        
        # 读取 3_1_ChIPseq.log 文件提取比对率
        chipseq_log = os.path.join(BASE_DIR, "logs", species, experiment, "3_1_ChIPseq.log")
        
        if os.path.exists(chipseq_log):
            import re
            
            alignment_data = []
            
            try:
                with open(chipseq_log, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                # 正则匹配模式：
                # 第一行: reads 数量
                # 第二行: paired 百分比
                # 第三行: aligned concordantly 0 times
                # 第四行: aligned concordantly exactly 1 time
                # 第五行: aligned concordantly >1 times
                # 第六行: overall alignment rate
                
                pattern = r'(\d+) reads; of these:\s+\d+ \([\d.]+%\) were paired; of these:\s+(\d+) \([\d.]+%\) aligned concordantly 0 times\s+(\d+) \([\d.]+%\) aligned concordantly exactly 1 time\s+(\d+) \([\d.]+%\) aligned concordantly >1 times\s+([\d.]+)% overall alignment rate'
                
                matches = re.findall(pattern, content)
                
                # 同时提取样本名
                sample_pattern = r'\[[\d\-:\s]+\] 执行 Bowtie2 比对: (\S+)'
                samples = re.findall(sample_pattern, content)
                
                if matches and samples:
                    for i, (total_reads, unaligned, aligned_once, aligned_multi, rate) in enumerate(matches):
                        if i < len(samples):
                            alignment_data.append({
                                "样本": samples[i],
                                "总 Reads 数": int(total_reads),
                                "比对率 (%)": float(rate),
                                "未比对": int(unaligned),
                                "唯一比对": int(aligned_once),
                                "多位点比对": int(aligned_multi)
                            })
                
                if alignment_data:
                    df_align = pd.DataFrame(alignment_data)
                    
                    # 显示表格
                    st.dataframe(df_align, use_container_width=True)
                    
                    # 绘制柱状图
                    st.markdown("---")
                    st.markdown("**比对率柱状图**")
                    
                    import plotly.express as px
                    
                    fig = px.bar(
                        df_align,
                        x="样本",
                        y="比对率 (%)",
                        text="比对率 (%)",
                        title=f"{species}/{experiment} — Bowtie2 比对率",
                        color="比对率 (%)",
                        color_continuous_scale="RdYlGn",
                        range_color=[0, 100]
                    )
                    
                    fig.update_traces(texttemplate='%{text:.2f}%', textposition='outside')
                    fig.update_layout(
                        xaxis_title="样本名称",
                        yaxis_title="比对率 (%)",
                        yaxis_range=[0, 100],
                        showlegend=False,
                        height=500
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # 下载按钮
                    st.markdown("---")
                    csv_data = df_align.to_csv(index=False, encoding="utf-8-sig")
                    st.download_button(
                        "⬇️ 下载比对率统计表",
                        data=csv_data,
                        file_name=f"{species}_{experiment}_alignment_stats.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("⚠️ 日志中未找到比对率信息（可能任务未完成或已中断）")
            
            except Exception as e:
                st.error(f"解析日志文件失败: {e}")
        else:
            st.info(f"📌 日志文件不存在: `logs/{species}/{experiment}/3_1_ChIPseq.log`")
            st.caption("运行 3_ChIPseq 模块后会生成该日志")

    # ============================================================
    # Tab 2: Peak Annotation 结果
    # ============================================================
    with tab2:
        anno_dir = os.path.join(result_dir, "4_analyse", "annoChIPPeaks")
        if os.path.isdir(anno_dir):
            # 列出所有 pair 目录
            pairs = sorted([d for d in os.listdir(anno_dir) if os.path.isdir(os.path.join(anno_dir, d))])

            if pairs:
                selected_pair = st.selectbox("选择 IP vs INPUT 对", pairs)
                pair_dir = os.path.join(anno_dir, selected_pair)

                col1, col2 = st.columns(2)

                # 查找图表文件
                with col1:
                    st.markdown("**📊 Peak 注释可视化**")
                    
                    # PDF 文件
                    pdf_files = [f for f in os.listdir(pair_dir) if f.endswith(".pdf")]
                    for pdf in pdf_files:
                        pdf_path = os.path.join(pair_dir, pdf)
                        st.caption(f"📄 {pdf}")
                        
                        # 尝试预览 PDF
                        with st.expander("🔍 预览 PDF", expanded=True):
                            pdf_images = pdf_to_images(pdf_path)
                            if pdf_images:
                                for idx, img in enumerate(pdf_images[:3]):  # 最多显示前3页
                                    st.image(img, caption=f"第 {idx+1} 页", use_container_width=True)
                                if len(pdf_images) > 3:
                                    st.caption(f"...（共 {len(pdf_images)} 页，仅显示前 3 页）")
                            else:
                                st.info("💡 安装 `pdf2image` 和 `poppler-utils` 可预览 PDF 文件")
                        
                        # 下载按钮
                        with open(pdf_path, "rb") as f:
                            st.download_button(
                                f"⬇️ 下载 {pdf}",
                                data=f.read(),
                                file_name=pdf,
                                mime="application/pdf",
                                key=f"dl_{pdf}",
                            )

                    # PNG/JPG 图片
                    image_files = [f for f in os.listdir(pair_dir) if f.endswith((".png", ".jpg", ".jpeg"))]
                    if image_files:
                        for img_file in image_files:
                            img_path = os.path.join(pair_dir, img_file)
                            st.caption(f"🖼️ {img_file}")
                            try:
                                img = Image.open(img_path)
                                st.image(img, use_container_width=True)
                            except Exception as e:
                                st.error(f"无法加载图片 {img_file}: {e}")
                    
                    if not pdf_files and not image_files:
                        st.info("暂无可视化文件")

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
    # Tab 3: DeepTools 热图
    # ============================================================
    with tab3:
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
                    st.markdown(f"---\n**📊 {img_file}**")

                    if img_file.endswith((".png", ".jpg", ".jpeg")):
                        try:
                            img = Image.open(img_path)
                            st.image(img, use_container_width=True)
                        except Exception as e:
                            st.error(f"无法加载图片: {e}")
                    elif img_file.endswith(".svg"):
                        try:
                            with open(img_path, "r", encoding="utf-8") as f:
                                svg_content = f.read()
                            st.markdown(svg_content, unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"无法加载 SVG: {e}")
                    elif img_file.endswith(".pdf"):
                        # PDF 预览
                        with st.expander("🔍 预览 PDF", expanded=False):
                            pdf_images = pdf_to_images(img_path)
                            if pdf_images:
                                for idx, pimg in enumerate(pdf_images[:3]):
                                    st.image(pimg, caption=f"第 {idx+1} 页", use_container_width=True)
                                if len(pdf_images) > 3:
                                    st.caption(f"...（共 {len(pdf_images)} 页，仅显示前 3 页）")
                            else:
                                st.info("💡 安装 `pdf2image` 和 `poppler-utils` 可预览 PDF")
                        
                        # 下载按钮
                        with open(img_path, "rb") as f:
                            st.download_button(
                                f"⬇️ 下载 {os.path.basename(img_file)}",
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
    # Tab 4: 所有输出文件
    # ============================================================
    with tab4:
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

