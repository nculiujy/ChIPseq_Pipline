# 检查是否开启了 1_download 模块
def is_download_enabled(wildcards):
    for p in config["projects"]:
        if p["species"] == wildcards.species and p["experiment"] == wildcards.experiment:
            return p.get("modules", {}).get("1_download", False)
    return False

def get_qc_input_dir(wildcards):
    if is_download_enabled(wildcards):
        return f"result/{wildcards.species}/{wildcards.experiment}/1_download"
    else:
        for p in config["projects"]:
            if p["species"] == wildcards.species and p["experiment"] == wildcards.experiment:
                return p["rawdata_dir"]
    return ""

def get_qc_input_markers(wildcards):
    if is_download_enabled(wildcards):
        return f"result/{wildcards.species}/{wildcards.experiment}/1_download/finished.txt"
    else:
        return []

rule QC_trim_galore:
    input:
        script = "workflow/scripts/2_1_QC.pl",
        download_marker = get_qc_input_markers
    output:
        "result/{species}/{experiment}/2_QC/QC_finished.txt"
    log:
        "logs/{species}/{experiment}/2_QC.log"
    params:
        indir = get_qc_input_dir
    shell:
        """
        perl {input.script} \
            --inputdir {params.indir} \
            --outputdir result/{wildcards.species}/{wildcards.experiment}/2_QC \
            --threads {config[threads]} > {log} 2>&1
        
        touch {output}
        """