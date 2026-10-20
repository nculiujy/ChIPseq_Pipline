#!/usr/bin/env bash
# ChIPseq Pipeline — 卸载 CLI 与（可选）镜像/环境（仓库内直接调用的兵分——优先用 chipseq-app-uninstall）
#
# 项目内目录默认不删 data/ 子目录；如需彻底清理加 --purge-data
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CHIPSEQ_REPO="$SRC"
# shellcheck disable=SC1091
source "$SRC/scripts/lib/common.sh"

RUNTIME_DIR="$CHIPSEQ_RUNTIME_DIR"
BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/chipseq-pipeline"    # 旧 HOME 侧副本，一并清理
DATA_DIR="$CHIPSEQ_DEFAULT_DATA_DIR"
CONDA_ENV="$CHIPSEQ_DEFAULT_ANALYSIS_ENV"
LAUNCH_ENV="$CHIPSEQ_DEFAULT_LAUNCH_ENV"

PURGE_DATA=0
ASSUME_YES=0
for a in "$@"; do
    case "$a" in
        --purge-data) PURGE_DATA=1 ;;
        -y|--yes)     ASSUME_YES=1 ;;
        -h|--help)
            cat <<EOF
用法: uninstall.sh [选项]

选项:
  --purge-data   一并删除用户数据目录 $DATA_DIR
  -y, --yes      全部问题自动回答 yes
  -h, --help     显示本帮助
EOF
            exit 0 ;;
        *) die "未知参数 $a" ;;
    esac
done

log "==== 卸载 ChIPseq Pipeline CLI ===="

# ---------- 1. 停止运行中的服务 ----------
RT="none"
[ -f "$DATA_DIR/runtime" ] && RT=$(cat "$DATA_DIR/runtime" 2>/dev/null || echo "none")
case "$RT" in
    docker|podman)
        command -v "$RT" >/dev/null 2>&1 && \
            "$RT" rm -f chipseq-pipeline >/dev/null 2>&1 || true
        ;;
    apptainer)
        if [ -f "$DATA_DIR/apptainer.pid" ]; then
            p=$(cat "$DATA_DIR/apptainer.pid" 2>/dev/null || echo "")
            [ -n "$p" ] && kill -TERM -"$p" 2>/dev/null || true
        fi
        pkill -f "chipseq-pipeline.sif" 2>/dev/null || true
        ;;
    conda)
        if [ -f "$DATA_DIR/streamlit.pid" ]; then
            p=$(cat "$DATA_DIR/streamlit.pid" 2>/dev/null || echo "")
            [ -n "$p" ] && kill -TERM -"$p" 2>/dev/null || true
        fi
        ;;
esac

# ---------- 2. 询问是否删除镜像/环境 ----------
ask_yes() {
    [ "$ASSUME_YES" = "1" ] && return 0
    read -rp "$1 [y/N] " a
    [ "$a" = "y" ] || [ "$a" = "Y" ]
}

if ask_yes "删除运行时产物（镜像/.sif/conda 环境）？"; then
    case "$RT" in
        docker|podman)
            log "删除镜像 ($RT) ..."
            command -v "$RT" >/dev/null 2>&1 && \
                "$RT" rmi "${CHIPSEQ_IMAGE:-ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}" \
                >/dev/null 2>&1 || true
            ;;
        apptainer)
            log "删除 .sif ..."
            rm -f "$DATA_DIR/chipseq-pipeline.sif"
            ;;
        conda)
            log "删除 conda 环境: $CONDA_ENV"
            rm -rf "$CONDA_ENV"
            ;;
    esac
fi

# ---------- 3. 删除 CLI 与项目内运行时目录 ----------
log "删除软链 $BIN/chipseq-app* ..."
rm -f "$BIN"/chipseq-app*

log "删除旧版 HOME 侧副本 $SHARE ..."
rm -rf "$SHARE"

if [ -d "$RUNTIME_DIR" ]; then
    log "删除项目内运行时目录 $RUNTIME_DIR（data/ 下用 --purge-data 控制）..."
    for sub in "$RUNTIME_DIR"/* "$RUNTIME_DIR"/.[!.]*; do
        [ -e "$sub" ] || continue
        base="${sub##*/}"
        [ "$base" = "data" ] && [ "$PURGE_DATA" != "1" ] && continue
        rm -rf "$sub"
    done
fi

# ---------- 4. 用户数据（可选） ----------
if [ "$PURGE_DATA" = "1" ]; then
    if ask_yes "确认删除用户数据目录 $DATA_DIR？此操作不可恢复"; then
        rm -rf "$DATA_DIR"
        ok "用户数据已删除"
    fi
else
    log "保留用户数据目录: $DATA_DIR"
    log "如需删除，手动执行: rm -rf '$DATA_DIR'"
fi

echo
ok "卸载完成"
