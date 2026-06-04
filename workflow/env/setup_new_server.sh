#!/usr/bin/env bash
# 新服务器一键部署脚本
# 用法: bash workflow/env/setup_new_server.sh
set -euo pipefail

CONDA_ENV="ChIPseq_Pipline"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "=== [1/3] 创建 conda 环境 ==="
conda env create -f "${PROJECT_ROOT}/environment.yml" || {
  echo "环境已存在，尝试更新..."
  conda env update -f "${PROJECT_ROOT}/environment.yml" --prune
}

echo ""
echo "=== [2/3] 安装 R 包 ==="
conda run -n "${CONDA_ENV}" Rscript "${SCRIPT_DIR}/install_R_packages.R"

echo ""
echo "=== [3/3] 验证核心工具 ==="
tools=(bowtie2 samtools macs2 snakemake deeptools)
all_ok=true
for tool in "${tools[@]}"; do
  if conda run -n "${CONDA_ENV}" which "${tool}" &>/dev/null; then
    echo "  ✓ ${tool}"
  else
    echo "  ✗ ${tool} — 未找到"
    all_ok=false
  fi
done

echo ""
if $all_ok; then
  echo "✓ 部署完成。激活环境: conda activate ${CONDA_ENV}"
else
  echo "✗ 部分工具缺失，请检查 environment.yml"
  exit 1
fi
