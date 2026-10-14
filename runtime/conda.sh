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
: "${CHIPSEQ_CONDA_ENV:=$HOME/.local/chipseq-env}"
: "${CHIPSEQ_ENV_URL:=https://github.com/chipseq-pipeline/ChIPseq_Pipline/releases/latest/download/chipseq-env-1.0.0.tar.gz}"

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

# ---------- 安装 ----------
runtime_install() {
    local env_root="$CHIPSEQ_CONDA_ENV"

    # 已就绪？
    if [ -x "$env_root/bin/streamlit" ] && [ -x "$env_root/bin/python" ]; then
        ok "conda-pack 环境已就绪: $env_root"
        return 0
    fi

    command -v curl >/dev/null 2>&1 || die "缺少 curl"
    command -v tar  >/dev/null 2>&1 || die "缺少 tar"

    # 探测本地 tarball（离线场景）
    local local_tar=""
    for f in ./chipseq-env-*.tar.gz ./chipseq-env.tar.gz; do
        [ -f "$f" ] && { local_tar="$f"; break; }
    done

    check_disk_space "$(dirname "$env_root")" 8

    mkdir -p "$env_root"
    local tmp; tmp=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN

    if [ -n "$local_tar" ]; then
        log "使用本地环境包: $local_tar"
        cp "$local_tar" "$tmp/env.tar.gz"
    else
        log "下载预打包 conda 环境 (约 4 GB) ..."
        log "  URL: $CHIPSEQ_ENV_URL"
        curl -fL --retry 3 --progress-bar -o "$tmp/env.tar.gz" "$CHIPSEQ_ENV_URL" \
            || die "下载失败。可手动下载 tarball 放到当前目录（chipseq-env-*.tar.gz）后重跑 bootstrap.sh"
    fi

    log "解压到 $env_root ..."
    tar -xzf "$tmp/env.tar.gz" -C "$env_root" || die "解压失败"

    # conda-pack 产物需要跑一次 conda-unpack 修正前缀路径
    if [ -x "$env_root/bin/conda-unpack" ]; then
        log "运行 conda-unpack 修正路径 ..."
        ( source "$env_root/bin/activate" && "$env_root/bin/conda-unpack" ) \
            || warn "conda-unpack 失败（可能已运行过），继续"
    fi

    [ -x "$env_root/bin/streamlit" ] || die "解压后未找到 streamlit，环境包可能损坏"
    ok "conda 环境就绪: $env_root"
}

# ---------- 启动 ----------
# runtime_run <binds_str(ignored)> <port> <bind_addr>
runtime_run() {
    local _binds="$1" port="$2" bind_addr="${3:-127.0.0.1}"
    local env_root="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir)
    local pid; pid=$(_pid_file)
    local outlog; outlog=$(_out_log)

    [ -x "$env_root/bin/streamlit" ] || die "conda 环境未安装，请先跑 bootstrap.sh"

    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        log "Streamlit 已在运行 (PID $(cat "$pid"))"
        return 0
    fi
    rm -f "$pid"

    mkdir -p "$(dirname "$outlog")"
    mkdir -p "$repo/logs" "$repo/result"

    log "启动 Streamlit (repo=$repo, port=$port, bind=$bind_addr) ..."

    # shellcheck disable=SC2086
    (
        cd "$repo" || exit 1
        # 激活 conda-pack 环境（设置 PATH、CONDA_PREFIX 等）
        # shellcheck disable=SC1091
        source "$env_root/bin/activate"
        # 后台运行；用 setsid 让它脱离终端
        setsid nohup streamlit run streamlit_app/app.py \
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
    local env_root="$CHIPSEQ_CONDA_ENV"
    local pid; pid=$(_pid_file)
    local repo; repo=$(_repo_dir 2>/dev/null || echo "?")
    echo "运行时:      conda-pack"
    echo "环境路径:    $env_root"
    [ -d "$env_root" ] && echo "环境大小:    $(du -sh "$env_root" 2>/dev/null | awk '{print $1}')"
    echo "仓库路径:    $repo"
    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        echo "状态:        运行中 (PID $(cat "$pid"))"
        ps -o pid,ppid,etime,pcpu,pmem,cmd -p "$(cat "$pid")" 2>/dev/null || true
    else
        echo "状态:        未运行"
    fi
}

# ---------- shell ----------
runtime_shell() {
    local env_root="$CHIPSEQ_CONDA_ENV"
    local repo; repo=$(_repo_dir)
    [ -x "$env_root/bin/bash" ] || [ -x "$env_root/bin/activate" ] \
        || die "conda 环境未安装"
    cd "$repo" || die "无法进入仓库目录 $repo"
    # shellcheck disable=SC1091
    source "$env_root/bin/activate"
    exec "${SHELL:-/bin/bash}"
}

# ---------- 升级 ----------
runtime_update() {
    local env_root="$CHIPSEQ_CONDA_ENV"
    runtime_stop || true
    log "删除旧环境: $env_root"
    rm -rf "$env_root"
    runtime_install
}
