#!/usr/bin/env bash
# ChIPseq Pipeline — 把 CLI 装到项目内，再从 ~/.local/bin 建软链接
#
# 布局（C 方案：项目内封装）：
#   <repo>/.chipseq-runtime/bin/chipseq-app*       可执行实体（占位符已替换）
#   <repo>/.chipseq-runtime/lib/common.sh          共享 bash 函数
#   <repo>/.chipseq-runtime/runtime/*.sh           apptainer/podman/docker/conda/_oci
#   <repo>/.chipseq-runtime/assets/*               默认 config.yaml / metadata.csv
#   ~/.local/bin/chipseq-app*                      软链接 → 上面的实体（PATH 入口）
#
# 卸载：`chipseq-app-uninstall` 会删 <repo>/.chipseq-runtime 与 ~/.local/bin 软链
#
# 环境变量：
#   CHIPSEQ_RUNTIME_DIR  项目内运行时目录（默认 <repo>/.chipseq-runtime）
#   PREFIX               HOME 侧软链前缀（默认 $HOME/.local），只影响软链位置
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CHIPSEQ_REPO="$SRC"
# shellcheck disable=SC1091
source "$SRC/scripts/lib/common.sh"

RUNTIME_DIR="$CHIPSEQ_RUNTIME_DIR"
RT_BIN="$RUNTIME_DIR/bin"
RT_LIB="$RUNTIME_DIR/lib"
RT_RUNTIME="$RUNTIME_DIR/runtime"
RT_ASSETS="$RUNTIME_DIR/assets"

LINK_DIR="${PREFIX:-$HOME/.local}/bin"

log "安装 CLI（项目内封装）"
log "  源目录:     $SRC"
log "  运行时目录: $RUNTIME_DIR"
log "  软链目录:   $LINK_DIR"

mkdir -p "$RT_BIN" "$RT_LIB" "$RT_RUNTIME" "$RT_ASSETS"

# ---------- 拷贝共享资源 ----------
install -m 644 "$SRC/scripts/lib/common.sh"   "$RT_LIB/common.sh"
install -m 644 "$SRC"/runtime/*.sh            "$RT_RUNTIME/"
install -m 644 "$SRC"/assets/*                "$RT_ASSETS/" 2>/dev/null || true

# ---------- 拷贝 CLI 实体 + 替换占位符 ----------
for f in "$SRC"/cli/chipseq-app*; do
    name=$(basename "$f")
    install -m 755 "$f" "$RT_BIN/$name"
    # | 作分隔符避免路径中的 / 冲突
    sed -i "s|@@LIB@@|$RT_LIB/common.sh|g"           "$RT_BIN/$name"
    sed -i "s|@@RUNTIME_DIR@@|$RT_RUNTIME|g"         "$RT_BIN/$name"
    sed -i "s|@@ASSETS@@|$RT_ASSETS|g"               "$RT_BIN/$name"
    sed -i "s|@@REPO@@|$SRC|g"                        "$RT_BIN/$name"
    sed -i "s|@@RUNTIME_DIR_PLACEHOLDER@@|$RUNTIME_DIR|g" "$RT_BIN/$name"
done

# ---------- ~/.local/bin 软链接 ----------
mkdir -p "$LINK_DIR" 2>/dev/null || warn "无法创建 $LINK_DIR，跳过软链，直接用 $RT_BIN/chipseq-app 亦可"
linked=0; failed=0
for f in "$RT_BIN"/chipseq-app*; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    if ln -sfn "$f" "$LINK_DIR/$name" 2>/dev/null; then
        linked=$((linked+1))
    else
        warn "创建软链失败: $LINK_DIR/$name（可能是权限或已存在同名目录）"
        failed=$((failed+1))
    fi
done
ok "已建立 $linked 个软链接到 $LINK_DIR"
[ "$failed" -gt 0 ] && warn "$failed 个软链失败；可直接用 $RT_BIN/<命令名> 调用"

# ---------- 清理旧版本残留（可选）----------
_legacy_share="$HOME/.local/share/chipseq-pipeline"
if [ -d "$_legacy_share" ]; then
    warn "检测到旧版安装副本 $_legacy_share（本版不再使用）"
    warn "  可手动清理：rm -rf $_legacy_share"
fi

# ---------- PATH 提示 ----------
case ":$PATH:" in
    *":$LINK_DIR:"*) ;;
    *)
        warn "$LINK_DIR 不在 PATH 中。请追加到 shell rc："
        warn "  echo 'export PATH=\"$LINK_DIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
        ;;
esac

ok "CLI 已安装到 $RT_BIN（软链 $LINK_DIR）"
echo
echo "可用命令:"
ls -1 "$RT_BIN"/chipseq-app* 2>/dev/null | sed 's|.*/|  |'
