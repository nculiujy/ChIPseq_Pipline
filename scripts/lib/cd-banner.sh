#!/usr/bin/env bash
# ChIPseq Pipeline — cd 进仓库目录时自动打印命令速查
#
# 由 scripts/install-cli.sh 安装时注入到 ~/.bashrc（挂在 PROMPT_COMMAND 上），
# 用户不需要手工 source 本文件。
#
# 行为：
#   - 只在「当前目录是仓库根」时打印，子目录静默
#   - 同一目录反复回车不重复打印；本 shell 内首次给完整菜单，之后再进入只给单行
#   - 关闭：export CHIPSEQ_NO_CD_BANNER=1
#
# 注意：本文件会被 source 进用户的交互 shell，因此**不能**改动 shell 选项
# （set -e 之类），任何可能失败的语句都要自己兜住，否则会把用户的终端搞坏。

_chipseq_cd_banner() {
    # 用户主动关闭
    [ "${CHIPSEQ_NO_CD_BANNER:-0}" = "1" ] && return 0

    # 目录没变直接返回：避免每次回车都去 stat 文件（慢盘上这一步很贵）
    [ "${_CHIPSEQ_BANNER_AT:-}" = "$PWD" ] && return 0
    _CHIPSEQ_BANNER_AT="$PWD"

    # 只认仓库根（同时认 snakefile 与 cli/chipseq-app，避免同名目录误触发）
    [ -f ./snakefile ] && [ -f ./cli/chipseq-app ] || return 0

    # ---------- 采集状态（都做静默降级，取不到就算了） ----------
    local ver="" state="未初始化（先跑 ./bootstrap.sh）"
    ver=$(sed -n 's/^APP_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
          ./streamlit_app/utils/version.py 2>/dev/null | head -n 1)
    if [ -x ./.chipseq-runtime/launch/bin/streamlit ]; then
        state="环境已就绪"
    elif [ -d ./.chipseq-runtime ]; then
        state="环境不完整（跑 chipseq-app-update 补齐）"
    fi

    local tunnel_line
    if [ -x ./cloudflared-linux-amd64 ] || [ -x ./cloudflared ] \
       || command -v cloudflared >/dev/null 2>&1 \
       || { [ -n "${CHIPSEQ_CLOUDFLARED:-}" ] && [ -x "${CHIPSEQ_CLOUDFLARED}" ]; }; then
        tunnel_line="chipseq-app --tunnel    启动并同时开 cloudflared 公网临时地址"
    else
        tunnel_line="chipseq-app --tunnel    公网临时地址（需先放 cloudflared-linux-amd64）"
    fi

    local sep
    sep=$(printf '%.0s-' $(seq 1 60))

    # 本 shell 内已经看过完整菜单 → 之后只给一行，避免每次都刷屏
    if [ "${_CHIPSEQ_BANNER_FULL:-0}" = "1" ]; then
        printf '%s\n' " ChIPseq Pipeline${ver:+ v$ver} · $state · 输入 chipseq-app 启动，-h 看全部选项"
        return 0
    fi
    _CHIPSEQ_BANNER_FULL=1

    printf '\n'
    printf ' ChIPseq Pipeline%s  ·  %s\n' "${ver:+ v$ver}" "$state"
    printf '%s\n' "$sep"
    printf '%s\n' ' chipseq-app             启动面板（浏览器 + 局域网直连）'
    printf '%s\n' " $tunnel_line"
    printf '%s\n' ' chipseq-app -h          全部启动选项（端口 / 绑定 / 运行时）'
    printf '%s\n' ' chipseq-app-logs        实时日志'
    printf '%s\n' ' chipseq-app-status      运行状态（含分析环境是否就绪）'
    printf '%s\n' ' chipseq-app-shell       进入分析环境（R / snakemake 调试）'
    printf '%s\n' ' chipseq-app-stop        停止面板'
    printf '%s\n' ' chipseq-app-update      升级；all/analysis/online/offline 建分析环境'
    printf '%s\n' ' chipseq-app-uninstall   卸载（默认保留数据；--data/--all 见 -h）'
    printf '%s\n' "$sep"
    printf '%s\n' ' 关闭本提示：export CHIPSEQ_NO_CD_BANNER=1'
    printf '\n'
}

# 只定义函数，不立即执行；真正的触发由 ~/.bashrc 里的 PROMPT_COMMAND 负责
