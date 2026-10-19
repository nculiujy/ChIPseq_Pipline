# shellcheck shell=bash
# ChIPseq Pipeline — conda-pack 后端（无容器环境兜底）
# 依赖 common.sh 已加载。
#
# 与容器后端的差异：
#   - 直接在宿主上跑 streamlit（无网络/挂载命名空间）
#   - 用户数据 = git clone 下来的仓库目录（$REPO_DIR），
#     ~/.chipseq-pipeline/ 只放运行时元数据（PID、日志、repo_path、runtime 标记）
#   - 参考基因组/结果/日志都写到仓库内相应路径

: "${DATA_DIR:=$HOME/.chipseq-pipeline}"
# 分析环境 prefix（全部生信工具 + R），由浏览器「环境配置」页(B)构建
: "${CHIPSEQ_CONDA_ENV:=$HOME/.local/chipseq-env}"
# 精简启动器 prefix（仅 python + streamlit + pyyaml），由 bootstrap.sh(A)秒级创建
: "${CHIPSEQ_LAUNCH_ENV:=$HOME/.local/chipseq-launch}"
# 离线分析环境包下载地址（供 provision_analysis_* 兼底；可用环境变量覆盖）
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
    # 不再下载 4GB 分析环境（那是 B【浏览器「环境配置」页】的事）。
    local launcher="$CHIPSEQ_LAUNCH_ENV"

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
    fi

    # 写 analysis_env 标记（供 Streamlit 侧 env_paths.analysis_prefix() 读取）
    mkdir -p "$DATA_DIR"
    echo "$CHIPSEQ_CONDA_ENV" > "$DATA_DIR/analysis_env"

    if [ -x "$CHIPSEQ_CONDA_ENV/bin/snakemake" ]; then
        ok "分析环境已就绪: $CHIPSEQ_CONDA_ENV"
    else
        warn "分析环境尚未构建（$CHIPSEQ_CONDA_ENV）。"
        warn "启动后请到浏览器【环境配置】页完成阶段③～⑦（离线包优先、缺则在线 create）。"
    fi
}

# ---------- 分析环境构建（命令行兼底；主路由为浏览器【环境配置】页）----------
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

# 在线构建：conda env create/update -p <prefix> -f environment.yml
provision_analysis_online() {
    local env_root="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir 2>/dev/null || echo ".")
    local yml="$repo/environment.yml"
    [ -f "$yml" ] || die "未找到 environment.yml: $yml"
    command -v conda >/dev/null 2>&1 || die "未检测到 conda"
    log "在线构建分析环境: conda env create -p $env_root -f $yml"
    check_disk_space "$(dirname "$env_root")" 8
    if [ -d "$env_root/conda-meta" ]; then
        conda env update -p "$env_root" -f "$yml" || die "conda env update 失败"
    else
        conda env create -p "$env_root" -f "$yml" || die "conda env create 失败"
    fi
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
        export CHIPSEQ_DATA="$DATA_DIR"
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
        echo "  状态:      未构建 → 请到浏览器【环境配置】页补全（或运行 provision_analysis_online）"
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
    export PATH="$launcher/bin:$analysis/bin:$PATH"
    export CHIPSEQ_CONDA_ENV="$analysis"
    export CHIPSEQ_DATA="$DATA_DIR"
    local croot=""
    [ -n "${CONDA_EXE:-}" ] && croot="$(dirname "$(dirname "$CONDA_EXE")")"
    if [ -n "$croot" ] && [ -f "$croot/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1091
        source "$croot/etc/profile.d/conda.sh" && conda activate "$launcher" 2>/dev/null || true
    fi
    exec "${SHELL:-/bin/bash}"
}

# ---------- 升级 ----------
# 默认只重建启动器（秒级）；传 all 同时重建分析环境。
runtime_update() {
    runtime_stop || true
    local launcher="$CHIPSEQ_LAUNCH_ENV"
    log "更新启动器环境: $launcher"
    rm -rf "$launcher"
    runtime_install
    if [ "${1:-}" = "all" ]; then
        log "同时重建分析环境: $CHIPSEQ_CONDA_ENV"
        rm -rf "$CHIPSEQ_CONDA_ENV"
        provision_analysis_online || provision_analysis_offline
    fi
}
