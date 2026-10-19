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
#
# 容器后端（apptainer/podman/docker）若命中未发布的占位镜像地址且无本地/已加载镜像，
# 会自动降级为 conda 后端（除非显式 CHIPSEQ_RUNTIME 强制指定，那样改为报错给出可执行指引）。
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

# ---------- 2.5 容器后端占位镜像预检：不可达则自动降级 conda ----------
# 仓库默认的镜像/SIF 地址指向从未发布的 chipseq-pipeline/ 命名空间，直接拉会 403/404。
# 若既没自定义地址、也没有本地/已加载的镜像，则自动改用 conda 后端（秒级启动器、开箱即用）。
case "$RUNTIME" in
    podman|docker|apptainer)
        _placeholder=1
        if [ -n "${CHIPSEQ_IMAGE:-}" ]; then _placeholder=0; fi
        if [ "$RUNTIME" = "apptainer" ] && [ -n "${CHIPSEQ_SIF_URI:-}" ]; then _placeholder=0; fi

        _offline=0
        if [ -f "$HERE/chipseq-pipeline-image.tar" ] || [ -f "$HERE/chipseq-pipeline-image.tar.zst" ] \
           || [ -f "$HERE/chipseq-pipeline.sif" ] || [ -f "$DATA_DIR/chipseq-pipeline.sif" ]; then
            _offline=1
        fi
        case "$RUNTIME" in
            podman|docker)
                if "$RUNTIME" image inspect "$CHIPSEQ_DEFAULT_IMAGE" >/dev/null 2>&1; then _offline=1; fi
                ;;
        esac

        if [ "$_placeholder" = 1 ] && [ "$_offline" = 0 ]; then
            warn "默认容器镜像地址（chipseq-pipeline/ 命名空间）从未发布，直接拉取会返回 403/404。"
            if [ -n "${CHIPSEQ_RUNTIME:-}" ]; then
                # 用户显式指定了容器后端：不擅自切换，给出可执行指引
                die "请三选一后重跑：1) 设 CHIPSEQ_IMAGE=<真实镜像>（apptainer 亦可设 CHIPSEQ_SIF_URI）；2) 把离线包 chipseq-pipeline-image.tar.zst 或 chipseq-pipeline.sif 放到 $HERE；3) 改用 conda：CHIPSEQ_RUNTIME=conda ./bootstrap.sh"
            elif command -v conda >/dev/null 2>&1 || command -v mamba >/dev/null 2>&1; then
                warn "自动降级到 conda 后端（秒级建启动器，完整分析环境稍后在浏览器「环境配置」页构建）。"
                RUNTIME=conda
            else
                die "容器镜像不可达，且本机未安装 conda/mamba。请安装 miniforge 后重跑，或提供上面的离线包/自定义镜像。"
            fi
        fi
        ;;
esac

init_data_dir "$DATA_DIR" "$HERE/assets"
echo "$RUNTIME" > "$DATA_DIR/runtime"

# conda 后端需要仓库路径（不用 bind mount）
echo "$HERE" > "$DATA_DIR/repo_path"

# ---------- 3. 后端安装（容器：拉镜像；conda：建精简启动器） ----------
BACKEND="$HERE/runtime/${RUNTIME}.sh"
[ -f "$BACKEND" ] || die "找不到后端脚本 $BACKEND"
# shellcheck disable=SC1090
source "$BACKEND"

if [ "${SKIP_INSTALL:-0}" = "1" ]; then
    warn "SKIP_INSTALL=1，跳过镜像/环境下载"
else
    if [ "$RUNTIME" = "conda" ]; then
        log "conda 模式：仅创建秒级启动器（完整分析环境稍后在浏览器【环境配置】页构建）"
    else
        log "开始安装运行时依赖（可能几个 GB，请耐心等待）..."
    fi
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

   5) （仅 conda 模式）启动后打开浏览器【环境配置】页，
      一键构建完整分析环境（离线包优先、缺则在线 create）。
============================================================

EOF
