#!/usr/bin/env bash
# ChIPseq Pipeline — 从 OCI 镜像构建 Apptainer .sif
#
# 用法:
#   bash container/build-sif.sh                                     # 用默认 IMAGE
#   bash container/build-sif.sh ghcr.io/foo/bar:1.0                 # 指定镜像
#   OUT=/tmp/x.sif bash container/build-sif.sh                      # 指定输出
#
# 前置：apptainer 或 singularity（需 root 权限，或使用 --fakeroot）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${IMAGE:-ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}"
[ $# -ge 1 ] && IMAGE="$1"

OUT="${OUT:-$REPO/chipseq-pipeline.sif}"
FAKEROOT="${FAKEROOT:-1}"

# 找到 apptainer 或 singularity
APPTAINER=""
for b in apptainer singularity; do
    command -v "$b" >/dev/null 2>&1 && { APPTAINER="$b"; break; }
done
[ -n "$APPTAINER" ] || { echo "缺少 apptainer / singularity"; exit 1; }

echo "==== 构建 .sif ===="
echo "  工具:     $APPTAINER ($("$APPTAINER" --version 2>/dev/null | head -1))"
echo "  来源镜像: docker://$IMAGE"
echo "  输出:     $OUT"
echo

BUILD_ARGS=(build --force)
if [ "$FAKEROOT" = "1" ] && [ "$(id -u)" != "0" ]; then
    BUILD_ARGS+=(--fakeroot)
    echo "  以 --fakeroot 模式构建（非 root 用户）"
fi
BUILD_ARGS+=("$OUT" "docker://$IMAGE")

"$APPTAINER" "${BUILD_ARGS[@]}"

SIZE=$(du -h "$OUT" | awk '{print $1}')
echo
echo "==== .sif 构建完成 ===="
echo "  $OUT ($SIZE)"
echo
echo "上传到 GitHub Release:"
echo "  gh release upload <tag> $OUT"
echo "或："
echo "  curl -X POST -H 'Authorization: token <TOKEN>' ..."
