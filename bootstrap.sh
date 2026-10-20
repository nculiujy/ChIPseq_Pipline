#!/usr/bin/env bash
# ChIPseq Pipeline — 一键安装入口（无 root）
#
# 用法：
#   git clone <repo> && cd ChIPseq_Pipline && ./bootstrap.sh [--full]
#
# 参数：
#   --full    一键到底：装完启动器+CLI 后立即建分析环境 + 补齐 R 包
#             等价于 ./bootstrap.sh 后紧跟 chipseq-app-update all
#             可搭 CHIPSEQ_SKIP_R=1 跳过 R 包步骤
#
# 环境变量：
#   CHIPSEQ_RUNTIME_DIR  项目内运行时目录（默认 <repo>/.chipseq-runtime）
#   CHIPSEQ_DATA         数据目录（默认 <runtime>/data）
#   CHIPSEQ_LAUNCH_ENV   启动器 prefix（默认 <runtime>/launch）
#   CHIPSEQ_CONDA_ENV    分析 prefix （默认 <runtime>/analysis）
#   CHIPSEQ_R_LIB        R 包库     （默认 <runtime>/analysis/lib/R/library）
#   CHIPSEQ_RUNTIME      强制指定运行时（跳过探测）
#   CHIPSEQ_IMAGE        OCI 镜像地址
#   CHIPSEQ_SIF_URI      Apptainer .sif 下载地址
#   CHIPSEQ_ENV_URL      conda-pack tarball 地址
#   CHIPSEQ_SKIP_R=1     跳过 R 包补齐步骤
#   SKIP_INSTALL         1 = 只探测不下载镜像/环境
#
# 容器后端（apptainer/podman/docker）若命中未发布的占位镜像地址且无本地/已加载镜像，
# 会自动降级为 conda 后端（除非显式 CHIPSEQ_RUNTIME 强制指定，那样改为报错给出可执行指引）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 先定住项目侧派生链，common.sh 依赖 CHIPSEQ_REPO
export CHIPSEQ_REPO="$HERE"
# shellcheck disable=SC1091
source "$HERE/scripts/lib/common.sh"
# 把 conda / mamba / pip / R 的写入锁到项目内；后续所有子进程自动继承
_chipseq_export_isolation_env

# ---------- 0a. 参数解析 ----------
FULL=0
for a in "$@"; do
    case "$a" in
        --full)     FULL=1 ;;
        -h|--help)
            cat <<'EOF'
用法：CHIPSEQ_RUNTIME=conda ./bootstrap.sh [--full]

  --full   一键到底：启动器 + CLI + 分析环境 + R 包（首次 10–30 分钟，后续幂等秒级）
           可搭 CHIPSEQ_SKIP_R=1 跳过 R 步骤
           可搭 SKIP_INSTALL=1 只探测不下载
EOF
            exit 0 ;;
        *) ;;
    esac
done

log "==== ChIPseq Pipeline 一键安装（无 root）===="
log "仓库目录: $HERE"

# ---------- 0. 前置检查 ----------
[ -w "$HOME" ] || die "\$HOME 不可写，无法安装"

for c in curl tar; do
    command -v "$c" >/dev/null 2>&1 || die "缺少 '$c'，请先安装（通常系统自带）"
done

# ---------- 0.5 旧安装位置探测（仅提供迁移提示，不自动删除）----------
_legacy_hits=()
[ -d "$HOME/.chipseq-pipeline" ] && _legacy_hits+=("  - 数据目录：$HOME/.chipseq-pipeline")
[ -d "$HOME/.local/chipseq-env" ] && _legacy_hits+=("  - 分析环境：$HOME/.local/chipseq-env")
[ -d "$HOME/.local/chipseq-launch" ] && _legacy_hits+=("  - 启动器：  $HOME/.local/chipseq-launch")
if [ ${#_legacy_hits[@]} -gt 0 ]; then
    warn "检测到旧版本的位置不在项目内（本版开始封装到 $CHIPSEQ_RUNTIME_DIR）："
    printf '%s\n' "${_legacy_hits[@]}" >&2
    warn "  可选："
    warn "    a) 迁移：手动 'mv <旧> $CHIPSEQ_RUNTIME_DIR/{data,analysis,launch}'，重跑本脚本即可识别"
    warn "    b) 新开：不管旧数据，直接确认安装（旧目录自行 'rm -rf' 卸掉）"
    warn "    c) 保持旧布局：'export CHIPSEQ_DATA=~/.chipseq-pipeline CHIPSEQ_CONDA_ENV=~/.local/chipseq-env CHIPSEQ_LAUNCH_ENV=~/.local/chipseq-launch' 后重跑"
    unset _legacy_hits
fi

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
DATA_DIR="$CHIPSEQ_DATA"
log "项目运行时目录: $CHIPSEQ_RUNTIME_DIR"
log "数据目录:     $DATA_DIR"
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
                warn "自动降级到 conda 后端（秒级建启动器）。完整分析环境需随后用命令行构建（见安装完成后的【下一步】提示）。"
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
        log "conda 模式：仅创建秒级启动器（完整分析环境请用命令行构建，见下方【下一步】第 5 步）"
    else
        log "开始安装运行时依赖（可能几个 GB，请耐心等待）..."
    fi
    runtime_install
fi

# ---------- 4. 安装 CLI ----------
log "安装 CLI 命令到 ~/.local/bin ..."
"$HERE/scripts/install-cli.sh"

# ---------- 4.5 --full: 一键到底，直接建分析环境 + 补 R 包 ----------
if [ "$FULL" = "1" ]; then
    if [ "$RUNTIME" != "conda" ]; then
        warn "--full 目前仅对 conda 后端自动展开（当前 RUNTIME=$RUNTIME）；容器模式请手动预拉镜像/加载 .sif"
    elif [ "${SKIP_INSTALL:-0}" = "1" ]; then
        warn "--full 已忽略（SKIP_INSTALL=1）；去掉 SKIP_INSTALL 重跑才会下载分析环境"
    else
        log "--full 模式：接着建分析环境 + 补齐 R 包（首次 10–30 分钟；已装则幂等秒级）"
        log "  想只补命令行工具不装 R：CHIPSEQ_SKIP_R=1 重跑"
        log "  想中断：Ctrl+C；之后可单独跑 chipseq-app-update all 继续"
        runtime_build_all
    fi
fi

# ---------- 5. 完成 ----------
BIN_DIR="$HOME/.local/bin"
cat <<EOF

============================================================
 安装完成！
------------------------------------------------------------
 运行时:        $RUNTIME
 项目内目录:    $CHIPSEQ_RUNTIME_DIR
   ├─ launch/    启动器 prefix env
   ├─ analysis/  分析 prefix env（含 r-base + compilers）
   │   └─ lib/R/library/  R 包库（ChIPseeker/clusterProfiler/ggtree…）
   ├─ data/      数据目录（配置/参考/结果）
   ├─ pkgs/      conda 包缓存（隔离）
   ├─ cache/     pip 缓存 + install.packages 临时目录
   └─ bin/       CLI 实体（~/.local/bin 中为软链接）
 CLI 目录:      $BIN_DIR（软链接到 项目内 bin/）

 下一步:
   1) 确保 $BIN_DIR 在 PATH 中（很多 Linux 默认已含）:
        echo 'export PATH="$BIN_DIR:\$PATH"' >> ~/.bashrc && source ~/.bashrc

   2) 参考基因组、结果、日志都在项目内：
        $CHIPSEQ_RUNTIME_DIR/data/references/
        $CHIPSEQ_RUNTIME_DIR/data/annotations/
        $CHIPSEQ_RUNTIME_DIR/data/config/config.yaml

   3) 启动:
        chipseq-app

      远程服务器访问（无桌面）:
        本地机器执行: ssh -L 8501:localhost:8501 \$USER@<服务器地址>
        浏览器打开:   http://localhost:8501
EOF
if [ "$FULL" != "1" ] && [ "$RUNTIME" = "conda" ]; then
    cat <<EOF

   4) 尚未 --full，分析环境未建。一行补齐（含 R 包）：
        chipseq-app-update all
      等价手动（路径已在项目内）：
        conda env create -p "$CHIPSEQ_CONDA_ENV" -f environment.yml
        Rscript workflow/env/install_R_packages.R
      完成后启动 chipseq-app，到侧栏【环境监测】页确认。
EOF
elif [ "$FULL" = "1" ] && [ "$RUNTIME" = "conda" ]; then
    cat <<EOF

   4) --full 已建完分析环境 + 补齐 R 包；如中途中断，重跑：
        chipseq-app-update all      # 幂等，已装则秒过
EOF
fi
cat <<EOF

 卸载：chipseq-app-uninstall   （默认删项目内目录 + ~/.local/bin 软链，保留数据）
       rm -rf $CHIPSEQ_RUNTIME_DIR 是同样效果（数据也在里面，自行备份）
============================================================

EOF
