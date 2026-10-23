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

import sys

# 让 streamlit_app/utils 可导入（控制台主题）
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from utils.ui_theme import inject_theme, page_header, render_sidebar  # noqa: E402

from utils.version import APP_FULL_NAME  # noqa: E402
from utils import genome_browser  # noqa: E402

# plotly 只用来画比对率柱状图。启动器环境万一没装（早期版本的 launch 环境就是这么
# 建的），退回 st.bar_chart 也比整页报错强——所以在这里探一次，而不是放在 try 里：
# 放在下面那个 try 里时，ImportError 会被当成「日志解析失败」，把已经解析好的表格
# 和下载按钮一起吃掉。
try:
    import plotly.express as px
except ImportError:  # pragma: no cover - 取决于部署环境
    px = None

st.set_page_config(page_title=f"结果预览 · {APP_FULL_NAME}", layout="wide")
inject_theme()
render_sidebar("preview")
page_header("结果预览", "浏览分析产出：比对统计、Peak 注释、热图、富集与 Motif")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pdf_to_images(pdf_path):
    """把 PDF 前 3 页渲成图片，供 st.image 预览。

    优先 PyMuPDF（import 名 fitz）：单 wheel 自带渲染引擎，不依赖任何系统二进制。
    旧方案 pdf2image 要配 poppler 的 pdftoppm，而这两样当初都装进了 analysis 环境，
    UI 却跑在 launch 环境，import 必然失败——这就是预览一直提示“安装 pdf2image
    和 poppler-utils”的原因（包是装了的，只是装错了环境）。
    pdf2image 仅作回退，兼容老环境里恰好装好的情况。
    """
    # 1) PyMuPDF（现役方案，依赖在 CHIPSEQ_LAUNCHER_PKGS 里）
    # >=1.24 的正名是 pymupdf；fitz 只是向后兼容别名，已标弃用会打 FutureWarning
    fitz = None
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # 老版本 PyMuPDF 只有这个入口
        except ImportError:
            pass
    if fitz is not None:
        from PIL import Image
        try:
            doc = fitz.open(pdf_path)
            try:
                out = []
                for i in range(min(3, doc.page_count)):
                    pix = doc[i].get_pixmap(dpi=150)
                    out.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                return out
            finally:
                doc.close()
        except Exception as e:
            st.warning(f"PDF 转换失败（PyMuPDF）: {e}")
            return None

    # 2) pdf2image 回退
    try:
        from pdf2image import convert_from_path
        return convert_from_path(pdf_path, first_page=1, last_page=3, dpi=150)
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
    # 基因浏览器要项目级的 index_dir / txdb 才能找到与 bigWig 同源的那份注释
    proj = next((p for p in projects
                 if p.get("species") == species and p.get("experiment") == experiment), {})
    result_dir = os.path.join(BASE_DIR, "result", species, experiment)

    if not os.path.isdir(result_dir):
        st.warning(f"结果目录不存在: `result/{species}/{experiment}/`")
        st.stop()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["比对率统计", "Peak Annotation", "DeepTools Heatmap", "富集分析 (GO/KEGG)", "Motif Analysis", "所有输出文件", "基因组浏览器"])

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

                    if px is not None:
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
                    else:
                        st.bar_chart(df_align.set_index("样本")["比对率 (%)"], height=420)
                        st.caption(
                            "启动器环境没装 plotly，用的是内置柱状图。"
                            "运行 `chipseq-app-update` 会自动补上（也能看，只是没有数值标注）。"
                        )
                    # 下载按钮
                    st.markdown("---")
                    csv_data = df_align.to_csv(index=False, encoding="utf-8-sig")
                    st.download_button(
                        "下载比对率统计表",
                        data=csv_data,
                        file_name=f"{species}_{experiment}_alignment_stats.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("日志中未找到比对率信息（可能任务未完成或已中断）")
            
            except Exception as e:
                st.error(f"解析日志文件失败: {e}")
        else:
            st.info(f"日志文件不存在: `logs/{species}/{experiment}/3_1_ChIPseq.log`")
            st.caption("运行 3_ChIPseq 模块后会生成该日志")

    # ============================================================
    # Tab 2: Peak Annotation 结果
    # ============================================================
    with tab2:
        anno_dir = os.path.join(result_dir, "4_analyse", "annoChIPPeaks")
        if os.path.isdir(anno_dir):
            # 列出所有 pair 目录
            all_dirs = sorted([d for d in os.listdir(anno_dir) if os.path.isdir(os.path.join(anno_dir, d))])

            # 分类：单样本 vs Consensus
            consensus_dirs = [d for d in all_dirs if d.startswith("consensus_")]
            sample_dirs = [d for d in all_dirs if not d.startswith("consensus_")]

            if all_dirs:
                anno_type = st.radio(
                    "注释类型",
                    ["单样本 Peak Annotation", "Consensus Peak Annotation"],
                    horizontal=True,
                    key="anno_type_radio",
                )

                if anno_type == "单样本 Peak Annotation":
                    if sample_dirs:
                        selected_pair = st.selectbox("选择 IP vs INPUT 对", sample_dirs, key="sample_pair_select")
                    else:
                        st.info("暂无单样本注释结果")
                        selected_pair = None
                else:
                    if consensus_dirs:
                        selected_pair = st.selectbox(
                            "选择重复组 Consensus",
                            consensus_dirs,
                            format_func=lambda x: f"重复组: {x.replace('consensus_', '')}",
                            key="consensus_pair_select",
                        )
                    else:
                        st.info("暂无 Consensus 注释结果（需要先运行含 group 的 consensus peaks 模块）")
                        selected_pair = None

                if selected_pair:
                    pair_dir = os.path.join(anno_dir, selected_pair)

                    col1, col2 = st.columns(2)

                    # 查找图表文件
                    with col1:
                        st.markdown("**Peak 注释可视化**")
                        
                        # PDF 文件
                        pdf_files = [f for f in os.listdir(pair_dir) if f.endswith(".pdf")]
                        for pdf in pdf_files:
                            pdf_path = os.path.join(pair_dir, pdf)
                            st.caption(f"{pdf}")
                            
                            # 尝试预览 PDF
                            with st.expander("预览 PDF", expanded=True):
                                pdf_images = pdf_to_images(pdf_path)
                                if pdf_images:
                                    for idx, img in enumerate(pdf_images[:3]):  # 最多显示前3页
                                        st.image(img, caption=f"第 {idx+1} 页", use_container_width=True)
                                    if len(pdf_images) > 3:
                                        st.caption(f"...（共 {len(pdf_images)} 页，仅显示前 3 页）")
                                else:
                                    st.info("当前启动器环境缺少 PDF 渲染库（PyMuPDF）；运行 `chipseq-app-update` 会自动补齐")
                            
                            # 下载按钮
                            with open(pdf_path, "rb") as f:
                                st.download_button(
                                    f"下载 {pdf}",
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
                                st.caption(f"{img_file}")
                                try:
                                    img = Image.open(img_path)
                                    st.image(img, use_container_width=True)
                                except Exception as e:
                                    st.error(f"无法加载图片 {img_file}: {e}")
                        
                        if not pdf_files and not image_files:
                            st.info("暂无可视化文件")

                    # 查找 CSV 数据
                    with col2:
                        st.markdown("**Peak 注释数据**")
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
                                        f"下载完整 CSV",
                                        data=df.to_csv(index=False),
                                        file_name=csv_file,
                                        mime="text/csv",
                                        key=f"csv_{csv_file}",
                                    )
                                except Exception as e:
                                    st.error(f"读取 {csv_file} 失败: {e}")
                        else:
                            st.info("暂无 CSV 数据")

                    # Consensus 对比汇总（仅在 Consensus 模式且有多个组时显示）
                    if anno_type == "Consensus Peak Annotation" and len(consensus_dirs) > 1:
                        st.markdown("---")
                        st.markdown("**各重复组 Consensus 注释分布对比**")

                        comparison_data = []
                        for cp in consensus_dirs:
                            csv_path = os.path.join(anno_dir, cp, f"{cp}_peak_anno.csv")
                            if os.path.exists(csv_path):
                                try:
                                    df_cp = pd.read_csv(csv_path)
                                    if "annotation_category" in df_cp.columns and len(df_cp) > 1:
                                        counts = df_cp["annotation_category"].value_counts().to_dict()
                                        counts["重复组"] = cp.replace("consensus_", "")
                                        counts["Total Peaks"] = len(df_cp)
                                        comparison_data.append(counts)
                                except Exception:
                                    pass

                        if comparison_data:
                            df_comp = pd.DataFrame(comparison_data).fillna(0)
                            # 将 "重复组" 列放到最前面
                            cols = ["重复组", "Total Peaks"] + [c for c in df_comp.columns if c not in ["重复组", "Total Peaks"]]
                            df_comp = df_comp[cols]
                            st.dataframe(df_comp, use_container_width=True)
                        else:
                            st.info("暂无可对比的注释数据")

                # 合并结果（单样本模式下显示）
                if anno_type == "单样本 Peak Annotation":
                    st.markdown("---")
                    st.markdown("**所有样本汇总**")
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
                    st.markdown(f"---\n**{img_file}**")

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
                        with st.expander("预览 PDF", expanded=False):
                            pdf_images = pdf_to_images(img_path)
                            if pdf_images:
                                for idx, pimg in enumerate(pdf_images[:3]):
                                    st.image(pimg, caption=f"第 {idx+1} 页", use_container_width=True)
                                if len(pdf_images) > 3:
                                    st.caption(f"...（共 {len(pdf_images)} 页，仅显示前 3 页）")
                            else:
                                st.info("当前启动器环境缺少 PDF 渲染库（PyMuPDF）；运行 `chipseq-app-update` 会自动补齐")
                        
                        # 下载按钮
                        with open(img_path, "rb") as f:
                            st.download_button(
                                f"下载 {os.path.basename(img_file)}",
                                data=f.read(),
                                file_name=os.path.basename(img_file),
                                mime="application/pdf",
                                key=f"dt_{img_file}",
                            )

                # 查找 matrix 文件信息
                st.markdown("---")
                st.markdown("**Matrix 文件**")
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
    # Tab 4: 富集分析 (GO/KEGG)
    # ============================================================
    with tab4:
        gsea_dir = os.path.join(result_dir, "5_gsea")
        if os.path.isdir(gsea_dir):
            # 列出所有子目录（按 rep / all_groups_combined 分组）
            gsea_subdirs = sorted([
                d for d in os.listdir(gsea_dir)
                if os.path.isdir(os.path.join(gsea_dir, d))
            ])

            if gsea_subdirs:
                selected_gsea_group = st.selectbox(
                    "选择分析组",
                    gsea_subdirs,
                    key="gsea_group_select"
                )
                gsea_group_dir = os.path.join(gsea_dir, selected_gsea_group)

                # 收集该目录下的图表文件
                gsea_plots = []
                gsea_csvs = []
                for f in sorted(os.listdir(gsea_group_dir)):
                    fpath = os.path.join(gsea_group_dir, f)
                    if not os.path.isfile(fpath):
                        continue
                    if f.endswith((".pdf", ".png", ".jpg", ".jpeg", ".svg")):
                        gsea_plots.append(f)
                    elif f.endswith(".csv"):
                        gsea_csvs.append(f)

                if gsea_plots:
                    st.markdown(f"### 富集分析图表 ({selected_gsea_group})")
                    st.caption(f"共 {len(gsea_plots)} 个图表文件")

                    # 将图表分为三类：GO综合图、GO分类图、KEGG图
                    combined_plots = [f for f in gsea_plots if "combined" in f.lower()]
                    go_plots = [f for f in gsea_plots if "combined" not in f.lower() and "kegg" not in f.lower() and f.startswith("GO")]
                    kegg_plots = [f for f in gsea_plots if "kegg" in f.lower()]

                    # --- 辅助函数：展示单个图表文件 ---
                    def show_plot_file(plot_file, group_dir, key_prefix, expanded=False):
                        plot_path = os.path.join(group_dir, plot_file)
                        if plot_file.endswith((".png", ".jpg", ".jpeg")):
                            with st.expander(f"{plot_file}", expanded=expanded):
                                try:
                                    img = Image.open(plot_path)
                                    st.image(img, use_container_width=True)
                                except Exception as e:
                                    st.error(f"无法加载图片: {e}")
                        elif plot_file.endswith(".pdf"):
                            with st.expander(f"{plot_file}", expanded=expanded):
                                pdf_images = pdf_to_images(plot_path)
                                if pdf_images:
                                    for idx, pimg in enumerate(pdf_images[:3]):
                                        st.image(pimg, caption=f"第 {idx+1} 页", use_container_width=True)
                                else:
                                    st.info("当前启动器环境缺少 PDF 渲染库（PyMuPDF）；运行 `chipseq-app-update` 会自动补齐")

                                with open(plot_path, "rb") as f:
                                    st.download_button(
                                        f"下载 {plot_file}",
                                        data=f.read(),
                                        file_name=plot_file,
                                        mime="application/pdf",
                                        key=f"{key_prefix}_{plot_file}",
                                    )
                        elif plot_file.endswith(".svg"):
                            with st.expander(f"{plot_file}", expanded=expanded):
                                try:
                                    with open(plot_path, "r", encoding="utf-8") as svgf:
                                        svg_content = svgf.read()
                                    st.markdown(svg_content, unsafe_allow_html=True)
                                except Exception as e:
                                    st.error(f"无法加载 SVG: {e}")

                    # ---- 区域 1: GO 综合图 ----
                    if combined_plots:
                        st.markdown("#### GO 综合图 (BP + MF + CC)")
                        for plot_file in combined_plots:
                            show_plot_file(plot_file, gsea_group_dir, "gsea_combined", expanded=True)

                    # ---- 区域 2: GO 分类图 ----
                    if go_plots:
                        st.markdown("#### GO 分类图 (BP / MF / CC)")
                        for plot_file in go_plots:
                            show_plot_file(plot_file, gsea_group_dir, "gsea_go", expanded=False)

                    # ---- 区域 3: KEGG 通路图 ----
                    if kegg_plots:
                        st.markdown("#### KEGG 通路富集")
                        for plot_file in kegg_plots:
                            show_plot_file(plot_file, gsea_group_dir, "gsea_kegg", expanded=True)

                    if not combined_plots and not go_plots and not kegg_plots:
                        st.info(f"目录 `{selected_gsea_group}` 中暂无可识别的图表文件")

                else:
                    st.info(f"目录 `{selected_gsea_group}` 中暂无图表文件")

                # 显示 CSV 数据
                if gsea_csvs:
                    st.markdown("---")
                    st.markdown("#### 富集分析数据表")
                    selected_csv = st.selectbox(
                        "选择数据文件",
                        gsea_csvs,
                        key="gsea_csv_select"
                    )
                    if selected_csv:
                        csv_path = os.path.join(gsea_group_dir, selected_csv)
                        try:
                            df_gsea = pd.read_csv(csv_path)
                            st.dataframe(df_gsea.head(100), use_container_width=True)
                            st.caption(f"共 {len(df_gsea)} 行，显示前 100 行")

                            st.download_button(
                                f"下载 {selected_csv}",
                                data=df_gsea.to_csv(index=False),
                                file_name=selected_csv,
                                mime="text/csv",
                                key=f"gsea_csv_dl_{selected_csv}",
                            )
                        except Exception as e:
                            st.error(f"读取 CSV 失败: {e}")
            else:
                st.info("富集分析目录为空，请先运行 GSEA 分析步骤")
        else:
            st.info(f"目录不存在: `5_gsea/`\n\n请先运行富集分析（Step 5）后再查看结果。")

    # ============================================================
    # Tab 5: Motif Analysis
    # ============================================================
    with tab5:
        motif_dir = os.path.join(result_dir, "6_motif")
        if os.path.isdir(motif_dir):
            # 列出所有子目录（consensus_rep1, consensus_rep2, 或 IP_vs_Input）
            motif_subdirs = sorted([
                d for d in os.listdir(motif_dir)
                if os.path.isdir(os.path.join(motif_dir, d))
            ])

            if motif_subdirs:
                selected_motif_group = st.selectbox(
                    "选择分析组",
                    motif_subdirs,
                    format_func=lambda x: f"重复组: {x.replace('consensus_', '')}" if x.startswith("consensus_") else x,
                    key="motif_group_select",
                )
                motif_group_dir = os.path.join(motif_dir, selected_motif_group)

                # 检查 meme-chip 输出目录
                meme_out_dir = os.path.join(motif_group_dir, "meme_chip_out")
                if os.path.isdir(meme_out_dir):
                    # HTML 报告
                    report_html = os.path.join(meme_out_dir, "meme-chip.html")
                    if os.path.exists(report_html):
                        st.markdown("#### MEME-ChIP 完整报告")
                        with open(report_html, "rb") as f:
                            st.download_button(
                                "下载 meme-chip.html 报告",
                                data=f.read(),
                                file_name=f"meme-chip_{selected_motif_group}.html",
                                mime="text/html",
                                key="motif_html_dl",
                            )
                        st.caption("下载后在浏览器中打开可查看完整交互式报告")

                    st.markdown("---")

                    # 展示发现的 motif logos (PNG) + PDF 下载
                    st.markdown("#### 发现的 Motifs")

                    # 检查是否有 PDF logos
                    pdf_logo_dir = os.path.join(meme_out_dir, "logos_pdf")
                    pdf_files = []
                    if os.path.isdir(pdf_logo_dir):
                        pdf_files = sorted([
                            f for f in os.listdir(pdf_logo_dir)
                            if f.endswith(".pdf") or f.endswith(".eps")
                        ])
                    if pdf_files:
                        st.markdown("**PDF/EPS 矢量图（发表质量）：**")
                        for pdf_name in pdf_files:
                            pdf_path = os.path.join(pdf_logo_dir, pdf_name)
                            with open(pdf_path, "rb") as f:
                                st.download_button(
                                    f"{pdf_name}",
                                    data=f.read(),
                                    file_name=pdf_name,
                                    mime="application/pdf" if pdf_name.endswith(".pdf") else "application/postscript",
                                    key=f"motif_pdf_{selected_motif_group}_{pdf_name}",
                                )
                        st.markdown("---")

                    # PNG 预览
                    logo_files = []
                    for root, dirs, files in os.walk(meme_out_dir):
                        for f in files:
                            if f.endswith(".png") and "logo" in f.lower() and "_rc" not in f:
                                logo_files.append(os.path.relpath(os.path.join(root, f), meme_out_dir))

                    if logo_files:
                        logo_files.sort()
                        cols = st.columns(min(3, len(logo_files)))
                        for idx, logo in enumerate(logo_files[:9]):  # 最多展示 9 个
                            logo_path = os.path.join(meme_out_dir, logo)
                            with cols[idx % 3]:
                                try:
                                    img = Image.open(logo_path)
                                    st.image(img, caption=os.path.basename(logo), use_container_width=True)
                                except Exception as e:
                                    st.error(f"无法加载: {e}")
                        if len(logo_files) > 9:
                            st.caption(f"...共 {len(logo_files)} 个 motif logos，仅展示前 9 个")
                    else:
                        st.info("暂无 motif logo 图片")

                    # 展示 TOMTOM 匹配结果
                    st.markdown("---")
                    st.markdown("#### 已知 Motif 匹配 (TOMTOM)")
                    tomtom_tsv = os.path.join(meme_out_dir, "tomtom_out", "tomtom.tsv")
                    if os.path.exists(tomtom_tsv):
                        try:
                            df_tomtom = pd.read_csv(tomtom_tsv, sep='\t', comment='#')
                            st.dataframe(df_tomtom.head(50), use_container_width=True)
                            st.caption(f"共 {len(df_tomtom)} 条匹配记录")
                        except Exception as e:
                            st.error(f"读取 TOMTOM 结果失败: {e}")
                    else:
                        st.info("暂无 TOMTOM 匹配结果")

                    # 所有输出文件列表
                    st.markdown("---")
                    st.markdown("#### Motif 输出文件")
                    motif_files = []
                    for root, dirs, files in os.walk(meme_out_dir):
                        for f in files:
                            fpath = os.path.join(root, f)
                            rel = os.path.relpath(fpath, meme_out_dir)
                            size_kb = os.path.getsize(fpath) / 1024
                            motif_files.append({
                                "文件": rel,
                                "大小": f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                            })
                    if motif_files:
                        st.dataframe(pd.DataFrame(motif_files).head(50), use_container_width=True)
                else:
                    st.info(f"meme_chip_out 目录不存在: `{meme_out_dir}`")
            else:
                st.info("Motif 分析目录为空，请先运行 Motif 分析步骤")
        else:
            st.info(f"目录不存在: `6_motif/`\n\n请先在项目配置中开启 6_motif 模块并运行。")

    # ============================================================
    # Tab 6: 所有输出文件
    # ============================================================
    with tab6:
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
                filter_text = st.text_input("过滤文件路径", placeholder="输入关键词，例如: .pdf, deeptools, peak")
                if filter_text:
                    df_files = df_files[df_files["路径"].str.contains(filter_text, case=False)]

                st.dataframe(df_files, use_container_width=True, height=500)
                st.caption(f"共 {len(df_files)} 个文件")
            else:
                st.info("结果目录为空")
        else:
            st.warning("结果目录不存在")

    # ============================================================
    # Tab 7: 基因组信号浏览器
    # ============================================================
    with tab7:
        st.markdown("### 基因组信号浏览器")
        st.caption(
            "把 `3_ChIPseq` 的 bigWig（`bamCoverage --binSize 5 --normalizeUsing BPM`）"
            "按任意区间重采样后叠图。滚轮缩放、拖拽平移、框选放大、悬停看数值、"
            "导出 PNG 都在浏览器端完成，不重新取数；换视野时才重新读 bigWig。"
        )

        _gb_ok = True
        if not genome_browser.have_pybigwig():
            st.error(
                "启动器环境缺少 `pyBigWig`，读不了 bigWig。\n\n"
                "在终端执行 `chipseq-app-update` 会自动补齐（已加进启动器依赖清单），"
                "之后重启面板即可。"
            )
            _gb_ok = False

        _gb_samples = (genome_browser.list_samples(
            result_dir, os.path.join(BASE_DIR, "config", "metadata.csv")
        ) if _gb_ok else [])

        if _gb_ok and not _gb_samples:
            st.info(
                "本项目还没有样本 bigWig。\n\n"
                "跑完 `3_ChIPseq` 模块后，每个样本目录下会生成 `<样本名>.bw`，"
                "这里就能出图。"
            )
            _gb_ok = False

        if _gb_ok:
            _gb_chroms, _gb_contig_warn = genome_browser.common_contigs(_gb_samples)
            for _w in _gb_contig_warn:
                st.warning(_w)

            # ---- 注释：先自动按「染色体命名与 bw 是否一致」挑，再允许手动改 ----
            _gb_auto = genome_browser.resolve_annotation(
                BASE_DIR, species, proj.get("index_dir", ""), proj.get("txdb", ""), _gb_chroms,
            )
            _gb_cands = _gb_auto["candidates"]
            _gb_anno_info = _gb_auto
            if _gb_cands:
                _gb_paths = [c["path"] for c in _gb_cands]
                _gb_labels = {
                    c["path"]: (f"{c['source']} · {os.path.basename(c['path'])}"
                                 f"（染色体匹配 {c['score']:.0%}）")
                    for c in _gb_cands
                }
                _gb_default = (_gb_paths.index(_gb_auto["path"])
                               if _gb_auto["path"] in _gb_paths else 0)
                _gb_chosen = st.selectbox(
                    "注释文件", _gb_paths, index=_gb_default,
                    format_func=lambda p: _gb_labels.get(p, p), key="gb_anno_path",
                    help="基因模型和基因名定位都来自这份注释。默认选染色体命名与 bigWig 一致的那份，"
                         "换一份不兼容的会让基因坐标整体错位。",
                )
                _gb_anno_info = next(c for c in _gb_cands if c["path"] == _gb_chosen)
            if _gb_anno_info.get("score", 0) < 0.5:
                st.warning(
                    f"这份注释只有 {_gb_anno_info['score']:.0%} 的染色体名能对上 bigWig"
                    f"（注释用 `{_gb_anno_info.get('seqid_sample', '')}`）。"
                    "基因模型可能整体错位，建议换一份与比对索引同源的注释。"
                )

            _gb_anno = None
            if _gb_anno_info.get("path"):
                try:
                    with st.spinner("载入注释索引（首次会解析 GFF，之后走缓存）…"):
                        _gb_anno = genome_browser.load_annotation(_gb_anno_info["path"])
                except Exception as _e:  # 注释格式千奇百怪，不该把整页带崩
                    st.error(f"注释解析失败：{_e}")
            else:
                st.info("当前项目没找到可用的 GFF/GTF：基因模型与基因名定位不可用，仍可按坐标查看信号。")

            # ---- 默认视野：本项目最强的那个 MACS2 peak ----
            _gb_tops = genome_browser.top_peaks(result_dir, 20)
            if "gb_locus" not in st.session_state:
                if _gb_tops:
                    _p0 = _gb_tops[0]
                    _c0 = (_p0["start"] + _p0["end"]) // 2
                    _sz = _gb_chroms.get(_p0["chrom"], _c0 + 3000)
                    st.session_state.gb_locus = {
                        "chrom": _p0["chrom"],
                        "start": max(0, _c0 - 3000), "end": min(_sz, _c0 + 3000),
                        "anchor": _c0, "gene": None,
                        "label": f"{_p0['chrom']}:{max(0, _c0 - 3000):,}-{min(_sz, _c0 + 3000):,}"
                                 f"（最强 peak · {_p0['sample']}）",
                    }
                else:
                    _c0 = min(20000, max(_gb_chroms.values()) if _gb_chroms else 20000)
                    _chr0 = next(iter(_gb_chroms), "")
                    st.session_state.gb_locus = {
                        "chrom": _chr0, "start": 0, "end": min(20000, _gb_chroms.get(_chr0, 20000)),
                        "anchor": None, "gene": None,
                        "label": f"{_chr0}:0-{min(20000, _gb_chroms.get(_chr0, 20000)):,}",
                    }

            # ---- 位置输入 ----
            with st.form("gb_locus_form", clear_on_submit=False):
                _fc1, _fc2 = st.columns([5, 1])
                with _fc1:
                    _gb_q = st.text_input(
                        "基因组位置",
                        placeholder="AT1G01010 / AT1G01010:up=2k,down=2k / AT1G01010:body / Chr1:3630-5899 / Chr1:5000000",
                        label_visibility="collapsed",
                    )
                with _fc2:
                    _gb_go = st.form_submit_button("定位", type="primary", use_container_width=True)
            if _gb_go:
                try:
                    st.session_state.gb_locus = genome_browser.parse_locus(_gb_q, _gb_anno, _gb_chroms)
                    st.session_state.gb_err = None
                except ValueError as _e:
                    st.session_state.gb_err = str(_e)
            if st.session_state.get("gb_err"):
                st.error(st.session_state.gb_err)

            # ---- 快捷跳转：本项目 call 出来的峰，按显著性排序 ----
            if _gb_tops:
                _gb_plabels = [
                    "— 选择 —", *[
                        f"{p['sample']} · {p['chrom']}:{p['start']:,}-{p['end']:,} · q={p['score']:.0f}"
                        for p in _gb_tops
                    ]
                ]
                _gb_pick = st.selectbox("或直接跳到本项目最强的 MACS2 peak",
                                        _gb_plabels, key="gb_peak_pick")
                # 只在选项真的变了才跳，否则每次 rerun 都会把用户手动缩放的位置弹回去
                if _gb_pick != st.session_state.get("gb_peak_applied"):
                    st.session_state.gb_peak_applied = _gb_pick
                    if _gb_pick != "— 选择 —":
                        _pk = _gb_tops[_gb_plabels.index(_gb_pick) - 1]
                        _c = (_pk["start"] + _pk["end"]) // 2
                        _sz = _gb_chroms.get(_pk["chrom"], _c + 3000)
                        st.session_state.gb_locus = {
                            "chrom": _pk["chrom"],
                            "start": max(0, _c - 3000), "end": min(_sz, _c + 3000),
                            "anchor": _c, "gene": None,
                            "label": f"{_pk['chrom']}:{max(0, _c - 3000):,}-{min(_sz, _c + 3000):,}"
                                     f"（peak · {_pk['sample']}）",
                        }

            # ---- 显示选项 ----
            _oc1, _oc2, _oc3, _oc4 = st.columns([2.2, 1.6, 1.2, 1.6])
            with _oc1:
                _gb_ids = st.multiselect(
                    "样本", [s["id"] for s in _gb_samples],
                    default=[s["id"] for s in _gb_samples],
                    format_func=lambda i: f"{i}（{next(s['role'] for s in _gb_samples if s['id'] == i)}）",
                    key="gb_sel",
                )
            with _oc2:
                _gb_mode = st.radio("布局", ["堆叠", "叠加", "差值"], horizontal=True,
                                    key="gb_mode",
                                    help="堆叠：每样本一行；叠加：全叠一行共用 y 轴；"
                                         "差值：CHIP − INPUT（按 metadata.csv 配对）。")
            with _oc3:
                _gb_smooth = st.select_slider("平滑 (bp)", options=[0, 25, 50, 100, 200, 500, 1000],
                                              value=100, key="gb_smooth")
            with _oc4:
                _gb_shared_y = st.checkbox(
                    "统一 y 轴", value=True, key="gb_shared_y",
                    disabled=(_gb_mode == "叠加"),
                    help="所有轨道共用一套 y 范围，顶 = 全场最高信号向上取整，"
                         "这样轨道之间的高低可以直接比。关掉后每轨各自缩放："
                         "弱信号看得更细，但不能再比高矮。",
                )
                _gb_show_gene = st.checkbox("基因模型", value=True, key="gb_gene")
                _gb_show_peak = st.checkbox("高亮 peak", value=False, key="gb_peak",
                                            help="把当前视野内的 MACS2 peak 画成浅色底带")

            _gb_sel = [s for s in _gb_samples if s["id"] in _gb_ids]
            _gb_locus = st.session_state.gb_locus
            _gb_span = max(1, _gb_locus["end"] - _gb_locus["start"])
            # bins 跟画布宽度走，不要为「更细」拉高：stats() 耗时 ≈ 0.1ms × bins，
            # 跨度超过 5 Mb 时 800 bins 肉眼已经够了
            _gb_bins = 800 if _gb_span > 5_000_000 else genome_browser.DEFAULT_BINS

            if not _gb_sel:
                st.warning("至少选一个样本")
            else:
                _gb_warn: list = []
                _gb_mode_key = {"堆叠": "stacked", "叠加": "overlay", "差值": "delta"}[_gb_mode]
                with st.spinner("读取 bigWig…"):
                    _gb_rows = genome_browser.build_rows(
                        _gb_sel, chrom=_gb_locus["chrom"], start=_gb_locus["start"],
                        end=_gb_locus["end"], bins=_gb_bins, smooth_bp=_gb_smooth,
                        mode=_gb_mode_key, all_samples=_gb_samples, warnings=_gb_warn,
                    )
                for _w in _gb_warn:
                    st.warning(_w)

                if not _gb_rows:
                    st.info("没有可画的轨道（差值模式要求样本在 metadata.csv 里配好 INPUT）。")
                else:
                    _gb_peaks = (genome_browser.peaks_in(result_dir, _gb_locus["chrom"],
                                                        _gb_locus["start"], _gb_locus["end"])
                                 if _gb_show_peak else [])
                    _gb_fig = genome_browser.build_figure(
                        locus=_gb_locus, rows=_gb_rows, anno=_gb_anno,
                        smooth_bp=_gb_smooth, bins=_gb_bins, peaks=_gb_peaks,
                        show_gene=_gb_show_gene, overlay=(_gb_mode == "叠加"),
                        shared_y=_gb_shared_y,
                    )

                    # 位置信息：视野、跨度、bin 宽度一并给出，方便写进图注
                    st.caption(
                        f"当前位置：**{_gb_locus['label']}**　·　"
                        f"跨度 {_gb_span:,} bp　·　{_gb_bins} bins ≈ {_gb_span / _gb_bins:.1f} bp/bin"
                        + (f"　·　平滑 {_gb_smooth} bp（仅影响显示）" if _gb_smooth else "　·　未平滑")
                        + ("　·　Δ = CHIP − INPUT" if _gb_mode_key == "delta" else "")
                    )
                    st.plotly_chart(
                        _gb_fig, use_container_width=True, key="gb_chart",
                        config={
                            "scrollZoom": True,       # 滚轮即缩放，IGV 的手感
                            "displaylogo": False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                            "toImageButtonOptions": {
                                "format": "png", "scale": 2,
                                "filename": f"signal_{_gb_locus['chrom']}_{_gb_locus['start']}-{_gb_locus['end']}",
                            },
                        },
                    )

                    _gb_stats = genome_browser.window_stats(_gb_rows, _gb_span / _gb_bins)
                    with st.expander("当前视野统计（raw，不含平滑）", expanded=False):
                        st.dataframe(_gb_stats, use_container_width=True, hide_index=True)
                        st.caption(
                            "均值按等宽 bin 等权平均，与平滑后的显示值无关；"
                            "放大到峰上看数值时以这张表为准。"
                        )
                        st.download_button(
                            "下载窗口统计 CSV",
                            _gb_stats.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"signal_stats_{_gb_locus['chrom']}_{_gb_locus['start']}-{_gb_locus['end']}.csv",
                            mime="text/csv", key="gb_stats_dl",
                        )

            with st.expander("这个 tab 的数据从哪来、口径是什么", expanded=False):
                st.markdown(
                    "- **信号**：各样本 `3_ChIPseq/<样本>/<样本>.bw`，由 "
                    "`bamCoverage --binSize 5 --ignoreDuplicates --normalizeUsing BPM` 生成，"
                    "同项目内所有轨道归一化方式相同，可以直接比高低。\n"
                    "- **注释**：与比对索引同源的那份 GFF/GTF（按染色体命名与 bigWig 的匹配率自动选），"
                    "基因坐标与峰位置在同一坐标系里。\n"
                    "- **差值**：`CHIP − INPUT` 按 `config/metadata.csv` 的配对逐 bin 相减，"
                    "只能在同 binSize、同归一化方式下做，本项目两者都满足。\n"
                    "- **平滑**：滑动均值，窗口宽度 = `平滑bp / bin宽度`，**只影响显示**；"
                    "窗口统计与导出 CSV 一律用原始值。\n"
                    "- **y 轴**：默认所有轨道共用一套范围，顶 = 全场最高信号向上取整到 "
                    "1/2/2.5/5×10ⁿ，所以轨道之间的高低可以直接比；关掉「统一 y 轴」后"
                    "每轨各自缩放，弱信号看得更细，但就不能再比高矮了。\n"
                    "- **分辨率**：`3_ChIPseq` 的 bw 是 5 bp 一个 bin，视野拉到几百 kb 以后"
                    "每像素覆盖的 bin 数远超 1，此时曲线是重采样结果，不应当作原始分辨率看待。"
                )


