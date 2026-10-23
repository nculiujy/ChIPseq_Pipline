"""
env_check.py — 当前环境「分析要求」监测（只读、快速、无安装）
===========================================================

用途：浏览器「环境监测」页调用本模块，检测**当前运行环境**是否满足本流程
分析所需的命令与 R 包，逐项给出「是否就位 + 版本」，并汇总缺失清单。
本模块**不触发任何 conda/R 安装**——环境构建已交回命令行 `bootstrap.sh`。

判定基线（与真实调用对齐，非拍脑袋）：
  - CLI 命令取自 workflow/rules 与 workflow/scripts 实际调用的外部程序；
  - R 包取自 workflow/env/install_R_packages.R + 各 .R 脚本 library()。

工具解析走 env_paths.resolved_tool_path()：优先分析 prefix/bin，再当前
python 环境 bin，最后系统 PATH——即「UI 进程实际能用到的环境」，与真实跑
流程时的 PATH 视图一致。R 用 --vanilla 探测（避开 renv 启动横幅污染取值）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

try:  # 包内导入（streamlit 页里 from utils import env_check）
    from utils import env_paths
except ImportError:  # 同目录脚本式执行
    import env_paths  # type: ignore

# ---------- 分析要求基线 ----------
# (分组, [(命令名, 版本参数), ...])
REQUIRED_TOOLS: List[tuple[str, List[tuple[str, List[str]]]]] = [
    ("比对 & QC", [
        ("bowtie2", ["--version"]),
        ("bowtie2-build", ["--version"]),
        ("samtools", ["--version"]),
        ("fastqc", ["--version"]),
        ("trim_galore", ["--version"]),
    ]),
    ("Peak & bed 操作", [
        ("macs2", ["--version"]),
        ("bedtools", ["--version"]),
    ]),
    ("deeptools", [
        ("bamCoverage", ["--version"]),
        ("computeMatrix", ["--version"]),
        ("plotProfile", ["--version"]),
        ("plotCorrelation", ["--version"]),
    ]),
    ("motif (MEME suite)", [
        ("meme", ["-version"]),      # MEME 用单横线 -version；--version 不支持会打 usage
        ("streme", ["--version"]),
    ]),
    ("流程编排 / 其它", [
        ("snakemake", ["--version"]),
        ("picard", ["--version"]),
    ]),
]

# 可选工具组：缺失**不**判「不满足」（与 OPTIONAL_R_BIOCG 同一套口径）。
# MEME suite 的 meme 包把 perl 钉在 5.32.1，与 conda-forge 的 r-base 4.6 冲突，
# 装它会连累 r-base 及约 10 个包降级，故不放进 environment.yml，按需单独装；
# 只有启用 6_motif 模块时才需要。
OPTIONAL_TOOL_GROUPS = {"motif (MEME suite)"}

# R 必需包（CRAN）
REQUIRED_R_CRAN: List[str] = [
    "optparse", "yaml", "RColorBrewer", "ggplot2", "dplyr",
    "pheatmap", "ggrepel", "readr", "stringr",
]
# R 必需包（Bioconductor）
REQUIRED_R_BIOCG: List[str] = [
    "ChIPseeker", "rtracklayer", "GenomicFeatures",
    "GenomicRanges", "clusterProfiler",
]
# 可选 R 包：缺失**不**判「不满足」（(包名, 说明)）。
# txdbmaker 仅在 Bioconductor ≥3.21(R 4.5) 才存在且必需——那时 makeTxDbFromGFF
# 从 GenomicFeatures 拆分到 txdbmaker；旧版(GenomicFeatures ≤1.5x / Bioc 3.20)
# 仍自带该函数。4_2_annoChIPPeaks.R 已做 requireNamespace 回退，故低版本不装也算满足。
OPTIONAL_R_BIOCG: List[tuple[str, str]] = [
    ("txdbmaker",
     "仅 Bioconductor ≥3.21(R 4.5) 需要；旧版 GenomicFeatures 自带 makeTxDbFromGFF，"
     "脚本有回退，本环境可不装"),
]

_VER_RE = re.compile(r"(\d+(?:\.\d+)+)")

# class file version 与 Java 大版本相差 44：52 → Java 8，61 → Java 17
_CLASS_TO_JAVA = 44


def _probe_version(exe: str, args: List[str], timeout: int = 12) -> tuple[str, str]:
    """运行 `<exe> <args>`，返回 (版本号, 跑不起来的原因)。

    版本号只扫描前几行非空输出：支持 --version/-version 的工具版本一般都在首行；
    不支持的工具会打整页 usage（里面全是无关数字），限定行数可避免误抓。

    第二个返回值只在命令**找到了却没起来**时才给——最典型的是 java 版本不够，
    picard 直接抛 UnsupportedClassVersionError。这种时候 UI 不能笼统写“已装
    （版本未知）”，那会让人以为只是没取到版本号。
    """
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    out = (r.stdout or "") + "\n" + (r.stderr or "")

    # java 类加载失败时，堆栈里也带着 "class file version 61.0" 这种数字，
    # 会被下面的版本正则误当成工具版本号，所以先认错再抓版本。
    if "UnsupportedClassVersionError" in out or "LinkageError" in out:
        need = re.search(r"class file version (\d+)", out)
        cur = re.search(r"recognizes class file versions up to (\d+)", out)
        note = "java 版本过低"
        if need:
            note += f"（需要 Java {int(need.group(1)) - _CLASS_TO_JAVA}）"
        if cur:
            note += f"，当前只支持到 Java {int(cur.group(1)) - _CLASS_TO_JAVA}"
        return "", note

    ver = ""
    scanned = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        m = _VER_RE.search(line)
        if m:
            ver = m.group(1)
            break
        scanned += 1
        if scanned >= 3:          # 前 3 行非空仍无版本号 → 判为“版本未知”
            break
    return ver, ""


def check_tools() -> List[Dict]:
    """逐个解析必需 CLI 命令，返回 [{group,name,present,path,version}]（保持分组顺序）。"""
    rows: List[Dict] = []
    for group, items in REQUIRED_TOOLS:
        for name, vargs in items:
            path = env_paths.resolved_tool_path(name)
            present = path is not None
            version, note = _probe_version(path, vargs) if present else ("", "")
            rows.append({
                "group": group, "name": name, "present": present,
                "path": path, "version": version, "note": note,
                "optional": group in OPTIONAL_TOOL_GROUPS,
            })
    return rows


def _rscript() -> Optional[str]:
    return env_paths.resolved_tool_path("Rscript")


def check_r() -> Dict:
    """一次 Rscript --vanilla 探测 R 版本 + 全部必需包，避免反复启动 R。"""
    result: Dict = {"available": False, "rscript": None, "r_version": "",
                    "packages": [], "annotation": []}
    rs = _rscript()
    if not rs:
        return result
    result["available"] = True
    result["rscript"] = rs

    req = REQUIRED_R_CRAN + REQUIRED_R_BIOCG
    opt_names = [n for n, _ in OPTIONAL_R_BIOCG]
    opt_note = dict(OPTIONAL_R_BIOCG)
    probe = req + opt_names
    try:
        r = subprocess.run([rs, "--vanilla", "-e", _r_probe_expr(probe)],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        result["error"] = f"Rscript 探测失败：{e}"
        return result
    out = (r.stdout or "")
    lines = [ln for ln in out.splitlines() if "\t" in ln]
    in_anno = False
    pkg_map: Dict[str, str] = {}
    anno_map: Dict[str, str] = {}
    for ln in lines:
        name, _, ver = ln.partition("\t")
        name = name.strip()
        ver = ver.strip()
        if name == "RVER":
            result["r_version"] = ver
        elif name == "ANNOBEGIN":
            in_anno = True
        elif in_anno:
            anno_map[name] = ver
        else:
            pkg_map[name] = ver

    for p in probe:
        ver = pkg_map.get(p, "")
        item = {"name": p, "present": bool(ver), "version": ver,
                "optional": p in opt_names}
        if p in opt_names:
            item["note"] = opt_note.get(p, "")
        result["packages"].append(item)
    for a in sorted(anno_map):
        result["annotation"].append({"name": a, "present": bool(anno_map[a]),
                                     "version": anno_map[a]})
    return result


def _r_probe_expr(req: List[str]) -> str:
    """生成 R 探测脚本：先必需包，再注释库（用 ANNOBEGIN 分隔），Tab 分隔名/版本。"""
    pkgs_r = ", ".join(f'"{p}"' for p in req)
    return (
        'ver <- function(p){ v <- tryCatch(as.character(packageVersion(p)), '
        'error=function(e) ""); cat(p, "\\t", if (nzchar(v)) v else "", "\\n", sep="") }; '
        f'cat("RVER\\t", paste0(R.version$major, ".", R.version$minor), "\\n", sep=""); '
        f'for (p in c({pkgs_r})) ver(p); '
        'cat("ANNOBEGIN\\t\\n", sep=""); '
        'ip <- rownames(installed.packages()); '
        'anno <- grep("^(TxDb\\\\.|org\\\\..+\\\\.db$)", ip, value=TRUE); '
        'for (a in anno) ver(a)'
    )


def check_system() -> Dict:
    """系统级前检（架构/glibc/磁盘），与流程可运行性相关的硬性条件。"""
    info: Dict = {}
    un = os.uname()
    info["arch"] = un.machine
    info["sysname"] = un.sysname
    try:
        r = subprocess.run(["getconf", "GNU_LIBC_VERSION"],
                           capture_output=True, text=True, timeout=10)
        info["glibc"] = (r.stdout or "").strip() or "未知"
    except (OSError, subprocess.SubprocessError):
        info["glibc"] = "未知"
    probe = os.path.expanduser("~")
    try:
        s = os.statvfs(probe)
        info["disk_free_gb"] = round(s.f_bavail * s.f_frsize / (1024 ** 3), 1)
    except OSError:
        info["disk_free_gb"] = None
    return info


def check_environment() -> Dict:
    """汇总一次完整监测。返回结构化报告，供页面渲染 + 顶层结论。"""
    prefix = env_paths.analysis_prefix()
    container = env_paths.uses_container_runtime()
    tools = check_tools()
    r = check_r()

    missing_tools = [t["name"] for t in tools
                     if not t["present"] and not t.get("optional")]
    optional_tools_missing = [t["name"] for t in tools
                              if not t["present"] and t.get("optional")]
    # 找到了但跑不起来（典型：java 版本不够，picard 起不来）与“没装”同等致命，
    # 只按 present 判的话流程会在第 3 步才炸，且报错跟依赖完全对不上。
    broken_tools = [t["name"] for t in tools
                    if t["present"] and t.get("note") and not t.get("optional")]
    missing_r = [p["name"] for p in r["packages"]
                 if not p["present"] and not p.get("optional")]

    return {
        "checked_at": time.time(),
        "meta": {
            "runtime": "container" if container else "conda",
            "container": container,
            "analysis_prefix": prefix,
            "prefix_exists": os.path.isdir(os.path.join(prefix, "bin")),
            "sys_prefix": sys.prefix,
            "python": sys.version.split()[0],
        },
        "system": check_system(),
        "tools": tools,
        "r": r,
        "missing_tools": missing_tools,
        "broken_tools": broken_tools,
        "optional_tools_missing": optional_tools_missing,
        "missing_r": missing_r,
        "ok": not missing_tools and not broken_tools and r["available"] and not missing_r,
    }
