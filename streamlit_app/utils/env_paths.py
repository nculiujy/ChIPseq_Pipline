"""
env_paths.py — 统一的"分析工具定向"层（launcher / analysis 双 prefix 解耦）
=========================================================================

目录约定（C 方案：项目内封装）：
  <repo>/.chipseq-runtime/          CHIPSEQ_RUNTIME_DIR
    ├ launch/                       CHIPSEQ_LAUNCH_ENV   启动器 prefix env
    ├ analysis/                     CHIPSEQ_CONDA_ENV    分析  prefix env
    ├ data/                         CHIPSEQ_DATA         用户数据根
    ├ pkgs/  cache/                隔离的 conda 包缓存 / pip 缓存 + install.packages 临时目录
    ├ analysis/lib/R/library/      CHIPSEQ_R_LIB      R 包库（与 workflow 中 CONDA_PREFIX/lib/R/library 同步）
    └ bin/ lib/ runtime/ assets/    CLI 实体

标记文件：
  <data_dir>/analysis_env   单行绝对路径（runtime_install / _run 时会写）
  <data_dir>/runtime        apptainer|podman|docker|conda
  <data_dir>/repo_path      仓库目录（向后兼容旧安装）

开发态直接 streamlit run 时（没跑 bootstrap），本模块从 __file__ 上溯三级得到
仓库目录，默认指向 <repo>/.chipseq-runtime/*。如果 prefix 不存在，分析工具搜索
会回落到 sys.prefix/bin（即当前 Streamlit 运行环境），保持开发可用。

旧版本将 env 落在 ~/.local/chipseq-{env,launch} 与 ~/.chipseq-pipeline；本模块
提供 fallback：新位置 marker 不存在时尝试读旧位置，避免旧安装 UI 直接坏掉。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# ---------- 项目内默认路径派生 ----------
# env_paths.py 位于 <repo>/streamlit_app/utils/，parents[2] 即仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_DIR_DEFAULT = Path(
    os.environ.get("CHIPSEQ_RUNTIME_DIR") or (_REPO_ROOT / ".chipseq-runtime")
)
_DEFAULT_ANALYSIS_PREFIX = _RUNTIME_DIR_DEFAULT / "analysis"
_DEFAULT_LAUNCH_PREFIX = _RUNTIME_DIR_DEFAULT / "launch"
_DEFAULT_DATA_DIR = _RUNTIME_DIR_DEFAULT / "data"

# 旧版 HOME 侧位置（仅作 fallback，存在 marker 文件才用）
_LEGACY_DATA_DIR = Path(os.path.expanduser("~/.chipseq-pipeline"))
_LEGACY_ANALYSIS_PREFIX = Path(os.path.expanduser("~/.local/chipseq-env"))
_LEGACY_LAUNCH_PREFIX = Path(os.path.expanduser("~/.local/chipseq-launch"))

_CONTAINER_RUNTIMES = ("apptainer", "podman", "docker")


def repo_root() -> Path:
    """仓库目录（env_paths.py 上溯三级）。"""
    return _REPO_ROOT


def runtime_dir() -> Path:
    """项目内运行时目录（.chipseq-runtime）。"""
    return _RUNTIME_DIR_DEFAULT


def data_dir() -> Path:
    """数据目录：$CHIPSEQ_DATA → <runtime>/data → 旧版 ~/.chipseq-pipeline（存在则用）。"""
    env = os.environ.get("CHIPSEQ_DATA")
    if env:
        return Path(os.path.expanduser(env))
    if _DEFAULT_DATA_DIR.is_dir():
        return _DEFAULT_DATA_DIR
    # 向后兼容：旧位置存在 marker 文件则沿用
    if (_LEGACY_DATA_DIR / "runtime").is_file() or (_LEGACY_DATA_DIR / "repo_path").is_file():
        return _LEGACY_DATA_DIR
    return _DEFAULT_DATA_DIR


def analysis_prefix() -> str:
    """分析环境 prefix（绝对路径字符串）。

    优先读 <data_dir>/analysis_env marker；缺省回落到 CHIPSEQ_CONDA_ENV → 项目默认。
    """
    marker = data_dir() / "analysis_env"
    try:
        if marker.is_file():
            txt = marker.read_text(encoding="utf-8").strip()
            if txt:
                return os.path.expanduser(txt)
    except OSError:
        pass
    env = os.environ.get("CHIPSEQ_CONDA_ENV")
    if env:
        return os.path.expanduser(env)
    # 向后兼容：旧位置存在则用，否则用项目内默认
    if _LEGACY_ANALYSIS_PREFIX.is_dir():
        return str(_LEGACY_ANALYSIS_PREFIX)
    return str(_DEFAULT_ANALYSIS_PREFIX)


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
