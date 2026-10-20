# shellcheck shell=bash
# ChIPseq Pipeline — conda-pack 后端（无容器环境兜底）
# 依赖 common.sh 已加载。
#
# 目录约定（C 方案：项目内封装）：
#   <repo>/.chipseq-runtime/   CHIPSEQ_RUNTIME_DIR
#     ├ launch/                CHIPSEQ_LAUNCH_ENV   启动器 prefix env
#     ├ analysis/              CHIPSEQ_CONDA_ENV    分析  prefix env
#     ├ data/                  CHIPSEQ_DATA         用户数据根（配置/参考/结果/logs/marker）
#     ├ pkgs/                  CONDA_PKGS_DIRS      conda 包缓存（不写 $CONDA_ROOT/pkgs）
#     ├ Rlib/                  CHIPSEQ_R_LIB        R 用户库（不写 ~/R/...-library）
#     └ cache/pip/             PIP_CACHE_DIR        pip 缓存
#   ~/.local/bin/chipseq-*                     软链接，卸载时一并删
#   ~/.conda/environments.txt                   conda -p 会静默追加，调后清

# 项目内默认路径（common.sh 已派生 CHIPSEQ_RUNTIME_DIR，同名环境变量可覆盖）
: "${DATA_DIR:=$CHIPSEQ_DEFAULT_DATA_DIR}"
: "${CHIPSEQ_CONDA_ENV:=$CHIPSEQ_DEFAULT_ANALYSIS_ENV}"
: "${CHIPSEQ_LAUNCH_ENV:=$CHIPSEQ_DEFAULT_LAUNCH_ENV}"
# 离线分析环境包下载地址（供 provision_analysis_* 兜底；可用环境变量覆盖）
: "${CHIPSEQ_ENV_URL:=https://github.com/nculiujy/ChIPseq_Pipline/releases/latest/download/chipseq-env-1.0.0.tar.gz}"

_pid_file() { echo "$DATA_DIR/streamlit.pid"; }
_out_log()  { echo "$DATA_DIR/logs/streamlit.out"; }

# 从 $DATA_DIR/repo_path 读取仓库位置；bootstrap.sh 会写入
_repo_dir() {
    local f="$DATA_DIR/repo_path"
    [ -f "$f" ] || die "未找到 $f；请先在仓库目录内跑 ./bootstrap.sh"
    local d; d=$(cat "$f")
    [ -d "$d" ] || die "记录的仓库路径不存在: $d"
    echo "$d"
}

# ---------- 安装（A：只装精简启动器，让 B 能跑）----------
# 选择 conda/mamba 作为环境创建器
_installer() {
    if command -v mamba >/dev/null 2>&1; then echo mamba; else echo conda; fi
}

runtime_install() {
    # A 的职责：确保有 conda/mamba → 建精简启动器 → 写 analysis_env 标记。
    # 不下载 4GB 分析环境（由命令行 provision_analysis_* / chipseq-app-update all 构建）。
    local launcher="$CHIPSEQ_LAUNCH_ENV"
    _chipseq_export_isolation_env

    command -v conda >/dev/null 2>&1 || command -v mamba >/dev/null 2>&1 \
        || die "未检测到 conda/mamba。请先安装 Miniforge（https://github.com/conda-forge/miniforge）后重跑 bootstrap.sh"

    if [ -x "$launcher/bin/streamlit" ]; then
        ok "启动器环境已就绪: $launcher"
    else
        log "创建精简启动器环境（python=3.11 + streamlit + pyyaml）: $launcher"
        check_disk_space "$(dirname "$launcher")" 1
        local inst; inst=$(_installer)
        "$inst" create -p "$launcher" -y python=3.11 streamlit pyyaml \
            || die "创建启动器环境失败（可重试：$inst create -p $launcher -y python=3.11 streamlit pyyaml）"
        _chipseq_sanitize_environments_txt
    fi

    # 写 analysis_env 标记（供 Streamlit 侧 env_paths.analysis_prefix() 读取）
    mkdir -p "$DATA_DIR"
    echo "$CHIPSEQ_CONDA_ENV" > "$DATA_DIR/analysis_env"

    if [ -x "$CHIPSEQ_CONDA_ENV/bin/snakemake" ]; then
        ok "分析环境已就绪: $CHIPSEQ_CONDA_ENV"
    else
        warn "分析环境尚未构建（$CHIPSEQ_CONDA_ENV）。"
        warn "请用命令行构建：conda env create -p $CHIPSEQ_CONDA_ENV -f <仓库>/environment.yml（或 chipseq-app-update all）；随后可在【环境监测】页确认。"
    fi
}

# ---------- 分析环境构建（命令行；【环境监测】页只读检测、不触发安装）----------
# 优先本地离线包：chipseq-env*.tar.gz 解压 + conda-unpack
provision_analysis_offline() {
    local env_root="$CHIPSEQ_CONDA_ENV"
    command -v tar >/dev/null 2>&1 || die "缺少 tar"
    local local_tar=""
    for f in ./chipseq-env-*.tar.gz ./chipseq-env.tar.gz "$DATA_DIR"/chipseq-env-*.tar.gz; do
        [ -f "$f" ] && { local_tar="$f"; break; }
    done
    if [ -z "$local_tar" ]; then
        command -v curl >/dev/null 2>&1 || die "缺少 curl 且本地无 chipseq-env*.tar.gz"
        log "下载分析环境包: $CHIPSEQ_ENV_URL"
        local_tar="$DATA_DIR/chipseq-env.tar.gz"
        curl -fL --retry 3 --progress-bar -o "$local_tar" "$CHIPSEQ_ENV_URL" \
            || die "下载失败；可手动下载 tarball 放到仓库目录后重跑"
    fi
    log "离线解压分析环境: $local_tar → $env_root"
    check_disk_space "$(dirname "$env_root")" 8
    mkdir -p "$env_root"
    tar -xzf "$local_tar" -C "$env_root" || die "解压失败"
    if [ -x "$env_root/bin/conda-unpack" ]; then
        log "运行 conda-unpack 修正路径 ..."
        # conda-pack 环境可能无 bin/activate，conda-unpack 为独立脚本，直接按绝对路径调用
        "$env_root/bin/conda-unpack" || warn "conda-unpack 失败（可能已运行过），继续"
    fi
    [ -x "$env_root/bin/snakemake" ] || warn "解压后未找到 snakemake，环境包可能不完整"
    echo "$env_root" > "$DATA_DIR/analysis_env"
    ok "分析环境就绪: $env_root"
}

# 在线构建：优先 mamba（求解/下载快数倍），否则 conda env create/update -p <prefix> -f environment.yml
provision_analysis_online() {
    local env_root="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir 2>/dev/null || echo ".")
    local yml="$repo/environment.yml"
    [ -f "$yml" ] || die "未找到 environment.yml: $yml"
    local inst; inst=$(_installer)          # mamba 优先，无则 conda
    command -v "$inst" >/dev/null 2>&1 || die "未检测到 conda/mamba，无法在线构建"
    if [ "$inst" = conda ]; then
        warn "当前用经典 conda 求解较慢；建议装 mamba，或 conda config --set solver libmamba 后重试"
    fi
    log "在线构建分析环境：$inst env … -p $env_root -f $yml"
    check_disk_space "$(dirname "$env_root")" 8
    _chipseq_export_isolation_env
    if [ -d "$env_root/conda-meta" ]; then
        log "分析环境已存在 → 执行 update（补缺）"
        "$inst" env update -p "$env_root" -f "$yml" || die "$inst env update 失败"
    else
        "$inst" env create -p "$env_root" -f "$yml" || die "$inst env create 失败"
    fi
    _chipseq_sanitize_environments_txt
    echo "$env_root" > "$DATA_DIR/analysis_env"
    ok "分析环境就绪: $env_root"
}

# ---------- 启动 ----------
# runtime_run <binds_str(ignored)> <port> <bind_addr>
runtime_run() {
    local _binds="$1" port="$2" bind_addr="${3:-127.0.0.1}"
    local launcher="$CHIPSEQ_LAUNCH_ENV"
    local analysis="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir)
    local pid; pid=$(_pid_file)
    local outlog; outlog=$(_out_log)

    [ -x "$launcher/bin/streamlit" ] || die "启动器环境未安装，请先跑 bootstrap.sh"

    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        log "Streamlit 已在运行 (PID $(cat "$pid"))"
        return 0
    fi
    rm -f "$pid"

    mkdir -p "$(dirname "$outlog")"
    mkdir -p "$repo/logs" "$repo/result"

    # 幂等写 analysis_env 标记（env_paths.analysis_prefix() 依赖）
    echo "$analysis" > "$DATA_DIR/analysis_env"

    log "启动 Streamlit (launcher=$launcher, analysis=$analysis, port=$port, bind=$bind_addr) ..."

    # shellcheck disable=SC2086
    (
        cd "$repo" || exit 1
        # conda create -p 前缀环境内没有 bin/activate，故不 source activate：
        # 直接用启动器 env 的 streamlit 绝对路径（shebang 指向该 env 的 python），
        # 并把 launcher/bin、analysis/bin 前置到 PATH，同时用标记/环境变量告知子进程。
        export PATH="$launcher/bin:$analysis/bin:$PATH"
        export CHIPSEQ_CONDA_ENV="$analysis"
        export CHIPSEQ_LAUNCH_ENV="$launcher"
        export CHIPSEQ_DATA="$DATA_DIR"
        export CHIPSEQ_REPO="$repo"
        export CHIPSEQ_RUNTIME_DIR="${CHIPSEQ_RUNTIME_DIR:-$repo/.chipseq-runtime}"
        export CHIPSEQ_R_LIB="${CHIPSEQ_R_LIB:-$CHIPSEQ_RUNTIME_DIR/Rlib}"
        export R_LIBS_USER="${R_LIBS_USER:-$CHIPSEQ_R_LIB}"
        # 后台运行；用 setsid 让它脱离终端
        setsid nohup "$launcher/bin/streamlit" run streamlit_app/app.py \
            --server.address="$bind_addr" \
            --server.port="$port" \
            --server.headless=true \
            --browser.gatherUsageStats=false \
            >>"$outlog" 2>&1 < /dev/null &
        echo $! > "$pid"
    )

    sleep 1
    [ -f "$pid" ] && log "Streamlit 已启动 (PID $(cat "$pid"))"
}

# ---------- 停止 ----------
runtime_stop() {
    local pid; pid=$(_pid_file)
    if [ -f "$pid" ]; then
        local p; p=$(cat "$pid" 2>/dev/null || echo "")
        if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
            kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
            sleep 2
            kill -KILL -"$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null || true
        fi
        rm -f "$pid"
        log "已停止"
    else
        log "无运行中进程"
    fi
}

# ---------- 日志 ----------
runtime_logs() {
    local outlog; outlog=$(_out_log)
    [ -f "$outlog" ] || { echo "无日志文件"; return 0; }
    tail -f -n 200 "$outlog"
}

runtime_logs_head() {
    local n="${1:-80}"
    local outlog; outlog=$(_out_log)
    [ -f "$outlog" ] && tail -n "$n" "$outlog" || echo "(无日志)"
}

# ---------- 状态 ----------
runtime_status() {
    local launcher="$CHIPSEQ_LAUNCH_ENV" analysis="$CHIPSEQ_CONDA_ENV"
    local pid; pid=$(_pid_file)
    local repo; repo=$(_repo_dir 2>/dev/null || echo "?")
    echo "运行时:      conda（launcher + analysis 双 prefix）"
    echo "启动器环境:  $launcher"
    [ -x "$launcher/bin/streamlit" ] && echo "  状态:      就绪" || echo "  状态:      未安装（请跑 bootstrap.sh）"
    [ -d "$launcher" ] && echo "  大小:      $(du -sh "$launcher" 2>/dev/null | awk '{print $1}')"
    echo "分析环境:    $analysis"
    if [ -x "$analysis/bin/snakemake" ]; then
        echo "  状态:      就绪"
    else
        echo "  状态:      未构建 → 命令行构建：conda env create -p $analysis -f <仓库>/environment.yml（或 chipseq-app-update all）"
    fi
    [ -d "$analysis" ] && echo "  大小:      $(du -sh "$analysis" 2>/dev/null | awk '{print $1}')"
    echo "仓库路径:    $repo"
    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        echo "进程:        运行中 (PID $(cat "$pid"))"
        ps -o pid,ppid,etime,pcpu,pmem,cmd -p "$(cat "$pid")" 2>/dev/null || true
    else
        echo "进程:        未运行"
    fi
}

# ---------- shell ----------
runtime_shell() {
    local launcher="$CHIPSEQ_LAUNCH_ENV" analysis="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir)
    [ -x "$launcher/bin/python" ] || die "启动器环境未安装"
    cd "$repo" || die "无法进入仓库目录 $repo"
    # -p 前缀环境没有 bin/activate：优先用 base 的 conda.sh 规范激活，失败则仅设 PATH 兜底
    _chipseq_export_isolation_env
    export PATH="$launcher/bin:$analysis/bin:$PATH"
    local croot=""
    [ -n "${CONDA_EXE:-}" ] && croot="$(dirname "$(dirname "$CONDA_EXE")")"
    if [ -n "$croot" ] && [ -f "$croot/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1091
        source "$croot/etc/profile.d/conda.sh" && conda activate "$launcher" 2>/dev/null || true
    fi
    exec "${SHELL:-/bin/bash}"
}

# ---------- 升级 ----------
# 默认只重建启动器（秒级）。第二参数控制分析环境：
#   all / analysis  本地离线包优先→秒级解压；否则在线 mamba/conda 构建（存在则 update 补缺）
#   offline         强制从离线包解压重装
#   online          强制在线构建（存在则 update 补缺，不删旧）
_has_local_env_tarball() {
    local repo; repo=$(_repo_dir 2>/dev/null || echo ".")
    local f
    for f in "$repo"/chipseq-env-*.tar.gz ./chipseq-env-*.tar.gz \
             "$DATA_DIR"/chipseq-env-*.tar.gz; do
        if [ -f "$f" ]; then return 0; fi
    done
    return 1
}

# 幂等补齐 R 包（best-effort）。
# 约定：分析环境不含 R（environment.yml 故意去掉 r-base），这里直接调 PATH 上的
# 系统 Rscript；install_R_packages.R 自己会探可写 lib、选 Bioc 版本、txdbmaker 条件安装。
_install_R_packages_best_effort() {
    if [ "${CHIPSEQ_SKIP_R:-0}" = "1" ]; then
        log "CHIPSEQ_SKIP_R=1 → 跳过 R 包补齐"
        return 0
    fi
    local repo; repo=$(_repo_dir 2>/dev/null || echo ".")
    local r_script="$repo/workflow/env/install_R_packages.R"
    if [ ! -f "$r_script" ]; then
        warn "未找到 $r_script，跳过 R 包补齐"
        return 0
    fi
    local rscript_bin
    rscript_bin="$(command -v Rscript 2>/dev/null || true)"
    if [ -z "$rscript_bin" ]; then
        warn "未检测到 Rscript（分析环境不含 R、系统也未装）→ 跳过 R 包补齐"
        warn "可先装系统 R，随后手动：Rscript $r_script"
        return 0
    fi
    log "补齐 R 包（幂等；已就位秒过；首次可能几分钟）: $rscript_bin"
    # 确保项目内 Rlib 存当，并把 R_LIBS_USER 锁到项目内
    _chipseq_export_isolation_env
    NONINTERACTIVE=1 "$rscript_bin" "$r_script" \
        || warn "R 包安装/校验有告警；可稍后手动重试：$rscript_bin $r_script"
}

# 建分析环境 + 补 R 包，不碰启动器（供 bootstrap --full 直接调用）
runtime_build_all() {
    if _has_local_env_tarball; then
        log "检测到本地离线分析环境包 → 秒级解压安装"
        rm -rf "$CHIPSEQ_CONDA_ENV"
        provision_analysis_offline
    else
        log "在线构建/补齐分析环境（mamba/conda，存在则 update）: $CHIPSEQ_CONDA_ENV"
        provision_analysis_online
    fi
    _install_R_packages_best_effort
}

runtime_update() {
    runtime_stop || true
    local launcher="$CHIPSEQ_LAUNCH_ENV" mode="${1:-}"
    log "更新启动器环境: $launcher"
    rm -rf "$launcher"
    runtime_install
    case "$mode" in
        all|analysis)
            runtime_build_all ;;
        offline)
            rm -rf "$CHIPSEQ_CONDA_ENV"; provision_analysis_offline
            _install_R_packages_best_effort ;;
        online)
            provision_analysis_online
            _install_R_packages_best_effort ;;
        "")
            : ;;  # 仅更新启动器
        *)
            warn "未知模式 '$mode'（可用: all|analysis|online|offline）；仅更新了启动器" ;;
    esac
}
