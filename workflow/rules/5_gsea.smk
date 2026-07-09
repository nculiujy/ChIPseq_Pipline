import os

# ============================================================
# 模块5: GSEA 富集分析
# 整合 annotation 结果中的 gene_id，进行 GO + KEGG 富集分析
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
    整合所有样本对的 peak_anno.csv，提取去重 gene_id，
    运行 GO（BP/MF/CC）和 KEGG 通路富集分析
    """
    input:
        anno_finished = "result/{species}/{experiment}/4_analyse/annoChIPPeaks_finished.txt"
    output:
        finished = "result/{species}/{experiment}/5_gsea/gsea_finished.txt"
    log:
        "logs/{species}/{experiment}/5_1_gsea.log"
    params:
        indir      = lambda wc: os.path.join("result", wc.species, wc.experiment, "4_analyse", "peakcalling"),
        outdir     = lambda wc: os.path.join("result", wc.species, wc.experiment, "5_gsea"),
        species    = lambda wc: wc.species,
        experiment = lambda wc: wc.experiment,
        p          = lambda wc: get_gsea_params(config),
    shell:
        """
        Rscript workflow/scripts/5_1_gsea.R \
            --indir      {params.indir} \
            --outdir     {params.outdir} \
            --config     config/config.yaml \
            --species    {params.species} \
            --experiment {params.experiment} \
            --pvalue_cutoff  {params.p[pvalue_cutoff]} \
            --qvalue_cutoff  {params.p[qvalue_cutoff]} \
            --go_types       "{params.p[go_types]}" \
            --min_gs_size    {params.p[min_gs_size]} \
            --max_gs_size    {params.p[max_gs_size]} \
            --top_n          {params.p[top_n]} \
            {params.p[run_go]:?--run_go:} \
            {params.p[run_kegg]:?--run_kegg:} \
            {params.p[promoter_only]:?--promoter_only:} \
            > {log} 2>&1
        """
