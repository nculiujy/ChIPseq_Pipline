#!/usr/bin/env bash
# ChIPseq Pipeline — 构建 conda-pack 预打包环境（无 root 兜底方案）
#
# 用法:
#   bash container/build-conda-pack.sh                # 版本 1.0.0
#   bash container/build-conda-pack.sh 1.2.3          # 指定版本
#   ENV_NAME=foo OUT_DIR=/tmp bash container/build-conda-pack.sh
#
# 前置：mamba 或 conda；建议在 condaforge/miniforge3 容器内构建以获得
#       glibc 2.17 兼容性（覆盖 CentOS7+/Ubuntu16+/RHEL7+）
#
# 产物：chipseq-env-<VERSION>.tar.gz （3-5 GB）
# 用户侧：runtime/conda.sh 会下载后解压并跑 conda-unpack 修正前缀
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

VERSION="${1:-${VERSION:-1.0.0}}"
ENV_NAME="${ENV_NAME:-ChIPseq_Pipline}"
OUT_DIR="${OUT_DIR:-$REPO}"
OUT_FILE="$OUT_DIR/chipseq-env-${VERSION}.tar.gz"
KEEP_ENV="${KEEP_ENV:-0}"   # 1 = 保留 conda env（调试用）

# 找到包管理器
MAMBA=""
for b in mamba micromamba conda; do
    command -v "$b" >/dev/null 2>&1 && { MAMBA="$b"; break; }
done
[ -n "$MAMBA" ] || { echo "缺少 mamba/micromamba/conda"; exit 1; }

echo "==== 构建 conda-pack 环境 ===="
echo "  工具:     $MAMBA"
echo "  ENV_NAME: $ENV_NAME"
echo "  VERSION:  $VERSION"
echo "  OUT:      $OUT_FILE"
echo

# ---------- 1. 清理旧的 ----------
if [ "$KEEP_ENV" != "1" ]; then
    echo "[1/4] 移除旧环境（如存在）..."
    "$MAMBA" env remove -n "$ENV_NAME" -y >/dev/null 2>&1 || true
fi

# ---------- 2. 创建环境 ----------
echo "[2/4] 从 environment.yml 创建环境（可能耗时 20-60 分钟）..."
"$MAMBA" env create -f environment.yml -n "$ENV_NAME"

# ---------- 3. 安装 conda-pack + 预恢复 renv ----------
echo "[3/4] 安装 conda-pack 并预恢复 renv ..."
"$MAMBA" install -n "$ENV_NAME" -c conda-forge conda-pack -y

# 预恢复 R 包（失败不阻断，用户运行时可再 restore）
"$MAMBA" run -n "$ENV_NAME" R -e '
    if (!requireNamespace("renv", quietly=TRUE)) {
        install.packages("renv", repos="https://cloud.r-project.org")
    }
    renv::restore(confirm=FALSE)
' || echo "[warn] renv::restore 部分失败，继续"

# ---------- 4. 打包 ----------
echo "[4/4] 打包为 $OUT_FILE ..."
mkdir -p "$OUT_DIR"
rm -f "$OUT_FILE"

# conda-pack 参数：
#   --n-threads -1        用所有 CPU 并行压缩
#   --ignore-editable-packages  允许 pip -e 安装的包（如有）
#   --force               覆盖已存在的输出
"$MAMBA" run -n "$ENV_NAME" conda pack \
    -n "$ENV_NAME" \
    -o "$OUT_FILE" \
    --n-threads -1 \
    --ignore-editable-packages \
    --force

SIZE=$(du -h "$OUT_FILE" | awk '{print $1}')
SHA=$(sha256sum "$OUT_FILE" | awk '{print $1}')

echo
echo "==== 构建完成 ===="
echo "  文件:     $OUT_FILE"
echo "  大小:     $SIZE"
echo "  SHA256:   $SHA"
echo
echo "上传到 GitHub Release（文件名保持 chipseq-env-${VERSION}.tar.gz 以便 bootstrap 下载）："
echo "  gh release upload <tag> $OUT_FILE"
echo
if [ "$KEEP_ENV" != "1" ]; then
    echo "如需删除本地环境：$MAMBA env remove -n $ENV_NAME"
fi
