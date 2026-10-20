# shellcheck shell=bash
# ChIPseq Pipeline — 公共 Bash 函数库
# 被 bootstrap.sh、cli/chipseq-app*、runtime/*.sh 共同 source

# ---------- 日志 ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    _C_INFO=$'\033[1;34m'; _C_WARN=$'\033[1;33m'; _C_ERR=$'\033[1;31m'
    _C_OK=$'\033[1;32m';   _C_RST=$'\033[0m'
else
    _C_INFO=""; _C_WARN=""; _C_ERR=""; _C_OK=""; _C_RST=""
fi

log()   { printf '%s[chipseq]%s %s\n'      "$_C_INFO" "$_C_RST" "$*"; }
ok()    { printf '%s[chipseq][OK]%s %s\n'   "$_C_OK"   "$_C_RST" "$*"; }
warn()  { printf '%s[chipseq][WARN]%s %s\n' "$_C_WARN" "$_C_RST" "$*" >&2; }
die()   { printf '%s[chipseq][ERROR]%s %s\n' "$_C_ERR" "$_C_RST" "$*" >&2; exit 1; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令 '$1'：$2"
}

# ---------- 全局常量 ----------
CHIPSEQ_APP_NAME="chipseq-pipeline"
CHIPSEQ_CONTAINER_NAME="chipseq-pipeline"
CHIPSEQ_DEFAULT_PORT=8501
CHIPSEQ_DEFAULT_IMAGE="${CHIPSEQ_IMAGE:-ghcr.io/chipseq-pipeline/chipseq-pipeline:latest}"

# ---------- 项目内运行时目录派生（C 方案：全封装到 <repo>/.chipseq-runtime/） ----------
# 派生 repo：从 common.sh 自身位置向上两级。
#   开发态 <repo>/scripts/lib/common.sh  -> <repo>
#   安装态 <repo>/.chipseq-runtime/lib/common.sh -> <repo>
# 兜底：若派生不出（比如 source 到软链接），从旧版 marker <data>/repo_path 读；仍失败则回落 HOME。
_chipseq_default_repo() {
    local self="${BASH_SOURCE[0]}" dir=""
    dir=$(cd "$(dirname "$self")/../.." 2>/dev/null && pwd) || dir=""
    # 排除误派到 HOME 顶层（老式 ~/.local/share/... 场景）
    case "$dir" in
        "$HOME"|"/"|"") dir="" ;;
    esac
    if [ -z "$dir" ] && [ -f "$HOME/.chipseq-pipeline/repo_path" ]; then
        dir=$(cat "$HOME/.chipseq-pipeline/repo_path" 2>/dev/null)
    fi
    [ -n "$dir" ] && [ -d "$dir" ] && echo "$dir"
}

CHIPSEQ_REPO="${CHIPSEQ_REPO:-$(_chipseq_default_repo)}"
CHIPSEQ_RUNTIME_DIR="${CHIPSEQ_RUNTIME_DIR:-${CHIPSEQ_REPO:+$CHIPSEQ_REPO/.chipseq-runtime}}"
# 派生不出（少见）时兜底 HOME，避免下游变量为空
: "${CHIPSEQ_RUNTIME_DIR:=$HOME/.chipseq-pipeline-runtime}"

# 默认路径常量（都可被同名环境变量覆盖；调用方负责 mkdir）
CHIPSEQ_DEFAULT_DATA_DIR="${CHIPSEQ_DATA:-$CHIPSEQ_RUNTIME_DIR/data}"
CHIPSEQ_DEFAULT_LAUNCH_ENV="${CHIPSEQ_LAUNCH_ENV:-$CHIPSEQ_RUNTIME_DIR/launch}"
CHIPSEQ_DEFAULT_ANALYSIS_ENV="${CHIPSEQ_CONDA_ENV:-$CHIPSEQ_RUNTIME_DIR/analysis}"
# R 库默认写到分析环境的 site-library（workflow/scripts/*.R 里 5_1_gsea.R 硬编码
# 读 `$CONDA_PREFIX/lib/R/library`），写到那里 = 跟 conda env 同命同终。
# 如果分析环境尚未创建（bootstrap 早期步骤），仍可手动设 CHIPSEQ_R_LIB 到临时位置。
CHIPSEQ_DEFAULT_RLIB="${CHIPSEQ_R_LIB:-$CHIPSEQ_DEFAULT_ANALYSIS_ENV/lib/R/library}"
CHIPSEQ_DEFAULT_PKGS="${CHIPSEQ_RUNTIME_DIR}/pkgs"
CHIPSEQ_DEFAULT_ENVS="${CHIPSEQ_RUNTIME_DIR}/envs"
CHIPSEQ_DEFAULT_PIP_CACHE="${CHIPSEQ_RUNTIME_DIR}/cache/pip"

# 一键把 conda / mamba / pip / R 的写入位置全部锁进项目运行时目录，
# 不动 ~/.condarc、$CONDA_ROOT/pkgs、~/R/...-library、~/.cache/pip。
# 需要在调用 conda env create / Rscript / pip 之前 source 或调用本函数。
_chipseq_export_isolation_env() {
    export CHIPSEQ_REPO CHIPSEQ_RUNTIME_DIR
    export CHIPSEQ_DATA="${CHIPSEQ_DATA:-$CHIPSEQ_DEFAULT_DATA_DIR}"
    export CHIPSEQ_LAUNCH_ENV="${CHIPSEQ_LAUNCH_ENV:-$CHIPSEQ_DEFAULT_LAUNCH_ENV}"
    export CHIPSEQ_CONDA_ENV="${CHIPSEQ_CONDA_ENV:-$CHIPSEQ_DEFAULT_ANALYSIS_ENV}"
    export CHIPSEQ_R_LIB="${CHIPSEQ_R_LIB:-$CHIPSEQ_DEFAULT_RLIB}"
    export CONDA_PKGS_DIRS="${CONDA_PKGS_DIRS:-$CHIPSEQ_DEFAULT_PKGS}"
    export CONDA_ENVS_DIRS="${CONDA_ENVS_DIRS:-$CHIPSEQ_DEFAULT_ENVS}"
    export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$CHIPSEQ_DEFAULT_PIP_CACHE}"
    # 若用户 shell 里已设 R_LIBS_USER，尊重之；否则指向分析环境的 lib/R/library
    export R_LIBS_USER="${R_LIBS_USER:-$CHIPSEQ_R_LIB}"
    mkdir -p "$CHIPSEQ_RUNTIME_DIR" "$CHIPSEQ_DATA" \
             "$CHIPSEQ_DEFAULT_PKGS" "$CHIPSEQ_DEFAULT_ENVS" \
             "$CHIPSEQ_R_LIB" "$PIP_CACHE_DIR" 2>/dev/null || true
}

# conda -p <prefix> 会静默追加 ~/.conda/environments.txt，污染用户 conda env 列表。
# 每次 conda env create/update 之后清掉本项目相关行；卸载时再兜一次。
_chipseq_sanitize_environments_txt() {
    local f="$HOME/.conda/environments.txt"
    [ -f "$f" ] || return 0
    [ -n "${CHIPSEQ_RUNTIME_DIR:-}" ] || return 0
    local tmp="$f.tmp.$$"
    # index==1 = 前缀匹配；只保留不属于本项目运行时目录的行
    if awk -v p="$CHIPSEQ_RUNTIME_DIR" 'index($0,p)==1{next}{print}' "$f" > "$tmp" 2>/dev/null; then
        # 只有内容真变时才 mv，避免 mtime 抖动
        if ! cmp -s "$tmp" "$f"; then mv "$tmp" "$f"; else rm -f "$tmp"; fi
    else
        rm -f "$tmp"
    fi
}

# ---------- 数据目录 ----------
# init_data_dir <root> <assets_dir>
init_data_dir() {
    local root="$1" assets="${2:-}"
    mkdir -p "$root"/{config,data,references,annotations,results,logs,renv,.snakemake}
    if [ -n "$assets" ] && [ -d "$assets" ]; then
        [ -f "$root/config/config.yaml"  ] || cp "$assets/config.yaml"  "$root/config/" 2>/dev/null || true
        [ -f "$root/config/metadata.csv" ] || cp "$assets/metadata.csv" "$root/config/" 2>/dev/null || true
    fi
}

# ---------- 运行时读取 ----------
# read_runtime <data_dir> -> echo runtime name (apptainer|podman|docker|conda)
read_runtime() {
    local data_dir="$1"
    [ -f "$data_dir/runtime" ] || die "未初始化，请先在仓库目录执行 ./bootstrap.sh"
    local rt; rt=$(cat "$data_dir/runtime")
    case "$rt" in
        apptainer|podman|docker|conda) echo "$rt" ;;
        *) die "未知运行时 '$rt'，请重跑 ./bootstrap.sh" ;;
    esac
}

# ---------- 端口探测 ----------
# find_free_port <起始端口>
find_free_port() {
    local p="${1:-$CHIPSEQ_DEFAULT_PORT}"
    local max_tries=100 i=0
    while [ "$i" -lt "$max_tries" ]; do
        if ! _port_in_use "$p"; then
            echo "$p"; return 0
        fi
        p=$((p + 1))
        i=$((i + 1))
        [ "$p" -gt 65535 ] && break
    done
    die "在 $1 起始的 $max_tries 个端口内未找到可用端口"
}

_port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | grep -qE "[:.]${port}\$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk 'NR>2{print $4}' | grep -qE "[:.]${port}\$"
    else
        # 兜底：尝试连一下
        (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null
    fi
}

# ---------- HTTP 探活 ----------
# wait_ready <url> <timeout_sec>
wait_ready() {
    local url="$1" timeout="${2:-60}" i=0
    while [ "$i" -lt "$timeout" ]; do
        if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1; i=$((i + 1))
    done
    return 1
}

# ---------- bind mount 列表构建 ----------
# build_binds <runtime> <data_dir>
# 输出：docker/podman -> "-v a:b -v c:d ..."；apptainer -> "a:b,c:d,..."；conda -> ""
build_binds() {
    local rt="$1" d="$2"
    local pairs=(
        "$d/config:$d/config"
        "$d/data:$d/data"
        "$d/references:$d/references"
        "$d/annotations:$d/annotations"
        "$d/results:$d/results"
        "$d/logs:$d/logs"
        "$d/renv:$d/renv"
        "$d/.snakemake:$d/.snakemake"
    )
    # 容器内使用固定路径 /app/*
    local container_pairs=(
        "$d/config:/app/config"
        "$d/data:/app/data"
        "$d/references:/app/workflow/resources"
        "$d/annotations:/app/workflow/anno"
        "$d/results:/app/result"
        "$d/logs:/app/logs"
        "$d/renv:/app/renv"
        "$d/.snakemake:/app/.snakemake"
    )
    case "$rt" in
        docker|podman)
            local out=""
            for p in "${container_pairs[@]}"; do out="$out -v $p"; done
            echo "$out"
            ;;
        apptainer)
            local IFS=,
            echo "${container_pairs[*]}"
            ;;
        conda)
            echo ""
            ;;
        *)
            die "build_binds: 未知运行时 '$rt'"
            ;;
    esac
}

# ---------- 访问信息打印 ----------
# print_access_info <bind> <port> <data_dir> <runtime>
print_access_info() {
    local bind="$1" port="$2" data="$3" rt="$4" tunnel_url="${5:-}"
    local host_ip=""
    if command -v hostname >/dev/null 2>&1; then
        # 多网卡/多网段机器：列出所有非回环 IPv4，逐个给候选地址（以前只取第一个会误导）
        host_ip=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -vE '^(127\.|::1|$)' | tr '\n' ' ')
        host_ip=${host_ip% }
        [ -z "$host_ip" ] && host_ip=$(hostname -i 2>/dev/null | awk '{print $1}')
    fi

    cat <<EOF

============================================================
 ChIPseq Pipeline 已启动  (runtime: $rt)
------------------------------------------------------------
 本机访问:    http://localhost:${port}
EOF
    if [ -n "$tunnel_url" ]; then
        cat <<EOF
 🌐 公网地址:   $tunnel_url
              ← cloudflared 快隧道，任何地方浏览器直接打开；断网/关终端仍在后台跑
              ⚠ 该 URL 公网可访问，知道链接的人都能进；用完 chipseq-app-stop 关闭
              ⚠ 重启隧道会换一个临时地址（需命名隧道才能固定）
EOF
    else
        if [ "$bind" = "0.0.0.0" ]; then
            if [ -n "$host_ip" ]; then
                local _ip _n=0
                for _ip in $host_ip; do
                    _n=$((_n+1))
                    if [ "$_n" = 1 ]; then
                        echo " 局域网访问:  http://${_ip}:${port}   ← Windows/Mac 浏览器开这个（不是 localhost）"
                    else
                        echo "              http://${_ip}:${port}   ← 若上面那个不通（本机在另一个网段），换这个试"
                    fi
                done
            else
                echo " 局域网访问:  已绑 0.0.0.0（未探到本机 IP，用 'hostname -I' 查）"
            fi
        else
            echo " (当前 --private：只监听 127.0.0.1，本机以外无法直连；去掉 --private 或加 --public 可局域网直连)"
        fi
        cat <<EOF
 SSH 隧道:    在本地机器执行:
                ssh -L ${port}:localhost:${port} \$USER@<服务器地址>
              然后浏览器打开 http://localhost:${port}
              （或直接加 --tunnel 走 cloudflared 公网临时地址，免配 SSH）
 ⚠ Xshell/PuTTY: 请关远程的 X11 转发（会话属性 > SSH > 隧道 > 取消 X11），
              否则启动脚本会误弹一个远程 Firefox 、延迟极高。
              新版已自动识别 SSH 会话，不会弹远程浏览器。
EOF
    fi
    cat <<EOF
 数据目录:    ${data}
 容器/进程:   ${CHIPSEQ_CONTAINER_NAME}
------------------------------------------------------------
 chipseq-app-logs    查看实时日志
 chipseq-app-status  查看运行状态
 chipseq-app-stop    停止服务
 chipseq-app-update  升级到最新版本
============================================================

EOF
}

# ---------- cloudflared 快速隧道（可选，临时公网 URL） ----------
# 目的：云服务器/多层 NAT 后拿不到局域网 IP、SSH 隧道也不方便时，用 cloudflared
# quick tunnel 把 http://localhost:PORT 暴露成一个 *.trycloudflare.com 临时地址，
# 任何地方浏览器直接打开。与 Streamlit 同款 setsid+nohup 脱离终端，断网不死。

_cloudflared_log()     { echo "${1:-$CHIPSEQ_DEFAULT_DATA_DIR}/logs/cloudflared.out"; }
_cloudflared_pid_file() { echo "${1:-$CHIPSEQ_DEFAULT_DATA_DIR}/cloudflared.pid"; }

# 定位 cloudflared 二进制：CHIPSEQ_CLOUDFLARED > <repo>/cloudflared-linux-amd64 > <repo>/cloudflared > PATH
_find_cloudflared() {
    if [ -n "${CHIPSEQ_CLOUDFLARED:-}" ] && [ -x "${CHIPSEQ_CLOUDFLARED:-}" ]; then
        echo "$CHIPSEQ_CLOUDFLARED"; return 0
    fi
    local repo="${CHIPSEQ_REPO:-$(_chipseq_default_repo)}" cand
    for cand in "$repo/cloudflared-linux-amd64" "$repo/cloudflared" "$(command -v cloudflared 2>/dev/null || true)"; do
        [ -n "$cand" ] && [ -x "$cand" ] && { echo "$cand"; return 0; }
    done
    return 1
}

# 从隧道日志里抓最后一条 trycloudflare URL
_tunnel_url_from_log() {
    local outlog; outlog=$(_cloudflared_log "${1:-$CHIPSEQ_DEFAULT_DATA_DIR}")
    [ -f "$outlog" ] && grep -oE 'https://[a-zA-Z0-9._-]+\.trycloudflare\.com' "$outlog" | tail -1
}

# 隧道是否在跑（pid 存活）
_tunnel_alive() {
    local pidf; pidf=$(_cloudflared_pid_file "${1:-$CHIPSEQ_DEFAULT_DATA_DIR}")
    [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null || echo 0)" 2>/dev/null
}

# start_tunnel <port> [data_dir]：后台起 cloudflared 快隧道，最多等 20s 拓 URL
start_tunnel() {
    local port="$1" data="${2:-$CHIPSEQ_DEFAULT_DATA_DIR}"
    local cf; cf=$(_find_cloudflared) || {
        warn "找不到 cloudflared 二进制。下载并放到仓库根目录："
        warn "  wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x cloudflared-linux-amd64"
        warn "  或用 CHIPSEQ_CLOUDFLARED=/绝对/路径/cloudflared 指定"
        return 1
    }
    local pidf outlog; pidf=$(_cloudflared_pid_file "$data"); outlog=$(_cloudflared_log "$data")
    mkdir -p "$data/logs"
    if _tunnel_alive "$data"; then
        local u; u=$(_tunnel_url_from_log "$data")
        log "隧道已在运行 (PID $(cat "$pidf"))${u:+ → $u}"
        TUNNEL_URL="$u"; export TUNNEL_URL; return 0
    fi
    rm -f "$pidf"
    log "启动 cloudflared 快隧道 → http://localhost:${port} ..."
    : > "$outlog"   # 每次重开都清空，避免抓到上一次会话的陈旧 URL
    (
        setsid nohup "$cf" tunnel --no-autoupdate --url "http://localhost:${port}" \
            >>"$outlog" 2>&1 < /dev/null &
        echo $! > "$pidf"
    )
    local url="" i
    for i in $(seq 1 20); do
        url=$(_tunnel_url_from_log "$data")
        [ -n "$url" ] && break
        kill -0 "$(cat "$pidf" 2>/dev/null || echo 0)" 2>/dev/null || { warn "cloudflared 进程异常退出，见 $outlog"; return 1; }
        sleep 1
    done
    if [ -n "$url" ]; then
        TUNNEL_URL="$url"; export TUNNEL_URL
        ok "公网临时地址: $url"
    else
        TUNNEL_URL=""; export TUNNEL_URL
        warn "20s 内未抓到 trycloudflare URL；可稍后手动查看：grep -o 'https://[^ ]*trycloudflare.com' $outlog"
    fi
}

# stop_tunnel [data_dir]
stop_tunnel() {
    local data="${1:-$CHIPSEQ_DEFAULT_DATA_DIR}" pidf; pidf=$(_cloudflared_pid_file "$data")
    [ -f "$pidf" ] || return 0
    local p; p=$(cat "$pidf" 2>/dev/null || echo "")
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
        kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
        sleep 1
        kill -KILL "$p" 2>/dev/null || true
        log "隧道已停止"
    fi
    rm -f "$pidf"
}

# ---------- 浏览器打开 ----------
# maybe_open_browser <bind> <port>
# 设计：只在真本地桌面会话里才弹浏览器；远程 SSH/X11 转发下启动远程 Firefox 会走 X 协议
# 回传到本地（典型表现为 Xshell 自带火狐弹出 + 高延迟），且用户本就该用 SSH 隧道 + 本地浏览器，
# 因此一律跳过。
maybe_open_browser() {
    local port="$2"
    # 1) 显式 --no-browser / NO_BROWSER=1
    [ "${NO_BROWSER:-0}" = "1" ] && return 0
    # 2) 任何 SSH 会话（Xshell / PuTTY / iTerm / linux term / 包括开了 X11 转发的）
    if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_CLIENT:-}" ] || [ -n "${SSH_TTY:-}" ]; then
        log "SSH 会话→不弹远程浏览器；请在本地机器建 SSH 隧道后用本地浏览器打开 http://localhost:${port}"
        return 0
    fi
    # 3) 没 SSH_* 变量但 DISPLAY 仍为 localhost:N 形态（X 转发遗留环境）
    case "${DISPLAY:-}" in
        localhost:*|localhost*:[0-9]*)
            warn "DISPLAY=${DISPLAY} 看起来是 X11 转发→不弹远程浏览器（会非常慢）"
            return 0 ;;
    esac
    # 4) 无显示环境直接跳过
    [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && return 0
    command -v xdg-open >/dev/null 2>&1 || return 0
    (xdg-open "http://localhost:${port}" >/dev/null 2>&1 &) || true
}

# ---------- usage ----------
usage() {
    cat <<EOF
用法: chipseq-app [选项]

选项:
  --public           绑定到 0.0.0.0，允许局域网访问（默认已局域网开放）
  --private, --local  只绑 127.0.0.1（仅服务器本机/SSH 隧道可访问）
  --tunnel           启动后再后台起一个 cloudflared 快隧道，打印 *.trycloudflare.com 公网临时 URL（适合云服务器/堡垒机，断网不死）
  --port PORT        指定宿主端口（默认 ${CHIPSEQ_DEFAULT_PORT}，冲突时自动 +1）
  --runtime NAME     强制指定运行时 (apptainer|podman|docker|conda)
  --no-browser       即使有桌面也不自动打开浏览器
  --pull             启动前强制拉取最新镜像/环境
  -h, --help         显示本帮助

环境变量:
  CHIPSEQ_IMAGE      覆盖 OCI 镜像名（docker/podman/apptainer）
  CHIPSEQ_SIF_URI    覆盖 .sif 下载地址（apptainer）
  CHIPSEQ_ENV_URL    覆盖分析环境 conda-pack tarball 地址（conda，供 provision_analysis_* 兜底）
  CHIPSEQ_PORT       覆盖端口
  CHIPSEQ_BIND       覆盖绑定地址（默认 0.0.0.0；只想回环就设 127.0.0.1）
  CHIPSEQ_TUNNEL     设为 1 等效 --tunnel（cloudflared 公网临时 URL）
  CHIPSEQ_CLOUDFLARED cloudflared 二进制绝对路径（默认找 <repo>/cloudflared-linux-amd64 或 PATH）
  CHIPSEQ_RUNTIME_DIR 项目内运行时目录（默认 <repo>/.chipseq-runtime）
  CHIPSEQ_DATA       数据目录（默认 <runtime>/data）
  CHIPSEQ_CONDA_ENV  分析环境 prefix（默认 <runtime>/analysis）
  CHIPSEQ_LAUNCH_ENV 启动器环境 prefix（默认 <runtime>/launch）
  CHIPSEQ_R_LIB      R 包库（默认 <runtime>/analysis/lib/R/library）
  NO_COLOR           设置后禁用彩色输出
EOF
}

# ---------- 磁盘空间检查 ----------
# check_disk_space <path> <required_gb>
check_disk_space() {
    local path="$1" need_gb="${2:-6}"
    command -v df >/dev/null 2>&1 || return 0
    local avail_kb avail_gb
    avail_kb=$(df -Pk "$path" 2>/dev/null | awk 'NR==2{print $4}')
    [ -z "$avail_kb" ] && return 0
    avail_gb=$((avail_kb / 1024 / 1024))
    if [ "$avail_gb" -lt "$need_gb" ]; then
        warn "$path 可用空间 ${avail_gb}G，可能不足以容纳 ${need_gb}G 的镜像/环境。"
        warn "如空间不足，可将项目仓库放到大盘上，或设 CHIPSEQ_DATA=/scratch/\$USER/chipseq-pipeline 换到大容量存储。"
    fi
}
