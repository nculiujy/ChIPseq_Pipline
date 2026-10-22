import os

# ============================================================
# 模块5: GSEA 富集分析
# 基于重复组（group）的 consensus peaks 注释基因进行 GO + KEGG 富集
# ============================================================

def get_gsea_params(config):
    """从 config 中提取 GSEA 参数，带默认值"""
    gsea = config.get("gsea", {})
    return {
        "pvalue_cutoff":  gsea.get("pvalue_cutoff", 0.05),
        "qvalue_cutoff":  gsea.get("qvalue_cutoff", 0.2),
        "go_types":       ",".join(gsea.get("go_types", ["BP", "MF", "CC"])),
        "run_go":         gsea.get("run_go", True),
        "run_kegg":       gsea.get("run_kegg", True),
        "min_gs_size":    gsea.get("min_gs_size", 10),
        "max_gs_size":    gsea.get("max_gs_size", 500),
        "top_n":          gsea.get("top_n", 20),
        "promoter_only":  gsea.get("promoter_only", False),
    }


rule gsea_analysis:
    """
    基于重复组的 GSEA 富集分析：
    1. 读取 metadata.csv 中的 group 列
    2. 对每个 group，优先使用 consensus peaks 注释的基因列表
    3. 若无 consensus annotation，回退到取组内所有样本对的注释基因交集
    4. 对基因列表运行 GO + KEGG 富集分析
    """
    input:
        anno_finished = "result/{species}/{experiment}/4_analyse/annoChIPPeaks_finished.txt",
        anno_consensus_finished = "result/{species}/{experiment}/4_analyse/anno_consensus_finished.txt"
    output:
        finished = "result/{species}/{experiment}/5_gsea/gsea_finished.txt"
    log:
        "logs/{species}/{experiment}/5_1_gsea.log"
    params:
        indir      = lambda wc: os.path.join("result", wc.species, wc.experiment, "4_analyse", "annoChIPPeaks"),
        outdir     = lambda wc: os.path.join("result", wc.species, wc.experiment, "5_gsea"),
        species    = lambda wc: wc.species,
        experiment = lambda wc: wc.experiment,
        p          = lambda wc: get_gsea_params(config),
        flags      = lambda wc: " ".join(filter(None, [
            "--run_go" if get_gsea_params(config)["run_go"] else "",
            "--run_kegg" if get_gsea_params(config)["run_kegg"] else "",
            "--promoter_only" if get_gsea_params(config)["promoter_only"] else "",
        ])),
    shell:
        """
        export LD_LIBRARY_PATH="${{CONDA_PREFIX}}/lib:${{LD_LIBRARY_PATH:-}}"
        Rscript workflow/scripts/5_1_gsea.R \
            --indir      "{params.indir}" \
            --outdir     "{params.outdir}" \
            --metadata   config/metadata.csv \
            --config     config/config.yaml \
            --species    "{params.species}" \
            --experiment "{params.experiment}" \
            --pvalue_cutoff  {params.p[pvalue_cutoff]} \
            --qvalue_cutoff  {params.p[qvalue_cutoff]} \
            --go_types       "{params.p[go_types]}" \
            --min_gs_size    {params.p[min_gs_size]} \
            --max_gs_size    {params.p[max_gs_size]} \
            --top_n          {params.p[top_n]} \
            {params.flags} \
            > "{log}" 2>&1
        """
