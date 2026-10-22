#!/usr/bin/env bash
# ChIPseq Pipeline — 把已构建好的「分析环境」打包成离线 tar.gz（conda-pack）
#
# 用途：在任意一台已经把分析环境装好的机器上跑一次，产出一个可搬运的
#       chipseq-env-<版本>.tar.gz。其它机器用 provision_analysis_offline 秒级解压
#       安装（tar -xzf + conda-unpack），**完全跳过 conda/mamba 求解**——这是重复
#       部署 / 给同事装机最快的方式。
#
# 用法：
#   bash scripts/pack-analysis-env.sh                 # 输出到仓库根目录 chipseq-env-1.0.0.tar.gz
#   bash scripts/pack-analysis-env.sh /path/out.tar.gz
#   CHIPSEQ_ENV_VERSION=1.0.1 bash scripts/pack-analysis-env.sh
#
# 环境变量：
#   CHIPSEQ_CONDA_ENV     要打包的分析 prefix（默认 <repo>/.chipseq-runtime/analysis）
#   CHIPSEQ_ENV_VERSION   产物版本号（默认 1.0.0，决定默认文件名 chipseq-env-<版本>.tar.gz）
#
# 产物放置（新机器会自动识别）：
#   - 放仓库根目录或 <repo>/.chipseq-runtime/data/ 下，命名 chipseq-env-*.tar.gz；
#   - 或上传到 GitHub Release，资产名与 runtime/conda.sh 的 CHIPSEQ_ENV_URL 一致。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # <repo>/scripts
REPO="$(cd "$HERE/.." && pwd)"
export CHIPSEQ_REPO="$REPO"
# shellcheck disable=SC1091
source "$HERE/lib/common.sh"

ANALYSIS_ENV="${CHIPSEQ_CONDA_ENV:-$CHIPSEQ_DEFAULT_ANALYSIS_ENV}"
VERSION="${CHIPSEQ_ENV_VERSION:-1.0.0}"
OUT="${1:-$REPO/chipseq-env-${VERSION}.tar.gz}"

_pick_inst() {
    if command -v mamba >/dev/null 2>&1; then echo mamba; else echo conda; fi
}

log "==== 打包分析环境为离线包 ===="
log "分析 prefix: $ANALYSIS_ENV"
log "输出文件   : $OUT"

# ---------- 1. 分析环境必须已就绪 ----------
if [ ! -x "$ANALYSIS_ENV/bin/snakemake" ]; then
    die "分析环境未构建或不含 snakemake：$ANALYSIS_ENV
  请先构建：$(_pick_inst) env create -p $ANALYSIS_ENV -f $REPO/environment.yml
  或：chipseq-app-update online"
fi

# ---------- 2. conda-pack 必须可用（缺则装进 base） ----------
PACK=()
if command -v conda-pack >/dev/null 2>&1; then
    PACK=(conda-pack)
elif "$ANALYSIS_ENV/bin/conda-pack" --help >/dev/null 2>&1; then
    PACK=("$ANALYSIS_ENV/bin/conda-pack")
elif conda pack --help >/dev/null 2>&1; then
    PACK=(conda pack)
else
    log "未检测到 conda-pack，尝试安装到 conda base ..."
    inst="$(_pick_inst)"
    if ! "$inst" install -n base -c conda-forge conda-pack -y; then
        die "安装 conda-pack 失败。请手动执行 'pip install conda-pack' 或 '$inst install -n base -c conda-forge conda-pack' 后重试"
    fi
    # 刚装到 base 的 conda-pack 不一定在当前 PATH（激活的是别的环境时尤其如此），
    # 直接到 base 的 bin 下取绝对路径，别依赖 command -v。
    if command -v conda-pack >/dev/null 2>&1; then
        PACK=(conda-pack)
    else
        base_bin="$(conda info --base 2>/dev/null || true)/bin"
        [ -x "$base_bin/conda-pack" ] || die "conda-pack 已安装但找不到可执行入口（预期 $base_bin/conda-pack）"
        log "conda-pack 在 $base_bin（不在 PATH，使用绝对路径）"
        PACK=("$base_bin/conda-pack")
    fi
fi

# ---------- 3. 打包 ----------
mkdir -p "$(dirname "$OUT")"
if [ -e "$OUT" ]; then warn "输出文件已存在，将覆盖: $OUT"; rm -f "$OUT"; fi
log "打包中（环境较大，可能几分钟）：${PACK[*]} -p $ANALYSIS_ENV -o $OUT"
# 忽略 editable / 缺失文件告警，避免个别 pip 包导致整体失败
if ! "${PACK[@]}" -p "$ANALYSIS_ENV" -o "$OUT" \
        --ignore-editable-packages --ignore-missing-files; then
    warn "带忽略参数失败，回退为最小参数重试 ..."
    "${PACK[@]}" -p "$ANALYSIS_ENV" -o "$OUT" || die "conda-pack 打包失败"
fi

sz=""
if command -v du >/dev/null 2>&1; then sz="（$(du -h "$OUT" | awk '{print $1}')）"; fi
echo
ok "已生成离线分析环境包: $OUT $sz"
cat <<EOF

下一步（让别的机器秒级安装，无需 conda 求解）：
  1) 搬运：把该文件放到目标机器的仓库根目录，或 ~/.chipseq-pipeline/ 下
     （文件名保持 chipseq-env-*.tar.gz，provision_analysis_offline 会自动识别）；
     或上传到 GitHub Release，资产名对齐 runtime/conda.sh 里的 CHIPSEQ_ENV_URL。
  2) 目标机器执行：
       chipseq-app-update offline        # 强制从离线包解压安装
     或
       chipseq-app-update all            # 有离线包则秒级解压、否则在线构建
  3) 解压后会自动运行 conda-unpack 修正路径；随后在【环境监测】页确认。
EOF
