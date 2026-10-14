import os

# ============================================================
# 模块6: MEME-ChIP Motif 富集分析
# 基于 consensus peaks 或单样本 peaks 进行 motif 发现与富集
# ============================================================

rule motif_analysis:
    """
    MEME-ChIP Motif 富集分析：
    1. 从基因组提取 peaks 中心 ±flank_size bp 的 FASTA 序列
    2. 调用 meme-chip 进行 motif 发现 + 已知 motif 富集
    3. 输出 HTML 报告及各子工具结果
    """
    input:
        script = "workflow/scripts/6_1_motif.py",
        metadata = "config/metadata.csv",
        consensus_marker = "result/{species}/{experiment}/4_analyse/consensus_finished.txt",
        peak_marker = "result/{species}/{experiment}/4_analyse/peakcalling_finished.txt",
        config = ancient("config/config.yaml")
    output:
        "result/{species}/{experiment}/6_motif/motif_finished.txt"
    log:
        "logs/{species}/{experiment}/6_1_motif.log"
    params:
        threads = config["threads"]
    shell:
        """
        CONDA_ENV_BIN="${{CONDA_PREFIX:-$(dirname $(which python))}}"
        MEME_BIN="${{MEME_BIN_DIR:-/usr/local/meme/bin}}"
        MEME_LIBEXEC="${{MEME_SECONDARY_BIN_DIR:-/usr/local/meme/libexec/meme-5.5.7}}"
        export PATH="$CONDA_ENV_BIN:$MEME_BIN:$MEME_LIBEXEC:$PATH"
        export MEME_BIN_DIR="$MEME_BIN"
        export MEME_SECONDARY_BIN_DIR="$MEME_LIBEXEC"
        export MEME_SCRIPT_DIR="$MEME_LIBEXEC"
        export MEME_DATA_DIR="${{MEME_DATA_DIR:-/usr/local/meme/share/meme-5.5.7}}"
        export PERL5LIB="/usr/local/meme/lib/meme-5.5.7/perl:$CONDA_ENV_BIN/../lib/perl5/5.32/site_perl:$CONDA_ENV_BIN/../lib/perl5/site_perl:$CONDA_ENV_BIN/../lib/perl5/5.32/vendor_perl:$CONDA_ENV_BIN/../lib/perl5/vendor_perl:${{PERL5LIB:-}}"
        export CONDA_PREFIX="${{CONDA_PREFIX:-$CONDA_ENV_BIN/..}}"

        python {input.script} \
            --metadata {input.metadata} \
            --peakdir result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling \
            --consensus_dir result/{wildcards.species}/{wildcards.experiment}/4_analyse/consensus \
            --outdir result/{wildcards.species}/{wildcards.experiment}/6_motif \
            --config {input.config} \
            --species {wildcards.species} \
            --experiment {wildcards.experiment} \
            --threads {params.threads} > {log} 2>&1

        touch {output}
        """
