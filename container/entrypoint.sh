#!/usr/bin/env bash
# ChIPseq Pipeline — 容器入口
#
# 支持三种后端：
#   Docker/Podman: 端口映射 -p HOST:8501，容器内绑 0.0.0.0:8501
#   Apptainer:     共享宿主网络，通过 PORT / SERVER_ADDRESS 环境变量指定绑定
set -e

# ---------- 数据目录 ----------
# 容器内约定：/app/config, /app/data, /app/workflow/resources, /app/workflow/anno,
#             /app/result, /app/logs, /app/renv, /app/.snakemake
# 若挂载卷不存在（例如 Apptainer 无 --bind），退化到 /app/config.default
if [ ! -d "/app/config" ] && [ -d "/app/config.default" ]; then
    echo "[entrypoint] /app/config 未挂载，使用 /app/config.default" >&2
    cp -rn /app/config.default/. /app/config 2>/dev/null || true
fi

mkdir -p /app/logs /app/result /app/.snakemake

# ---------- renv ----------
if [ -d "/app/renv/library" ]; then
    export RENV_PATHS_LIBRARY=/app/renv/library
fi

# ---------- Streamlit 端口/绑定 ----------
# Apptainer 后端会传入 PORT 和 SERVER_ADDRESS（默认 8501 / 0.0.0.0）
PORT="${PORT:-8501}"
SERVER_ADDRESS="${SERVER_ADDRESS:-0.0.0.0}"

# ---------- 启动 ----------
echo "[entrypoint] 启动 Streamlit: ${SERVER_ADDRESS}:${PORT}" >&2
exec conda run --no-capture-output -n ChIPseq_Pipline \
    streamlit run /app/streamlit_app/app.py \
        --server.address="${SERVER_ADDRESS}" \
        --server.port="${PORT}" \
        --server.headless=true \
        --browser.gatherUsageStats=false
