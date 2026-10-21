"""
ChIPseq Pipeline 总览台（首页）
================================
首屏固定三层：
  顶层：当前项目 · 配置状态 · 运行状态（hero 面板；主要操作由侧栏承担，不重复放置）
  中层：管线阶段进度表（唯一高视觉权重区块，进度口径=目标文件）
  底层：当前项目 / 最近运行 / 最近日志（辅助区，视觉降权），
        及「现有项目」排序列表：第一位即当前项目，↑↓ 调序并写回 config.yaml
进度口径拆分为「已启用模块完成 / 模块已跳过 / 目标文件完成」，
避免“全流程完成”与“启用模块完成”混用。数据全部来自 utils.runtime。

启动方式: streamlit run streamlit_app/app.py
"""

import os
import sys
import socket
from datetime import datetime

import streamlit as st

# 让 streamlit_app/utils 可导入
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from utils.ui_theme import (  # noqa: E402
    inject_theme,
    page_header,
    section_title,
    render_sidebar,
    status_bar,
    pipeline_table,
    metrics,
    pill,
    list_row,
    code_html,
    render_html_list,
)
from utils import runtime as rt  # noqa: E402
from utils.version import APP_FULL_NAME  # noqa: E402

st.set_page_config(
    page_title=APP_FULL_NAME,
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()

# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
config = rt.load_config()
projects = rt.get_projects(config)
running = rt.is_snakemake_running()
rsum = rt.running_summary(config) if running else None
primary = projects[0] if projects else None
now = datetime.now().strftime("%H:%M:%S")

issues = rt.config_issues(config)
n_err = sum(1 for i in issues if i["level"] == "error")
n_warn = sum(1 for i in issues if i["level"] == "warn")

try:
    _cfg_mtime = datetime.fromtimestamp(
        os.path.getmtime(os.path.join(_HERE, "..", "config", "config.yaml"))
    ).strftime("%Y-%m-%d %H:%M")
except Exception:  # noqa: BLE001
    _cfg_mtime = "未知"

# 侧栏品牌区 + 分组导航（选中态 = 总览）
_run_text = f"运行中 · {rsum['stage']}" if (running and rsum) else ("运行中" if running else "空闲")
try:
    _foot = f"{os.environ.get('USER') or 'user'} @ {socket.gethostname()}"
except Exception:  # noqa: BLE001
    _foot = None
render_sidebar("home", running=_run_text, kind="run" if running else "idle", footer=_foot)

page_header(f"{APP_FULL_NAME} 总览", "当前项目是否可运行、进行到哪一步、下一步做什么")

# ---------------------------------------------------------------------------
# 全局状态条（长时间任务的全局反馈，避免重复点击运行）
# ---------------------------------------------------------------------------
if running and rsum:
    _txt = f"运行中 · {rsum['stage'] or '进行中'}"
    if rsum.get("elapsed_text"):
        _txt += f" · 已运行 {rsum['elapsed_text']}"
    if rsum.get("pid"):
        _txt += f" · PID {rsum['pid']}"
    status_bar(_txt, "run", meta="查看日志 / 管理运行 → 运行管理")
elif running:
    status_bar("运行中 · 检测到 snakemake 进程", "run", meta="进入运行管理查看进度")
else:
    status_bar("空闲 · 当前没有正在运行的任务", "idle", meta=f"状态同步于 {now}")

if not config:
    st.warning("未找到 config.yaml，请检查路径配置。")
elif not projects:
    st.info("config.yaml 中暂无项目，先到「配置编辑」添加。")

# ---------------------------------------------------------------------------
# 第一层（首屏主线）：当前项目 · 配置状态 · 运行状态
# ---------------------------------------------------------------------------
ps = rt.progress_summary(primary) if primary else None

with st.container(border=True):
    h_proj, h_run = st.columns([2.6, 1.6], gap="medium")
    with h_proj:
        if primary:
            mods = primary.get("modules", {}) or {}
            enabled_readable = "、".join(
                f"{rt.MODULE_LABELS.get(m, m)}（{m}）" for m in rt.MODULE_ORDER if mods.get(m)
            ) or "无启用模块"
            st.markdown(
                f"##### 当前项目 · {primary['species']} / {primary['experiment']}",
            )
            render_html_list([
                list_row(main="配置状态",
                         sub_html=(f'{code_html(f"config/config.yaml")} · 修改于 {_cfg_mtime} · '
                                   + (f"🔴 {n_err} 错误 / 🟡 {n_warn} 警告" if n_err
                                      else (f"🟡 {n_warn} 警告" if n_warn else "✅ 校验通过")))),
                list_row(main="启用模块", sub=enabled_readable),
            ])
        else:
            st.markdown("##### 暂无项目")
    with h_run:
        if running and rsum:
            st.markdown("**运行状态** " + pill("运行中", "run"), unsafe_allow_html=True)
            render_html_list([
                list_row(main=rsum["stage"] or "进行中", sub="当前阶段"),
                list_row(main=str(rsum.get("pid") or "-"), sub="主进程 PID"),
                list_row(main=rsum.get("elapsed_text") or "-", sub="已运行"),
            ])
        else:
            st.markdown("**运行状态** " + pill("空闲", "idle"), unsafe_allow_html=True)
            st.caption("没有正在运行的 Snakemake 任务，可以安全启动新管线。")

# ---------------------------------------------------------------------------
# 关键指标：进度口径拆分（模块完成 / 跳过 / 目标文件 / 磁盘）
# ---------------------------------------------------------------------------
if ps:
    _disk = rt.disk_usage()
    metrics([
        {"value": f"{ps['done_modules']}/{ps['enabled_modules']}", "label": "已启用模块完成",
         "kind": "ok" if ps["done_modules"] == ps["enabled_modules"] and ps["enabled_modules"] else "warn",
         "icon": "✓" if ps["done_modules"] == ps["enabled_modules"] and ps["enabled_modules"] else "!"},
        {"value": str(ps["skipped_modules"]), "unit": "个", "label": "模块已跳过（未启用）", "kind": None},
        {"value": f"{ps['targets_done']}/{ps['targets_total']}", "label": "目标文件完成",
         "kind": "accent"},
        {"value": f"{_disk['free_gb']:.0f}" if _disk else "?", "unit": "GB",
         "label": f"磁盘可用 · 已用 {(_disk['used_frac'] * 100):.0f}%" if _disk else "磁盘可用",
         "bar_frac": _disk["used_frac"] if _disk else None},
    ])
    st.caption(
        "口径说明：「模块完成」按该模块全部 marker 文件计；进度百分比按目标文件计；"
        "「已跳过」= 配置中未启用的顶层模块，不参与运行。"
    )

# ---------------------------------------------------------------------------
# 第二层（核心区块）：管线阶段进度
# ---------------------------------------------------------------------------
if primary and ps:
    stages = rt.classify_stages(
        primary["species"], primary["experiment"], primary.get("modules", {}), running, config
    )
    frac = ps["targets_frac"]
    section_title(f"分析管线进度 · {primary['species']} / {primary['experiment']}")
    with st.container(border=True):
        pipeline_table(stages)
        st.caption("「阶段间隔」= 与上一完成阶段的时间差（近似，非纯计算耗时）；内部阶段 ID 以等宽标签展示")
    st.progress(
        frac,
        text=(f"管线进度（按目标文件）· {ps['targets_done']}/{ps['targets_total']} · {int(frac * 100)}%"
              + (f" · 待完成模块：{'、'.join(ps['pending_modules'])}" if ps["pending_modules"] else "")),
    )

# ---------------------------------------------------------------------------
# 第三层（辅助区，降权）：当前项目 | 最近运行 | 最近日志
# ---------------------------------------------------------------------------
st.markdown("###### 辅助信息 · 资源与最近活动")
c_res, c_recent, c_act = st.columns([1.1, 1.0, 1.0], gap="medium")

with c_res:
    with st.container(border=False):
        if primary:
            _size = rt.project_size(primary["species"], primary["experiment"])
            _nfiles = rt.project_outputs_count(primary["species"], primary["experiment"])
            _dur = rt.last_run_duration(primary["species"], primary["experiment"])
            _fail = rt.last_failed_stage(
                primary["species"], primary["experiment"], primary.get("modules", {})
            )
            render_html_list([
                list_row(main="当前项目", sub_html=code_html(f"result/{primary['species']}/{primary['experiment']}")),
                list_row(main=_size, sub="当前项目占用"),
                list_row(main=(f"{_nfiles} 个" if _nfiles is not None else "?"), sub="输出文件数量"),
                list_row(main=_dur or "—", sub="最近一次阶段耗时（近似）"),
                list_row(main=_fail or "无", sub="最近一次失败阶段"),
            ])
        else:
            st.caption("暂无项目")

with c_recent:
    runs = rt.recent_runs(limit=5)
    render_html_list(
        [list_row(main="最近运行", sub="暂无运行记录")]
        if not runs
        else [
            list_row(
                main=r["project"],
                sub=f"最后活动 {datetime.fromtimestamp(r['last_run']).strftime('%Y-%m-%d %H:%M')}",
            )
            for r in runs
        ]
    )
    if runs:
        st.markdown("[查看各项目日志 →](/运行管理)")

with c_act:
    logs = rt.list_recent_logs(limit=4)
    render_html_list(
        [list_row(main="最近日志", sub="暂无日志文件")]
        if not logs
        else [
            list_row(main=f"{lg['mtime']} · {lg['size_kb']:.0f} KB",
                     sub_html=code_html(lg["name"]))
            for lg in logs
        ]
    )
    if logs:
        st.markdown("[打开日志查看 →](/运行管理)")
