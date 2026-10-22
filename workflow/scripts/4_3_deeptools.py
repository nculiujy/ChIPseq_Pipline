"""
DeepTools 分析脚本 - 基于 comparisons 配置的组间比较

设计逻辑:
1. 每个 IP 样本生成一个 bigwig 文件（bamCoverage）
2. 根据 comparisons 配置，将指定 groups 的 IP bigwig 放在一起做:
   - computeMatrix (reference-point 或 scale-regions)
   - plotHeatmap + plotProfile
3. replicate_qc: 对每个 group 内部做 multiBigwigSummary + plotCorrelation + plotPCA
"""

import os
import sys
import argparse
import subprocess
import yaml
import pandas as pd
from collections import defaultdict


def run_cmd(cmd):
    print(f"[RUNNING] {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed with exit code {e.returncode}")
        sys.exit(1)


def parse_metadata(metadata_file, species=None, experiment=None):
    """
    解析 metadata.csv，返回:
    - pairs: list of dict {treat, control, group}
    - group_map: dict {group_name: [list of IP sample names]}

    metadata.csv 可带可选的 project 列（值形如 TAIR10/314Com5）：
    project 为空的行走“通用”语义，对所有项目生效（兼容老的两三列格式）；
    写了 project 的行只属于那一个项目，避免多项目共用一张表时互相污染。
    """
    df = pd.read_csv(metadata_file)
    if species is not None and "project" in df.columns:
        proj = df["project"].fillna("").astype(str).str.strip()
        df = df[(proj == "") | (proj == f"{species}/{experiment}")]
    pairs = []
    group_map = defaultdict(list)

    for _, row in df.iterrows():
        treat = str(row.get("IP sample", "")).strip()
        control = str(row.get("Input", "")).strip()
        group = str(row.get("group", "")).strip()

        if not treat or not control or treat == "nan" or control == "nan":
            continue

        pair = {"treat": treat, "control": control, "group": group if group and group != "nan" else ""}
        pairs.append(pair)

        if pair["group"]:
            if treat not in group_map[pair["group"]]:
                group_map[pair["group"]].append(treat)

    return pairs, dict(group_map)


def generate_bigwigs(pairs, bamdir, outdir, threads, norm, input_mode="none", operation="log2"):
    """
    为所有 IP 样本生成 bigwig 文件。
    
    input_mode:
    - "none": 只生成 IP 的绝对信号 bigwig（bamCoverage）
    - "include_input": 同时生成 IP 和 Input 的 bigwig，热图并排展示
    - "log2_ratio": 使用 bamCompare 生成 IP/Input 比值 bigwig
    
    返回:
    - bw_map: dict {sample_name -> bigwig_path} (IP 样本)
    - input_bw_map: dict {input_name -> bigwig_path} (仅 include_input 模式)
    """
    bw_map = {}  # sample_name -> bigwig_path
    input_bw_map = {}  # input_name -> bigwig_path
    norm_arg = f"--normalizeUsing {norm}" if norm != "none" else ""

    if input_mode == "log2_ratio":
        # bamCompare 模式: 每个 IP-Input 对生成一个比值 bigwig
        print(f"[INFO] input_mode=log2_ratio, using bamCompare (operation={operation})")
        for pair in pairs:
            sample = pair["treat"]
            control = pair["control"]
            
            ip_bam = os.path.join(bamdir, sample, "accepted_hits.sorted.unique.bam")
            input_bam = os.path.join(bamdir, control, "accepted_hits.sorted.unique.bam")
            bw_path = os.path.join(outdir, "bigwig", f"{sample}_vs_{control}_{operation}.bw")
            
            if not os.path.exists(ip_bam):
                print(f"[WARNING] IP BAM not found: {ip_bam}")
                continue
            if not os.path.exists(input_bam):
                print(f"[WARNING] Input BAM not found: {input_bam}")
                continue
            
            os.makedirs(os.path.dirname(bw_path), exist_ok=True)
            
            if not os.path.exists(bw_path):
                cmd = (
                    f"bamCompare "
                    f"-b1 {ip_bam} -b2 {input_bam} "
                    f"-o {bw_path} "
                    f"--operation {operation} "
                    f"--scaleFactorsMethod None "
                    f"{norm_arg} "
                    f"-p {threads} "
                    f"--skipZeroOverZero"
                )
                run_cmd(cmd)
            else:
                print(f"[INFO] BigWig already exists: {bw_path}")
            
            bw_map[sample] = bw_path
    else:
        # bamCoverage 模式: 生成 IP 绝对信号 bigwig
        all_ip_samples = list(set(p["treat"] for p in pairs))

        for sample in all_ip_samples:
            bam_path = os.path.join(bamdir, sample, "accepted_hits.sorted.unique.bam")
            bw_path = os.path.join(outdir, "bigwig", f"{sample}.bw")

            if not os.path.exists(bam_path):
                print(f"[WARNING] BAM not found for {sample}: {bam_path}")
                continue

            os.makedirs(os.path.dirname(bw_path), exist_ok=True)

            if not os.path.exists(bw_path):
                cmd = f"bamCoverage -b {bam_path} -o {bw_path} -p {threads} {norm_arg}"
                run_cmd(cmd)
            else:
                print(f"[INFO] BigWig already exists: {bw_path}")

            bw_map[sample] = bw_path

        # include_input 模式: 额外生成 Input 的 bigwig
        if input_mode == "include_input":
            print(f"[INFO] input_mode=include_input, also generating Input bigwigs")
            all_input_samples = list(set(p["control"] for p in pairs))
            for sample in all_input_samples:
                bam_path = os.path.join(bamdir, sample, "accepted_hits.sorted.unique.bam")
                bw_path = os.path.join(outdir, "bigwig", f"{sample}_input.bw")

                if not os.path.exists(bam_path):
                    print(f"[WARNING] Input BAM not found: {bam_path}")
                    continue

                os.makedirs(os.path.dirname(bw_path), exist_ok=True)

                if not os.path.exists(bw_path):
                    cmd = f"bamCoverage -b {bam_path} -o {bw_path} -p {threads} {norm_arg}"
                    run_cmd(cmd)
                else:
                    print(f"[INFO] BigWig already exists: {bw_path}")

                input_bw_map[sample] = bw_path

    return bw_map, input_bw_map


def run_replicate_qc(group_map, bw_map, outdir, dt_config, threads):
    """对每个 group 做重复间 QC (multiBigwigSummary + plotCorrelation + plotPCA)"""
    qc_config = dt_config.get("replicate_qc", {})
    if not qc_config.get("enabled", True):
        print("[INFO] Replicate QC is disabled, skipping")
        return

    corr_method = qc_config.get("correlation_method", "spearman")
    qc_dir = os.path.join(outdir, "replicate_qc")
    os.makedirs(qc_dir, exist_ok=True)

    for group_name, samples in group_map.items():
        if len(samples) < 2:
            print(f"[INFO] Group '{group_name}' has only {len(samples)} sample(s), skipping QC")
            continue

        # 过滤出有 bigwig 的样本
        valid_bws = [bw_map[s] for s in samples if s in bw_map]
        valid_samples = [s for s in samples if s in bw_map]

        if len(valid_bws) < 2:
            print(f"[WARNING] Group '{group_name}': not enough bigwig files for QC")
            continue

        group_dir = os.path.join(qc_dir, group_name)
        os.makedirs(group_dir, exist_ok=True)

        # multiBigwigSummary
        summary_npz = os.path.join(group_dir, f"{group_name}_multiBigwigSummary.npz")
        if not os.path.exists(summary_npz):
            bw_args = " ".join(valid_bws)
            labels = " ".join(valid_samples)
            cmd = (
                f"multiBigwigSummary bins "
                f"-b {bw_args} "
                f"--labels {labels} "
                f"-p {threads} "
                f"-o {summary_npz}"
            )
            run_cmd(cmd)

        # plotCorrelation
        if qc_config.get("plot_correlation", True):
            corr_pdf = os.path.join(group_dir, f"{group_name}_correlation.pdf")
            corr_tab = os.path.join(group_dir, f"{group_name}_correlation.tab")
            if not os.path.exists(corr_pdf):
                cmd = (
                    f"plotCorrelation -in {summary_npz} "
                    f"--corMethod {corr_method} "
                    f"--whatToPlot heatmap "
                    f"--skipZeros "
                    f"--plotTitle '{group_name} Replicate Correlation ({corr_method})' "
                    f"--plotFile {corr_pdf} "
                    f"--outFileCorMatrix {corr_tab}"
                )
                run_cmd(cmd)

        # plotPCA
        if qc_config.get("plot_pca", True):
            pca_pdf = os.path.join(group_dir, f"{group_name}_PCA.pdf")
            if not os.path.exists(pca_pdf):
                cmd = (
                    f"plotPCA -in {summary_npz} "
                    f"--plotTitle '{group_name} PCA' "
                    f"--plotFile {pca_pdf}"
                )
                run_cmd(cmd)

        print(f"[INFO] Replicate QC done for group '{group_name}' ({len(valid_samples)} samples)")


def get_bed_for_comparison(comparison, peakdir, outdir, group_map, dt_config, per_group=False):
    """
    确定 comparison 使用的 BED 区域文件。
    
    当 per_group=True 时，返回每个 group 独立的 BED 文件列表（用于 --perGroup 按组拆分展示）。
    当 per_group=False 时，返回单个合并 BED 文件路径。
    
    返回值：
    - per_group=True: list of (bed_path, label) 或 None
    - per_group=False: bed_path (str) 或 None

    核心逻辑（基于 group 组别）:
    - 单组 (1 group):
        无论 bed_source 设置，优先使用该组的 consensus peaks BED。
        若 consensus 不存在则 fallback 到该组所有样本的 MACS2 peaks 合并。
    - 多组 (≥2 groups):
        - union_peaks / consensus_peaks: 取所有涉及组的 consensus peaks 并集
        - macs2_peaks: 取所有涉及组样本的 MACS2 peaks 并集
    """
    comp_bed_source = comparison.get("bed_source", dt_config.get("bed_source", "union_peaks"))
    groups = comparison.get("groups", [])
    n_groups = len(groups)
    comp_name = comparison.get("name", "comparison")
    # 查找 consensus BED: 支持两种路径格式
    consensus_dir = os.path.join(peakdir, "..", "consensus")
    if not os.path.isdir(consensus_dir):
        consensus_dir = os.path.join(peakdir, "..", "consensus_peaks")

    # ================================================================
    # 单组: 直接使用该组的 consensus peaks BED（合并后的 peak）
    # ================================================================
    if n_groups == 1:
        grp = groups[0]
        # 尝试多种命名格式
        grp_bed = None
        for pattern in [f"consensus_{grp}.bed", f"{grp}_consensus.bed"]:
            candidate = os.path.join(consensus_dir, pattern)
            if os.path.exists(candidate):
                grp_bed = candidate
                break

        if grp_bed:
            print(f"[INFO] Single group '{grp}': using consensus peaks BED")
            if per_group:
                return [(grp_bed, grp)]
            return grp_bed
        else:
            # Fallback: 合并该组所有样本的 MACS2 peaks
            print(f"[WARNING] Consensus BED not found for group '{grp}', "
                  f"falling back to merging group's MACS2 peaks")
            fallback = _get_group_macs2_bed(grp, group_map, peakdir, outdir)
            if per_group:
                return [(fallback, grp)] if fallback else None
            return fallback

    # ================================================================
    # 多组 + per_group=True: 返回每个组独立的 BED 文件列表
    # ================================================================
    if per_group and comp_bed_source in ("consensus_peaks", "union_peaks"):
        bed_list = []
        for grp in groups:
            grp_bed = None
            for pattern in [f"consensus_{grp}.bed", f"{grp}_consensus.bed"]:
                candidate = os.path.join(consensus_dir, pattern)
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    grp_bed = candidate
                    break
            if grp_bed:
                bed_list.append((grp_bed, grp))
            else:
                # Fallback: 该 group 的 MACS2 peaks
                fallback = _get_group_macs2_bed(grp, group_map, peakdir, outdir)
                if fallback:
                    bed_list.append((fallback, grp))
        if bed_list:
            return bed_list
        # 全部失败，fallback 到合并模式
        print("[WARNING] No per-group BED files found, falling back to merged mode")

    # ================================================================
    # 多组: 根据 bed_source 决定策略
    # ================================================================
    if comp_bed_source in ("consensus_peaks", "union_peaks"):
        # 优先尝试合并涉及组的 consensus peaks
        consensus_beds = []
        for grp in groups:
            grp_bed = None
            for pattern in [f"consensus_{grp}.bed", f"{grp}_consensus.bed"]:
                candidate = os.path.join(consensus_dir, pattern)
                if os.path.exists(candidate):
                    grp_bed = candidate
                    break
            if grp_bed:
                consensus_beds.append(grp_bed)
            else:
                print(f"[WARNING] Consensus BED not found for group '{grp}'")

        if consensus_beds:
            # 合并多个 consensus beds 取并集
            merged_bed = os.path.join(outdir, "comparisons", comp_name, "merged_consensus.bed")
            os.makedirs(os.path.dirname(merged_bed), exist_ok=True)

            if not os.path.exists(merged_bed) or os.path.getsize(merged_bed) == 0:
                cat_cmd = (f"cat {' '.join(consensus_beds)} | "
                           f"sort -k1,1 -k2,2n | bedtools merge -i - > {merged_bed}")
                run_cmd(cat_cmd)

            # 验证生成的文件非空
            if os.path.getsize(merged_bed) == 0:
                print(f"[ERROR] Merged consensus BED is empty: {merged_bed}")
                return None

            return merged_bed
        else:
            # 所有 consensus 都不存在，fallback 到 MACS2 peaks 并集
            print("[WARNING] No consensus BED files found, falling back to MACS2 peaks union")
            return _get_groups_macs2_union_bed(groups, group_map, peakdir, outdir, comp_name)

    elif comp_bed_source == "macs2_peaks":
        # 直接用涉及组样本的原始 MACS2 peaks 并集
        return _get_groups_macs2_union_bed(groups, group_map, peakdir, outdir, comp_name)

    else:
        # custom / genes 等其他 bed_source, 使用全局配置的文件
        custom_bed = dt_config.get("custom_bed", "")
        if custom_bed and os.path.exists(custom_bed):
            return custom_bed
        gtf_file = dt_config.get("gtf_file", "")
        if gtf_file and os.path.exists(gtf_file):
            return gtf_file
        # 最终 fallback
        print("[WARNING] Custom/genes BED not found, falling back to MACS2 peaks union")
        return _get_groups_macs2_union_bed(groups, group_map, peakdir, outdir, comp_name)


def _get_group_macs2_bed(group_name, group_map, peakdir, outdir):
    """
    获取单个 group 的 MACS2 peaks 合并 BED。
    合并该 group 内所有样本的 peak 文件。
    """
    import glob

    samples = group_map.get(group_name, [])
    if not samples:
        print(f"[ERROR] Group '{group_name}' has no samples")
        return None

    # 收集该组样本的 peak 文件
    peak_files = []
    for sample in samples:
        # 尝试匹配 peakdir/{sample}_vs_*/*_peaks_tab.bed
        pattern = os.path.join(peakdir, f"{sample}_vs_*", "*_peaks_tab.bed")
        found = glob.glob(pattern)
        peak_files.extend(found)

    # 也尝试 peakdir/*_{sample}_*/*_peaks_tab.bed 格式
    if not peak_files:
        for sample in samples:
            pattern = os.path.join(peakdir, f"*{sample}*", "*_peaks_tab.bed")
            found = glob.glob(pattern)
            peak_files.extend(found)

    peak_files = [f for f in peak_files if os.path.exists(f) and os.path.getsize(f) > 0]

    if not peak_files:
        # 最终 fallback: 抓取 peakdir 下所有 peak 文件
        print(f"[WARNING] No peak files found for group '{group_name}', using all available peaks")
        peak_files = glob.glob(os.path.join(peakdir, "*", "*_peaks_tab.bed"))
        peak_files = [f for f in peak_files if os.path.getsize(f) > 0]

    if not peak_files:
        print(f"[ERROR] No peak files found at all in {peakdir}")
        return None

    group_bed = os.path.join(outdir, "comparisons", f"single_{group_name}", f"{group_name}_peaks.bed")
    os.makedirs(os.path.dirname(group_bed), exist_ok=True)

    if not os.path.exists(group_bed) or os.path.getsize(group_bed) == 0:
        concat_cmd = (f"cat {' '.join(peak_files)} | "
                      f"sort -k1,1 -k2,2n | bedtools merge -i - > {group_bed}")
        run_cmd(concat_cmd)

    # 验证生成的文件非空
    if os.path.getsize(group_bed) == 0:
        print(f"[ERROR] Generated BED file is empty: {group_bed}")
        return None

    return group_bed


def _get_groups_macs2_union_bed(groups, group_map, peakdir, outdir, comp_name):
    """
    获取多个 group 的 MACS2 peaks 并集 BED。
    只收集涉及 groups 内样本的 peak 文件，而非全局所有 peak。
    """
    import glob

    # 收集所有涉及组的样本
    involved_samples = []
    for grp in groups:
        involved_samples.extend(group_map.get(grp, []))

    # 收集这些样本对应的 peak 文件
    peak_files = []
    for sample in involved_samples:
        pattern = os.path.join(peakdir, f"{sample}_vs_*", "*_peaks_tab.bed")
        found = glob.glob(pattern)
        peak_files.extend(found)

    # 备选匹配模式
    if not peak_files:
        for sample in involved_samples:
            pattern = os.path.join(peakdir, f"*{sample}*", "*_peaks_tab.bed")
            found = glob.glob(pattern)
            peak_files.extend(found)

    peak_files = list(set(f for f in peak_files if os.path.exists(f) and os.path.getsize(f) > 0))

    if not peak_files:
        # 最终 fallback: 使用 peakdir 下所有 peak 文件
        print(f"[WARNING] No group-specific peak files found, using all peaks in {peakdir}")
        peak_files = glob.glob(os.path.join(peakdir, "*", "*_peaks_tab.bed"))
        peak_files = [f for f in peak_files if os.path.getsize(f) > 0]

    if not peak_files:
        print(f"[ERROR] No peak files found at all")
        return None

    union_bed = os.path.join(outdir, "comparisons", comp_name, "union_peaks.bed")
    os.makedirs(os.path.dirname(union_bed), exist_ok=True)

    if not os.path.exists(union_bed) or os.path.getsize(union_bed) == 0:
        concat_cmd = (f"cat {' '.join(peak_files)} | "
                      f"sort -k1,1 -k2,2n | bedtools merge -i - > {union_bed}")
        run_cmd(concat_cmd)

    # 验证生成的文件非空
    if os.path.getsize(union_bed) == 0:
        print(f"[ERROR] Generated union BED file is empty: {union_bed}")
        return None

    return union_bed


def _plot_overlay_profile(matrix_gz, out_pdf, sample_labels, title="", color_map="RdYlBu_r"):
    """
    从 deeptools computeMatrix 的 .gz 输出中提取数据，
    将多个 sample 的 profile 画在同一坐标系内（重叠的线图）。
    """
    import gzip
    import json
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # 读取 matrix.gz (gzip tab 文件，头部有 JSON 元信息)
    with gzip.open(matrix_gz, "rt") as f:
        header = f.readline().strip()
        if header.startswith("@"):
            meta = json.loads(header[1:])
        else:
            meta = {}
        data_lines = f.readlines()

    if not data_lines:
        print(f"[WARNING] Empty matrix: {matrix_gz}")
        return

    # 解析元数据 (注意：值可能是 list，取第一个元素)
    sample_boundaries = meta.get("sample_boundaries", [0, len(data_lines[0].split("\t")) - 6])
    bin_size_val = meta.get("bin size", [10])
    bin_size_val = bin_size_val[0] if isinstance(bin_size_val, list) else bin_size_val
    upstream_val = meta.get("upstream", [3000])
    upstream_val = upstream_val[0] if isinstance(upstream_val, list) else upstream_val
    downstream_val = meta.get("downstream", [3000])
    downstream_val = downstream_val[0] if isinstance(downstream_val, list) else downstream_val

    # 解析数据
    all_rows = []
    for line in data_lines:
        parts = line.strip().split("\t")
        values = parts[6:]  # 前6列是 bed 信息
        row = []
        for v in values:
            try:
                row.append(float(v))
            except (ValueError, TypeError):
                row.append(np.nan)
        all_rows.append(row)

    data = np.array(all_rows)
    n_samples_detected = len(sample_boundaries) - 1

    # 计算每个 sample 的平均 profile
    profiles = []
    for i in range(n_samples_detected):
        start_col = sample_boundaries[i]
        end_col = sample_boundaries[i + 1]
        sample_data = data[:, start_col:end_col]
        profile = np.nanmean(sample_data, axis=0)
        profiles.append(profile)

    # X 轴坐标
    n_points = len(profiles[0]) if profiles else 0
    x = np.linspace(-upstream_val, downstream_val, n_points)

    # 颜色
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    # 绘图
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, profile in enumerate(profiles):
        label = sample_labels[i] if i < len(sample_labels) else f"sample_{i}"
        color = colors[i % len(colors)]
        ax.plot(x / 1000, profile, label=label, color=color, linewidth=1.5)

    ax.set_xlabel("Distance from center (kb)")
    ax.set_ylabel("Signal")
    ax.set_title(title)
    ax.legend(loc="upper right")
    ax.axvline(x=0, color="gray", linestyle="--", linewidth=0.5, alpha=0.5)

    plt.tight_layout()
    plt.savefig(out_pdf, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] Overlay profile saved: {out_pdf}")


def run_comparisons(comparisons, group_map, bw_map, peakdir, outdir, dt_config, threads, input_bw_map=None, pairs=None):
    """
    执行 comparisons 中定义的每个比较。
    支持:
    - 单组 (1 group): 直接展示该组所有样本的热图和 profile
    - 双组 (2 groups): 经典组间比较
    - 多组 (>2 groups): 多组同时展示在一张热图中
    
    当 input_bw_map 非空时（include_input 模式），会将各组对应的 Input bigwig 也加入热图。
    """
    matrix_mode = dt_config.get("matrix_mode", "reference-point")
    bin_size = dt_config.get("bin_size", 10)
    missing_as_zero = dt_config.get("missing_data_as_zero", True)
    plot_config = dt_config.get("plot", {})
    color_map = plot_config.get("color_map", "RdYlBu_r")
    z_min = plot_config.get("z_min", 0)
    z_max = plot_config.get("z_max", "auto")
    interpolation = plot_config.get("interpolation", "bilinear")
    # 绘图布局: per_group=True 时各 group 拆分展示, False 时合并在一张图
    per_group = plot_config.get("per_group", True)

    # 模式参数
    if matrix_mode == "reference-point":
        ref_config = dt_config.get("reference_point", {})
        ref_point = ref_config.get("point", "center")
        upstream = ref_config.get("upstream", 2000)
        downstream = ref_config.get("downstream", 2000)
    else:
        scale_config = dt_config.get("scale_regions", {})
        region_length = scale_config.get("region_length", 5000)
        upstream = scale_config.get("upstream", 2000)
        downstream = scale_config.get("downstream", 2000)
        unscaled_5 = scale_config.get("unscaled_5prime", 0)
        unscaled_3 = scale_config.get("unscaled_3prime", 0)

    for comp in comparisons:
        comp_name = comp.get("name", "unnamed")
        comp_groups = comp.get("groups", [])

        if not comp_groups:
            print(f"[WARNING] Comparison '{comp_name}' has no groups defined, skipping")
            continue

        # 判断比较类型
        n_groups = len(comp_groups)
        if n_groups == 1:
            print(f"\n{'='*60}")
            print(f"[SINGLE GROUP] {comp_name}: {comp_groups[0]} (展示单组热图)")
            print(f"{'='*60}")
        elif n_groups == 2:
            print(f"\n{'='*60}")
            print(f"[COMPARISON] {comp_name}: {comp_groups[0]} vs {comp_groups[1]}")
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print(f"[MULTI-GROUP] {comp_name}: {' vs '.join(comp_groups)} ({n_groups} groups)")
            print(f"{'='*60}")

        comp_dir = os.path.join(outdir, "comparisons", comp_name)
        os.makedirs(comp_dir, exist_ok=True)
        missing_arg = "--missingDataAsZero" if missing_as_zero else ""
        z_min_arg = f"--zMin {z_min}" if z_min != "auto" else ""
        z_max_arg = f"--zMax {z_max}" if z_max != "auto" else ""

        # 收集所有涉及 group 的 IP bigwig
        all_bws = []
        all_labels = []
        group_labels = []  # 用于标记每个样本属于哪个 group

        for grp in comp_groups:
            if grp not in group_map:
                print(f"[WARNING] Group '{grp}' not found in metadata, skipping")
                continue
            samples = group_map[grp]
            for sample in samples:
                if sample in bw_map:
                    all_bws.append(bw_map[sample])
                    all_labels.append(sample)
                    group_labels.append(grp)

        # include_input 模式：将各 group 对应的 Input bigwig 追加到列表末尾
        if input_bw_map and pairs:
            # 收集该 comparison 涉及的 Input 样本（去重）
            involved_inputs = set()
            for pair in pairs:
                if pair["group"] in comp_groups and pair["control"] in input_bw_map:
                    involved_inputs.add(pair["control"])
            for inp in sorted(involved_inputs):
                if inp in input_bw_map:
                    all_bws.append(input_bw_map[inp])
                    all_labels.append(f"{inp}_Input")
                    group_labels.append("Input")

        # ================================================================
        # per_group + 多组（不论 input_mode）: 合并组内 IP 为均值 bigwig
        # 这样图上只有"组级别"的线，而不是每个重复各一条线
        # ================================================================
        if per_group and n_groups >= 2:
            print(f"[INFO] per_group 模式: 合并组内 IP，统一出图")

            # 1. 为每个组生成平均 bigwig
            merged_bws = []
            merged_labels = []
            for grp in comp_groups:
                if grp not in group_map:
                    continue
                grp_samples = group_map[grp]
                grp_sample_bws = [bw_map[s] for s in grp_samples if s in bw_map]
                if not grp_sample_bws:
                    continue

                grp_dir = os.path.join(comp_dir, "merged_bigwigs")
                os.makedirs(grp_dir, exist_ok=True)

                if len(grp_sample_bws) > 1:
                    grp_avg_bw = os.path.join(grp_dir, f"{grp}_avg.bw")
                    if not os.path.exists(grp_avg_bw):
                        bw_list = " ".join(grp_sample_bws)
                        cmd = f"bigwigAverage -b {bw_list} -o {grp_avg_bw} -p {threads}"
                        run_cmd(cmd)
                else:
                    grp_avg_bw = grp_sample_bws[0]

                merged_bws.append(grp_avg_bw)
                merged_labels.append(grp)

            # 2. include_input 模式下追加 Input bigwig
            if input_bw_map and pairs:
                involved_inputs = set()
                for pair in pairs:
                    if pair["group"] in comp_groups and pair["control"] in input_bw_map:
                        involved_inputs.add(pair["control"])
                for inp in sorted(involved_inputs):
                    if inp in input_bw_map:
                        merged_bws.append(input_bw_map[inp])
                        merged_labels.append("Input")

            if not merged_bws:
                print(f"[WARNING] No bigwigs for comparison '{comp_name}', skipping")
                continue

            # 3. 获取 BED (使用合并的 consensus peaks)
            bed_result = get_bed_for_comparison(comp, peakdir, outdir, group_map, dt_config, per_group=False)
            if not bed_result or not os.path.exists(bed_result):
                print(f"[WARNING] No BED for comparison '{comp_name}', skipping")
                continue

            # 4. computeMatrix
            matrix_gz = os.path.join(comp_dir, f"{comp_name}_matrix.gz")
            bw_args_merged = " ".join(merged_bws)
            labels_merged = " ".join(merged_labels)

            if not os.path.exists(matrix_gz):
                if matrix_mode == "reference-point":
                    cmd = (
                        f"computeMatrix reference-point "
                        f"-R {bed_result} "
                        f"-S {bw_args_merged} "
                        f"--referencePoint {ref_point} "
                        f"-b {upstream} -a {downstream} "
                        f"--binSize {bin_size} "
                        f"-p {threads} "
                        f"{missing_arg} "
                        f"--samplesLabel {labels_merged} "
                        f"-o {matrix_gz}"
                    )
                else:
                    cmd = (
                        f"computeMatrix scale-regions "
                        f"-R {bed_result} "
                        f"-S {bw_args_merged} "
                        f"-m {region_length} "
                        f"-b {upstream} -a {downstream} "
                        f"--binSize {bin_size} "
                        f"-p {threads} "
                        f"{missing_arg} "
                        f"--samplesLabel {labels_merged} "
                        f"-o {matrix_gz}"
                    )
                run_cmd(cmd)

            # 5. plotHeatmap
            heatmap_pdf = os.path.join(comp_dir, f"{comp_name}_heatmap.pdf")
            if not os.path.exists(heatmap_pdf):
                cmd = (
                    f"plotHeatmap -m {matrix_gz} "
                    f"-out {heatmap_pdf} "
                    f"--colorMap {color_map} "
                    f"{z_min_arg} {z_max_arg} "
                    f"--interpolation {interpolation} "
                    f"--plotTitle '{comp_name}' "
                    f"--samplesLabel {labels_merged}"
                )
                run_cmd(cmd)

            # 6. 自定义 overlay profile (所有 sample 线重叠在一个图上)
            profile_pdf = os.path.join(comp_dir, f"{comp_name}_profile.pdf")
            if not os.path.exists(profile_pdf):
                _plot_overlay_profile(matrix_gz, profile_pdf, merged_labels,
                                     title=comp_name)

            print(f"[INFO] Comparison '{comp_name}' done: {' + '.join(merged_labels)}")
            continue

        # 单组只需 >= 1 个 bigwig, 多组需 >= 2
        if n_groups == 1 and len(all_bws) < 1:
            print(f"[WARNING] Comparison '{comp_name}': no bigwig files found, skipping")
            continue
        elif n_groups > 1 and len(all_bws) < 2:
            print(f"[WARNING] Comparison '{comp_name}': not enough bigwig files ({len(all_bws)}), skipping")
            continue

        # 获取 BED 区域
        if per_group and n_groups >= 2:
            # per_group 模式：尝试获取每个 group 独立的 BED 文件
            bed_result = get_bed_for_comparison(comp, peakdir, outdir, group_map, dt_config, per_group=True)
        else:
            bed_result = get_bed_for_comparison(comp, peakdir, outdir, group_map, dt_config, per_group=False)

        # 判断返回类型：list (多 BED) 或 str (单 BED)
        if isinstance(bed_result, list) and bed_result:
            # 多 BED 模式（per_group）
            bed_files = [b[0] for b in bed_result]
            bed_labels = [b[1] for b in bed_result]
            bed_args_str = " ".join(bed_files)
            use_per_group = True
            print(f"[INFO] Using {len(bed_files)} separate BED files for per-group display")
        elif isinstance(bed_result, str) and bed_result and os.path.exists(bed_result):
            bed_args_str = bed_result
            bed_labels = None
            use_per_group = False
        else:
            print(f"[WARNING] No BED file available for comparison '{comp_name}', skipping")
            continue

        # computeMatrix
        matrix_gz = os.path.join(comp_dir, f"{comp_name}_matrix.gz")
        missing_arg = "--missingDataAsZero" if missing_as_zero else ""
        bw_args = " ".join(all_bws)
        labels_arg = " ".join(all_labels)

        if not os.path.exists(matrix_gz):
            if matrix_mode == "reference-point":
                cmd = (
                    f"computeMatrix reference-point "
                    f"-R {bed_args_str} "
                    f"-S {bw_args} "
                    f"--referencePoint {ref_point} "
                    f"-b {upstream} -a {downstream} "
                    f"--binSize {bin_size} "
                    f"-p {threads} "
                    f"{missing_arg} "
                    f"--samplesLabel {labels_arg} "
                    f"-o {matrix_gz}"
                )
            else:
                cmd = (
                    f"computeMatrix scale-regions "
                    f"-R {bed_args_str} "
                    f"-S {bw_args} "
                    f"-m {region_length} "
                    f"-b {upstream} -a {downstream} "
                    f"--binSize {bin_size} "
                    f"-p {threads} "
                    f"{missing_arg} "
                    f"--samplesLabel {labels_arg} "
                )
                if unscaled_5 > 0:
                    cmd += f" --unscaled5prime {unscaled_5}"
                if unscaled_3 > 0:
                    cmd += f" --unscaled3prime {unscaled_3}"
                cmd += f" -o {matrix_gz}"

            run_cmd(cmd)

        # plotHeatmap
        heatmap_pdf = os.path.join(comp_dir, f"{comp_name}_heatmap.pdf")
        z_min_arg = f"--zMin {z_min}" if z_min != "auto" else ""
        z_max_arg = f"--zMax {z_max}" if z_max != "auto" else ""
        # --perGroup: 当使用多个 BED 文件时才有意义（按 region group 拆分展示）
        heatmap_per_group = "--perGroup" if (per_group and use_per_group) else ""

        if not os.path.exists(heatmap_pdf):
            cmd = (
                f"plotHeatmap -m {matrix_gz} "
                f"-out {heatmap_pdf} "
                f"--colorMap {color_map} "
                f"{z_min_arg} {z_max_arg} "
                f"--interpolation {interpolation} "
                f"--plotTitle '{comp_name}' "
                f"--samplesLabel {labels_arg} "
                f"{heatmap_per_group}"
            )
            # 当有多个 BED（region groups）时，添加 regionsLabel
            if use_per_group and bed_labels:
                cmd += f" --regionsLabel {' '.join(bed_labels)}"
            run_cmd(cmd)

        # plotProfile — 使用自定义 overlay 函数，所有 sample 线重叠在同一坐标系
        profile_pdf = os.path.join(comp_dir, f"{comp_name}_profile.pdf")

        if not os.path.exists(profile_pdf):
            _plot_overlay_profile(matrix_gz, profile_pdf, all_labels,
                                 title=f"{comp_name} Profile")

        print(f"[INFO] Comparison '{comp_name}' done: {len(all_bws)} bigwigs, BED={bed_args_str}")


def main():
    parser = argparse.ArgumentParser(
        description="DeepTools analysis based on comparisons config (group-vs-group)"
    )
    parser.add_argument("--metadata", required=True, help="Path to metadata.csv")
    parser.add_argument("--bamdir", required=True, help="Directory containing sample BAM dirs")
    parser.add_argument("--peakdir", required=True, help="Directory containing peak calling results")
    parser.add_argument("--outdir", required=True, help="Output directory for deeptools results")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--species", required=True, help="Species name")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--norm", default="BPM", choices=["BPM", "RPKM", "CPM", "RPGC", "none"])

    args = parser.parse_args()

    # 读取配置
    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    dt_config = config.get("deeptools", {})

    # 解析 metadata
    pairs, group_map = parse_metadata(args.metadata, args.species, args.experiment)

    if not pairs:
        print("[ERROR] No valid sample pairs in metadata")
        sys.exit(1)

    print(f"[INFO] Parsed {len(pairs)} sample pairs, {len(group_map)} groups")
    for grp, samples in group_map.items():
        print(f"  Group '{grp}': {', '.join(samples)}")

    os.makedirs(args.outdir, exist_ok=True)

    # Step 1: 生成所有 IP 样本的 bigwig
    print("\n" + "=" * 60)
    print("[STEP 1] Generating BigWig files for all IP samples")
    print("=" * 60)
    # 兼容旧配置（subtract_input bool）和新配置（input_mode 三选项）
    input_mode = dt_config.get("input_mode", "none")
    # 向后兼容：旧配置 subtract_input: true -> input_mode: log2_ratio
    if input_mode == "none" and dt_config.get("subtract_input", False):
        input_mode = "log2_ratio"
    subtract_operation = dt_config.get("subtract_input_operation", "log2")
    bw_map, input_bw_map = generate_bigwigs(
        pairs, args.bamdir, args.outdir, args.threads, args.norm,
        input_mode=input_mode, operation=subtract_operation
    )
    mode_desc = {"none": "", "include_input": " (含 Input 对照)", "log2_ratio": f" (IP/Input {subtract_operation} ratio)"}
    print(f"[INFO] Generated/found {len(bw_map)} IP bigwig files{mode_desc.get(input_mode, '')}")
    if input_bw_map:
        print(f"[INFO] Generated/found {len(input_bw_map)} Input bigwig files")

    # Step 2: 重复间 QC
    if group_map:
        print("\n" + "=" * 60)
        print("[STEP 2] Replicate QC (within-group correlation)")
        print("=" * 60)
        run_replicate_qc(group_map, bw_map, args.outdir, dt_config, args.threads)
    else:
        print("\n[INFO] No groups defined, skipping replicate QC")

    # Step 3: 运行 comparisons（组间比较）
    comparisons = dt_config.get("comparisons", [])
    if comparisons:
        print("\n" + "=" * 60)
        print(f"[STEP 3] Running {len(comparisons)} comparison(s)")
        print("=" * 60)
        run_comparisons(comparisons, group_map, bw_map, args.peakdir, args.outdir, dt_config, args.threads, input_bw_map=input_bw_map, pairs=pairs)
    else:
        # 如果没有定义 comparisons，fallback: 每个 group 单独出图 + 全部合并出图
        print("\n[INFO] No comparisons defined, running default comparisons")

        default_comps = []

        if not group_map:
            # 无 group 信息，创建虚拟 group 包含所有 IP
            group_map["all"] = list(bw_map.keys())

        # 为每个 group 创建单组展示
        for grp_name in group_map.keys():
            default_comps.append({
                "name": f"single_{grp_name}",
                "groups": [grp_name],
                "bed_source": dt_config.get("bed_source", "union_peaks"),
            })

        # 如果有多个 group，额外做一个全部合并的比较
        if len(group_map) >= 2:
            default_comps.append({
                "name": "all_groups",
                "groups": list(group_map.keys()),
                "bed_source": dt_config.get("bed_source", "union_peaks"),
            })

        if default_comps:
            run_comparisons(default_comps, group_map, bw_map, args.peakdir, args.outdir, dt_config, args.threads, input_bw_map=input_bw_map, pairs=pairs)

    print("\n" + "=" * 60)
    print("[DONE] DeepTools analysis complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
