"""
utils.picard — Picard jar 的获取与探测
=====================================
ChIP-seq 步骤 1 的 MarkDuplicates 需要 Picard。可用来源有三处，优先级从高到低：

1. config.yaml 的 ``picard_dir`` 指向含 ``picard.jar`` 的目录 → ``java -jar``
2. 本模块下载的 jar（``<runtime>/share/picard/picard.jar``）
3. 环境里的 ``picard`` 命令（conda 装的那份，自带 openjdk）

下载走 curl 而不是 urllib：这类网络会干扰 Python 的 TLS 握手，同一份文件
实测 urllib 要 174s、curl 只要 6s，与注释下载踩过的坑同源。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .env_paths import runtime_dir

# Picard 官方只发 jar，GitHub Releases 提供长期直链；size 用于下载后粗校验。
RELEASES: dict[str, dict] = {
    "3.4.0": {
        "url": "https://github.com/broadinstitute/picard/releases/download/3.4.0/picard.jar",
        "size": 63357182,
    },
    "3.5.0": {
        "url": "https://github.com/broadinstitute/picard/releases/download/3.5.0/picard.jar",
        "size": 64763062,
    },
}

# 与 environment.yml 里 conda 装的 picard 对齐，避免同一流程里两套版本行为不一致
DEFAULT_VERSION = "3.4.0"
JAR_NAME = "picard.jar"


def vendor_dir() -> Path:
    """本项目下载 jar 的存放目录（在 runtime 下，卸载应用时一并清掉）。"""
    return Path(runtime_dir()) / "share" / "picard"


def local_jar() -> Optional[Path]:
    """本模块下载过的 jar；没有返回 None。"""
    p = vendor_dir() / JAR_NAME
    return p if p.is_file() else None


def jar_size_mb(path) -> float:
    try:
        return Path(path).stat().st_size / (1024 ** 2)
    except OSError:
        return 0.0


def has_java() -> bool:
    return shutil.which("java") is not None


def _candidate_prefixes() -> list[str]:
    """可能装着 picard 的环境前缀：分析环境 / 当前 Python 环境 / picard 命令所在环境。"""
    cands: list[str] = []
    try:
        from .env_paths import analysis_prefix

        cands.append(analysis_prefix())
    except Exception:  # noqa: BLE001
        pass
    cands.append(sys.prefix)

    try:
        from .env_paths import resolved_tool_path

        picard_bin = resolved_tool_path("picard")
    except Exception:  # noqa: BLE001
        picard_bin = None
    if picard_bin:
        try:
            cands.append(str(Path(picard_bin).resolve().parents[1]))
        except (IndexError, OSError):
            pass
    return list(dict.fromkeys(c for c in cands if c))


def scan_jars(picard_dir: str = "") -> list[dict]:
    """列出各处能找到的 picard.jar，供 UI 展示。

    返回 [{label, path, size_mb, kind}]，kind ∈ config|vendor|conda。
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(label: str, path: Path, kind: str) -> None:
        try:
            real = str(Path(path).resolve())
        except OSError:
            real = str(path)
        if real in seen or not Path(real).is_file():
            return
        seen.add(real)
        out.append({"label": label, "path": real,
                    "size_mb": jar_size_mb(real), "kind": kind})

    if picard_dir:
        add("config 指定", Path(picard_dir) / JAR_NAME, "config")

    found = local_jar()
    if found:
        add("本项目下载", found, "vendor")

    for prefix in _candidate_prefixes():
        share = Path(prefix) / "share"
        if not share.is_dir():
            continue
        for d in sorted(share.glob("picard-*")):
            add(f"环境 {Path(prefix).name}", d / JAR_NAME, "conda")
    return out


def _network_hint(url: str) -> str:
    return (
        "若长时间无进展，可手动补下：\n"
        f"  curl -L -C - -o '{vendor_dir() / (JAR_NAME + '.part')}' '{url}'\n"
        "下完把 .part 后缀去掉即可。"
    )


def download(
    version: str = DEFAULT_VERSION,
    progress_cb: Optional[Callable[[float, int, int], None]] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    timeout: int = 1800,
) -> tuple[bool, str, Optional[Path]]:
    """下载 picard.jar 到 vendor_dir()。

    ``progress_cb(fraction, downloaded, total)`` 每轮询一次调用一次；
    ``cancel_flag()`` 返回 True 时中止。返回 (成功?, 提示, jar 路径)。
    """
    info = RELEASES.get(version)
    if not info:
        return False, f"未知版本 {version}", None

    dest_dir = vendor_dir()
    dest = dest_dir / JAR_NAME
    part = dest_dir / (JAR_NAME + ".part")
    total = int(info.get("size") or 0)

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"无法创建目录 {dest_dir}：{e}", None

    # 已下完且大小对得上，不重复下载
    if dest.is_file() and (not total or dest.stat().st_size == total):
        return True, f"已有可用 jar（{jar_size_mb(dest):.1f} MB），跳过下载", dest

    curl = shutil.which("curl")
    if not curl:
        return False, "系统里没有 curl，无法下载；可安装 curl 后重试，或手动放置 jar", None

    # -C - 断点续传；--fail 让 4xx/5xx 直接非 0 退出，而不是把错误页存成 jar
    cmd = [curl, "-L", "--fail", "-sS", "-C", "-", "-o", str(part), str(info["url"])]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return False, f"启动 curl 失败：{e}", None

    deadline = time.time() + timeout
    while proc.poll() is None:
        time.sleep(0.4)
        try:
            cur = part.stat().st_size if part.exists() else 0
        except OSError:
            cur = 0
        if progress_cb and total:
            progress_cb(min(cur / total, 0.995), cur, total)
        if cancel_flag and cancel_flag():
            proc.terminate()
            return False, "已取消下载", None
        if time.time() > deadline:
            proc.terminate()
            return False, "下载超时（网络过慢，可稍后重试，已下部分会续传）", None

    stderr_txt = ""
    try:
        stderr_txt = (proc.stderr.read() if proc.stderr else "") or ""
    except Exception:  # noqa: BLE001
        pass

    if proc.returncode != 0:
        # 416：part 已经是完整的（上次下完但没来得及改名），继续走 finalize
        cur = part.stat().st_size if part.exists() else 0
        if not (total and cur == total):
            return False, (
                f"curl 下载失败（退出码 {proc.returncode}）{stderr_txt.strip()[:200]}\n"
                + _network_hint(str(info["url"]))
            ), None

    cur = part.stat().st_size if part.exists() else 0
    if total and cur != total:
        return False, f"文件大小不符：期望 {total} 字节，实际 {cur} 字节（可能未下完）", None

    try:
        if dest.exists():
            dest.unlink()
        part.replace(dest)
    except OSError as e:
        return False, f"重命名失败：{e}", None

    if progress_cb and total:
        progress_cb(1.0, total, total)
    return True, f"已下载到 {dest}（{jar_size_mb(dest):.1f} MB）", dest


def remove_local_jar() -> tuple[bool, str]:
    """删除本项目下载的 jar（保留 conda / config 指定的那份）。"""
    removed = []
    for p in (vendor_dir() / JAR_NAME, vendor_dir() / (JAR_NAME + ".part")):
        try:
            if p.exists():
                p.unlink()
                removed.append(p.name)
        except OSError as e:
            return False, f"删除 {p} 失败：{e}"
    return True, ("已删除 " + "、".join(removed)) if removed else "没有可删除的 jar"
