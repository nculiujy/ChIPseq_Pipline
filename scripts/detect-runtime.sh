#!/usr/bin/env bash
# ChIPseq Pipeline — 运行时探测
# 输出（stdout）: apptainer | podman | docker | conda | none
# 优先级：Apptainer/Singularity > Podman rootless > Docker（已可用） > conda-pack

set -uo pipefail

VERBOSE="${VERBOSE:-0}"
_v() { [ "$VERBOSE" = "1" ] && printf '[detect] %s\n' "$*" >&2 || true; }

# ---------- 1) Apptainer / Singularity ----------
for bin in apptainer singularity; do
    if command -v "$bin" >/dev/null 2>&1; then
        if "$bin" --version >/dev/null 2>&1; then
            _v "found $bin: $("$bin" --version 2>/dev/null)"
            echo "apptainer"; exit 0
        fi
    fi
done

# ---------- 2) Podman（rootless） ----------
if command -v podman >/dev/null 2>&1; then
    if podman info >/dev/null 2>&1; then
        _v "podman info ok"
        # 进一步验证能否真的运行 rootless 容器
        if podman run --rm docker.io/library/hello-world >/dev/null 2>&1 \
           || podman unshare true >/dev/null 2>&1; then
            echo "podman"; exit 0
        else
            _v "podman present but cannot run rootless (missing subuid/userns?)"
        fi
    else
        _v "podman binary found but 'podman info' failed"
    fi
fi

# ---------- 3) Docker（用户已在 docker 组） ----------
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        _v "docker info ok"
        echo "docker"; exit 0
    else
        _v "docker binary found but no permission to daemon (not in docker group)"
    fi
fi

# ---------- 4) conda-pack 兵家必争地 ----------
missing=""
for c in curl tar; do
    command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
done
if [ -z "$missing" ]; then
    _v "falling back to conda-pack"
    echo "conda"; exit 0
fi

_v "missing basic tools:$missing"
echo "none"
