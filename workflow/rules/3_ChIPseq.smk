# 获取某个 project 是否开启了 2_QC 模块
def is_qc_enabled(wildcards):
    for p in config["projects"]:
        if p["species"] == wildcards.species and p["experiment"] == wildcards.experiment:
            return p.get("modules", {}).get("2_QC", False)
    return False

# 如果开启了 QC，那么 3_ChIPseq 的输入就是 QC 的输出目录
# 如果没有开启 QC，那么假设原始的 fastq 数据就在 rawdata_dir 或者某个已清理好的目录，这里直接指向 rawdata_dir
def get_fq_input_dir(wildcards):
    if is_qc_enabled(wildcards):
        return f"result/{wildcards.species}/{wildcards.experiment}/2_QC"
    else:
        # 未开启 QC 时，直接使用原始数据目录
        for p in config["projects"]:
            if p["species"] == wildcards.species and p["experiment"] == wildcards.experiment:
                return p["rawdata_dir"]
    return f"result/{wildcards.species}/{wildcards.experiment}/2_QC"

# 动态获取上游的 marker 依赖，如果没开启前置模块，就不强制要求文件
def get_step1_input_markers(wildcards):
    if is_qc_enabled(wildcards):
        return f"result/{wildcards.species}/{wildcards.experiment}/2_QC/QC_finished.txt"
    else:
        return [] # 不依赖 QC_finished.txt

rule ChIPseq_step1:
    input:
        script = "workflow/scripts/3_1_ChIPseq.pl",
        metadata = "config/metadata.csv",
        config = "config/config.yaml",
        qc_marker = get_step1_input_markers
    output:
        "result/{species}/{experiment}/3_ChIPseq/step1_finished.txt"
    log:
        "logs/{species}/{experiment}/3_1_ChIPseq.log"
    params:
        index_dir = lambda wildcards: [p["index_dir"] for p in config["projects"] if p["species"] == wildcards.species and p["experiment"] == wildcards.experiment][0],
        fqdir = get_fq_input_dir
    shell:
        """
        perl {input.script} \
            --inputdir "{params.fqdir}" \
            --outputdir "result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq" \
            --indexdir "{params.index_dir}" \
            --picarddir "{config[picard_dir]}" \
            --threads {config[threads]} > {log} 2>&1
            
        touch {output}
        """

rule ChIPseq_step2_bamtobw:
    input:
        script = "workflow/scripts/3_2_bamtobwfile.pl",
        step1_marker = "result/{species}/{experiment}/3_ChIPseq/step1_finished.txt"
    output:
        "result/{species}/{experiment}/3_ChIPseq/step2_finished.txt"
    log:
        "logs/{species}/{experiment}/3_2_bamtobw.log"
    shell:
        """
        perl {input.script} \
            --inputdir result/{wildcards.species}/{wildcards.experiment}/3_ChIPseq \
            --threads {config[threads]} > {log} 2>&1
        
        touch {output}
        """