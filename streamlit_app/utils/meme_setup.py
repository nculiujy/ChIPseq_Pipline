"""
meme_setup.py — 可选组件「MEME suite」的检测与安装
==================================================

MEME suite 只被「6_motif」模块使用，属于可选组件，缺失不影响主流程。

装到独立 prefix ``<runtime>/meme``，**不进 analysis 环境**：bioconda 的 meme
依赖链会把 numpy 抬到 2.x，而 analysis 里的 macs2 2.2.7.1 的 Cython 扩展是按
numpy 1.x 编译的，两者同环境共存必然 `numpy.dtype size changed`。独立 prefix
的好处是装坏、重装、卸载都只影响这一个目录，主分析环境纹丝不动。

安装走后台子进程（``start_new_session`` 脱离 Streamlit 进程组），页面关掉、
UI 重启都不会中断。进度用 ``<meme_prefix>/.install_status.json`` 记录，页面
每次 rerun 读一次即可，不阻塞渲染。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from functools import lru_cache
from typing import Dict, Optional

try:  # 包内导入（streamlit 页里 from utils import meme_setup）
    from utils import env_paths
except ImportError:  # 同目录脚本式执行
    import env_paths  # type: ignore

# 判定「装好」的核心命令；meme-chip 是 perl 脚本，通常同批装上
MEME_TOOLS = ("meme", "streme", "meme-chip")

# 各工具探测版本的参数不一样：meme 只认单横线 -version（传 --version 会打 usage）
_VERSION_ARGS = {"meme": ["-version"], "streme": ["--version"], "meme-chip": ["--version"]}


def _status_path() -> str:
    return os.path.join(env_paths.meme_prefix(), ".install_status.json")


def _log_path() -> str:
    return os.path.join(env_paths.meme_prefix(), ".install.log")


def find_tool(name: str) -> Optional[str]:
    """在 tool_search_path() 中查找命令，返回绝对路径或 None。"""
    return shutil.which(name, path=env_paths.tool_search_path())


def installed_tools() -> Dict[str, str]:
    """返回已就位的 MEME 工具 {命令名: 绝对路径}。"""
    found: Dict[str, str] = {}
    for t in MEME_TOOLS:
        p = find_tool(t)
        if p:
            found[t] = p
    return found


def is_installed() -> bool:
    """meme + streme 都在才算装好（meme-chip 同批安装，不单独判）。"""
    found = installed_tools()
    return "meme" in found and "streme" in found


def tool_version(cmd: str) -> str:
    """取版本号。按工具名选参数（meme 只认 -version），取输出首行。"""
    args = _VERSION_ARGS.get(os.path.basename(cmd), ["-version"])
    try:
        r = subprocess.run(
            [cmd] + args, capture_output=True, text=True, timeout=20,
        )
        lines = (r.stdout or r.stderr or "").strip().splitlines()
        return lines[0].strip() if lines else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def _write_status(st: Dict) -> None:
    p = _status_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def read_status() -> Dict:
    """读安装状态。

    进程已退出但状态还停在 running 的，纠正为 failed —— 否则页面会永远显示
    「安装中」，用户根本不知道其实早就挂了。
    只要命令已经能用，就直接报 done，不管状态文件写了什么。
    """
    st: Dict = {"state": "absent", "pid": None, "started": None, "log": _log_path()}
    p = _status_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                st.update(json.load(f) or {})
        except (OSError, ValueError):
            pass
    if st.get("state") == "running" and st.get("pid") and not _pid_alive(st["pid"]):
        st["state"] = "failed"
        st["note"] = "安装进程已退出但未写入完成标记（可能被中断）"
        _write_status(st)
    if is_installed():
        st["state"] = "done"
    return st


def tail_log(n: int = 40) -> str:
    """安装日志末尾 n 行。"""
    try:
        with open(_log_path(), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return ""


@lru_cache(maxsize=4)
def _meme_chip_probe(path: str, _mtime: float) -> tuple:
    """真正跑一次 meme-chip，看 perl 依赖齐不齐。_mtime 只用来区分缓存。"""
    try:
        r = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"执行失败：{e}"
    out = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    # 缺 perl 模块时它只往 stderr 吐一行 "Can't locate ..."，退出码未必非零
    if "Can't locate" in out:
        first = next((l for l in out.splitlines() if "Can't locate" in l), out)
        return False, first.strip()[:300]
    return True, ""


def meme_chip_ok() -> tuple:
    """检查 meme-chip 是否**真的能跑**，返回 (ok, 原因)。

    命令存在 ≠ 能跑：meme-chip 是 perl 脚本，缺 XML::Simple 时会直接报
    `Can't locate ...` 但退出码依然可能是 0。而 6_1_motif.py 里调它是
    allow_fail=True，这类失败会被静默吞掉，只在日志末尾印一句
    “meme-chip 可能未完全成功”—— 用户拿到的却是空报告。
    所以在安装/检查阶段就把这种“看着装好了其实跑不了”的情况摆出来。
    """
    p = find_tool("meme-chip")
    if not p:
        return False, "未找到 meme-chip 命令"
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = 0.0
    return _meme_chip_probe(p, mt)


def _conda_exe() -> Optional[str]:
    for c in ("mamba", "conda"):
        p = shutil.which(c)
        if p:
            return p
    return None


def install_command() -> str:
    """给用户看的等价命令行（自动安装不可用时手动执行）。"""
    return (
        f"conda create -p {env_paths.meme_prefix()} -y "
        "--override-channels -c conda-forge -c bioconda meme"
    )


def start_install() -> tuple[bool, str]:
    """后台启动 MEME suite 安装。返回 (是否已启动, 提示信息)。"""
    if is_installed():
        return False, "MEME suite 已经装好了，无需重复安装"

    st = read_status()
    if st.get("state") == "running":
        return False, f"安装已在进行中（PID {st.get('pid')}），请稍候刷新"

    exe = _conda_exe()
    if not exe:
        return False, "未检测到 conda/mamba，无法自动安装。请先安装 Miniforge，或用页面下方的命令行手动装"

    prefix = env_paths.meme_prefix()
    # conda create -p 碰到非空目录会直接报 “prefix already exists”。上次装一半失败
    # 或手工放过东西的情况都会卡在这里。改名备份而不是 rm -rf：万一里面是用户
    # 自己放的东西，删了就找不回来了，而备份只占点磁盘。
    if os.path.isdir(prefix) and os.listdir(prefix):
        bak = f"{prefix}.bak.{int(time.time())}"
        try:
            os.rename(prefix, bak)
        except OSError as e:
            return False, f"旧目录 {prefix} 无法移开（{e}），请手动处理后重试"
    try:
        os.makedirs(prefix, exist_ok=True)
    except OSError as e:
        return False, f"无法创建目录 {prefix}：{e}"

    log = _log_path()
    cmd = [
        exe, "create", "-p", prefix, "-y",
        "--override-channels", "-c", "conda-forge", "-c", "bioconda", "meme",
    ]
    try:
        with open(log, "w", encoding="utf-8") as lf:
            lf.write("$ " + " ".join(cmd) + "\n\n")
            lf.flush()
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                env=env_paths.routed_env(),
                start_new_session=True,  # 脱离 Streamlit 进程组，UI 重启不中断安装
            )
    except OSError as e:
        return False, f"启动安装失败：{e}"

    _write_status({
        "state": "running", "pid": proc.pid,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"), "log": log,
    })
    return True, f"已开始后台安装（PID {proc.pid}），预计 5-15 分钟，可关闭页面稍后回来看"
