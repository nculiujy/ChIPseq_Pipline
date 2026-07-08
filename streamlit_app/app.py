"""
ChIPseq_Pipline Streamlit 管理面板
===================================
启动方式: streamlit run streamlit_app/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="ChIPseq Pipeline 管理面板",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🧬 ChIPseq Pipeline 管理面板")
st.markdown("---")

st.markdown("""
### 欢迎使用 ChIPseq Pipeline 管理面板

本面板提供以下功能：

| 页面 | 功能 |
|------|------|
| **⚙️ 配置编辑** | 可视化编辑 `config.yaml` 和 `metadata.csv` |
| **🚀 运行控制** | 启动/监控 Snakemake 流程，查看实时日志 |
| **📊 结果预览** | 浏览分析输出：Peak 注释表、热图、饼图 |
| **📈 运行状态** | 各模块完成状态总览，marker 文件检查 |

---

#### 快速开始

1. 在左侧导航栏选择功能页面
2. **首次使用**建议先检查「⚙️ 配置编辑」页面确认参数
3. 在「🚀 运行控制」页面启动流程

#### 系统信息
""")

import os
import yaml

# 显示基本系统信息
config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
config_path = os.path.abspath(config_path)

if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    projects = config.get("projects", [])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("已配置项目数", len(projects))
    with col2:
        active = sum(1 for p in projects if any(p.get("modules", {}).values()))
        st.metric("活跃项目", active)
    with col3:
        st.metric("线程配置", config.get("threads", "N/A"))
    
    st.markdown("**已配置项目：**")
    for p in projects:
        modules = p.get("modules", {})
        active_modules = [k for k, v in modules.items() if v]
        status = "🟢" if active_modules else "⚪"
        st.markdown(f"- {status} **{p['species']}/{p['experiment']}** — 活跃模块: {', '.join(active_modules) if active_modules else '无'}")
else:
    st.warning("未找到 config.yaml 文件，请检查路径配置。")
