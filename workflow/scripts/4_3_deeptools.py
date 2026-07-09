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


def parse_metadata(metadata_file):
    """
    解析 metadata.csv，返回:
    - pairs: list of dict {treat, control, group}
    - group_map: dict {group_name: [list of IP sample names]}
    """
    df = pd.read_csv(metadata_file)
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


def generate_bigwigs(pairs, bamdir, outdir, threads, norm):
    """为所有 IP 样本生成 bigwig 文件（跳过已存在的）"""
    bw_map = {}  # sample_name -> bigwig_path
    norm_arg = f"--normalizeUsing {norm}" if norm != "none" else ""

    # 收集所有唯一的 IP 样本
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

    return bw_map


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


def get_bed_for_comparison(comparison, peakdir, outdir, group_map, dt_config):
    """
    确定 comparison 使用的 BED 区域文件。
    - consensus_peaks: 使用所有涉及 group 的 consensus peaks 合集
    - union_peaks: 使用全局 union peaks
    - 其他: 使用全局 bed_source 配置
    """
    comp_bed_source = comparison.get("bed_source", dt_config.get("bed_source", "union_peaks"))
    groups = comparison.get("groups", [])

    if comp_bed_source == "consensus_peaks":
        # 合并涉及的 groups 的 consensus peaks
        consensus_dir = os.path.join(peakdir, "..", "consensus_peaks")
        consensus_beds = []
        for grp in groups:
            grp_bed = os.path.join(consensus_dir, f"{grp}_consensus.bed")
            if os.path.exists(grp_bed):
                consensus_beds.append(grp_bed)
            else:
                print(f"[WARNING] Consensus BED not found for group '{grp}': {grp_bed}")

        if not consensus_beds:
            print("[WARNING] No consensus BED files found, falling back to union_peaks")
            return _get_union_bed(peakdir, outdir, group_map)

        # 合并多个 consensus beds
        comp_name = comparison.get("name", "comparison")
        merged_bed = os.path.join(outdir, "comparisons", comp_name, "merged_consensus.bed")
        os.makedirs(os.path.dirname(merged_bed), exist_ok=True)

        if not os.path.exists(merged_bed):
            cat_cmd = f"cat {' '.join(consensus_beds)} | sort -k1,1 -k2,2n | bedtools merge -i - > {merged_bed}"
            run_cmd(cat_cmd)

        return merged_bed

    elif comp_bed_source == "union_peaks":
        return _get_union_bed(peakdir, outdir, group_map)

    else:
        # 使用全局配置中的 bed_source
        return _get_union_bed(peakdir, outdir, group_map)


def _get_union_bed(peakdir, outdir, group_map):
    """获取或创建 union peaks BED"""
    union_bed = os.path.join(outdir, "union_peaks.bed")
    if os.path.exists(union_bed):
        return union_bed

    # 收集所有 peak 文件
    import glob
    peak_files = glob.glob(os.path.join(peakdir, "*", "*_peaks_tab.bed"))
    peak_files = [f for f in peak_files if os.path.getsize(f) > 0]

    if not peak_files:
        print("[ERROR] No peak files found for union_peaks")
        return None

    temp_concat = os.path.join(outdir, "temp_all_peaks.bed")
    concat_cmd = f"cat {' '.join(peak_files)} | sort -k1,1 -k2,2n > {temp_concat}"
    run_cmd(concat_cmd)

    merge_cmd = f"bedtools merge -i {temp_concat} > {union_bed}"
    run_cmd(merge_cmd)
    os.remove(temp_concat)

    return union_bed


def run_comparisons(comparisons, group_map, bw_map, peakdir, outdir, dt_config, threads):
    """执行 comparisons 中定义的每个组间比较"""
    matrix_mode = dt_config.get("matrix_mode", "reference-point")
    bin_size = dt_config.get("bin_size", 10)
    missing_as_zero = dt_config.get("missing_data_as_zero", True)
    plot_config = dt_config.get("plot", {})
    color_map = plot_config.get("color_map", "RdYlBu_r")
    z_min = plot_config.get("z_min", 0)
    z_max = plot_config.get("z_max", "auto")
    interpolation = plot_config.get("interpolation", "bilinear")

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

        print(f"\n{'='*60}")
        print(f"[COMPARISON] {comp_name}: {' vs '.join(comp_groups)}")
        print(f"{'='*60}")

        comp_dir = os.path.join(outdir, "comparisons", comp_name)
        os.makedirs(comp_dir, exist_ok=True)

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

        if len(all_bws) < 2:
            print(f"[WARNING] Comparison '{comp_name}': not enough bigwig files ({len(all_bws)}), skipping")
            continue

        # 获取 BED 区域
        bed_file = get_bed_for_comparison(comp, peakdir, outdir, group_map, dt_config)
        if not bed_file or not os.path.exists(bed_file):
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
                    f"-R {bed_file} "
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
                    f"-R {bed_file} "
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

        if not os.path.exists(heatmap_pdf):
            cmd = (
                f"plotHeatmap -m {matrix_gz} "
                f"-out {heatmap_pdf} "
                f"--colorMap {color_map} "
                f"{z_min_arg} {z_max_arg} "
                f"--interpolation {interpolation} "
                f"--plotTitle '{comp_name}' "
                f"--samplesLabel {labels_arg}"
            )
            run_cmd(cmd)

        # plotProfile
        profile_pdf = os.path.join(comp_dir, f"{comp_name}_profile.pdf")
        if not os.path.exists(profile_pdf):
            cmd = (
                f"plotProfile -m {matrix_gz} "
                f"-out {profile_pdf} "
                f"--plotTitle '{comp_name} Profile' "
                f"--samplesLabel {labels_arg} "
                f"--perGroup"
            )
            run_cmd(cmd)

        print(f"[INFO] Comparison '{comp_name}' done: {len(all_bws)} bigwigs, BED={bed_file}")


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
    pairs, group_map = parse_metadata(args.metadata)

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
    bw_map = generate_bigwigs(pairs, args.bamdir, args.outdir, args.threads, args.norm)
    print(f"[INFO] Generated/found {len(bw_map)} bigwig files")

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
        run_comparisons(comparisons, group_map, bw_map, args.peakdir, args.outdir, dt_config, args.threads)
    else:
        # 如果没有定义 comparisons，fallback 到旧逻辑：所有样本一起做一个比较
        print("\n[INFO] No comparisons defined, running default all-samples comparison")
        if len(bw_map) >= 2:
            default_comp = {
                "name": "all_samples",
                "groups": list(group_map.keys()) if group_map else [],
                "bed_source": dt_config.get("bed_source", "union_peaks"),
            }
            # 如果没有 group，直接用所有 IP 样本
            if not group_map:
                # 创建一个虚拟 group 包含所有 IP
                group_map["all"] = list(bw_map.keys())
                default_comp["groups"] = ["all"]

            run_comparisons([default_comp], group_map, bw_map, args.peakdir, args.outdir, dt_config, args.threads)

    print("\n" + "=" * 60)
    print("[DONE] DeepTools analysis complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
