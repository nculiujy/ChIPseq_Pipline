#!/usr/bin/env bash
# ChIPseq Pipeline — 构建 OCI 镜像并（可选）推送
#
# 用法:
#   bash container/build.sh                              # 本地构建 (默认 latest)
#   bash container/build.sh my-tag                       # 本地构建打 tag
#   PUSH=1 bash container/build.sh                       # 构建并推送
#   PLATFORMS=linux/amd64,linux/arm64 PUSH=1 bash ...    # 多架构
#   IMAGE=ghcr.io/foo/bar:1.0 bash container/build.sh    # 覆盖镜像名
#
# 前置：docker (或 podman) + buildx 插件（多架构时）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${IMAGE:-ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}"
[ $# -ge 1 ] && IMAGE="$1"

PLATFORMS="${PLATFORMS:-linux/amd64}"
PUSH="${PUSH:-0}"
ENGINE="${ENGINE:-docker}"
LOAD="${LOAD:-1}"   # 单架构时是否 --load 到本地

command -v "$ENGINE" >/dev/null 2>&1 || { echo "缺少 $ENGINE"; exit 1; }

echo "==== 构建镜像 ===="
echo "  ENGINE:    $ENGINE"
echo "  IMAGE:     $IMAGE"
echo "  PLATFORMS: $PLATFORMS"
echo "  PUSH:      $PUSH"
echo "  CONTEXT:   $REPO"
echo

BUILD_ARGS=(
    buildx build
    --platform "$PLATFORMS"
    -f container/Dockerfile
    -t "$IMAGE"
    --progress=plain
)

if [ "$PUSH" = "1" ]; then
    BUILD_ARGS+=(--push)
elif [ "$LOAD" = "1" ] && [[ "$PLATFORMS" != *","* ]]; then
    # 单架构可以 --load 到本地 docker images
    BUILD_ARGS+=(--load)
else
    echo "[warn] 多架构 + 非 push 模式：镜像只在 buildx 缓存中，无法 --load"
fi

if ! "$ENGINE" buildx version >/dev/null 2>&1; then
    echo "[warn] $ENGINE buildx 不可用，回落到普通 build（单架构）"
    BUILD_ARGS=(build -f container/Dockerfile -t "$IMAGE")
fi

"$ENGINE" "${BUILD_ARGS[@]}" "$REPO"

echo
echo "==== 构建完成 ===="
echo "  $IMAGE"
if [ "$PUSH" != "1" ]; then
    echo "  推送：$ENGINE push $IMAGE"
fi
