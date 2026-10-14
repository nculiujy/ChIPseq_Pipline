#!/usr/bin/env bash
# ChIPseq Pipeline — 把 CLI 安装到 ~/.local/bin
#
# 布局：
#   ~/.local/bin/chipseq-app*                    可执行入口（占位符已替换）
#   ~/.local/share/chipseq-pipeline/lib/         common.sh
#   ~/.local/share/chipseq-pipeline/runtime/     apptainer.sh / podman.sh / docker.sh / conda.sh / _oci.sh
#   ~/.local/share/chipseq-pipeline/assets/      默认 config.yaml / metadata.csv
#
# 环境变量：
#   PREFIX       安装前缀（默认 $HOME/.local）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$SRC/scripts/lib/common.sh"

BIN="${PREFIX:-$HOME/.local}/bin"
SHARE="${PREFIX:-$HOME/.local}/share/chipseq-pipeline"

log "安装 CLI"
log "  源目录:   $SRC"
log "  BIN:      $BIN"
log "  SHARE:    $SHARE"

mkdir -p "$BIN" "$SHARE/lib" "$SHARE/assets" "$SHARE/runtime"

# ---------- 拷贝资源 ----------
install -m 644 "$SRC/scripts/lib/common.sh"   "$SHARE/lib/common.sh"
install -m 644 "$SRC"/runtime/*.sh            "$SHARE/runtime/"
install -m 644 "$SRC"/assets/*                "$SHARE/assets/" 2>/dev/null || true

# ---------- 拷贝并替换 CLI 占位符 ----------
for f in "$SRC"/cli/chipseq-app*; do
    name=$(basename "$f")
    install -m 755 "$f" "$BIN/$name"
    # 用 | 作分隔符避免路径中的 / 冲突
    sed -i "s|@@LIB@@|$SHARE/lib/common.sh|g"          "$BIN/$name"
    sed -i "s|@@RUNTIME_DIR@@|$SHARE/runtime|g"        "$BIN/$name"
    sed -i "s|@@ASSETS@@|$SHARE/assets|g"              "$BIN/$name"
done

# ---------- PATH 提示 ----------
case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
        warn "$BIN 不在 PATH 中。请追加到 shell rc："
        warn "  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
        ;;
esac

ok "CLI 已安装到 $BIN"
echo
echo "已安装命令:"
ls -1 "$BIN"/chipseq-app* 2>/dev/null | sed 's|.*/|  |'
