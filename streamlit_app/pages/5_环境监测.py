"""
环境监测页面 —— 只读检测「当前环境是否满足分析要求」
===================================================
背景：在线 conda 建环境太慢、UI 里干等不划算。因此本页**不再触发任何安装**，
环境构建改由命令行完成（`bootstrap.sh` 建启动器 + `conda env create -p <分析prefix>
-f environment.yml` 建分析环境，或 `chipseq-app-update all`）；本页只负责**监测当前
运行环境**是否满足本流程分析所需：

  ① 运行模式 / prefix / Python / R / 系统（架构 · glibc · 磁盘）
  ② 核心 CLI 命令（bowtie2 / samtools / macs2 / deeptools / MEME / picard / snakemake …）
  ③ R 必需包（CRAN + Bioconductor）；txdbmaker 等版本相关包列为可选（缺失不算不满足）
  ④ 已装注释数据库（TxDb.* / org.*.db，按物种列出）

每项给出「是否就位 + 版本」，并汇总缺失清单与修复指引。检测走同步但很快
（工具用 which、R 用单次 --vanilla 探测），结果缓存、点「重新检测」再刷新。

启动方式: streamlit run streamlit_app/app.py → 侧栏「环境监测」
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_THIS_DIR = Path(__file__).resolve().parent
_APP_DIR = _THIS_DIR.parent                       # streamlit_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from utils import env_check  # noqa: E402
from utils.ui_theme import (  # noqa: E402
    inject_theme,
    page_header,
    section_title,
    pill,
    list_row,
    render_html_list,
    render_sidebar,
    status_bar,
)

from utils.version import APP_FULL_NAME  # noqa: E402

st.set_page_config(page_title=f"环境监测 · {APP_FULL_NAME}", layout="wide")
inject_theme()
render_sidebar("env")
page_header(
    "环境监测",
    "只读检测当前环境是否满足分析要求（不安装任何东西）；构建环境请用命令行 conda env create / chipseq-app-update all",
)


# ---------------------------------------------------------------------------
# 检测（缓存 + 手动刷新）
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="正在检测当前环境……")
def _run_check(nonce: int) -> dict:
    # nonce 仅用于强制失效缓存；与内容无关
    return env_check.check_environment()


if "env_check_nonce" not in st.session_state:
    st.session_state["env_check_nonce"] = 0

_bar1, _bar2 = st.columns([1, 4])
with _bar1:
    if st.button("🔄 重新检测", use_container_width=True, key="recheck"):
        st.session_state["env_check_nonce"] += 1
        _run_check.clear()
        st.rerun()
with _bar2:
    st.caption("环境若有改动（新装工具 / R 包），点「重新检测」刷新；本页只读，不会触发安装。")

report = _run_check(st.session_state["env_check_nonce"])
meta = report["meta"]
r = report["r"]

# ---------------------------------------------------------------------------
# 顶部：总体结论 + 关键信息
# ---------------------------------------------------------------------------
n_tools_missing = len(report["missing_tools"])
n_r_missing = len(report["missing_r"])
if report["ok"]:
    status_bar("✅ 当前环境满足分析要求：核心命令与 R 包均已就位", "ok",
               meta=f"runtime={meta['runtime']} · R {r.get('r_version') or '-'} · Python {meta['python']}")
else:
    parts = []
    if n_tools_missing:
        parts.append(f"缺 {n_tools_missing} 个命令")
    if not r["available"]:
        parts.append("未找到 Rscript")
    elif n_r_missing:
        parts.append(f"缺 {n_r_missing} 个 R 包")
    status_bar("⚠ 当前环境尚不满足分析要求：" + "、".join(parts), "err",
               meta=f"runtime={meta['runtime']} · Python {meta['python']}")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("运行模式", meta["runtime"])
c2.metric("分析 prefix", Path(meta["analysis_prefix"]).name,
          help=meta["analysis_prefix"])
c3.metric("prefix/bin", "已建" if meta["prefix_exists"] else "未建")
c4.metric("Python", meta["python"])
c5.metric("R", r.get("r_version") or "未找到")
sysi = report["system"]
c6.metric("磁盘可用", f"{sysi.get('disk_free_gb') or '?'} GB")
st.caption(f"架构 {sysi.get('arch')} · {sysi.get('sysname')} · glibc {sysi.get('glibc')} "
           f"· sys.prefix `{meta['sys_prefix']}`")
if meta["container"]:
    st.info("当前为**容器模式**（apptainer/podman/docker）：工具由镜像提供，本页检测的是镜像内 PATH 可见的命令与 R 包。")


def _tool_sub(present: bool, version: str, path: str, note: str) -> str:
    """命令的副标题：版本 / 跑不起来的原因 / 未找到，末尾带上解析到的路径。"""
    if version:
        s = f"v{version}"
    elif present and note:
        s = f"跑不起来 · {note}"
    elif present:
        s = "已装（版本未知）"
    else:
        s = "未找到"
    return s + (f" · {path}" if path else "")


# ---------------------------------------------------------------------------
# ① 核心 CLI 命令
# ---------------------------------------------------------------------------
section_title("核心命令检测")
_groups: dict[str, list] = {}
for row in report["tools"]:
    _groups.setdefault(row["group"], []).append(row)
for gname, rows in _groups.items():
    _opt = any(x.get("optional") for x in rows)
    st.markdown(f"**{gname}**" + ("（可选，未装不影响主流程）" if _opt else ""))
    render_html_list([
        list_row(
            main=name,
            sub=_tool_sub(present, version, path, note),
            right_html=(pill("就位", "ok") if present and not note
                        else pill("跑不起来", "warn") if present
                        else pill("可选·未装", "idle") if optional
                        else pill("缺失", "err")),
        )
        for name, present, version, path, note, optional in (
            (x["name"], x["present"], x["version"], x["path"],
             x.get("note", ""), x.get("optional"))
            for x in rows
        )
    ])

# ---------------------------------------------------------------------------
# ② R 必需包
# ---------------------------------------------------------------------------
section_title("R 包检测")
if not r["available"]:
    st.error("未检测到 Rscript：无法检测 R 包。请确认分析环境已就绪或已加载 R。")
else:
    render_html_list([
        list_row(
            main=p["name"] + ("（可选）" if p.get("optional") else ""),
            sub=(f"v{p['version']}" if p["present"]
                 else (p.get("note") or "未安装")),
            right_html=(pill("就位", "ok") if p["present"]
                        else pill("可选·未装", "idle") if p.get("optional")
                        else pill("缺失", "err")),
        )
        for p in r["packages"]
    ])
    if r.get("annotation"):
        st.markdown("**已装注释数据库（TxDb / OrgDb，按物种）**")
        render_html_list([
            list_row(main=a["name"], sub=f"v{a['version']}" if a["version"] else "",
                     right_html=pill("已装", "ok"))
            for a in r["annotation"]
        ])
    else:
        st.caption("未检测到任何 TxDb.* / org.*.db 注释包（做 peak 注释/GSEA 前需按物种安装）。")

# ---------------------------------------------------------------------------
# ③ 缺失汇总 + 修复指引
# ---------------------------------------------------------------------------
if not report["ok"]:
    section_title("待补齐项与指引")
    _ap = meta["analysis_prefix"]
    if n_tools_missing:
        st.warning("缺失命令：" + ", ".join(report["missing_tools"]))
    if report.get("broken_tools"):
        st.warning(
            "命令找到了但跑不起来：" + ", ".join(report["broken_tools"])
            + "\n\n常见原因是 Java 版本过低——Picard 3.x 需要 Java 17+，"
            "而系统 JAVA_HOME 指向 JDK 8/11 时会盖掉 conda 自带的 JVM。"
            "升级 JDK 或让 JAVA_HOME 指向分析环境的 lib/jvm。"
        )
    if r["available"] and n_r_missing:
        st.warning("缺失 R 包：" + ", ".join(report["missing_r"]))
    st.markdown(
        "**修复方式（命令行一次性完成，不在本页执行）：**\n\n"
        "1. 构建/补齐分析环境（补齐缺失命令；conda 模式的 bootstrap 只建启动器、不建此环境）：\n"
        f"   `conda env create -p {_ap} -f environment.yml`（已存在则用 `conda env update -p {_ap} -f environment.yml`；或 `chipseq-app-update all` 重建）\n"
        "2. 仅补 R 包：\n"
        "   `Rscript workflow/env/install_R_packages.R`\n"
        "3. 单个 R 包：\n"
        "   `Rscript -e 'BiocManager::install(\"包名\", ask=FALSE)'`（CRAN 包用 `install.packages(\"包名\")`）\n\n"
        "完成后回到本页点「重新检测」。"
    )
else:
    st.success("所有核心命令与 R 包均已就位，可直接在「运行管理」页启动分析流程。")
    if report.get("optional_tools_missing"):
        st.info(
            "可选工具未安装：" + ", ".join(report["optional_tools_missing"]) + "\n\n"
            "它们只服务于 motif 分析（6_motif 模块），未启用该模块时不影响流程。需要时单独安装：\n\n"
            f"`mamba install -p {meta['analysis_prefix']} -y -c bioconda meme`\n\n"
            "注意：MEME suite 把 perl 钉在 5.32.1，与 r-base 4.6 冲突，可能连带降级 r-base，"
            "建议确定要做 motif 后再装。"
        )
