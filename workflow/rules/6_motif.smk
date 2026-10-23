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
        # MEME 可能装在 /usr/local/meme（系统级），也可能装在项目内的独立 prefix
        # （UI 的「安装 MEME suite」按钮装到 .chipseq-runtime/meme，并以 PATH 注入）。
        # 硬编码某一个路径，换成另一种装法就全盘失效，所以先按 PATH 上的
        # meme-chip 反推安装根目录，找不到才退回原默认。
        MEME_CHIP="$(command -v meme-chip 2>/dev/null || true)"
        if [ -n "$MEME_CHIP" ]; then
            MEME_BIN="$(dirname "$MEME_CHIP")"
        else
            MEME_BIN="${{MEME_BIN_DIR:-/usr/local/meme/bin}}"
        fi
        MEME_ROOT="$(dirname "$MEME_BIN")"
        # 版本目录用 glob 取，不走 ls|head：Snakemake 的 shell 是 set -euo pipefail，
        # head 读够行数就关管道，ls 会吃 SIGPIPE，整条命令非零退出会把 job 带挂
        MEME_VER_NAME=""
        for _md in "$MEME_ROOT"/libexec/meme-*; do
            if [ -d "$_md" ]; then MEME_VER_NAME="${{_md##*/}}"; break; fi
        done
        MEME_VER_NAME="${{MEME_VER_NAME:-meme-5.5.7}}"
        MEME_LIBEXEC="${{MEME_SECONDARY_BIN_DIR:-$MEME_ROOT/libexec/$MEME_VER_NAME}}"
        export PATH="$CONDA_ENV_BIN:$MEME_BIN:$MEME_LIBEXEC:$PATH"
        export MEME_BIN_DIR="$MEME_BIN"
        export MEME_SECONDARY_BIN_DIR="$MEME_LIBEXEC"
        export MEME_SCRIPT_DIR="$MEME_LIBEXEC"
        export MEME_DATA_DIR="${{MEME_DATA_DIR:-$MEME_ROOT/share/$MEME_VER_NAME}}"
        # perl 路径跟着 MEME_ROOT 走；conda 装的 meme 带自己的 perl，
        # 系统装的才需要这些额外目录，目录不存在就不加
        PERL_PATHS="$MEME_ROOT/lib/$MEME_VER_NAME/perl"
        for _d in "$CONDA_ENV_BIN/../lib/perl5/site_perl" "$CONDA_ENV_BIN/../lib/perl5/vendor_perl"; do
            if [ -d "$_d" ]; then PERL_PATHS="$PERL_PATHS:$_d"; fi
        done
        export PERL5LIB="$PERL_PATHS${{PERL5LIB:+:$PERL5LIB}}"
        export CONDA_PREFIX="${{CONDA_PREFIX:-$CONDA_ENV_BIN/..}}"

        python "{input.script}" \
            --metadata "{input.metadata}" \
            --peakdir "result/{wildcards.species}/{wildcards.experiment}/4_analyse/peakcalling" \
            --consensus_dir "result/{wildcards.species}/{wildcards.experiment}/4_analyse/consensus" \
            --outdir "result/{wildcards.species}/{wildcards.experiment}/6_motif" \
            --config "{input.config}" \
            --species "{wildcards.species}" \
            --experiment "{wildcards.experiment}" \
            --threads {params.threads} > "{log}" 2>&1

        touch "{output}"
        """
