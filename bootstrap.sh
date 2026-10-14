#!/usr/bin/env bash
# ChIPseq Pipeline — 一键安装入口（无 root）
#
# 用法：
#   git clone <repo> && cd ChIPseq_Pipline && ./bootstrap.sh
#
# 环境变量：
#   CHIPSEQ_DATA       数据目录（默认 ~/.chipseq-pipeline）
#   CHIPSEQ_RUNTIME    强制指定运行时（跳过探测）
#   CHIPSEQ_IMAGE      OCI 镜像地址
#   CHIPSEQ_SIF_URI    Apptainer .sif 下载地址
#   CHIPSEQ_ENV_URL    conda-pack tarball 地址
#   SKIP_INSTALL       1 = 只探测不下载镜像/环境
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/scripts/lib/common.sh"

log "==== ChIPseq Pipeline 一键安装（无 root）===="
log "仓库目录: $HERE"

# ---------- 0. 前置检查 ----------
[ -w "$HOME" ] || die "\$HOME 不可写，无法安装"

for c in curl tar; do
    command -v "$c" >/dev/null 2>&1 || die "缺少 '$c'，请先安装（通常系统自带）"
done

# ---------- 1. 探测运行时 ----------
if [ -n "${CHIPSEQ_RUNTIME:-}" ]; then
    RUNTIME="$CHIPSEQ_RUNTIME"
    log "使用强制指定的运行时: $RUNTIME"
    case "$RUNTIME" in
        apptainer|podman|docker|conda) ;;
        *) die "无效运行时 '$RUNTIME'（可选: apptainer|podman|docker|conda）" ;;
    esac
else
    log "探测运行时 ..."
    RUNTIME=$(VERBOSE=1 "$HERE/scripts/detect-runtime.sh" || echo "none")
    if [ "$RUNTIME" = "none" ]; then
        die "无可用运行时。请让管理员安装 Apptainer/Singularity，或安装 conda/mamba 后重试。"
    fi
    log "选定运行时: $RUNTIME"
fi

# ---------- 2. 初始化数据目录 ----------
DATA_DIR="${CHIPSEQ_DATA:-$HOME/.chipseq-pipeline}"
log "数据目录: $DATA_DIR"
mkdir -p "$DATA_DIR"
check_disk_space "$DATA_DIR" 6

init_data_dir "$DATA_DIR" "$HERE/assets"
echo "$RUNTIME" > "$DATA_DIR/runtime"

# conda 后端需要仓库路径（不用 bind mount）
echo "$HERE" > "$DATA_DIR/repo_path"

# ---------- 3. 后端安装（拉镜像 / 下载 conda-pack） ----------
BACKEND="$HERE/runtime/${RUNTIME}.sh"
[ -f "$BACKEND" ] || die "找不到后端脚本 $BACKEND"
# shellcheck disable=SC1090
source "$BACKEND"

if [ "${SKIP_INSTALL:-0}" = "1" ]; then
    warn "SKIP_INSTALL=1，跳过镜像/环境下载"
else
    log "开始安装运行时依赖（可能几个 GB，请耐心等待）..."
    runtime_install
fi

# ---------- 4. 安装 CLI ----------
log "安装 CLI 命令到 ~/.local/bin ..."
"$HERE/scripts/install-cli.sh"

# ---------- 5. 完成 ----------
BIN_DIR="${PREFIX:-$HOME/.local}/bin"
cat <<EOF

============================================================
 安装完成！
------------------------------------------------------------
 运行时:    $RUNTIME
 数据目录:  $DATA_DIR
 CLI 目录:  $BIN_DIR

 下一步:
   1) 确保 $BIN_DIR 在 PATH 中:
        echo 'export PATH="$BIN_DIR:\$PATH"' >> ~/.bashrc && source ~/.bashrc

   2) 把参考基因组放到:
        $DATA_DIR/references/       （基因组索引）
        $DATA_DIR/annotations/      （物种注释）

   3) 编辑配置:
        $DATA_DIR/config/config.yaml
        $DATA_DIR/config/metadata.csv

   4) 启动:
        chipseq-app

      远程服务器访问（无桌面）:
        本地机器执行: ssh -L 8501:localhost:8501 \$USER@<服务器地址>
        浏览器打开:   http://localhost:8501
============================================================

EOF
