import os
import sys
import argparse
import subprocess
import yaml

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
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--species", required=True, help="Species name")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    parser.add_argument("--threads", type=int, default=8, help="Number of threads")
    parser.add_argument("--norm", default="BPM", choices=["BPM", "RPKM", "CPM", "none"], help="Normalization method for bamCoverage")

    args = parser.parse_args()
    
    # 读取配置文件
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)
    
    # 获取deeptools配置
    dt_config = config.get('deeptools', {})
    matrix_mode = dt_config.get('matrix_mode', 'reference-point')
    bin_size = dt_config.get('bin_size', 10)
    missing_as_zero = dt_config.get('missing_data_as_zero', True)
    bed_source = dt_config.get('bed_source', 'macs2_peaks')
    
    # 获取plot配置
    plot_config = dt_config.get('plot', {})
    color_map = plot_config.get('color_map', 'RdYlBu_r')
    z_min = plot_config.get('z_min', 0)
    z_max = plot_config.get('z_max', 'auto')
    interpolation = plot_config.get('interpolation', 'bilinear')
    
    # 获取模式特定参数
    if matrix_mode == "reference-point":
        ref_config = dt_config.get('reference_point', {})
        ref_point = ref_config.get('point', 'center')
        upstream = ref_config.get('upstream', 2000)
        downstream = ref_config.get('downstream', 2000)
        print(f"[INFO] Using reference-point mode: point={ref_point}, upstream={upstream}, downstream={downstream}")
    else:  # scale-regions
        scale_config = dt_config.get('scale_regions', {})
        region_length = scale_config.get('region_length', 5000)
        upstream = scale_config.get('upstream', 2000)
        downstream = scale_config.get('downstream', 2000)
        unscaled_5 = scale_config.get('unscaled_5prime', 0)
        unscaled_3 = scale_config.get('unscaled_3prime', 0)
        print(f"[INFO] Using scale-regions mode: length={region_length}, upstream={upstream}, downstream={downstream}")

    os.makedirs(args.outdir, exist_ok=True)

    # 1. Parse metadata to get sample pairs
    pairs = []
    with open(args.metadata, "r") as f:
        header = f.readline()
        for line in f:
            if line.strip():
                treat, control = [x.strip() for x in line.strip().split(",")]
                pairs.append({"treat": treat, "control": control})
    
    # 如果使用 union_peaks，需要先合并所有样本的peaks
    union_bed = None
    if bed_source == "union_peaks":
        union_bed = os.path.join(args.outdir, "union_peaks.bed")
        if not os.path.exists(union_bed):
            print(f"[INFO] Creating union peaks from all sample pairs...")
            all_peak_files = []
            for pair in pairs:
                pair_name = f"{pair['treat']}_vs_{pair['control']}"
                peak_file = os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks_tab.bed")
                if os.path.exists(peak_file) and os.path.getsize(peak_file) > 0:
                    all_peak_files.append(peak_file)
            
            if not all_peak_files:
                print(f"[ERROR] No valid peak files found for union_peaks")
                sys.exit(1)
            
            # 合并所有peaks并去重、排序
            # 使用bedtools merge需要先sort，这里用简单的cat + sort + bedtools merge
            temp_concat = os.path.join(args.outdir, "temp_all_peaks.bed")
            concat_cmd = f"cat {' '.join(all_peak_files)} | sort -k1,1 -k2,2n > {temp_concat}"
            run_cmd(concat_cmd)
            
            # 使用bedtools merge合并重叠区域
            merge_cmd = f"bedtools merge -i {temp_concat} > {union_bed}"
            run_cmd(merge_cmd)
            
            # 清理临时文件
            os.remove(temp_concat)
            
            # 统计信息
            with open(union_bed) as f:
                num_regions = sum(1 for _ in f)
            print(f"[INFO] Union peaks created: {num_regions} regions in {union_bed}")
        else:
            print(f"[INFO] Using existing union peaks: {union_bed}")

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

        # Step B: 获取BED文件
        # 根据配置决定使用哪个BED文件作为背景区域
        project_key = f"{args.species}_{args.experiment}"
        
        if bed_source == "macs2_peaks":
            # 使用MACS2 peak calling的输出（每对样本用自己的peaks）
            peak_bed = os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks_tab.bed")
            bed_description = f"MACS2 peaks (pair-specific: {pair_name})"
        elif bed_source == "union_peaks":
            # 使用所有样本的peaks合集
            peak_bed = union_bed
            bed_description = f"Union peaks (all samples merged)"
        elif bed_source == "custom":
            # 使用自定义BED文件
            custom_beds = dt_config.get('custom_bed_files', {})
            if project_key in custom_beds:
                peak_bed = custom_beds[project_key]
                bed_description = f"custom BED ({peak_bed})"
            else:
                print(f"[ERROR] bed_source='custom' but no custom_bed_files defined for {project_key}")
                continue
        elif bed_source == "genes":
            # 使用基因注释GTF文件
            gtf_files = dt_config.get('gtf_files', {})
            if args.species in gtf_files:
                gtf_file = gtf_files[args.species]
                # 将GTF转换为BED格式（只提取基因区域）
                peak_bed = os.path.join(pair_outdir, "genes_from_gtf.bed")
                if not os.path.exists(peak_bed):
                    convert_cmd = f"awk '$3==\"gene\" {{print $1,$4-1,$5,$10,$6,$7}}' {gtf_file} | sed 's/[;\"]//g' > {peak_bed}"
                    print(f"[INFO] Converting GTF to BED: {convert_cmd}")
                    run_cmd(convert_cmd)
                bed_description = f"genes from GTF ({gtf_file})"
            else:
                print(f"[ERROR] bed_source='genes' but no gtf_files defined for {args.species}")
                continue
        else:
            print(f"[ERROR] Unknown bed_source: {bed_source}")
            continue
        
        print(f"[INFO] Using BED source: {bed_description}")
        
        if not os.path.exists(peak_bed):
            print(f"[WARNING] BED file not found: {peak_bed}. Cannot run computeMatrix. Skipping.")
            continue
        
        if os.path.getsize(peak_bed) == 0:
            print(f"[WARNING] BED file is empty (0 bytes): {peak_bed}. Skipping computeMatrix.")
            continue

        matrix_gz = os.path.join(pair_outdir, f"matrix_{pair_name}.gz")
        
        # 构建computeMatrix命令
        missing_arg = "--missingDataAsZero" if missing_as_zero else ""
        
        if matrix_mode == "reference-point":
            cmd = (
                f"computeMatrix reference-point "
                f"-R {peak_bed} "
                f"-S {treat_bw} {control_bw} "
                f"--referencePoint {ref_point} "
                f"-b {upstream} -a {downstream} "
                f"--binSize {bin_size} "
                f"-p {args.threads} "
                f"{missing_arg} "
                f"-o {matrix_gz}"
            )
        else:  # scale-regions
            cmd = (
                f"computeMatrix scale-regions "
                f"-R {peak_bed} "
                f"-S {treat_bw} {control_bw} "
                f"-m {region_length} "
                f"-b {upstream} -a {downstream} "
                f"--binSize {bin_size} "
                f"-p {args.threads} "
                f"{missing_arg} "
            )
            if unscaled_5 > 0:
                cmd += f" --unscaled5prime {unscaled_5}"
            if unscaled_3 > 0:
                cmd += f" --unscaled3prime {unscaled_3}"
            cmd += f" -o {matrix_gz}"
        
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
        
        # 构建热图参数
        z_min_arg = f"--zMin {z_min}" if z_min != "auto" else ""
        z_max_arg = f"--zMax {z_max}" if z_max != "auto" else ""
        
        cmd = (
            f"plotHeatmap -m {matrix_gz} "
            f"-out {plot_heatmap_pdf} "
            f"--colorMap {color_map} "
            f"{z_min_arg} {z_max_arg} "
            f"--interpolation {interpolation} "
            f"--plotTitle '{pair_name} Heatmap' "
            f"--samplesLabel {treat} {control}"
        )
        run_cmd(cmd)

    print("\n========== Deeptools processing finished! ==========")

if __name__ == "__main__":
    main()