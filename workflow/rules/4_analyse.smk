# 根据物种确定 MACS2 的 genome_size 参数
def get_genome_size(species):
    """
    获取 MACS2 的 genome size 参数。
    优先使用 config 中的 macs2_genome_size；
    如果设置为 'auto' 或未设置，则根据 species 推断。
    """
    genome_size = config.get("macs2_genome_size", "auto")
    
    # 如果配置了固定值（非 auto），直接返回
    if genome_size != "auto":
        return genome_size
    
    # 否则根据物种自动推断
    species = str(species).lower()
    if species == "tair":
        return "1.2e8"  # 拟南芥
    elif species == "mm":
        return "mm"     # 小鼠
    elif species == "homo":
        return "hs"     # 人类
    else:
        # 默认返回用户输入的原始值或一个安全默认值
        return species

rule analyse_peakcalling:
    input:
        script = "workflow/scripts/4_1_peakcalling.pl",
        metadata = "config/metadata.csv",
        step1_marker = "result/{species}/{experiment}/3_ChIPseq/step1_finished.txt"
    output:
        "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt"
    log:
        "logs/{species}/{experiment}/4_1_peakcalling.log"
    params:
        qval = config["macs2_cutoff_value"],
        cutoff_type = config["macs2_cutoff_type"],
        norm_method = config["normalization_method"],
        peak_type = config["macs2_peak_type"],
        broad_cutoff = config.get("macs2_broad_cutoff", 0.1),
        threads = config["threads"],
        genome_size = lambda wildcards: get_genome_size(wildcards.species),
        broad_cutoff_flag = lambda wildcards: f"--broad_cutoff {config.get('macs2_broad_cutoff', 0.1)}" if config["macs2_peak_type"] == "broad" else ""
    shell:
        """
        perl {input.script} \
            --metadata {input.metadata} \
            --bamdir result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq \
            --outdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --picdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling/pictures \
            --genome {params.genome_size} \
            --qval {params.qval} \
            --cutoff_type {params.cutoff_type} \
            --norm_method {params.norm_method} \
            --peak_type {params.peak_type} \
            {params.broad_cutoff_flag} \
            --threads {params.threads} > {log} 2>&1
        
        touch {output}
        """

# 定义一个辅助函数，用于获取所有样本对名称
def get_sample_pairs(metadata_file):
    """解析 metadata.csv，支持 2 列 (IP,Input) 和 3 列 (IP,Input,group) 格式"""
    pairs = []
    with open(metadata_file, 'r') as f:
        next(f)  # skip header
        for line in f:
            if line.strip():
                fields = line.strip().split(',')
                treat = fields[0].strip()
                control = fields[1].strip()
                pairs.append(f"{treat}_vs_{control}")
    return pairs


def get_replicate_groups(metadata_file):
    """
    解析 metadata.csv 的 group 列，返回 {group_name: [pair_name, ...]} 字典。
    仅当 metadata 含第 3 列且 group 非空时生效。
    """
    groups = {}
    with open(metadata_file, 'r') as f:
        header = next(f).strip().split(',')
        has_group = len(header) >= 3
        for line in f:
            if line.strip():
                fields = line.strip().split(',')
                treat = fields[0].strip()
                control = fields[1].strip()
                pair_name = f"{treat}_vs_{control}"
                group = fields[2].strip() if has_group and len(fields) >= 3 else ""
                if group:
                    groups.setdefault(group, []).append(pair_name)
    return groups


# 动态获取样本对和重复组
SAMPLE_PAIRS = get_sample_pairs("config/metadata.csv")
REPLICATE_GROUPS = get_replicate_groups("config/metadata.csv")

rule analyse_annoChIPPeaks_single:
    input:
        script = "workflow/scripts/4_2_annoChIPPeaks.R",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        config = ancient("config/config.yaml")
    output:
        "result/{species}/{experiment}/4_analyse/annoChIPPeaks/{pair}/{pair}_peak_anno.csv",
        "result/{species}/{experiment}/4_analyse/annoChIPPeaks/{pair}/{pair}_pie_bp±2000.pdf"
    log:
        "logs/{species}/{experiment}/4_2_annoChIPPeaks_{pair}.log"
    params:
        peak_ext = lambda wildcards: "broadPeak" if config["macs2_peak_type"] == "broad" else "narrowPeak"
    shell:
        """
        Rscript {input.script} \
            --peakfile result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling/{wildcards.pair}/{wildcards.pair}_peaks.{params.peak_ext} \
            --output result/{wildcards.species}/{wildcards.experiment}/4_analyse/annoChIPPeaks/{wildcards.pair} \
            --name {wildcards.pair} \
            --config {input.config} > {log} 2>&1
        """

# 这里因为有 {pair} 通配符和 {species}/{experiment} 的组合，
# 为了简化 DAG，我们需要明确告诉 snakemake expand 所有的 pair
def get_anno_targets(wildcards):
    return expand("result/{species}/{experiment}/4_analyse/annoChIPPeaks/{pair}/{pair}_peak_anno.csv",
                  species=wildcards.species,
                  experiment=wildcards.experiment,
                  pair=SAMPLE_PAIRS)

rule analyse_annoChIPPeaks_all:
    input:
        # 依赖于所有 single 注释任务的完成
        single_annos=get_anno_targets
    output:
        "result/{species}/{experiment}/4_analyse/annoChIPPeaks_finished.txt"
    shell:
        """
        touch {output}
        """

rule analyse_deeptools:
    input:
        script = "workflow/scripts/4_3_deeptools.py",
        metadata = "config/metadata.csv",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        config = ancient("config/config.yaml")
    output:
        "result/{species}/{experiment}/4_analyse/deeptools_finished.txt"
    log:
        "logs/{species}/{experiment}/4_3_deeptools.log"
    params:
        threads = config["threads"],
        norm = config["normalization_method"]
    shell:
        """
        python {input.script} \
            --metadata {input.metadata} \
            --bamdir result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq \
            --peakdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --outdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/deeptools \
            --config {input.config} \
            --species {wildcards.species} \
            --experiment {wildcards.experiment} \
            --threads {params.threads} \
            --norm {params.norm} > {log} 2>&1
            
        touch {output}
        """

# ============================================================
# Consensus Peaks（重复样本取交集）
# ============================================================
rule analyse_consensus_peaks:
    input:
        script = "workflow/scripts/4_4_consensus_peaks.py",
        metadata = "config/metadata.csv",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        config = ancient("config/config.yaml")
    output:
        "result/{species}/{experiment}/4_analyse/consensus_finished.txt"
    log:
        "logs/{species}/{experiment}/4_4_consensus_peaks.log"
    params:
        threads = config["threads"],
        min_overlap = config.get("consensus_min_overlap", 2)
    shell:
        """
        python {input.script} \
            --metadata {input.metadata} \
            --peakdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --outdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/consensus \
            --min-overlap {params.min_overlap} \
            --config {input.config} > {log} 2>&1

        touch {output}
        """

# ============================================================
# Replicate QC（重复样本相关性分析 via deeptools）
# ============================================================
rule analyse_replicate_qc:
    input:
        metadata = "config/metadata.csv",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        deeptools_marker = "result/{species}/{experiment}/4_analyse/deeptools_finished.txt",
        config = ancient("config/config.yaml")
    output:
        "result/{species}/{experiment}/4_analyse/replicate_qc_finished.txt"
    log:
        "logs/{species}/{experiment}/4_5_replicate_qc.log"
    params:
        threads = config["threads"],
        norm = config["normalization_method"]
    run:
        import os, yaml, subprocess

        with open(input.config, 'r') as f:
            cfg = yaml.safe_load(f)

        # 解析重复组
        groups = {}
        with open(input.metadata, 'r') as f:
            header = next(f).strip().split(',')
            has_group = len(header) >= 3
            for line in f:
                if line.strip():
                    fields = line.strip().split(',')
                    treat = fields[0].strip()
                    control = fields[1].strip()
                    group = fields[2].strip() if has_group and len(fields) >= 3 else ""
                    if group:
                        pair_name = f"{treat}_vs_{control}"
                        groups.setdefault(group, []).append(pair_name)

        if not groups:
            # 无重复组则直接完成
            with open(output[0], 'w') as f:
                f.write("No replicate groups defined.\n")
            return

        outdir = f"result/{wildcards.species}/{wildcards.experiment}/4_analyse/replicate_qc"
        os.makedirs(outdir, exist_ok=True)
        dt_dir = f"result/{wildcards.species}/{wildcards.experiment}/4_analyse/deeptools"

        for group_name, pair_list in groups.items():
            # 收集该组所有 IP 样本的 bigwig 文件
            bw_files = []
            labels = []
            for pair_name in pair_list:
                treat = pair_name.split("_vs_")[0]
                bw = os.path.join(dt_dir, pair_name, f"{treat}.bw")
                if os.path.exists(bw):
                    bw_files.append(bw)
                    labels.append(treat)

            if len(bw_files) < 2:
                continue

            # multiBigwigSummary
            npz_out = os.path.join(outdir, f"{group_name}_multiBwSummary.npz")
            cmd_summary = (
                f"multiBigwigSummary bins "
                f"-b {' '.join(bw_files)} "
                f"--labels {' '.join(labels)} "
                f"-p {params.threads} "
                f"-o {npz_out}"
            )
            shell(f"{cmd_summary} >> {log} 2>&1")

            # plotCorrelation - heatmap
            corr_pdf = os.path.join(outdir, f"{group_name}_correlation_heatmap.pdf")
            cmd_corr = (
                f"plotCorrelation -in {npz_out} "
                f"--corMethod pearson --skipZeros "
                f"--whatToPlot heatmap "
                f"--plotNumbers "
                f"-o {corr_pdf} "
                f"--plotTitle '{group_name} Replicate Correlation'"
            )
            shell(f"{cmd_corr} >> {log} 2>&1")

            # plotCorrelation - scatterplot
            scatter_pdf = os.path.join(outdir, f"{group_name}_correlation_scatter.pdf")
            cmd_scatter = (
                f"plotCorrelation -in {npz_out} "
                f"--corMethod pearson --skipZeros "
                f"--whatToPlot scatterplot "
                f"-o {scatter_pdf} "
                f"--plotTitle '{group_name} Replicate Scatter'"
            )
            shell(f"{cmd_scatter} >> {log} 2>&1")

            # plotPCA
            pca_pdf = os.path.join(outdir, f"{group_name}_PCA.pdf")
            cmd_pca = (
                f"plotPCA -in {npz_out} "
                f"-o {pca_pdf} "
                f"--plotTitle '{group_name} PCA'"
            )
            shell(f"{cmd_pca} >> {log} 2>&1")

        shell(f"touch {output}")