"""
utils.ui_theme — 控制台 / CI-CD 风设计系统
==========================================
ChIPseq Pipeline 管理面板的统一视觉层。设计语言参考 GitHub Actions /
GitLab Pipeline / Linear / Vercel：中性灰底、白色面板、细边框、克制的
teal 强调色、清晰排版层级、等宽日志面板。刻意「去 AI 味」：无 emoji
图标、无彩虹渐变、无厚重黏土阴影。

用法（在 app.py 和每个页面 set_page_config 之后各调用一次）：

    from utils.ui_theme import inject_theme, page_header, stat_strip, stage_rail
    inject_theme()
    page_header("运行管理", "监控流程状态、触发运行、查看日志")

纯视觉层：只改样式与页面骨架，不碰任何业务逻辑。
"""

from __future__ import annotations

import html as _html

import streamlit as st

from utils.version import APP_NAME, APP_VERSION

# ---------------------------------------------------------------------------
# 设计令牌 —— 与 .streamlit/config.toml 的 [theme] 保持一致
# ---------------------------------------------------------------------------
THEME_CSS = """
/* 故意不用外网字体 @import：CSS 中的 @import 是阻塞渲染的。客户端连不上
   fonts.googleapis.com（国内 / 受限网络常见，TCP 会长时间挂起而非快速失败）时，
   排在它后面的整张主题 <style> 迟迟不生效 → 页面长时间一片空白（DOM 有内容但不显）。
   --ux-sans / --ux-mono 已带系统字体回退，去掉 @import 后自动用本地字体栈，视觉几乎一致
   且不再依赖外网。若确需 Inter/JetBrains Mono，请 self-host 到 static/ 再用 <link> 异步加载。 */

:root {
  --ux-bg: #F6F7F9;
  --ux-surface: #FFFFFF;
  --ux-surface-2: #FAFBFC;
  --ux-border: #E4E7EC;
  --ux-border-strong: #D0D5DD;
  --ux-text: #1A1D21;
  --ux-muted: #5B6470;
  --ux-subtle: #8A94A6;
  --ux-accent: #0D9488;
  --ux-accent-strong: #0B7C72;
  --ux-accent-soft: #E6F4F2;
  --ux-ok: #12855F;      --ux-ok-soft: #E7F6EF;
  --ux-run: #2563EB;     --ux-run-soft: #EAF1FE;
  --ux-warn: #B45309;    --ux-warn-soft: #FDF3E7;
  --ux-err: #C0392B;     --ux-err-soft: #FBEAE8;
  --ux-idle: #98A2B3;    --ux-idle-soft: #F2F4F7;
  --ux-radius: 10px;
  --ux-radius-sm: 8px;
  --ux-shadow-sm: 0 1px 2px rgba(16,24,40,.05);
  --ux-shadow: 0 1px 3px rgba(16,24,40,.08), 0 1px 2px rgba(16,24,40,.04);
  --ux-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --ux-sans: 'Inter', -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
}

/* ---------- 全局 ---------- */
[data-testid="stAppViewContainer"], .main, [data-testid="stMain"] {
  background: var(--ux-bg);
}
[data-testid="stHeader"] { background: transparent !important; }
/* 隐藏右上角原生操作区：Deploy 按钮 / 标题锚点链接（业务化命名要求） */
[data-testid="stAppDeployButton"], [data-testid="stBaseButton-header"] { display: none !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1360px; }

html, body, p, li, span, div, label, td, th,
[data-testid="stMarkdownContainer"] {
  font-family: var(--ux-sans);
  color: var(--ux-text);
}
h1, h2, h3, h4, h5, h6 {
  font-family: var(--ux-sans) !important;
  color: var(--ux-text) !important;
  letter-spacing: -.01em;
}
/* 去掉默认大标题的夸张感，交给 page_header 统一 */
[data-testid="stMarkdownContainer"] h1 { font-size: 1.55rem; font-weight: 700; }
[data-testid="stMarkdownContainer"] h2 { font-size: 1.25rem; font-weight: 700; }
[data-testid="stMarkdownContainer"] h3 { font-size: 1.05rem; font-weight: 650; }
[data-testid="stCaptionContainer"], .stCaption, small { color: var(--ux-muted) !important; font-size: .82rem; }
code, pre, [data-testid="stCode"] { font-family: var(--ux-mono) !important; }

/* ---------- 侧边栏：常驻导航 ---------- */
[data-testid="stSidebar"] {
  background: var(--ux-surface) !important;
  border-right: 1px solid var(--ux-border);
}
[data-testid="stSidebar"] * { color: var(--ux-text); }
[data-testid="stSidebarNav"] a, section[data-testid="stSidebar"] a[href] {
  border-radius: var(--ux-radius-sm) !important;
  padding: .1rem .2rem;
  transition: background .14s ease, color .14s ease;
  font-weight: 500;
}
[data-testid="stSidebarNav"] a:hover { background: var(--ux-surface-2); }
[data-testid="stSidebarNav"] [aria-current="page"],
[data-testid="stSidebarNav"] a[aria-current="true"] {
  background: var(--ux-accent-soft) !important;
  color: var(--ux-accent-strong) !important;
  font-weight: 700;
  box-shadow: inset 3px 0 0 var(--ux-accent);
}

/* ---------- 页面主标题（自定义组件 .ux-pagehead） ---------- */
.ux-pagehead { margin: 0 0 1.1rem 0; padding-bottom: .85rem; border-bottom: 1px solid var(--ux-border); }
.ux-pagehead .ux-title { font-size: 1.5rem; font-weight: 750; letter-spacing: -.02em; color: var(--ux-text); line-height: 1.2; }
.ux-pagehead .ux-subtitle { margin-top: .3rem; font-size: .9rem; color: var(--ux-muted); font-weight: 450; }

/* ---------- 顶部状态条 ---------- */
.ux-statstrip {
  display: flex; flex-wrap: wrap; gap: 0; align-items: stretch;
  background: var(--ux-surface); border: 1px solid var(--ux-border);
  border-radius: var(--ux-radius); box-shadow: var(--ux-shadow-sm);
  margin-bottom: 1.15rem; overflow: hidden;
}
.ux-stat { flex: 1 1 0; min-width: 130px; padding: .7rem 1rem; border-right: 1px solid var(--ux-border); }
.ux-stat:last-child { border-right: none; }
.ux-stat-label { font-size: .72rem; font-weight: 600; color: var(--ux-subtle); text-transform: uppercase; letter-spacing: .04em; }
.ux-stat-value { margin-top: .28rem; font-size: 1.02rem; font-weight: 700; color: var(--ux-text); display: flex; align-items: center; gap: .42rem; }

/* 状态圆点 */
.ux-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.ux-dot-ok { background: var(--ux-ok); box-shadow: 0 0 0 3px var(--ux-ok-soft); }
.ux-dot-run { background: var(--ux-run); box-shadow: 0 0 0 3px var(--ux-run-soft); animation: ux-pulse 1.4s ease-in-out infinite; }
.ux-dot-warn { background: var(--ux-warn); box-shadow: 0 0 0 3px var(--ux-warn-soft); }
.ux-dot-err { background: var(--ux-err); box-shadow: 0 0 0 3px var(--ux-err-soft); }
.ux-dot-idle { background: var(--ux-idle); box-shadow: 0 0 0 3px var(--ux-idle-soft); }
@keyframes ux-pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }

/* ---------- 状态药丸 ---------- */
.ux-pill {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .12rem .6rem; border-radius: 999px;
  font-size: .76rem; font-weight: 650; line-height: 1.7;
  border: 1px solid transparent; white-space: nowrap;
}
.ux-pill-ok { background: var(--ux-ok-soft); color: var(--ux-ok); border-color: rgba(18,133,95,.22); }
.ux-pill-run { background: var(--ux-run-soft); color: var(--ux-run); border-color: rgba(37,99,235,.22); }
.ux-pill-warn { background: var(--ux-warn-soft); color: var(--ux-warn); border-color: rgba(180,83,9,.22); }
.ux-pill-err { background: var(--ux-err-soft); color: var(--ux-err); border-color: rgba(192,57,43,.22); }
.ux-pill-idle { background: var(--ux-idle-soft); color: var(--ux-muted); border-color: var(--ux-border); }
.ux-pill-accent { background: var(--ux-accent-soft); color: var(--ux-accent-strong); border-color: rgba(13,148,136,.22); }

/* ---------- 管线阶段轨道 ---------- */
.ux-rail { display: flex; flex-wrap: wrap; align-items: stretch; gap: 0;
  background: var(--ux-surface); border: 1px solid var(--ux-border);
  border-radius: var(--ux-radius); box-shadow: var(--ux-shadow-sm);
  padding: .5rem; margin-bottom: 1.15rem; overflow-x: auto; }
.ux-node { position: relative; flex: 1 1 0; min-width: 92px; padding: .55rem .5rem .5rem .7rem;
  border-radius: var(--ux-radius-sm); display: flex; flex-direction: column; gap: .3rem; }
.ux-node + .ux-node::before { content: ""; position: absolute; left: -1px; top: 22%; bottom: 22%;
  width: 2px; background: var(--ux-border); }
.ux-node-head { display: flex; align-items: center; gap: .4rem; font-size: .82rem; font-weight: 650; color: var(--ux-text); }
.ux-node-meta { font-size: .7rem; color: var(--ux-subtle); font-weight: 500; }
.ux-node-done { background: var(--ux-ok-soft); }
.ux-node-done .ux-node-head { color: var(--ux-ok); }
.ux-node-run { background: var(--ux-run-soft); }
.ux-node-run .ux-node-head { color: var(--ux-run); }
.ux-node-pending { background: var(--ux-surface-2); }
.ux-node-disabled { background: transparent; opacity: .55; }
.ux-check { width: 15px; height: 15px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: 9px; font-weight: 800; color: #fff; flex: none; }
.ux-check-done { background: var(--ux-ok); }
.ux-check-run { background: var(--ux-run); }
.ux-check-pending { background: var(--ux-border-strong); }
.ux-check-disabled { background: var(--ux-idle); }

/* ---------- 卡片 / 网格 ---------- */
.ux-card { background: var(--ux-surface); border: 1px solid var(--ux-border);
  border-radius: var(--ux-radius); box-shadow: var(--ux-shadow-sm); padding: 1rem 1.1rem; height: 100%; }
.ux-card-title { font-size: .95rem; font-weight: 700; color: var(--ux-text); margin-bottom: .15rem; }
.ux-card-sub { font-size: .8rem; color: var(--ux-muted); margin-bottom: .7rem; }
.ux-listrow { display: flex; align-items: center; justify-content: space-between; gap: .6rem;
  padding: .5rem 0; border-top: 1px solid var(--ux-border); font-size: .86rem;
  min-width: 0; overflow: hidden; }
.ux-listrow > div { min-width: 0; }
.ux-listrow .ux-lr-main, .ux-listrow .ux-lr-sub { overflow-wrap: anywhere; }
.ux-listrow:first-of-type { border-top: none; }
.ux-listrow .ux-lr-main { font-weight: 600; color: var(--ux-text); }
.ux-listrow .ux-lr-sub { font-size: .76rem; color: var(--ux-subtle); font-family: var(--ux-mono); }
.ux-section-title { font-size: .8rem; font-weight: 700;
  letter-spacing: .05em; color: var(--ux-subtle); margin: .2rem 0 .6rem 0; }

/* ---------- 日志控制台（白底深字，保证可读） ---------- */
.ux-console { background: #FFFFFF; border: 1px solid var(--ux-border); border-radius: var(--ux-radius);
  box-shadow: var(--ux-shadow-sm); overflow: hidden; }
.ux-console-bar { display: flex; align-items: center; gap: .4rem; padding: .45rem .8rem;
  background: #F1F4F8; border-bottom: 1px solid var(--ux-border); }
.ux-console-bar span.t { font-family: var(--ux-mono); font-size: .74rem; color: #5A6B7B; font-weight: 500; }
.ux-console-dotc { width: 10px; height: 10px; border-radius: 50%; }
.ux-console pre { margin: 0; padding: .85rem 1rem; max-height: 380px; overflow: auto;
  font-family: var(--ux-mono) !important; font-size: .78rem; line-height: 1.55;
  color: #1F2933 !important; background: #FFFFFF !important; border: none !important; white-space: pre-wrap; word-break: break-word; }
/* Streamlit 会把 <pre> 当代码块做语法高亮，真实文字落在 pre>code>span；
   强制深色文字 + 透明底，否则被注入的深色字压白底看不清。只作用于 pre 内，不影响标题栏圆点。 */
.ux-console pre code, .ux-console pre code span, .ux-console pre span,
.ux-console pre .codehilite, .ux-console pre [class*="token"] {
  color: #1F2933 !important; background: transparent !important;
  font-family: var(--ux-mono) !important; white-space: pre-wrap; word-break: break-word; }

/* ---------- 原生组件：控制台化 ---------- */
/* 指标卡 */
[data-testid="stMetric"], .stMetric {
  background: var(--ux-surface); border: 1px solid var(--ux-border) !important;
  border-radius: var(--ux-radius) !important; padding: .85rem 1rem !important;
  box-shadow: var(--ux-shadow-sm) !important;
}
[data-testid="stMetricValue"] { color: var(--ux-text) !important; font-weight: 750 !important; font-size: 1.5rem !important; }
[data-testid="stMetricLabel"] { color: var(--ux-subtle) !important; font-weight: 600; }
[data-testid="stMetricDelta"] { font-weight: 600; }

/* 按钮 */
.stButton > button, [data-testid="stButton"] button, [data-testid="baseButton-secondary"] {
  border-radius: var(--ux-radius-sm) !important; font-weight: 600 !important;
  font-family: var(--ux-sans) !important; border: 1px solid var(--ux-border-strong) !important;
  background: var(--ux-surface) !important; color: var(--ux-text) !important;
  box-shadow: var(--ux-shadow-sm) !important; transition: all .13s ease !important;
  padding: .45rem 1rem !important;
}
.stButton > button:hover, [data-testid="stButton"] button:hover {
  border-color: var(--ux-accent) !important; color: var(--ux-accent-strong) !important;
  background: var(--ux-accent-soft) !important;
}
[data-testid="baseButton-primary"], .stButton > button[kind="primary"],
.stForm [data-testid="stFormSubmitButton"] button, [data-testid="stFormSubmitButton"] button {
  background: var(--ux-accent) !important; color: #fff !important;
  border: 1px solid var(--ux-accent) !important; box-shadow: var(--ux-shadow-sm) !important;
}
[data-testid="baseButton-primary"]:hover, .stButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button:hover {
  background: var(--ux-accent-strong) !important; border-color: var(--ux-accent-strong) !important; color: #fff !important;
}
.stDownloadButton > button, [data-testid="stDownloadButton"] button {
  border-radius: var(--ux-radius-sm) !important; border: 1px solid var(--ux-border-strong) !important;
  background: var(--ux-surface) !important; color: var(--ux-text) !important; font-weight: 600 !important;
}
.stButton > button:disabled, [data-testid="stButton"] button:disabled {
  background: var(--ux-surface-2) !important; color: var(--ux-subtle) !important;
  border-color: var(--ux-border) !important; box-shadow: none !important;
}

/* 提示框：细左边框 + 极淡底 */
[data-testid="stAlert"], .stAlert {
  border-radius: var(--ux-radius-sm) !important; border: 1px solid var(--ux-border) !important;
  border-left-width: 3px !important; box-shadow: none !important; background: var(--ux-surface-2) !important;
}
[data-testid="stAlertContainer"] { font-size: .88rem; }
div[data-testid="stAlert"].stInfo, [data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) { border-left-color: var(--ux-run) !important; }
[data-testid="stAlertContentInfo"] { background: transparent !important; }
[data-testid="stAlertContentSuccess"] { background: transparent !important; }
[data-testid="stAlertContentWarning"] { background: transparent !important; }
[data-testid="stAlertContentError"] { background: transparent !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: var(--ux-ok) !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) { border-left-color: var(--ux-warn) !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { border-left-color: var(--ux-err) !important; }

/* 输入控件：1px 边框 + teal 聚焦 */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  border-radius: var(--ux-radius-sm) !important; border: 1px solid var(--ux-border-strong) !important;
  background: var(--ux-surface) !important; box-shadow: var(--ux-shadow-sm) !important;
  transition: border-color .14s ease, box-shadow .14s ease;
}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea {
  background: transparent !important; border: none !important; box-shadow: none !important;
  font-weight: 500; color: var(--ux-text) !important; font-family: var(--ux-sans);
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {
  border-color: var(--ux-accent) !important; box-shadow: 0 0 0 3px var(--ux-accent-soft) !important;
}
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"], [data-baseweb="input"] button { border-radius: 6px !important; }
[data-testid="stCheckbox"] [data-baseweb="checkbox"] span, [data-testid="stRadio"] [data-baseweb="radio"] span { border-radius: 4px !important; }

/* slider */
[data-testid="stSlider"] [data-baseweb="slider"] > div > div { background: var(--ux-border) !important; height: 6px !important; border-radius: 4px; }
[data-baseweb="slider"] [role="slider"] { background: var(--ux-accent) !important; border: 2px solid #fff !important; box-shadow: var(--ux-shadow) !important; }

/* 展开器 */
[data-testid="stExpander"], .streamlit-expanderContent {
  border: 1px solid var(--ux-border) !important; border-radius: var(--ux-radius) !important;
  background: var(--ux-surface) !important; box-shadow: var(--ux-shadow-sm) !important; overflow: hidden;
}
[data-testid="stExpander"] details, [data-testid="stExpander"] summary { border: none !important; background: transparent !important; border-radius: var(--ux-radius) !important; }
[data-testid="stExpander"] summary { font-weight: 650 !important; color: var(--ux-text) !important; }

/* 标签页：控制台下划线分段。
   这几个 tab 是页面主入口，默认样式又小又淡，用户扫一眼根本注意不到，
   所以字号与字重都比正文高一档，未选中也不能弱化成灰字。 */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: 4px; border-bottom: 1px solid var(--ux-border-strong); background: transparent; border-radius: 0; padding: 0;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
  border-radius: var(--ux-radius-sm) var(--ux-radius-sm) 0 0 !important;
  background: transparent !important; border: none !important;
  border-bottom: 3px solid transparent !important;
  font-size: 1.02rem !important; font-weight: 700 !important; letter-spacing: .01em !important;
  color: var(--ux-text) !important; opacity: .78;
  padding: .6rem 1.05rem !important;
}
/* 文字在内层 markdown 容器里，只改 tab 本体盖不住它 */
[data-testid="stTabs"] [data-baseweb="tab"] p,
[data-testid="stTabs"] [data-baseweb="tab"] div[data-testid="stMarkdownContainer"] {
  font-size: inherit !important; font-weight: inherit !important; color: inherit !important;
  letter-spacing: inherit !important; margin: 0 !important;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {
  opacity: 1 !important; color: var(--ux-accent-strong) !important;
  background: var(--ux-accent-soft) !important;
}
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
  opacity: 1 !important; color: var(--ux-accent-strong) !important;
  border-bottom: 3px solid var(--ux-accent) !important; background: var(--ux-accent-soft) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { display: none; }

/* 进度条 */
[data-testid="stProgress"] .stProgress > div > div > div, .stProgress > div > div { border-radius: 6px !important; }
[data-testid="stProgress"] div[role="progressbar"] > div, .stProgress > div > div > div { background: var(--ux-accent) !important; }

/* ---------- 加载指示器：横向扫动进度条（替掉原生旋转图标） ---------- */
/* 原生 spinner 是一个原地转圈的图标：画面确实在动，但看不出任何进展，长任务
   （下注释、建索引、解析 DAG）盯着它等很容易怀疑是不是卡死了。换成一条从左
   扫到右、到头再收回的进度条，持续位移比圆周运动更能说明「还在跑」。
   动画与配色取自 Uiverse (satyamchaudharydev) 的 loader。 */
[data-testid="stSpinner"] {
  position: relative !important;
  display: flex !important;
  flex-direction: row !important;   /* 原生是竖排（图标在上、文字在下），改回一行 */
  align-items: center !important;
  gap: 0 !important;
  padding-left: 146px !important;    /* 左侧让出 130px 轨道 + 16px 间距 */
  min-height: 22px !important;
}
/* 只藏图标本体，文字（role="status"）照常可见 */
[data-testid="stSpinner"] [data-testid="stSpinnerIcon"] { display: none !important; }

/* 轨道 */
[data-testid="stSpinner"]::before {
  content: "";
  position: absolute; left: 0; top: 50%;
  transform: translateY(-50%);
  width: 130px; height: 4px;
  border-radius: 30px;
  background: rgba(0, 0, 0, .2);
}
/* 扫动的进度条：left 与 width 一起插值 → 0‒50% 从左长满，50‒100% 收回到右端。
   不用 right 定位是因为伪元素的包含块是整个 spinner 容器，right:0 会跑到行尾去。 */
[data-testid="stSpinner"]::after {
  content: "";
  position: absolute; left: 0; top: 50%;
  transform: translateY(-50%);
  width: 0; height: 4px;
  border-radius: 30px;
  background: var(--ux-accent);     /* 示例里是 #0071E2，改跟主题的青绿走，免得弹出个蓝条 */
  animation: ux-loader-move 1s ease-in-out infinite;
}
@keyframes ux-loader-move {
  0%   { left: 0;     width: 0; }
  50%  { left: 0;     width: 130px; }
  100% { left: 130px; width: 0; }
}
/* 关掉动效的系统设置下，扫动会变成静止的一条空轨道，反而像卡住；改为整条淡入淡出 */
@media (prefers-reduced-motion: reduce) {
  [data-testid="stSpinner"]::after {
    left: 0 !important; width: 130px !important;
    opacity: .55; animation: ux-pulse 2s ease-in-out infinite !important;
  }
}

/* 分隔线 */
hr { border: none !important; height: 1px !important; background: var(--ux-border) !important; margin: 1.1rem 0 !important; }

/* 数据面：干净可读 */
[data-testid="stDataFrame"], .stDataFrame, [data-testid="stDataEditor"], .stTable {
  border: 1px solid var(--ux-border) !important; border-radius: var(--ux-radius-sm) !important;
  overflow: hidden; box-shadow: var(--ux-shadow-sm) !important;
}
[data-testid="stCode"], .stCode, pre, [data-testid="stCodeBlock"] {
  border-radius: var(--ux-radius-sm) !important; background: var(--ux-surface-2) !important;
  border: 1px solid var(--ux-border) !important;
}
[data-testid="stImage"] img { border-radius: var(--ux-radius-sm) !important; border: 1px solid var(--ux-border); }
[data-testid="stPlotlyChart"], .stPlotlyChart { background: var(--ux-surface); border: 1px solid var(--ux-border); border-radius: var(--ux-radius-sm); box-shadow: var(--ux-shadow-sm); padding: 6px; }

/* 带边框容器 st.container(border=True) */
[data-testid="stVerticalBlockBorderWrapper"] > div { border-color: var(--ux-border) !important; border-radius: var(--ux-radius) !important; background: var(--ux-surface); }

/* 表单 */
[data-testid="stForm"], .stForm { border: 1px solid var(--ux-border) !important; border-radius: var(--ux-radius) !important; background: var(--ux-surface) !important; box-shadow: var(--ux-shadow-sm) !important; }

/* page_link 卡片 */
[data-testid="stPageLink"] { border: 1px solid var(--ux-border) !important; border-radius: var(--ux-radius-sm) !important; background: var(--ux-surface) !important; }
[data-testid="stPageLink"]:hover { border-color: var(--ux-accent) !important; background: var(--ux-accent-soft) !important; }

/* ---------- 自定义分组导航（取代平铺原生导航） ---------- */
[data-testid="stSidebarNav"] { display: none !important; }
[data-testid="stSidebar"] { min-width: 258px; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .3rem; }
.ux-brand { display: flex; align-items: center; gap: .55rem; padding: .1rem 0 .6rem 0;
  margin-bottom: .2rem; border-bottom: 1px solid var(--ux-border); }
.ux-brand-logo { width: 28px; height: 28px; border-radius: 8px; background: var(--ux-accent);
  color: #fff; display: inline-flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: .78rem; letter-spacing: -.03em; flex: none; }
.ux-brand-name { font-weight: 750; font-size: .98rem; color: var(--ux-text); letter-spacing: -.01em; line-height: 1.15; }
.ux-brand-ver { display: inline-block; margin-left: .36rem; padding: .05rem .3rem;
  border-radius: 5px; background: var(--ux-accent-soft); color: var(--ux-accent);
  font-size: .63rem; font-weight: 700; letter-spacing: .01em; vertical-align: middle; }
.ux-brand-sub { font-size: .72rem; color: var(--ux-subtle); }
.ux-nav { display: flex; flex-direction: column; gap: 2px; margin: .15rem 0 .4rem 0; }
.ux-nav-group { font-size: .68rem; font-weight: 700; letter-spacing: .08em; color: var(--ux-subtle);
  text-transform: uppercase; margin: .7rem 0 .1rem .35rem; }
.ux-nav a.ux-nav-link { display: flex; align-items: center; gap: .5rem; padding: .42rem .6rem;
  border-radius: 8px; text-decoration: none; color: var(--ux-muted); font-weight: 550;
  font-size: .86rem; border-left: 3px solid transparent; transition: all .13s ease; }
.ux-nav a.ux-nav-link:hover { background: var(--ux-surface-2); color: var(--ux-text); }
.ux-nav a.ux-nav-link .ux-nav-ic { width: 16px; text-align: center; font-size: .92rem; opacity: .85; }
.ux-nav a.ux-nav-link.ux-nav-active { background: var(--ux-accent-soft); color: var(--ux-accent-strong) !important;
  font-weight: 700; border-left: 3px solid var(--ux-accent); }
.ux-nav a.ux-nav-link:focus-visible { outline: 2px solid var(--ux-accent); outline-offset: 1px; }
.ux-nav-foot { margin-top: .6rem; padding-top: .5rem; border-top: 1px solid var(--ux-border);
  font-size: .7rem; color: var(--ux-subtle); font-family: var(--ux-mono); }
/* 当前页静态高亮块（div，不可点） */
.ux-nav-active-static { display: flex; align-items: center; gap: .5rem; padding: .42rem .6rem;
  border-radius: 8px; background: var(--ux-accent-soft); color: var(--ux-accent-strong) !important;
  font-weight: 700; font-size: .86rem; border-left: 3px solid var(--ux-accent); }
.ux-nav-active-static .ux-nav-ic { width: 16px; text-align: center; font-size: .92rem; opacity: .9; }
/* 侧栏内的 page_link 还原为扁平 nav 行（区别于内容区的卡片式 page_link） */
[data-testid="stSidebar"] [data-testid="stPageLink"] {
  border: none !important; background: transparent !important; box-shadow: none !important;
  border-radius: 8px !important; border-left: 3px solid transparent !important;
  padding: 0 !important; margin: 0 !important; }
[data-testid="stSidebar"] [data-testid="stPageLink"]:hover {
  background: var(--ux-surface-2) !important; border-color: transparent !important; }
[data-testid="stSidebar"] [data-testid="stPageLink"] p,
[data-testid="stSidebar"] [data-testid="stPageLink"] span,
[data-testid="stSidebar"] [data-testid="stPageLink"] a,
[data-testid="stSidebar"] [data-testid="stPageLink"] [data-testid="stPageLink-Row"] {
  color: var(--ux-muted) !important; font-weight: 550 !important; font-size: .86rem !important;
  text-decoration: none !important; }
/* 收紧侧栏 markdown 块外边距/间距，使分组标题与链接贴近原生 nav */
[data-testid="stSidebar"] .stMarkdown { margin-bottom: .1rem !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .18rem; }

/* ---------- 侧栏折叠按钮：hover / focus 反馈 + 中文 tooltip ---------- */
/* Streamlit 原生折叠按钮已带 aria-label（收起/展开侧栏），这里补齐可见反馈 */
[data-testid="stSidebarCollapseButton"] { position: relative; }
[data-testid="stSidebarCollapseButton"] button { border-radius: 8px !important;
  transition: background .13s ease, box-shadow .13s ease; }
[data-testid="stSidebarCollapseButton"] button:hover { background: var(--ux-accent-soft) !important; }
[data-testid="stSidebarCollapseButton"] button:focus-visible {
  outline: 2px solid var(--ux-accent); outline-offset: 1px; }
[data-testid="stSidebarCollapseButton"]::after {
  content: "收起 / 展开导航"; position: absolute; top: 50%; left: calc(100% + 8px);
  transform: translateY(-50%); white-space: nowrap; pointer-events: none;
  background: var(--ux-text); color: var(--ux-surface); font-size: .72rem; font-weight: 600;
  padding: .2rem .5rem; border-radius: 6px; opacity: 0;
  transition: opacity .13s ease; z-index: 50; }
[data-testid="stSidebarCollapseButton"]:hover::after,
[data-testid="stSidebarCollapseButton"]:focus-within::after { opacity: 1; }
/* 侧栏折叠后出现在左上角的展开按钮同样给出反馈 */
[data-testid="stCollapsedControlContainer"] button:hover { background: var(--ux-accent-soft) !important; }
[data-testid="stCollapsedControlContainer"] button:focus-visible {
  outline: 2px solid var(--ux-accent); outline-offset: 1px; }

/* ---------- 全局状态条 ---------- */
.ux-statusbar { display: flex; align-items: center; gap: .6rem; padding: .55rem .95rem;
  border: 1px solid var(--ux-border); border-radius: 10px; background: var(--ux-surface);
  margin-bottom: 1rem; box-shadow: var(--ux-shadow-sm); }
.ux-statusbar .sb-txt { font-weight: 650; font-size: .9rem; color: var(--ux-text); }
.ux-statusbar .sb-meta { margin-left: auto; font-size: .78rem; color: var(--ux-subtle); font-family: var(--ux-mono); }

/* ---------- 面板容器（HTML-only 区块，靠边框/标题/间距分层，不依赖大阴影） ---------- */
.ux-panel { background: var(--ux-surface); border: 1px solid var(--ux-border); border-radius: 10px;
  padding: 20px 24px; }
.ux-panel-title { font-size: .98rem; font-weight: 720; color: var(--ux-text); margin-bottom: .1rem; }
.ux-panel-sub { font-size: .82rem; color: var(--ux-muted); margin-bottom: .9rem; }

/* ---------- 数据摘要（数值/单位/标签分离） ---------- */
.ux-metrics { display: flex; flex-wrap: wrap; gap: 1.6rem; }
.ux-metric { display: flex; flex-direction: column; }
.ux-metric-num { font-size: 1.7rem; font-weight: 700; color: var(--ux-text); line-height: 1.1; letter-spacing: -.02em; }
.ux-metric-num .ux-metric-unit { font-size: .95rem; font-weight: 600; color: var(--ux-muted); margin-left: .18rem; }
.ux-metric-label { font-size: .74rem; color: var(--ux-subtle); font-weight: 600; margin-top: .22rem; }
.ux-metric-num.ux-mk-ok { color: var(--ux-ok); } .ux-metric-num.ux-mk-run { color: var(--ux-run); }
.ux-metric-num.ux-mk-err { color: var(--ux-err); } .ux-metric-num.ux-mk-warn { color: var(--ux-warn); }
.ux-metric-num.ux-mk-accent { color: var(--ux-accent-strong); }
.ux-metric-ic { font-size: 1.05rem; font-weight: 800; margin-right: .25rem; }
.ux-metric-bar { width: 130px; height: 5px; border-radius: 3px; background: var(--ux-surface-2);
  border: 1px solid var(--ux-border); margin-top: .35rem; overflow: hidden; }
.ux-metric-bar i { display: block; height: 100%; background: var(--ux-accent); }

/* ---------- 管线状态表（统一列：阶段/状态/时间/耗时/操作） ---------- */
.ux-ptable { width: 100%; border-collapse: collapse; font-size: .86rem; }
.ux-ptable thead th { text-align: left; font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--ux-subtle); font-weight: 650; padding: .35rem .6rem; border-bottom: 1px solid var(--ux-border-strong); }
.ux-ptable thead th.ux-col-time, .ux-ptable thead th.ux-col-dur { text-align: right; }
.ux-ptable tbody td { padding: .55rem .6rem; border-bottom: 1px solid var(--ux-border); vertical-align: middle; }
.ux-ptable tbody tr:last-child td { border-bottom: none; }
.ux-pstage { font-weight: 600; color: var(--ux-text); }
.ux-pstage-sub { font-size: .71rem; color: var(--ux-subtle); font-family: var(--ux-mono); margin-top: .1rem; }
.pipeline-time { width: 150px; text-align: right; font-family: var(--ux-mono); color: var(--ux-muted); font-size: .82rem; }
.ux-pid { display: inline-block; font-family: var(--ux-mono); font-size: .68rem;
  color: var(--ux-subtle); background: var(--ux-surface-2); border: 1px solid var(--ux-border);
  border-radius: 5px; padding: 0 .3rem; margin-left: .4rem; vertical-align: 1px; }
code.ux-code { font-family: var(--ux-mono); font-size: .78em; color: var(--ux-muted);
  background: var(--ux-surface-2); border: 1px solid var(--ux-border); border-radius: 5px; padding: 0 .3rem; }
.ux-pdur { width: 88px; text-align: right; font-family: var(--ux-mono); color: var(--ux-subtle); font-size: .82rem; }
.ux-pop { text-align: right; width: 168px; min-width: 168px; white-space: nowrap; }
.ux-pop a { color: var(--ux-accent-strong); text-decoration: none; font-weight: 600; font-size: .8rem; }
.ux-pop a + a::before { content: " · "; color: var(--ux-subtle); font-weight: 400; }
.ux-pop a.danger { color: var(--ux-err); }
.ux-pop a:hover { text-decoration: underline; }
.ux-pop .ux-pop-mute { color: var(--ux-subtle); font-weight: 500; }

/* 状态图标圆点（复用 ux-dot 色系，文字+颜色+图标三重表达） */
.ux-ic-dot { display: inline-flex; align-items: center; justify-content: center;
  width: 15px; height: 15px; border-radius: 50%; font-size: 10px; font-weight: 800; color: #fff; flex: none; }
.ux-ic-ok { background: var(--ux-ok);} .ux-ic-run { background: var(--ux-run);}
.ux-ic-warn { background: var(--ux-warn);} .ux-ic-err { background: var(--ux-err);}
.ux-ic-accent { background: var(--ux-accent);} .ux-ic-idle { background: var(--ux-idle);}

/* 校验结果列表 */
.ux-issues { display: flex; flex-direction: column; gap: .35rem; }
.ux-issue { display: flex; align-items: flex-start; gap: .55rem; padding: .5rem .7rem;
  border: 1px solid var(--ux-border); border-left-width: 3px; border-radius: 8px; background: var(--ux-surface-2); font-size: .85rem; }
.ux-issue .ui-field { font-family: var(--ux-mono); font-weight: 600; color: var(--ux-text); }
.ux-issue-error { border-left-color: var(--ux-err);} .ux-issue-warn { border-left-color: var(--ux-warn);}
.ux-issue-info { border-left-color: var(--ux-run);}

/* 步骤条（配置编辑工作流） */
.ux-steps { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin-bottom: .3rem; }
.ux-step { display: inline-flex; align-items: center; gap: .4rem; padding: .34rem .7rem; border-radius: 999px;
  border: 1px solid var(--ux-border); background: var(--ux-surface); font-size: .8rem; font-weight: 600; color: var(--ux-muted); }
.ux-step .ux-step-n { font-family: var(--ux-mono); font-size: .72rem; color: var(--ux-subtle); }
.ux-step-done { border-color: rgba(18,133,95,.3); background: var(--ux-ok-soft); color: var(--ux-ok); }
.ux-step-err { border-color: rgba(192,57,43,.3); background: var(--ux-err-soft); color: var(--ux-err); }
.ux-step-todo { color: var(--ux-subtle); }
.ux-step-dirty { border-color: rgba(212,136,6,.35); background: var(--ux-warn-soft); color: var(--ux-warn); }
.ux-step-current { box-shadow: 0 0 0 2px var(--ux-accent-soft); border-color: var(--ux-accent); }
.ux-step-note { font-size: .68rem; font-weight: 700; opacity: .85; margin-left: .15rem; }

/* 滚动条 */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--ux-border-strong); border-radius: 8px; border: 3px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-thumb:hover { background: var(--ux-subtle); background-clip: content-box; }
"""


# ---------------------------------------------------------------------------
# 组件助手
# ---------------------------------------------------------------------------
def inject_theme() -> None:
    """把控制台主题 CSS 注入当前页面（每个页面 set_page_config 后调用一次）。"""
    # 注意：CSS 含 { }，禁止用 f-string 拼接
    st.markdown("<style>" + THEME_CSS + "</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    """控制台风格的页面主标题（替代 st.title，无 emoji、无渐变）。"""
    sub = f'<div class="ux-subtitle">{_html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="ux-pagehead"><div class="ux-title">{_html.escape(title)}</div>{sub}</div>',
        unsafe_allow_html=True,
    )


def section_title(text: str) -> None:
    """小节标题（大写字距风格）。"""
    st.markdown(f'<div class="ux-section-title">{_html.escape(text)}</div>', unsafe_allow_html=True)


def pill(text: str, kind: str = "idle") -> str:
    """返回一个状态药丸的 HTML 片段。kind: ok/run/warn/err/idle/accent。"""
    return f'<span class="ux-pill ux-pill-{kind}">{_html.escape(text)}</span>'


def stat_strip(stats: list[dict]) -> None:
    """顶部状态条。stats 每项: {"label": str, "value": str, "kind": "ok|run|warn|err|idle"|None}。"""
    cells = ""
    for s in stats:
        kind = s.get("kind")
        dot = f'<span class="ux-dot ux-dot-{kind}"></span>' if kind else ""
        cells += (
            f'<div class="ux-stat">'
            f'<div class="ux-stat-label">{_html.escape(str(s.get("label", "")))}</div>'
            f'<div class="ux-stat-value">{dot}{_html.escape(str(s.get("value", "")))}</div>'
            f"</div>"
        )
    st.markdown(f'<div class="ux-statstrip">{cells}</div>', unsafe_allow_html=True)


# 阶段状态 -> (css 后缀, 勾选标记符号)
_STAGE_MARK = {
    "done": ("done", "&#10003;"),
    "running": ("run", "&#9679;"),
    "pending": ("pending", ""),
    "disabled": ("disabled", ""),
}


def stage_rail(stages: list[dict]) -> None:
    """管线阶段轨道。stages 每项: {"name": str, "status": done|running|pending|disabled, "meta": str|None}。"""
    nodes = ""
    for s in stages:
        status = s.get("status", "pending")
        suffix, mark = _STAGE_MARK.get(status, ("pending", ""))
        meta = s.get("meta")
        meta_html = f'<div class="ux-node-meta">{_html.escape(str(meta))}</div>' if meta else ""
        check = f'<span class="ux-check ux-check-{suffix}">{mark}</span>'
        nodes += (
            f'<div class="ux-node ux-node-{suffix}">'
            f'<div class="ux-node-head">{check}{_html.escape(str(s.get("name", "")))}</div>'
            f"{meta_html}</div>"
        )
    st.markdown(f'<div class="ux-rail">{nodes}</div>', unsafe_allow_html=True)


def render_html(html: str) -> None:
    """渲染原生 HTML 片段。

    优先走 st.html：它不经过 markdown 解析，因此 <pre> 里的换行能被保留。
    用 st.markdown(unsafe_allow_html=True) 时，日志正文会被当成 markdown 处理——
    `# Command line: ...` 被识别成标题、行间单个换行被折叠成空格，
    最终整段日志挤成一坨，正是日志“看不清”的原因。
    老版本 Streamlit 没有 st.html 时回退到 markdown。
    """
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)


def log_console(text: str, title: str = "console") -> None:
    """深色等宽日志面板（替代 st.code 用于日志展示）。"""
    esc = _html.escape(text or "")
    render_html(
        '<div class="ux-console">'
        '<div class="ux-console-bar">'
        '<span class="ux-console-dotc" style="background:#FF5F56"></span>'
        '<span class="ux-console-dotc" style="background:#FFBD2E"></span>'
        '<span class="ux-console-dotc" style="background:#27C93F"></span>'
        f'<span class="t">&nbsp;&nbsp;{_html.escape(title)}</span>'
        "</div>"
        f"<pre>{esc}</pre>"
        "</div>"
    )


def card_grid_header(title: str, subtitle: str | None = None) -> str:
    """返回卡片头部 HTML（配合 st.container 使用）。"""
    sub = f'<div class="ux-card-sub">{_html.escape(subtitle)}</div>' if subtitle else ""
    return f'<div class="ux-card-title">{_html.escape(title)}</div>{sub}'


def list_row(main: str, sub: str | None = None, right_html: str | None = None,
             sub_html: str | None = None) -> str:
    """返回一行列表项 HTML。right_html 可放 pill()；sub_html 可放 code_html()。"""
    if sub_html:
        sub_block = f'<div class="ux-lr-sub">{sub_html}</div>'
    elif sub:
        sub_block = f'<div class="ux-lr-sub">{_html.escape(str(sub))}</div>'
    else:
        sub_block = ""
    right = right_html or ""
    return (
        f'<div class="ux-listrow"><div><div class="ux-lr-main">{_html.escape(str(main))}</div>'
        f"{sub_block}</div><div>{right}</div></div>"
    )


def short_path(p: str, tail: int = 34) -> str:
    """过长路径只保留末尾关键部分（前缀用 … 省略）。"""
    p = str(p or "")
    return ("…" + p[-tail:]) if len(p) > tail else p


def code_html(text: str, title: str | None = None) -> str:
    """路径/模块 ID/文件名等技术标识的等宽样式 HTML（完整值放 tooltip）。"""
    t = str(text or "")
    tip = _html.escape(title or t, quote=True)
    return f'<code class="ux-code" title="{tip}">{_html.escape(short_path(t))}</code>'


def render_html_list(rows_html: list[str]) -> None:
    """把 list_row() 生成的若干行渲染成一个列表块。"""
    st.markdown("<div>" + "".join(rows_html) + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 统一状态模型（文字 + 颜色 + 图标三重表达，不单独依赖颜色）
# ---------------------------------------------------------------------------
# key -> (pill kind, 图标字形, 默认中文标签)
STATE_META: dict[str, tuple[str, str, str]] = {
    "unconfigured": ("idle",    "\u2014", "未配置"),   # —
    "pending":      ("accent",  "\u25b7", "待运行"),   # ▷
    "running":      ("run",     "\u25cf", "运行中"),   # ●
    "done":         ("ok",      "\u2713", "已完成"),   # ✓
    "failed":       ("err",     "\u2717", "失败"),     # ✗
    "skipped":      ("idle",    "\u00bb", "已跳过"),   # »
}


def state_badge(state: str, label: str | None = None) -> str:
    """返回统一状态药丸 HTML（图标 + 文字 + 颜色）。state 为 STATE_META 的键。"""
    kind, icon, default_label = STATE_META.get(state, ("idle", "\u2014", state))
    text = label or default_label
    return (
        f'<span class="ux-pill ux-pill-{kind}">'
        f'<span class="ux-ic-dot ux-ic-{kind}">{icon}</span>{_html.escape(text)}</span>'
    )


def panel(title: str, subtitle: str | None = None) -> None:
    """开启一个面板头部（配合 st.container(border=True) 使用；此处仅渲染标题块）。"""
    sub = f'<div class="ux-panel-sub">{_html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="ux-panel-title">{_html.escape(title)}</div>{sub}',
        unsafe_allow_html=True,
    )


def metrics(items: list[dict]) -> None:
    """数据摘要。items 每项: {"value": str, "unit": str|None, "label": str,
    "kind": ok|run|err|warn|accent|None, "icon": str|None, "bar_frac": float|None}。"""
    cells = ""
    for it in items:
        unit = it.get("unit")
        unit_html = f'<span class="ux-metric-unit">{_html.escape(str(unit))}</span>' if unit else ""
        kind = it.get("kind")
        kcls = f" ux-mk-{kind}" if kind else ""
        icon = it.get("icon")
        icon_html = f'<span class="ux-metric-ic ux-ic-{kind or "idle"}">{icon}</span>' if icon else ""
        bar = it.get("bar_frac")
        bar_html = ""
        if bar is not None:
            pct = max(0.0, min(1.0, float(bar))) * 100
            bar_html = f'<div class="ux-metric-bar"><i style="width:{pct:.0f}%"></i></div>'
        cells += (
            f'<div class="ux-metric">'
            f'<div class="ux-metric-num{kcls}">{icon_html}{_html.escape(str(it.get("value", "")))}{unit_html}</div>'
            f'<div class="ux-metric-label">{_html.escape(str(it.get("label", "")))}</div>{bar_html}</div>'
        )
    st.markdown(f'<div class="ux-metrics">{cells}</div>', unsafe_allow_html=True)


def status_bar(text: str, kind: str | None = None, meta: str | None = None) -> None:
    """全局状态条：运行中阶段 / 最近同步时间 / 日志提示等。"""
    dot = f'<span class="ux-dot ux-dot-{kind}"></span>' if kind else ""
    meta_html = f'<span class="sb-meta">{_html.escape(meta)}</span>' if meta else ""
    st.markdown(
        f'<div class="ux-statusbar">{dot}<span class="sb-txt">{_html.escape(text)}</span>{meta_html}</div>',
        unsafe_allow_html=True,
    )


# 侧边栏导航项定义（按工作流顺序分组）。
# 元组：(key, icon, label, page)；page 为 st.page_link 需要的、相对于入口脚本的路径，
# 走客户端路由，切页不会新建会话→ session_state 不丢。home 即入口 app.py。
_NAV_GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ("开始", [("home", "\u25a4", "总览", "app.py")]),
    ("配置", [
        ("config", "\u2699", "配置编辑", "pages/1_配置编辑.py"),
        ("env", "\u2692", "环境监测", "pages/5_环境监测.py"),
        ("annotation", "\u2736", "物种注释", "pages/4_物种注释.py"),
    ]),
    ("运行", [("run", "\u25b6", "运行管理", "pages/2_运行管理.py")]),
    ("结果", [("preview", "\u25a6", "结果预览", "pages/3_结果预览.py")]),
]


def render_sidebar(current: str, running: str | None = None,
                   kind: str | None = None, footer: str | None = None) -> None:
    """品牌区 + 按工作流分组导航（含选中态）。
    当前页渲染为定制高亮块（div，本就在该页不可点），其余页用 st.page_link
    走客户端路由，避免锁定锚点整页跳转清空 session_state。
    current: home|config|env|annotation|run|preview。
    """
    brand_sub = running or "ChIP-seq 分析流程控制台"
    _k = kind or "idle"
    dot = f'<span class="ux-dot ux-dot-{_k}"></span> ' if running else ""
    st.sidebar.markdown(
        '<div class="ux-brand">'
        '<span class="ux-brand-logo">ChIP</span>'
        f'<div><div class="ux-brand-name">{_html.escape(APP_NAME)}'
        f'<span class="ux-brand-ver">v{_html.escape(APP_VERSION)}</span></div>'
        f'<div class="ux-brand-sub">{dot}{_html.escape(brand_sub)}</div></div></div>',
        unsafe_allow_html=True,
    )
    for group_name, items in _NAV_GROUPS:
        st.sidebar.markdown(
            f'<div class="ux-nav-group">{_html.escape(group_name)}</div>',
            unsafe_allow_html=True,
        )
        for key, ic, label, page in items:
            if key == current:
                st.sidebar.markdown(
                    '<div class="ux-nav"><div class="ux-nav-active-static">'
                    f'<span class="ux-nav-ic">{ic}</span>{_html.escape(label)}</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                # 必须用 st.sidebar.page_link（默认 st.page_link 会渲染到主区）
                st.sidebar.page_link(page, label=f"{ic}  {label}", use_container_width=True)
    if footer:
        st.sidebar.markdown(
            f'<div class="ux-nav-foot">{_html.escape(footer)}</div>',
            unsafe_allow_html=True,
        )


def pipeline_table(stages: list[dict]) -> None:
    """管线阶段统一表格。
    stages 每项: {name, id?, sub?, state, state_label?, time?, dur?,
    actions?: [(label, href)...], action_label?, action_href?}。"""
    head = (
        '<thead><tr><th>阶段名称</th><th>当前状态</th>'
        '<th class="ux-col-time">最近运行</th>'
        '<th class="ux-col-dur" title="与上一完成阶段的间隔（近似值，非纯计算耗时）">阶段间隔</th>'
        '<th class="ux-col-time">操作</th></tr></thead>'
    )
    body = ""
    _danger_actions = {"停止运行", "重试", "查看错误"}
    for s in stages:
        state = s.get("state", "pending")
        badge = state_badge(state, s.get("state_label"))
        sub = s.get("sub")
        sub_html = f'<div class="ux-pstage-sub">{_html.escape(str(sub))}</div>' if sub else ""
        pid = s.get("id") or s.get("key")
        pid_html = f'<span class="ux-pid" title="内部阶段 ID">{_html.escape(str(pid))}</span>' if pid else ""
        time_txt = s.get("time") or "—"
        dur_txt = s.get("dur") or "—"
        actions = s.get("actions") or (
            [(s["action_label"], s["action_href"])]
            if s.get("action_label") and s.get("action_href") else []
        )
        if actions:
            op = ""
            for label, href in actions:
                _dc = ' class="danger"' if label in _danger_actions else ""
                op += (
                    f'<a href="{href}" aria-label="{_html.escape(label)}"{_dc}>'
                    f"{_html.escape(label)}</a>"
                )
        else:
            op = '<span class="ux-pop-mute">—</span>'
        body += (
            "<tr>"
            f'<td><span class="ux-pstage">{_html.escape(str(s.get("name", "")))}</span>{pid_html}{sub_html}</td>'
            f"<td>{badge}</td>"
            f'<td class="pipeline-time">{_html.escape(str(time_txt))}</td>'
            f'<td class="ux-pdur">{_html.escape(str(dur_txt))}</td>'
            f'<td class="ux-pop">{op}</td>'
            "</tr>"
        )
    st.markdown(
        f'<table class="ux-ptable">{head}<tbody>{body}</tbody></table>',
        unsafe_allow_html=True,
    )


def render_issues(issues: list[dict]) -> None:
    """校验结果列表。issues 每项: {level: error|warn|info, field?: str, msg: str}。"""
    _cls = {"error": "ux-issue-error", "warn": "ux-issue-warn", "info": "ux-issue-info"}
    _ic = {"error": "\u2715", "warn": "!", "info": "i"}
    _ic_kind = {"error": "err", "warn": "warn", "info": "run"}
    rows = ""
    for it in issues:
        lvl = it.get("level", "info")
        field = it.get("field")
        field_html = f'<span class="ui-field">{_html.escape(str(field))}：</span>' if field else ""
        rows += (
            f'<div class="ux-issue {_cls.get(lvl, "ux-issue-info")}">'
            f'<span class="ux-ic-dot ux-ic-{_ic_kind.get(lvl, "run")}">{_ic.get(lvl, "i")}</span>'
            f"<div>{field_html}{_html.escape(str(it.get('msg', '')))}</div></div>"
        )
    st.markdown(f'<div class="ux-issues">{rows}</div>', unsafe_allow_html=True)


def steps_bar(steps: list[dict]) -> None:
    """工作流步骤条（只表示完成度/问题状态，具体编辑走 Tab）。
    steps 每项: {label, status: done|err|dirty|todo, note?, current?}。"""
    _cls = {"done": "ux-step-done", "err": "ux-step-err",
            "dirty": "ux-step-dirty", "todo": "ux-step-todo"}
    _ic = {"done": "\u2713", "err": "\u2717", "dirty": "!", "todo": "\u00b7"}
    _note_default = {"done": "已完成", "err": "有问题", "dirty": "需重校验", "todo": "待确认"}
    cells = ""
    for i, s in enumerate(steps, 1):
        st_key = s.get("status", "todo")
        ic = _ic.get(st_key, "\u00b7")
        cur = " ux-step-current" if s.get("current") else ""
        note = s.get("note", _note_default.get(st_key, ""))
        note_html = f'<span class="ux-step-note">{_html.escape(str(note))}</span>' if note else ""
        cells += (
            f'<span class="ux-step {_cls.get(st_key, "ux-step-todo")}{cur}">'
            f'<span class="ux-step-n">{i} {ic}</span>{_html.escape(str(s.get("label", "")))}{note_html}</span>'
        )
    st.markdown(f'<div class="ux-steps">{cells}</div>', unsafe_allow_html=True)
