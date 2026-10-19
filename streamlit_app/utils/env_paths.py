"""
env_paths.py — 统一的"分析工具定向"层（launcher / analysis 双 prefix 解耦）
=========================================================================

背景（对齐 A-B 组合环境配置重构方案）：
  - 启动器环境（launcher）：只含 python + streamlit + pyyaml，由 A（bootstrap.sh）
    秒级创建，负责把 Streamlit UI 拉起来。
  - 分析环境（analysis）：含全部生信工具 + R（bowtie2/samtools/macs2/snakemake…），
    由命令行 `conda env create -p <prefix> -f environment.yml` 构建（或 `chipseq-app-update all`
    重建；注：conda 模式的 bootstrap.sh 只建启动器、不建此环境）；
    浏览器「环境监测」页只读检测其是否满足分析要求，不再触发安装。

关键解耦：所有"分析工具"的子进程调用都通过 tool_search_path()/routed_env() 定向到
分析环境，而不是依赖"Streamlit 恰好跑在哪个环境里"。

向后兼容：开发态直接 `streamlit run`（在已含工具的 conda 环境里）时，若分析 prefix
不存在，tool_search_path() 会自动回落到 sys.prefix/bin，一切照旧可用。

约定路径：
  - 数据/元数据目录：$CHIPSEQ_DATA 或 ~/.chipseq-pipeline
  - 分析环境标记：  <data_dir>/analysis_env   （单行文本，写入 prefix 绝对路径）
  - 运行时标记：    <data_dir>/runtime        （apptainer|podman|docker|conda）
  - 分析环境默认：  $CHIPSEQ_CONDA_ENV 或 ~/.local/chipseq-env
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# 与 runtime/conda.sh、bootstrap.sh 中 CHIPSEQ_CONDA_ENV 的默认值保持一致
_DEFAULT_ANALYSIS_PREFIX = os.path.expanduser("~/.local/chipseq-env")
_DEFAULT_DATA_DIR = os.path.expanduser("~/.chipseq-pipeline")

_CONTAINER_RUNTIMES = ("apptainer", "podman", "docker")


def data_dir() -> Path:
    """运行时元数据目录：$CHIPSEQ_DATA 或 ~/.chipseq-pipeline。"""
    return Path(os.environ.get("CHIPSEQ_DATA") or _DEFAULT_DATA_DIR)


def analysis_prefix() -> str:
    """分析环境 prefix（绝对路径字符串）。

    优先读 <data_dir>/analysis_env 标记文件；缺省回落到 CHIPSEQ_CONDA_ENV 默认值。
    这里只负责"报告目标 prefix"，不要求它一定存在（B 会负责创建）。
    """
    marker = data_dir() / "analysis_env"
    try:
        if marker.is_file():
            txt = marker.read_text(encoding="utf-8").strip()
            if txt:
                return os.path.expanduser(txt)
    except OSError:
        pass
    return os.path.expanduser(os.environ.get("CHIPSEQ_CONDA_ENV") or _DEFAULT_ANALYSIS_PREFIX)


def analysis_bin() -> str:
    """分析环境的 bin 目录（可能不存在；调用方需自行判存在）。"""
    return os.path.join(analysis_prefix(), "bin")


def tool_search_path() -> str:
    """分析工具命令搜索路径：<prefix>/bin : sys.prefix/bin : 系统 PATH。

    - <prefix>/bin：命令行构建的分析环境（生产/发行包模式的主命中目录）。
    - sys.prefix/bin：当前运行 Streamlit 的 Python 环境 bin。开发态直接
      `streamlit run`（在已含工具的命名环境里）时，prefix 不存在，落到这里保持兼容。
    - 系统 PATH：其余（conda、git 等宿主命令）。
    """
    parts: list[str] = []
    ab = analysis_bin()
    if os.path.isdir(ab):
        parts.append(ab)
    sp_bin = os.path.join(sys.prefix, "bin")
    if sp_bin not in parts:
        parts.append(sp_bin)
    cur = os.environ.get("PATH", "")
    parts += [p for p in cur.split(os.pathsep) if p and p not in parts]
    return os.pathsep.join(parts)


def routed_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """返回注入了 tool_search_path() 的 env 字典，供 subprocess.Popen(env=...) 使用。"""
    env = dict(base if base is not None else os.environ)
    env["PATH"] = tool_search_path()
    return env


def resolved_tool_path(cmd: str) -> Optional[str]:
    """在 tool_search_path() 中查找命令，返回绝对路径或 None。"""
    import shutil
    return shutil.which(cmd, path=tool_search_path())


def has_analysis_tools() -> bool:
    """分析环境是否就绪（prefix/bin 下能找到 snakemake）。"""
    return resolved_tool_path("snakemake") is not None


def uses_container_runtime() -> bool:
    """当前是否为容器模式（apptainer/podman/docker）。conda / 开发态返回 False。"""
    marker = data_dir() / "runtime"
    try:
        if marker.is_file():
            rt = marker.read_text(encoding="utf-8").strip().lower()
            return rt in _CONTAINER_RUNTIMES
    except OSError:
        pass
    return False


def write_analysis_prefix_marker(prefix: Optional[str] = None) -> str:
    """把分析环境 prefix 写入 <data_dir>/analysis_env 标记（供 finalize 阶段调用）。

    返回写入的 prefix 绝对路径。
    """
    target = os.path.expanduser(prefix or analysis_prefix())
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "analysis_env").write_text(target + "\n", encoding="utf-8")
    return target
