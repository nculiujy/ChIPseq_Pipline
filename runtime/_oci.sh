# shellcheck shell=bash
# ChIPseq Pipeline — OCI 引擎共享实现（docker / podman 通用）
# 使用前必须设置 ENGINE=docker 或 ENGINE=podman。
# 由 runtime/docker.sh 与 runtime/podman.sh source。

: "${DATA_DIR:=$CHIPSEQ_DEFAULT_DATA_DIR}"
: "${ENGINE:?_oci.sh requires \$ENGINE to be set}"
: "${CHIPSEQ_IMAGE:=${CHIPSEQ_DEFAULT_IMAGE:-ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}}"

_CONTAINER="${CHIPSEQ_CONTAINER_NAME:-chipseq-pipeline}"

# 检查是否已启动过（存在容器）
_container_exists() {
    "$ENGINE" inspect "$_CONTAINER" >/dev/null 2>&1
}
_container_running() {
    [ "$("$ENGINE" inspect -f '{{.State.Running}}' "$_CONTAINER" 2>/dev/null || echo false)" = "true" ]
}

# ---------- 安装 ----------
runtime_install() {
    command -v "$ENGINE" >/dev/null 2>&1 || die "$ENGINE 命令未找到"
    if ! "$ENGINE" info >/dev/null 2>&1; then
        die "$ENGINE 守护进程/权限异常，无法执行 '$ENGINE info'。请检查权限设置或换其它后端。"
    fi

    # 探测本地 tarball（离线包）
    local local_tar="./chipseq-pipeline-image.tar"
    local local_tar_zst="./chipseq-pipeline-image.tar.zst"
    if [ -f "$local_tar_zst" ]; then
        log "使用本地离线镜像 $local_tar_zst"
        command -v zstd >/dev/null 2>&1 || die "需要 zstd 命令解压 $local_tar_zst"
        zstd -dc "$local_tar_zst" | "$ENGINE" load || die "$ENGINE load 失败"
        return 0
    elif [ -f "$local_tar" ]; then
        log "使用本地离线镜像 $local_tar"
        "$ENGINE" load -i "$local_tar" || die "$ENGINE load 失败"
        return 0
    fi

    if "$ENGINE" image inspect "$CHIPSEQ_IMAGE" >/dev/null 2>&1; then
        ok "镜像已存在: $CHIPSEQ_IMAGE"
        return 0
    fi

    check_disk_space "$HOME" 6
    log "拉取镜像 $CHIPSEQ_IMAGE (可能几个 GB) ..."
    "$ENGINE" pull "$CHIPSEQ_IMAGE" || die "拉取失败。可尝试：\n  1) 检查网络\n  2) 手动下载离线包放到当前目录（chipseq-pipeline-image.tar.zst）\n  3) 设置 CHIPSEQ_IMAGE 到可访问的镜像仓库"
    ok "镜像拉取完成"
}

# ---------- 启动 ----------
# runtime_run <binds_str> <port> <bind_addr>
runtime_run() {
    local binds="$1" port="$2" bind_addr="${3:-127.0.0.1}"

    if _container_running; then
        log "容器 $_CONTAINER 已在运行"
        return 0
    fi

    # 清理已存在的停止容器
    _container_exists && "$ENGINE" rm -f "$_CONTAINER" >/dev/null 2>&1 || true

    log "启动容器 $_CONTAINER (bind=${bind_addr}, port=${port}) ..."

    # podman rootless 通常不支持 --restart 与 host 端口 <1024；docker 支持 --restart
    local restart_flag=()
    if [ "$ENGINE" = "docker" ]; then
        restart_flag=(--restart unless-stopped)
    fi

    # shellcheck disable=SC2086
    "$ENGINE" run -d \
        --name "$_CONTAINER" \
        "${restart_flag[@]}" \
        -p "${bind_addr}:${port}:8501" \
        $binds \
        "$CHIPSEQ_IMAGE" >/dev/null || die "$ENGINE run 失败，请查看 '$ENGINE logs $_CONTAINER'"

    log "容器已启动: $_CONTAINER"
}

# ---------- 停止 ----------
runtime_stop() {
    if _container_exists; then
        "$ENGINE" rm -f "$_CONTAINER" >/dev/null 2>&1 || true
        log "已停止并移除容器 $_CONTAINER"
    else
        log "容器 $_CONTAINER 未创建"
    fi
}

# ---------- 日志 ----------
runtime_logs() {
    _container_exists || die "容器 $_CONTAINER 未创建"
    "$ENGINE" logs -f --tail 200 "$_CONTAINER"
}

runtime_logs_head() {
    local n="${1:-80}"
    _container_exists || { echo "(容器未创建)"; return 0; }
    "$ENGINE" logs --tail "$n" "$_CONTAINER" 2>&1 || echo "(无日志)"
}

# ---------- 状态 ----------
runtime_status() {
    echo "运行时:    $ENGINE ($("$ENGINE" --version 2>/dev/null | head -1))"
    echo "镜像:      $CHIPSEQ_IMAGE"
    if _container_exists; then
        "$ENGINE" inspect -f '容器:      {{.Name}}
状态:      {{.State.Status}} (Running={{.State.Running}})
启动时间:  {{.State.StartedAt}}
端口:      {{range $p, $c := .NetworkSettings.Ports}}{{$p}} -> {{(index $c 0).HostIp}}:{{(index $c 0).HostPort}} {{end}}' \
            "$_CONTAINER" 2>/dev/null || true
        echo
        "$ENGINE" stats --no-stream "$_CONTAINER" 2>/dev/null || true
    else
        echo "容器:      未创建"
    fi
}

# ---------- shell ----------
runtime_shell() {
    _container_running || die "容器 $_CONTAINER 未运行，请先 chipseq-app 启动"
    exec "$ENGINE" exec -it "$_CONTAINER" conda run --no-capture-output -n ChIPseq_Pipline bash
}

# ---------- 升级 ----------
runtime_update() {
    log "拉取最新镜像 $CHIPSEQ_IMAGE ..."
    "$ENGINE" pull "$CHIPSEQ_IMAGE" || die "拉取失败"
    runtime_stop || true
    ok "已升级；再次运行 chipseq-app 启动新版本"
}
