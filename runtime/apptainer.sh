# shellcheck shell=bash
# ChIPseq Pipeline — Apptainer/Singularity 后端
# 由 cli/chipseq-app* 与 bootstrap.sh source；依赖 common.sh 已加载。

: "${DATA_DIR:=$HOME/.chipseq-pipeline}"

# 可通过环境变量覆盖
: "${CHIPSEQ_SIF_URI:=https://github.com/chipseq-pipeline/ChIPseq_Pipline/releases/latest/download/chipseq-pipeline.sif}"
: "${CHIPSEQ_IMAGE:=docker://ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}"

_APPTAINER_BIN=""
_apptainer_bin() {
    if [ -z "$_APPTAINER_BIN" ]; then
        if command -v apptainer >/dev/null 2>&1; then
            _APPTAINER_BIN="apptainer"
        elif command -v singularity >/dev/null 2>&1; then
            _APPTAINER_BIN="singularity"
        else
            die "未找到 apptainer/singularity 命令"
        fi
    fi
    echo "$_APPTAINER_BIN"
}

_sif_path()      { echo "$DATA_DIR/chipseq-pipeline.sif"; }
_pid_file()      { echo "$DATA_DIR/apptainer.pid"; }
_out_log()       { echo "$DATA_DIR/logs/apptainer.out"; }

# ---------- 安装 ----------
runtime_install() {
    local sif; sif=$(_sif_path)
    local bin; bin=$(_apptainer_bin)

    if [ -f "$sif" ] && [ -s "$sif" ]; then
        ok ".sif 已存在: $sif ($(du -h "$sif" | awk '{print $1}'))"
        return 0
    fi

    # 优先探测仓库同级目录的离线包
    local local_sif="./chipseq-pipeline.sif"
    if [ -f "$local_sif" ]; then
        log "使用本地 .sif 文件: $local_sif"
        cp "$local_sif" "$sif"
        return 0
    fi

    check_disk_space "$(dirname "$sif")" 6

    log "下载 .sif 镜像 (可能几个 GB, 请耐心等待) ..."
    log "  URL: $CHIPSEQ_SIF_URI"
    if curl -fL --retry 3 --progress-bar -o "$sif.part" "$CHIPSEQ_SIF_URI"; then
        mv "$sif.part" "$sif"
        ok ".sif 下载完成: $sif"
        return 0
    fi
    rm -f "$sif.part"

    warn "从 Release 下载失败，回落到 apptainer pull (docker:// URI) ..."
    log "  URI: $CHIPSEQ_IMAGE"
    if "$bin" pull --force "$sif" "$CHIPSEQ_IMAGE"; then
        ok ".sif 构建完成: $sif"
        return 0
    fi

    die "无法获取 .sif 镜像。请手动下载后放到 $sif 或设置 CHIPSEQ_SIF_URI"
}

# ---------- 启动 ----------
# runtime_run <binds_str> <port> <bind_addr>
runtime_run() {
    local binds="$1" port="$2" bind_addr="${3:-127.0.0.1}"
    local bin sif pid outlog
    bin=$(_apptainer_bin); sif=$(_sif_path); pid=$(_pid_file); outlog=$(_out_log)

    [ -f "$sif" ] || die ".sif 未找到: $sif，请先跑 bootstrap.sh"

    # 已在跑？
    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        log "Apptainer 进程已在运行 (PID $(cat "$pid"))"
        return 0
    fi
    rm -f "$pid"

    mkdir -p "$(dirname "$outlog")"

    # Apptainer 与宿主共享网络命名空间，Streamlit 直接绑宿主端口。
    # 出于安全，默认只允许 127.0.0.1；--public 时通过 SERVER_ADDRESS=0.0.0.0 传入。
    local server_address="127.0.0.1"
    [ "$bind_addr" = "0.0.0.0" ] && server_address="0.0.0.0"

    log "启动 Apptainer 容器 (bind=${bind_addr}, port=${port}) ..."

    # 使用 setsid + nohup 让容器进程脱离当前 shell
    setsid nohup env \
        PORT="$port" \
        SERVER_ADDRESS="$server_address" \
        "$bin" exec \
            --bind "$binds" \
            --no-home \
            --cleanenv \
            --env PORT="$port" \
            --env SERVER_ADDRESS="$server_address" \
            "$sif" \
            /entrypoint.sh \
        >"$outlog" 2>&1 < /dev/null &

    echo $! > "$pid"
    log "Apptainer 已启动 (PID $(cat "$pid"))"
}

# ---------- 停止 ----------
runtime_stop() {
    local pid; pid=$(_pid_file)
    if [ -f "$pid" ]; then
        local p; p=$(cat "$pid" 2>/dev/null || echo "")
        if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
            # 杀掉整个进程组，因为 setsid 起了新会话
            kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
            sleep 2
            kill -KILL -"$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null || true
        fi
        rm -f "$pid"
    fi
    # 兜底：清理任何残留的 sif 执行进程
    pkill -f "chipseq-pipeline.sif" 2>/dev/null || true
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
    local pid; pid=$(_pid_file)
    local sif; sif=$(_sif_path)
    echo "运行时:      apptainer ($(_apptainer_bin) --version 2>/dev/null | head -1)"
    echo ".sif 路径:   $sif"
    [ -f "$sif" ] && echo ".sif 大小:   $(du -h "$sif" | awk '{print $1}')"
    if [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; then
        echo "状态:        运行中 (PID $(cat "$pid"))"
        ps -o pid,ppid,etime,pcpu,pmem,cmd -p "$(cat "$pid")" 2>/dev/null || true
    else
        echo "状态:        未运行"
    fi
}

# ---------- shell ----------
runtime_shell() {
    local bin sif binds
    bin=$(_apptainer_bin); sif=$(_sif_path)
    binds=$(build_binds apptainer "$DATA_DIR")
    [ -f "$sif" ] || die ".sif 未找到: $sif"
    exec "$bin" shell --bind "$binds" --no-home "$sif"
}

# ---------- 升级 ----------
runtime_update() {
    local sif; sif=$(_sif_path)
    runtime_stop || true
    rm -f "$sif"
    runtime_install
}
