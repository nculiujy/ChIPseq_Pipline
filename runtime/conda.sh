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
#     ├ analysis/lib/R/library/ CHIPSEQ_R_LIB  R 包库（与 workflow 中 CONDA_PREFIX/lib/R/library 同步）
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

# ---------- 启动器环境需要哪些包 ----------
# 启动器只跑 Streamlit UI，不碰分析，所以这里只要「页面里 import 到的第三方包」。
# 加新依赖时改这一处：新建环境会带上，已装好的环境在 update 时自动补齐。
# 格式 conda包名:python模块名，两者不同名（pyyaml -> yaml）不能省。
CHIPSEQ_LAUNCHER_PKGS="streamlit pyyaml plotly"
_LAUNCHER_IMPORTS="streamlit:streamlit pyyaml:yaml plotly:plotly"

# 打印缺的 conda 包名（空格分隔）；全部齐了输出为空
_launcher_missing() {
    local py="$CHIPSEQ_LAUNCH_ENV/bin/python" item pkg mod
    [ -x "$py" ] || { echo "$CHIPSEQ_LAUNCHER_PKGS"; return; }
    for item in $_LAUNCHER_IMPORTS; do
        pkg="${item%%:*}"; mod="${item##*:}"
        "$py" -c "import $mod" >/dev/null 2>&1 || printf '%s ' "$pkg"
    done
}

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

_create_launcher() {
    local inst="$1" launcher="$2"
    check_disk_space "$(dirname "$launcher")" 1

    # 三条路，从快到慢：
    #   1) conda 全离线 create —— 包缓存齐全时秒级（装过一次的机器才有缓存）
    #   2) venv + pip —— 不依赖任何缓存，实测约 30 秒（2.5s 建 venv + 29s 装包）
    #   3) conda 联网 create —— 兜底；光拉十几个 channel 的 repodata 就要十几秒到十几分钟
    #
    # 中间那条以前是「离线建基础件 + pip 补剩下的」。问题是缓存为空时第一条断、
    # 第二条也断（它还依赖 --offline），最后仍然掉到联网。venv 不看 pkgs/，
    # 只要系统里有 python3.9+ 就能建，这才是缓存缺失时真正的快路径。
    if "$inst" create -p "$launcher" -y --offline python=3.11 $CHIPSEQ_LAUNCHER_PKGS >/dev/null 2>&1; then
        ok "启动器环境已创建（全部走本地包缓存，未联网）"
    else
        rm -rf "$launcher"
        if _create_launcher_venv "$launcher" && [ -z "$(_launcher_missing)" ]; then
            ok "启动器环境已创建（venv + pip）: $launcher"
        else
            rm -rf "$launcher"
            log "venv 路径不可用，改用 conda 联网创建（需拉取 repodata，可能较慢）…"
            "$inst" create -p "$launcher" -y python=3.11 $CHIPSEQ_LAUNCHER_PKGS \
                || die "创建启动器环境失败（可重试：$inst create -p $launcher -y python=3.11 $CHIPSEQ_LAUNCHER_PKGS）"
        fi
    fi
    _chipseq_sanitize_environments_txt
}

# conda 只认带 conda-meta/ 的 prefix：目录在、python 也在，但 conda-meta 缺失时
# `install -p` 会直接 `Expected environment not found at prefix` + Aborting。
# 环境被 mv 过、装到一半中断、或曾被别的工具处理都可能造成这种「半不像」的状态。
_launcher_is_conda_env() { [ -d "$CHIPSEQ_LAUNCH_ENV/conda-meta" ]; }

# 启动器能不能真的跑起来（streamlit + pyyaml 都在）。
# 这是判断「该不该保留这个环境」的唯一依据——不是 conda 认不认，而是它能不能干活。
_launcher_runnable() {
    local py="$CHIPSEQ_LAUNCH_ENV/bin/python"
    [ -x "$py" ] || return 1
    "$py" -c "import streamlit, yaml" >/dev/null 2>&1
}

# pip 源：默认走清华镜像。PyPI 官方源在国内慢网络下单包就要几十秒，
# 而启动器要装二十来个依赖（含 pyarrow/numpy/pandas 这类几十 MB 的）。
# 海外机器或内网私有源用 CHIPSEQ_PIP_INDEX 覆盖。
_pip_index() {
    echo "${CHIPSEQ_PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
}

# 找可用的 base python（建 venv 用）。streamlit 要求 >=3.9，从新到旧挑第一个能用的。
_base_python() {
    local c v
    for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        v=$("$c" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null) || continue
        if [ "${v:-0}" -ge 309 ]; then command -v "$c"; return 0; fi
    done
    return 1
}

# 用 venv + pip 建启动器：不经过 conda 求解、不拉 channel 的 repodata，
# 只下二十来个纯 Python wheel。目录结构与 conda 环境一致（bin/python、bin/streamlit），
# 所以下游判断（_launcher_missing / runtime_install 的就绪分支）一行都不用改。
_create_launcher_venv() {
    local launcher="$1" base idx
    base=$(_base_python) || { warn "未找到 Python >= 3.9，无法用 venv 建启动器"; return 1; }
    log "用 venv 创建启动器（跳过 conda 求解）: $base → $launcher"
    "$base" -m venv "$launcher" || { warn "venv 创建失败"; rm -rf "$launcher"; return 1; }
    idx=$(_pip_index)
    log "pip 安装 $CHIPSEQ_LAUNCHER_PKGS（源: $idx）"
    if ! "$launcher/bin/python" -m pip install --disable-pip-version-check -q \
            -i "$idx" --timeout 60 --retries 3 $CHIPSEQ_LAUNCHER_PKGS; then
        warn "pip 安装失败（可用 CHIPSEQ_PIP_INDEX 换源后重试）"
        rm -rf "$launcher"
        return 1
    fi
    return 0
}

# 往现有 prefix 里用 pip 补包（不经过 conda，不需要 conda-meta）
_launcher_pip_install() {
    local py="$CHIPSEQ_LAUNCH_ENV/bin/python"
    "$py" -m pip install --disable-pip-version-check -q -i "$(_pip_index)" --timeout 60 --retries 3 "$@"
}

runtime_install() {
    # A 的职责：确保有 conda/mamba → 建精简启动器 → 写 analysis_env 标记。
    # 不下载 4GB 分析环境（由命令行 provision_analysis_* / chipseq-app-update all 构建）。
    local launcher="$CHIPSEQ_LAUNCH_ENV"
    _chipseq_export_isolation_env

    command -v conda >/dev/null 2>&1 || command -v mamba >/dev/null 2>&1 \
        || die "未检测到 conda/mamba。请先安装 Miniforge（https://github.com/conda-forge/miniforge）后重跑 bootstrap.sh"

    local inst; inst=$(_installer)
    local miss; miss=$(_launcher_missing)
    if [ -x "$launcher/bin/streamlit" ] && [ -z "$miss" ]; then
        ok "启动器环境已就绪: $launcher（跳过重建；需要刷新用 CHIPSEQ_REFRESH_LAUNCHER=1）"
    elif [ -z "$miss" ]; then
        ok "启动器环境可用: $launcher"
    elif _launcher_is_conda_env && _launcher_runnable; then
        # 正常情况：conda 能管的环境，缺包用 conda 补（几秒）。
        log "启动器环境缺依赖（$miss），补齐: $launcher"
        if ! "$inst" install -p "$launcher" -y $miss; then
            # 不 rm -rf：网络/镜像抖一下也会走到这里，删了就彻底跑不起来。
            warn "补齐失败（多为网络/镜像问题）；环境已保留，稍后重试：chipseq-app-update"
        fi
        _chipseq_sanitize_environments_txt
    elif _launcher_runnable; then
        # 缺 conda-meta（conda 不认这个 prefix），但 python 和 streamlit 都跑得起来。
        # 环境里几百个包都是好的，为了一两个纯 Python 包把它删掉重建（先重新求解
        # 整个环境、再下载几百 MB）纯属浪费——直接 pip 装进去，运行期完全等价。
        log "启动器缺依赖（$miss），该前缀无 conda-meta（conda 管不了），改用 pip 补装"
        if ! _launcher_pip_install $miss; then
            warn "pip 补装失败；环境已保留，稍后重试：chipseq-app-update"
        fi
    else
        # 到这里说明这个环境真的跑不起来（没有 python，或者 streamlit 已经坏了），
        # 没法就地修，只能重建。
        if [ -d "$launcher" ]; then
            warn "启动器环境不可用（缺 python 或 streamlit 无法导入），重建: $launcher"
            rm -rf "$launcher"
        fi
        log "创建精简启动器环境（python=3.11 + $CHIPSEQ_LAUNCHER_PKGS）: $launcher"
        _create_launcher "$inst" "$launcher"
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
        # 注意：mamba/conda 的 `env update` 子命令不接受 -y/--yes（只有 env create 接受），
        # 传了会直接报 "unrecognized arguments: -y"。update 本身不弹 Confirm，用 </dev/null 兜底。
        "$inst" env update -p "$env_root" -f "$yml" </dev/null || die "$inst env update 失败"
    else
        # 预建的 $env_root/lib/R/library 让 env_root 成为非 conda 目录，
        # libmamba 会报 Non-conda folder exists at prefix，须先清掉空壳。
        # rmdir 只删空目录，有真实文件时删不动，不会误删数据。
        if [ -d "$env_root" ]; then
            rmdir -p "$CHIPSEQ_R_LIB" 2>/dev/null || true
            if [ -d "$env_root" ]; then
                die "$env_root 已存在但非 conda 环境，请先 mv 走再重试"
            fi
            log "已清理预建的空壳目录，重新创建分析环境"
        fi
        "$inst" env create -y -p "$env_root" -f "$yml" </dev/null || die "$inst env create 失败"
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
        export CHIPSEQ_R_LIB="${CHIPSEQ_R_LIB:-$analysis/lib/R/library}"
        export R_LIBS_USER="${R_LIBS_USER:-$CHIPSEQ_R_LIB}"
        # 后台运行；用 setsid 让它脱离终端
        # enableCORS/enableXsrfProtection 关掉：否则 Streamlit 对非 localhost 来源
        # （Qoder 预览跨域 iframe、cloudflared *.trycloudflare.com、局域网 IP）会拒绝
        # WebSocket 握手 → 前端卡在空白页。关掉后接受任意 Origin，内嵌/隧道代理下正常渲染。
        setsid nohup "$launcher/bin/streamlit" run streamlit_app/app.py \
            --server.address="$bind_addr" \
            --server.port="$port" \
            --server.headless=true \
            --server.enableCORS=false \
            --server.enableXsrfProtection=false \
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
    local miss; miss=$(_launcher_missing)
    if [ -x "$launcher/bin/streamlit" ] && [ -z "$miss" ]; then
        echo "  状态:      就绪"
    elif [ -x "$launcher/bin/python" ] && _launcher_is_conda_env; then
        echo "  状态:      缺依赖（$miss）—— 跑 chipseq-app-update 补齐"
    elif [ -d "$launcher" ]; then
        echo "  状态:      环境不完整（缺 conda-meta/）—— 跑 chipseq-app-update 重建"
    else
        echo "  状态:      未安装（请跑 bootstrap.sh）"
    fi
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
# R 选取优先级：
#   1) $CHIPSEQ_CONDA_ENV/bin/Rscript（分析环境自带 r-base，与 workflow 统一）
#   2) PATH 上的 Rscript（系统 R，兼容旧行为）
# R 库目标：$CHIPSEQ_R_LIB（默认 = 分析环境的 lib/R/library，workflow 里硬编码读取）
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
    _chipseq_export_isolation_env
    local rscript_bin=""
    if [ -x "$CHIPSEQ_CONDA_ENV/bin/Rscript" ]; then
        rscript_bin="$CHIPSEQ_CONDA_ENV/bin/Rscript"
    else
        rscript_bin="$(command -v Rscript 2>/dev/null || true)"
    fi
    if [ -z "$rscript_bin" ]; then
        warn "未检测到 Rscript（分析环境含 r-base 或系统 R 都没）→ 跳过 R 包补齐"
        warn "可先建分析环境或装系统 R，随后手动：Rscript $r_script"
        return 0
    fi
    log "补齐 R 包（幂等；已就位秒过；首次可能几分钟）: $rscript_bin"
    log "  R 库目标: $CHIPSEQ_R_LIB"
    # 在 runtime/cache 下执行：
    #   1) 不在仓库根 → 不加载 .Rprofile 中的 renv 激活（避免“已装但加载失败”的
    #      假阳性：BiocManager::install 不递归修 enrichplot 类旧包的 ggtree 依赖）
    #   2) cache 可写且专属，避免基坐环境 /tmp 只读或 TMPDIR 不存在时 fatal
    local r_workdir="$CHIPSEQ_RUNTIME_DIR/cache"
    mkdir -p "$r_workdir"
    (
        cd "$r_workdir" && \
        PATH="$CHIPSEQ_CONDA_ENV/bin:$PATH" \
        TMPDIR="$r_workdir" \
        CONDA_PREFIX="$CHIPSEQ_CONDA_ENV" \
        R_LIBS_USER="$CHIPSEQ_R_LIB" \
        R_LIBS="" \
        R_PROFILE="" R_PROFILE_USER="" \
        NONINTERACTIVE=1 \
        "$rscript_bin" "$r_script"
    ) || warn "R 包安装/校验有告警；可稍后手动重试：$rscript_bin $r_script"
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
    # config/ 已纳入 .gitignore，git pull 命中「删除 config/*」的提交时会把它清空。
    # 每次更新补种一次模板（已存在则不覆盖，用户自己的配置不会被动）。
    local _repo; _repo=$(_repo_dir 2>/dev/null || echo ".")
    seed_repo_config "$_repo" "$CHIPSEQ_RUNTIME_DIR/assets"
    log "更新启动器环境: $launcher"
    # 启动器只依赖 $CHIPSEQ_LAUNCHER_PKGS，绝大多数更新不需要重建（runtime_install
    # 会先逐个 import 检查，缺什么只补什么）。早先这里无条件 rm -rf，导致每次 update
    # 都重跑一遍 conda 求解（慢盘上几十分钟）。
    if [ "${CHIPSEQ_REFRESH_LAUNCHER:-0}" = "1" ]; then
        log "强制刷新启动器环境（CHIPSEQ_REFRESH_LAUNCHER=1）: $launcher"
        rm -rf "$launcher"
    fi
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
