# ChIPseq Pipeline — OCI 镜像构建
# 支持 Docker / Podman / Apptainer（通过 apptainer build docker://...）
#
# 构建：  bash container/build.sh
# 或使用: docker build -f container/Dockerfile -t chipseq-pipeline:latest .
#
# 基础镜像选择 condaforge/miniforge3：
#   - 基于 CentOS 7 兼容 glibc 2.17，产物 conda-pack 后可覆盖 CentOS7+/Ubuntu16+/RHEL7+
#   - 自带 mamba，能并发解算 environment.yml

FROM condaforge/miniforge3:24.9.0

LABEL org.opencontainers.image.title="ChIPseq Pipeline" \
      org.opencontainers.image.description="Snakemake + Streamlit + R/Bioconductor ChIP-seq 分析流水线" \
      org.opencontainers.image.source="https://github.com/chipseq-pipeline/ChIPseq_Pipline" \
      org.opencontainers.image.licenses="MIT"

# ---------- 系统层依赖 ----------
# 大部分工具依赖已在 conda env 内解决；这里只装镜像运行/调试必备
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        procps \
        git \
        less \
        vim-tiny \
        locales \
    && sed -i 's/# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# ---------- Conda 环境 ----------
# 只拷 environment.yml 以最大化利用构建缓存
COPY environment.yml /tmp/environment.yml
RUN mamba env create -f /tmp/environment.yml -n ChIPseq_Pipline \
 && mamba clean -afy \
 && rm -f /tmp/environment.yml

# 后续所有 RUN 都在 env 内执行
SHELL ["conda", "run", "--no-capture-output", "-n", "ChIPseq_Pipline", "/bin/bash", "-c"]

# ---------- 项目代码 ----------
WORKDIR /app

# 顶层文件（.dockerignore 已排除 result/、logs/、.snakemake/、renv/library/ 等）
COPY snakefile .Rprofile renv.lock ./
COPY workflow ./workflow
COPY streamlit_app ./streamlit_app
COPY config ./config.default
COPY assets ./assets

# ---------- R 包（renv）预恢复 ----------
# 失败不阻断构建（用户挂载自己的 renv/ 时会覆盖）
RUN R -e 'if (!requireNamespace("renv", quietly=TRUE)) install.packages("renv", repos="https://cloud.r-project.org"); renv::restore(confirm=FALSE)' || true

# ---------- Streamlit 全局配置 ----------
RUN mkdir -p /root/.streamlit && cat > /root/.streamlit/config.toml <<'EOF'
[server]
headless = true
address = "0.0.0.0"
port = 8501
enableCORS = false
enableXsrfProtection = true

[browser]
gatherUsageStats = false

[theme]
base = "light"
EOF

# ---------- 入口 ----------
COPY container/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501

# Apptainer 共享宿主网络，entrypoint.sh 接受 PORT / SERVER_ADDRESS 环境变量
ENV PORT=8501 \
    SERVER_ADDRESS=0.0.0.0

ENTRYPOINT ["/entrypoint.sh"]
