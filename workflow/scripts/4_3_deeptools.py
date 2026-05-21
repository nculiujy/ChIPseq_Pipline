import os
import sys
import argparse
import subprocess

def run_cmd(cmd):
    print(f"[RUNNING] {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed with exit code {e.returncode}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Run deeptools (bamCoverage, computeMatrix, plotProfile/plotHeatmap) based on MACS2 peaks.")
    parser.add_argument("--metadata", required=True, help="Path to metadata.csv")
    parser.add_argument("--bamdir", required=True, help="Directory containing sample bam files (e.g. result/3_ChIPseq)")
    parser.add_argument("--peakdir", required=True, help="Directory containing peak calling results (e.g. result/4_analyse/peakcalling)")
    parser.add_argument("--outdir", required=True, help="Directory to save deeptools output")
    parser.add_argument("--threads", type=int, default=8, help="Number of threads")
    parser.add_argument("--norm", default="BPM", choices=["BPM", "RPKM", "CPM", "none"], help="Normalization method for bamCoverage")

    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # 1. Parse metadata to get sample pairs
    pairs = []
    with open(args.metadata, "r") as f:
        header = f.readline()
        for line in f:
            if line.strip():
                treat, control = [x.strip() for x in line.strip().split(",")]
                pairs.append({"treat": treat, "control": control})

    # 2. Process each pair
    for pair in pairs:
        treat = pair["treat"]
        control = pair["control"]
        pair_name = f"{treat}_vs_{control}"
        print(f"\n========== Processing {pair_name} ==========")

        pair_outdir = os.path.join(args.outdir, pair_name)
        os.makedirs(pair_outdir, exist_ok=True)

        treat_bam = os.path.join(args.bamdir, treat, "accepted_hits.sorted.unique.bam")
        control_bam = os.path.join(args.bamdir, control, "accepted_hits.sorted.unique.bam")

        if not os.path.exists(treat_bam):
            print(f"[WARNING] Treat BAM not found: {treat_bam}. Skipping.")
            continue
        if not os.path.exists(control_bam):
            print(f"[WARNING] Control BAM not found: {control_bam}. Skipping.")
            continue

        # Step A: Convert BAM to BigWig using bamCoverage
        treat_bw = os.path.join(pair_outdir, f"{treat}.bw")
        control_bw = os.path.join(pair_outdir, f"{control}.bw")

        norm_arg = f"--normalizeUsing {args.norm}" if args.norm != "none" else ""

        if not os.path.exists(treat_bw):
            cmd = f"bamCoverage -b {treat_bam} -o {treat_bw} -p {args.threads} {norm_arg}"
            run_cmd(cmd)
        
        if not os.path.exists(control_bw):
            cmd = f"bamCoverage -b {control_bam} -o {control_bw} -p {args.threads} {norm_arg}"
            run_cmd(cmd)

        # Step B: computeMatrix
        # deeptools 对 tab 分隔要求严格，MACS2 生成的可能是空格分隔，使用 tab_bed 更安全
        peak_bed = os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks_tab.bed")
        if not os.path.exists(peak_bed):
            print(f"[WARNING] Peak BED not found: {peak_bed}. Cannot run computeMatrix. Skipping.")
            continue
        
        if os.path.getsize(peak_bed) == 0:
            print(f"[WARNING] Peak BED is empty (0 bytes): {peak_bed}. MACS2 found no peaks. Skipping computeMatrix.")
            continue

        matrix_gz = os.path.join(pair_outdir, f"matrix_{pair_name}.gz")
        
        # 使用 reference-point 模式，以 peak 的中心点为 reference point
        cmd = (
            f"computeMatrix reference-point "
            f"-R {peak_bed} "
            f"-S {treat_bw} {control_bw} "
            f"--referencePoint center "
            f"-b 2000 -a 2000 "
            f"--binSize 10 "
            f"-p {args.threads} "
            f"-o {matrix_gz}"
        )
        run_cmd(cmd)

        # Step C: plotProfile
        plot_profile_pdf = os.path.join(pair_outdir, f"{pair_name}_plotProfile.pdf")
        cmd = (
            f"plotProfile -m {matrix_gz} "
            f"-out {plot_profile_pdf} "
            f"--plotTitle '{pair_name} Profile' "
            f"--samplesLabel {treat} {control}"
        )
        run_cmd(cmd)

        # Step D: plotHeatmap
        plot_heatmap_pdf = os.path.join(pair_outdir, f"{pair_name}_plotHeatmap.pdf")
        
        # deeptools 默认根据每个样本自身的分布自动计算 min 和 max（会导致强信号和弱信号看起来颜色一样深）
        # 我们添加 --zMin auto --zMax auto，或者限制百分位数（例如：--zMin 0）来让对比更合理，
        # 并通过 --colorMap 保证视觉上的一致性
        cmd = (
            f"plotHeatmap -m {matrix_gz} "
            f"-out {plot_heatmap_pdf} "
            f"--colorMap RdYlBu_r "
            f"--zMin 0 "
            f"--plotTitle '{pair_name} Heatmap' "
            f"--samplesLabel {treat} {control}"
        )
        run_cmd(cmd)

    print("\n========== Deeptools processing finished! ==========")

if __name__ == "__main__":
    main()