"""
4_4_consensus_peaks.py
======================
对同一重复组（group）内的 peaks 取交集，生成 consensus peaks。

策略：
1. 读取 metadata.csv，按 group 列分组
2. 对每个 group 内的所有 IP_vs_Input peak 文件做 bedtools multiinter
3. 根据阈值（min_overlap）筛选：要求 ≥ N 个重复都覆盖的区域才保留
4. 输出 consensus peaks BED 文件

同时，使用 deeptools multiBigwigSummary + plotCorrelation 评估重复间相关性。
"""

import os
import sys
import argparse
import subprocess
import pandas as pd


def run_cmd(cmd, allow_fail=False):
    print(f"[RUNNING] {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0 and not allow_fail:
        print(f"[ERROR] {result.stderr}")
        sys.exit(1)
    if result.stdout:
        print(result.stdout)
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate consensus peaks per replicate group")
    parser.add_argument("--metadata", required=True, help="Path to metadata.csv (IP sample,Input,group)")
    parser.add_argument("--peakdir", required=True, help="Directory containing peak calling results")
    parser.add_argument("--bamdir", required=True, help="Directory containing BAM files (for bigwig)")
    parser.add_argument("--outdir", required=True, help="Output directory for consensus peaks")
    parser.add_argument("--min_overlap", type=int, default=2, help="Minimum number of replicates a peak must appear in (default: 2)")
    parser.add_argument("--min_fraction", type=float, default=0.5, help="Minimum reciprocal overlap fraction to consider two peaks as overlapping (default: 0.5)")
    parser.add_argument("--threads", type=int, default=8, help="Number of threads")
    parser.add_argument("--norm", default="BPM", help="Normalization method for bamCoverage")

    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # 读取 metadata
    df = pd.read_csv(args.metadata)

    # 兼容性：如果没有 group 列，所有样本作为一个组
    if "group" not in df.columns:
        print("[WARNING] metadata.csv 中没有 group 列，将所有样本视为同一组 'all'")
        df["group"] = "all"

    # 按 group 分组
    groups = df.groupby("group")

    print(f"[INFO] 共 {len(groups)} 个重复组: {list(groups.groups.keys())}")
    print(f"[INFO] 最小重叠阈值: {args.min_overlap}")

    consensus_results = {}

    for group_name, group_df in groups:
        print(f"\n{'='*60}")
        print(f"[GROUP] {group_name} — 含 {len(group_df)} 个样本对")
        print(f"{'='*60}")

        # 收集该组内所有 peak 文件
        peak_files = []
        bw_files = []  # 用于后续 correlation 分析

        for _, row in group_df.iterrows():
            treat = row["IP sample"].strip()
            control = row["Input"].strip()
            pair_name = f"{treat}_vs_{control}"

            # peak 文件路径 - 尝试多种命名格式
            peak_candidates = [
                os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks_tab.bed"),
                os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks.narrowPeak"),
                os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks.broadPeak"),
            ]
            peak_file = None
            for candidate in peak_candidates:
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    peak_file = candidate
                    break

            if peak_file:
                peak_files.append(peak_file)
            else:
                print(f"  [WARNING] Peak file not found for {pair_name}, tried:")
                for c in peak_candidates:
                    print(f"    - {c}")

            # BAM → BigWig (用于相关性分析)
            treat_bam = os.path.join(args.bamdir, treat, "accepted_hits.sorted.unique.bam")
            if os.path.exists(treat_bam):
                bw_dir = os.path.join(args.outdir, "bigwig")
                os.makedirs(bw_dir, exist_ok=True)
                bw_file = os.path.join(bw_dir, f"{treat}.bw")
                if not os.path.exists(bw_file):
                    norm_arg = f"--normalizeUsing {args.norm}" if args.norm != "none" else ""
                    bam_cmd = f"bamCoverage -b {treat_bam} -o {bw_file} -p {args.threads} {norm_arg}"
                    run_cmd(bam_cmd)
                bw_files.append(bw_file)

        if len(peak_files) < 2:
            print(f"  [INFO] 组 {group_name} 只有 {len(peak_files)} 个有效 peak 文件，跳过 consensus 分析")
            if len(peak_files) == 1:
                # 单个文件直接作为 consensus
                consensus_bed = os.path.join(args.outdir, f"consensus_{group_name}.bed")
                run_cmd(f"cp {peak_files[0]} {consensus_bed}")
                consensus_results[group_name] = consensus_bed
            continue

        # ============================================================
        # Step 1: 找在 ≥ min_overlap 个重复中都出现的 peaks
        # 策略：用 bedtools intersect 找有重叠的 peaks，保留原始坐标并 merge
        # 这样保留的是完整的 peak 区域，而不是 multiinter 的碎片
        # ============================================================
        group_outdir = os.path.join(args.outdir, f"group_{group_name}")
        os.makedirs(group_outdir, exist_ok=True)

        # 先对每个 peak 文件排序
        sorted_peaks = []
        for i, pf in enumerate(peak_files):
            sorted_file = os.path.join(group_outdir, f"sorted_{i}.bed")
            run_cmd(f"cut -f1-3 {pf} | sort -k1,1 -k2,2n > {sorted_file}")
            sorted_peaks.append(sorted_file)

        consensus_bed = os.path.join(args.outdir, f"consensus_{group_name}.bed")

        if len(sorted_peaks) == 2 and args.min_overlap <= 2:
            # 两个重复：找互相有重叠的 peaks，然后 merge 为共识区域
            # 使用 -f 参数要求至少 min_fraction 的重叠比例（避免 1bp 碰瓷也算重叠）
            min_frac = args.min_fraction
            # A 中与 B 有 ≥min_fraction 重叠的 peaks（保留 A 的原始坐标）
            intersect_a = os.path.join(group_outdir, "intersect_a.bed")
            run_cmd(
                f"bedtools intersect -a {sorted_peaks[0]} -b {sorted_peaks[1]} "
                f"-f {min_frac} -wa | sort -k1,1 -k2,2n > {intersect_a}"
            )
            # B 中与 A 有 ≥min_fraction 重叠的 peaks（保留 B 的原始坐标）
            intersect_b = os.path.join(group_outdir, "intersect_b.bed")
            run_cmd(
                f"bedtools intersect -a {sorted_peaks[1]} -b {sorted_peaks[0]} "
                f"-f {min_frac} -wa | sort -k1,1 -k2,2n > {intersect_b}"
            )
            # 合并两个方向的 peaks，然后 bedtools merge 把重叠区域合并为单一 consensus peak
            run_cmd(
                f"cat {intersect_a} {intersect_b} | sort -k1,1 -k2,2n | "
                f"bedtools merge -i - > {consensus_bed}"
            )
            # 统计 merge 前后差异
            with open(intersect_a) as f:
                n_a = sum(1 for _ in f)
            with open(intersect_b) as f:
                n_b = sum(1 for _ in f)
            print(f"  [DEBUG] A∩B={n_a}, B∩A={n_b}, 合并前总={n_a+n_b}")
        else:
            # 多个重复：对每个 peak 计算在几个文件中出现（有任何碱基重叠就算）
            # 先合并所有 peaks 为候选区域
            all_peaks_merged = os.path.join(group_outdir, "all_peaks_merged.bed")
            run_cmd(
                f"cat {' '.join(sorted_peaks)} | sort -k1,1 -k2,2n | "
                f"bedtools merge -i - > {all_peaks_merged}"
            )

            # 对每个候选区域，统计在几个原始文件中有重叠
            candidates = []
            with open(all_peaks_merged) as f:
                for line in f:
                    parts = line.strip().split("\t")
                    candidates.append(parts[:3])

            hit_counts = [0] * len(candidates)
            for sp in sorted_peaks:
                tmp_out = os.path.join(group_outdir, "tmp_intersect.bed")
                run_cmd(
                    f"bedtools intersect -a {all_peaks_merged} -b {sp} "
                    f"-c > {tmp_out}",
                    allow_fail=True
                )
                with open(tmp_out) as f:
                    for idx, line in enumerate(f):
                        parts = line.strip().split("\t")
                        count = int(parts[-1])
                        if count > 0:
                            hit_counts[idx] += 1

            # 过滤：保留 >= min_overlap 个文件命中的区域
            with open(consensus_bed, "w") as fout:
                for idx, cand in enumerate(candidates):
                    if hit_counts[idx] >= args.min_overlap:
                        fout.write("\t".join(cand) + "\n")

        # 统计
        with open(consensus_bed) as f:
            n_consensus = sum(1 for _ in f)

        total_peaks_per_rep = []
        for pf in peak_files:
            with open(pf) as f:
                total_peaks_per_rep.append(sum(1 for _ in f))

        avg_peaks = sum(total_peaks_per_rep) / len(total_peaks_per_rep) if total_peaks_per_rep else 0

        print(f"  [RESULT] 组 {group_name}: {n_consensus} consensus peaks")
        print(f"           (平均每个重复 {avg_peaks:.0f} peaks, 阈值 ≥{args.min_overlap}/{len(peak_files)})")

        consensus_results[group_name] = consensus_bed

        # 清理排序临时文件
        for sf in sorted_peaks:
            os.remove(sf)

        # ============================================================
        # Step 3: deeptools — 重复样本间相关性分析
        # ============================================================
        if len(bw_files) >= 2:
            print(f"\n  [DEEPTOOLS] 计算重复间相关性 (multiBigwigSummary + plotCorrelation)")

            corr_outdir = os.path.join(group_outdir, "correlation")
            os.makedirs(corr_outdir, exist_ok=True)

            # multiBigwigSummary
            npz_file = os.path.join(corr_outdir, f"{group_name}_scores.npz")
            bw_list = " ".join(bw_files)
            labels = " ".join([os.path.basename(bw).replace(".bw", "") for bw in bw_files])

            run_cmd(
                f"multiBigwigSummary bins "
                f"-b {bw_list} "
                f"--labels {labels} "
                f"-p {args.threads} "
                f"--binSize 1000 "
                f"-o {npz_file}"
            )

            # plotCorrelation — 热图
            corr_heatmap = os.path.join(corr_outdir, f"{group_name}_correlation_heatmap.pdf")
            run_cmd(
                f"plotCorrelation "
                f"-in {npz_file} "
                f"--corMethod pearson "
                f"--whatToPlot heatmap "
                f"--skipZeros "
                f"--plotTitle 'Replicate Correlation ({group_name})' "
                f"--removeOutliers "
                f"-o {corr_heatmap} "
                f"--outFileCorMatrix {corr_outdir}/{group_name}_corr_matrix.tab"
            )

            # plotCorrelation — 散点图
            corr_scatter = os.path.join(corr_outdir, f"{group_name}_correlation_scatter.pdf")
            run_cmd(
                f"plotCorrelation "
                f"-in {npz_file} "
                f"--corMethod pearson "
                f"--whatToPlot scatterplot "
                f"--skipZeros "
                f"--plotTitle 'Replicate Scatter ({group_name})' "
                f"--removeOutliers "
                f"-o {corr_scatter}"
            )

            # plotPCA
            pca_plot = os.path.join(corr_outdir, f"{group_name}_PCA.pdf")
            run_cmd(
                f"plotPCA "
                f"-in {npz_file} "
                f"--plotTitle 'PCA ({group_name})' "
                f"-o {pca_plot}"
            )

            print(f"  [DEEPTOOLS] 相关性分析完成:")
            print(f"    - 热图: {corr_heatmap}")
            print(f"    - 散点: {corr_scatter}")
            print(f"    - PCA:  {pca_plot}")

    # ============================================================
    # 汇总报告
    # ============================================================
    print(f"\n{'='*60}")
    print("[SUMMARY] Consensus Peaks 汇总:")
    print(f"{'='*60}")
    for group_name, bed_path in consensus_results.items():
        if os.path.exists(bed_path):
            with open(bed_path) as f:
                n = sum(1 for _ in f)
            print(f"  {group_name}: {n} consensus peaks -> {bed_path}")
        else:
            print(f"  {group_name}: 文件未生成")

    print("\n[DONE] Consensus peaks 分析完成！")


if __name__ == "__main__":
    main()
