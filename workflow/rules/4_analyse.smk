# 根据物种确定 MACS2 的 genome_size 参数
def get_genome_size(species):
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
    shell:
        """
        perl {input.script} \
            --metadata {input.metadata} \
            --bamdir result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq \
            --outdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --picdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling/pictures \
            --genome {wildcards.species} \
            --qval {config[macs2_cutoff_value]} \
            --cutoff_type {config[macs2_cutoff_type]} \
            --norm_method {config[normalization_method]} \
            --peak_type {config[macs2_peak_type]} \
            --broad_cutoff {config[macs2_broad_cutoff]} \
            --threads {config[threads]} > {log} 2>&1
        
        touch {output}
        """

# 定义一个辅助函数，用于获取所有样本对名称
def get_sample_pairs(metadata_file):
    pairs = []
    with open(metadata_file, 'r') as f:
        next(f) # skip header
        for line in f:
            if line.strip():
                treat, control = line.strip().split(',')
                treat = treat.strip()
                control = control.strip()
                pairs.append(f"{treat}_vs_{control}")
    return pairs

# 动态获取样本对
SAMPLE_PAIRS = get_sample_pairs("config/metadata.csv")

rule analyse_annoChIPPeaks_single:
    input:
        script = "workflow/scripts/4_2_annoChIPPeaks.R",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        config = "config/config.yaml"
    output:
        "result/{species}/{experiment}/4_analyse/annoChIPPeaks/{pair}/{pair}_peak_anno.csv",
        "result/{species}/{experiment}/4_analyse/annoChIPPeaks/{pair}/{pair}_pie_bp±2000.pdf"
    log:
        "logs/{species}/{experiment}/4_2_annoChIPPeaks_{pair}.log"
    params:
        peak_ext = lambda wildcards: "broadPeak" if config.get("macs2_peak_type", "narrow") == "broad" else "narrowPeak"
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
        config = "config/config.yaml"
    output:
        "result/{species}/{experiment}/4_analyse/deeptools_finished.txt"
    log:
        "logs/{species}/{experiment}/4_3_deeptools.log"
    shell:
        """
        python {input.script} \
            --metadata {input.metadata} \
            --bamdir result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq \
            --peakdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --outdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/deeptools \
            --threads {config[threads]} \
            --norm {config[normalization_method]} > {log} 2>&1
            
        touch {output}
        """