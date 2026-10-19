"""
环境配置页面 —— 可搬运发行包「一键配置 + 过程可视化」
=====================================================
把 workflow/env/portable-distribution.md 描述的环境配置流程搬进 UI：
  ① 安装前自检（架构 / glibc / 磁盘 / 写权限）
  ② 依赖锁定（conda-lock，可选）
  ③ 环境安装（conda env create / update）
  ④ R 包补齐（install_R_packages.R）
  ⑤ 核心工具校验（bowtie2 / samtools / macs2 …）
  ⑥ 运行时自检（txdbmaker / R 库 / picard / 版本快照）

配置在后台 subprocess 执行（复用 env_setup_manager / env_setup_worker，
与「物种注释」页同构），页面轮询 state.json 展示分阶段进度 + 实时日志，
可中途取消、可回看历史任务。长任务不阻塞 UI。

启动方式: streamlit run streamlit_app/app.py → 侧栏「环境配置」
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st

_THIS_DIR = Path(__file__).resolve().parent
_APP_DIR = _THIS_DIR.parent                       # streamlit_app/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from utils import env_setup_manager as em  # noqa: E402
from utils.ui_theme import (  # noqa: E402
    inject_theme,
    page_header,
    section_title,
    pill,
    list_row,
    code_html,
    render_html_list,
    render_sidebar,
    status_bar,
    log_console,
)

st.set_page_config(page_title="环境配置", layout="wide")
inject_theme()
render_sidebar("env")
page_header(
    "环境配置",
    "在用户目录内一键构建版本明确、可验证、离线可运行的分析环境；全程可视化、可取消、可回溯",
)

# ---------- 路径 ----------
BASE_DIR = _APP_DIR.parent                                   # 项目根
ENV_DIR = BASE_DIR / "workflow" / "env"
ENVIRONMENT_YML = BASE_DIR / "environment.yml"
INSTALL_R = ENV_DIR / "install_R_packages.R"

# 状态里的阶段顺序 & 展示标签来自 manager（单一事实源）
_STAGE_ORDER = [s for s in em.STAGES if s != "done"]

# 各状态 → pill kind
_KIND = {
    "pending": "idle",
    "running": "run",
    "done": "ok",
    "error": "err",
    "skipped": "idle",
    "cancelled": "warn",
}
_TXT = {
    "pending": "待执行",
    "running": "进行中",
    "done": "完成",
    "error": "失败",
    "skipped": "跳过",
    "cancelled": "已取消",
}


# ---------------------------------------------------------------------------
# 顶部：目标环境 & 前置依赖
# ---------------------------------------------------------------------------
def render_header() -> None:
    prereqs = em.check_prereqs()
    c1, c2, c3, c4 = st.columns([1.4, 1.1, 1.1, 1.6])
    with c1:
        st.metric("conda 环境名", st.session_state.get("env_name", "ChIPseq_Pipline"))
        st.caption(f"environment.yml：{'✅ 存在' if ENVIRONMENT_YML.is_file() else '❌ 缺失'}")
    with c2:
        st.metric("install_R_packages.R", "✅" if INSTALL_R.is_file() else "❌")
        st.caption(f"环境 bin：`{em.conda_env_bin().split('/')[-1]}`")
    with c3:
        st.metric("状态/日志目录", ".setup_tasks/")
        st.caption(str(ENV_DIR.relative_to(BASE_DIR)))
    with c4:
        have = [k for k, v in prereqs.items() if v]
        miss = [k for k, v in prereqs.items() if not v]
        st.markdown("**前置命令检测**")
        render_html_list([
            list_row(main=name, sub=val or "未找到",
                     right_html=pill("可用" if val else "缺失", "ok" if val else "warn"))
            for name, val in prereqs.items()
        ])
        if "conda" not in have:
            st.error("未检测到 conda：③ 环境安装阶段无法执行。请先在 shell 里初始化 conda。")


with st.container(border=True):
    render_header()

# ---------------------------------------------------------------------------
# 配置参数区
# ---------------------------------------------------------------------------
section_title("配置参数")
env_name = st.text_input(
    "conda 环境名",
    value=st.session_state.get("env_name", "ChIPseq_Pipline"),
    help="③ 环境安装将创建/更新此名的 conda 环境（在用户目录内，不需管理员权限）",
    key="env_name_input",
)
st.session_state["env_name"] = env_name

_c1, _c2 = st.columns([3, 1])
with _c1:
    st.markdown("**要执行的阶段**（默认跳过 ② conda-lock，需另装 conda-lock 工具）")
    selected_stages = st.multiselect(
        "选择配置阶段",
        options=_STAGE_ORDER,
        default=[s for s in em.DEFAULT_STEPS],
        format_func=lambda s: em.STAGE_LABELS.get(s, s),
        key="stage_pick",
    )
with _c2:
    st.markdown("**选项**")
    dry_run = st.checkbox("Dry-run（只打印命令，不实际执行）", value=False, key="dry_run")
    prune = st.checkbox("env update 时 --prune", value=False, key="prune",
                        help="删除 environment.yml 中已移除的包，让环境严格匹配声明")
    threads = st.number_input("线程上限（传给底层工具）", 1, 64, 4, key="threads")

if not selected_stages:
    st.warning("请至少选择一个要执行的阶段。")

# ---------------------------------------------------------------------------
# 运行控制
# ---------------------------------------------------------------------------
running_task = None
active_id = st.session_state.get("env_active_task")
if active_id:
    t = em.get_task(ENV_DIR, active_id)
    if t and t.get("status") in ("pending", "running"):
        running_task = t

_launch_disabled = running_task is not None or not selected_stages

if st.button(
    "🚀 一键启动环境配置" if not dry_run else "🧪 启动 Dry-run 预览",
    type="primary",
    disabled=_launch_disabled,
    key="launch_env",
):
    spec = em.SetupSpec(
        task_id=em.new_task_id(),
        project_root=str(BASE_DIR),
        env_dir=str(ENV_DIR),
        conda_env=env_name.strip() or "ChIPseq_Pipline",
        environment_yml=str(ENVIRONMENT_YML),
        install_r_script=str(INSTALL_R),
        steps=list(selected_stages),
        prune=prune,
        threads=int(threads),
        dry_run=dry_run,
    )
    try:
        init = em.start_task(spec)
        st.session_state["env_active_task"] = init["task_id"]
        st.success(f"已启动配置任务 `{init['task_id']}`（PID {init.get('pid')}）")
        time.sleep(0.6)
        st.rerun()
    except Exception as e:  # noqa: BLE001
        st.error(f"启动失败：{e}")

if running_task is not None:
    st.info("已有配置任务在进行中，完成或取消前不能再次启动。")

# ---------------------------------------------------------------------------
# 当前任务可视化：stepper + 进度条 + 实时日志
# ---------------------------------------------------------------------------
def render_task_view(t: dict, live: bool) -> None:
    tid = t["task_id"]
    status = t.get("status", "?")
    stage = t.get("stage")
    progress = float(t.get("progress") or 0.0)
    msg = t.get("message") or ""
    stage_status = t.get("stage_status") or {}

    kind_map = {"done": "ok", "error": "err", "cancelled": "warn", "running": "run"}
    _head = {"done": "配置完成", "error": "配置失败", "cancelled": "已取消",
             "running": "配置进行中", "pending": "排队中"}.get(status, status)
    status_bar(_head + (f" · 当前阶段 {em.STAGE_LABELS.get(stage, stage) or '-'}" if stage else ""),
               kind_map.get(status, "idle"), meta=f"task `{tid}` · {msg}")

    # 阶段列表（stepper 风格）
    rows = []
    for s in _STAGE_ORDER:
        stt = stage_status.get(s, "pending")
        icon = {"done": "✓", "error": "✗", "running": "▶", "skipped": "»"}.get(stt, "·")
        rows.append(list_row(
            main=f"{icon} {em.STAGE_LABELS.get(s, s)}",
            sub=msg if stt == "running" else None,
            right_html=pill(_TXT.get(stt, stt), _KIND.get(stt, "idle")),
        ))
    render_html_list(rows)

    st.progress(min(1.0, max(0.0, progress)), text=f"{progress * 100:.0f}% · {msg}")

    # 结果摘要（若已采集）
    result = t.get("result") or {}
    if result:
        with st.expander("🔬 自检/校验结果快照", expanded=(status == "done")):
            for stage_key, data in result.items():
                st.markdown(f"**{em.STAGE_LABELS.get(stage_key, stage_key)}**")
                if isinstance(data, dict):
                    render_html_list([list_row(main=k, sub=str(v)) for k, v in data.items()])
                else:
                    st.write(data)

    # 操作 + 实时日志
    ctrl1, ctrl2 = st.columns([1, 3])
    with ctrl1:
        if status in ("pending", "running"):
            if st.button("⏹ 取消任务", key=f"cancel_{tid}"):
                em.cancel_task(ENV_DIR, tid)
                st.toast("已请求取消", icon="")
                time.sleep(0.5)
                st.rerun()
        else:
            if st.button("清除该任务记录", key=f"clr_{tid}"):
                em.clear_task(ENV_DIR, tid)
                st.session_state.pop("env_active_task", None)
                st.rerun()
    with ctrl2:
        auto = st.toggle("实时刷新日志（每 3 秒）", value=live and status in ("pending", "running"),
                         key=f"autolog_{tid}")

    log = em.tail_log(ENV_DIR, tid, 80)
    log_console(log or "(暂无日志输出)", title=f"{tid}.log")

    if auto and status in ("pending", "running"):
        time.sleep(3)
        st.rerun()


if running_task is not None:
    section_title("配置进度")
    with st.container(border=True):
        render_task_view(running_task, live=True)
elif active_id:
    # 任务已结束但仍是当前活动任务：展示最终态
    t = em.get_task(ENV_DIR, active_id)
    if t:
        section_title("最近一次配置")
        with st.container(border=True):
            render_task_view(t, live=False)

# ---------------------------------------------------------------------------
# 历史任务
# ---------------------------------------------------------------------------
tasks = em.list_tasks(ENV_DIR)
finished = [t for t in tasks if t.get("status") not in ("pending", "running")]
if finished:
    section_title("历史配置记录")
    for t in finished[:8]:
        tid = t["task_id"]
        status = t.get("status", "?")
        started = t.get("started_at") or 0
        fin = t.get("finished_at") or time.time()
        dur = int(fin - started)
        icon = {"done": "✅", "error": "❌", "cancelled": "⏹"}.get(status, "")
        label = f"{icon} `{t.get('conda_env', '?')}` · {status} · 用时 {dur}s · {time.strftime('%m-%d %H:%M', time.localtime(started))}"
        with st.expander(label, expanded=False):
            if t.get("error"):
                st.error(f"错误：{t['error']}")
            if t.get("message"):
                st.caption(f"最后消息：{t['message']}")
            ss = t.get("stage_status") or {}
            render_html_list([
                list_row(main=em.STAGE_LABELS.get(s, s),
                         right_html=pill(_TXT.get(ss.get(s, "skipped"), ss.get(s, "")),
                                         _KIND.get(ss.get(s, "skipped"), "idle")))
                for s in _STAGE_ORDER if s in ss
            ])
            c1, c2 = st.columns([1, 3])
            if c1.button("清除", key=f"hclr_{tid}"):
                em.clear_task(ENV_DIR, tid)
                if st.session_state.get("env_active_task") == tid:
                    st.session_state.pop("env_active_task", None)
                st.rerun()
            if c2.toggle("显示日志", key=f"hlog_{tid}"):
                st.code(em.tail_log(ENV_DIR, tid, 60) or "(无日志)", language="text")
