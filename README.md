# 🧬 ChIPseq Pipeline

**一键装、一键跑、浏览器里点点鼠标就能完成 ChIP-seq / CUT&Tag 数据分析。**

不需要写代码、不需要记命令、不需要管理员权限。

---

## ✨ 能做什么

- **🔍 从原始测序数据到 Peak 全自动**：比对（Bowtie2）→ 去重（Picard）→ Peak 鉴定（MACS2）→ Peak 注释（ChIPseeker）→ 热图（deepTools）
- **🌐 浏览器操作界面**：所有配置、运行、看图都在网页里点鼠标完成
- **🧬 物种注释一键下载**：内置拟南芥 / 人 / 小鼠 / 大豆 / 玉米 / 水稻 / 酵母等常用基因组，自动下载 + 建索引 + 写入配置
- **📦 无需管理员权限**：支持在 HPC / 学校集群 / 公司服务器上直接跑，全程不用 `sudo`
- **🗂 多项目并行**：一个配置文件里同时管理多个物种、多个实验，独立开关各个分析步骤

---

## 📁 目录结构

装好之后你只需要关心 3 个地方：

```
ChIPseq_Pipline/
├── bootstrap.sh         ← 一键安装（跑一次就够）
├── config/              ← 配置文件（浏览器里编辑，不用手改）
├── workflow/anno/       ← 基因组注释（浏览器里下载，不用手动放）
└── workflow/resources/  ← 你的原始 FASTQ 数据（拷进来）
```

其他目录（`workflow/rules/`、`workflow/scripts/`、`runtime/`、`cli/` 等）都是流程内部代码，**不用管**。

分析结果会自动生成在 `result/{物种}/{实验名}/` 下，浏览器里可以直接看图看表。

---

## 🚀 第一次启动

### 前置要求

一台 Linux 机器（服务器 / 工作站 / 虚拟机都行），满足：

- **能上网**（第一次要下载几个 GB 的环境和基因组）
- **至少 20 GB 空闲磁盘**（环境 8 GB + 基因组 5 GB + 分析结果）
- **至少 16 GB 内存**（比对和 peak calling 会吃内存）
- **不需要管理员权限**

**默认走 conda 方案**（无需任何容器、开箱即用）。只有当机器上装了 Apptainer/Singularity/Podman/Docker **且你们有真实可拉取的镜像**时，才需要考虑容器模式（详见下方「安装路径」说明）。

### 第 1 步：下载项目

```bash
git clone https://github.com/nculiujy/ChIPseq_Pipline.git
cd ChIPseq_Pipline
```

> 没有 git？让管理员装一下，或者从 GitHub 网页上点「Download ZIP」，解压后 `cd` 进目录即可。

> **安装路径（默认 conda，开箱即用）**
> - **conda 模式（默认推荐，无需容器）** —— 组合流程 A→B：
>   - **A. `CHIPSEQ_RUNTIME=conda ./bootstrap.sh`** 只创建一个**秒级精简启动器** `~/.local/chipseq-launch`（python + streamlit + pyyaml），负责把面板拉起来，**不下载几个 GB**。
>   - **B. 浏览器「环境配置」页** 一键构建**完整分析环境** `~/.local/chipseq-env`（全部生信工具 + R）：离线 `chipseq-env*.tar.gz` 优先、缺则在线 `conda env create/update`，全程分阶段可视化、可取消。
>   - 两者各司其职、互不覆盖：UI 跑在启动器里，分析工具由启动器定向到分析环境。
> - **容器模式（可选，仅当有真实镜像时）**：Apptainer / Podman / Docker 一次性给全量环境。⚠️ 仓库默认的 `ghcr.io/chipseq-pipeline/...` 是**从未发布的占位地址**，直接 `./bootstrap.sh` 走容器会报 **403 Forbidden**。要用容器需自备镜像：把离线包 `chipseq-pipeline-image.tar.zst` 放项目根目录（自动识别加载），或用 `CHIPSEQ_IMAGE=<真实可访问镜像>` 指定。
>
> 开发态直接 `streamlit run streamlit_app/app.py`（已激活含工具的 conda 环境）时，「环境配置」页会回落到当前环境，照常可用。

### 第 2 步：一键安装

```bash
CHIPSEQ_RUNTIME=conda ./bootstrap.sh
```

**这一步会做什么**（conda 方案仅数秒）：

1. 创建一个**秒级精简启动器** `~/.local/chipseq-launch`（python + streamlit + pyyaml），负责把面板拉起来——**不下载几个 GB**
2. 完整分析环境留到第 5 步在浏览器「环境配置」页一键构建
3. 把 6 个命令装到你的 `~/.local/bin/` 下

> 想走容器模式（需自备真实镜像，见上方说明）就直接 `./bootstrap.sh`，它会自动探测 Apptainer / Podman / Docker。若本机没有容器、或默认镜像地址不可达（占位 namespace 会 403），**会自动降级到 conda**，无需手动加环境变量。

安装脚本会打印详细进度。**中间不需要输入密码，不会碰系统文件**。

装完后按屏幕提示把 `~/.local/bin` 加到 PATH：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 第 3 步：一键启动

```bash
chipseq-app
```

启动后会看到类似输出：

```
============================================================
 ChIPseq Pipeline 已启动  (runtime: conda)
------------------------------------------------------------
 本机访问:    http://localhost:8501
 SSH 隧道:    在本地机器执行:
                ssh -L 8501:localhost:8501 $USER@<服务器地址>
              然后浏览器打开 http://localhost:8501
============================================================
```

### 第 4 步：打开浏览器

- **在自己电脑上跑**：直接打开 <http://localhost:8501>
- **通过 SSH 连到服务器跑**：在**本地电脑**新开一个终端，按上面提示执行 `ssh -L 8501:localhost:8501 用户名@服务器地址`，然后本地浏览器打开 <http://localhost:8501>

### 第 5 步：在浏览器里配置和运行

进入面板后，左侧导航按工作流分为「开始 / 配置 / 运行 / 结果」4 组、共 6 个页面，首次使用建议按下面顺序：

| 顺序 | 页面 | 做什么 |
|:---:|------|-------|
| 1️⃣ | **总览** | 看当前项目、配置是否可运行、管线进度与最近日志 |
| 2️⃣ | **配置编辑** | 填实验名、把 FASTQ 数据目录指过去、勾选要跑的分析模块 |
| 3️⃣ | **环境配置** | conda 模式一键构建分析环境（离线优先 / 在线兜底）+ R 包补齐 + 工具 / 运行时自检，分阶段进度与实时日志；容器模式仅做自检 |
| 4️⃣ | **物种注释** | 选择物种（如 TAIR10 拟南芥）→ 下载基因组 + 建 bowtie2 索引 → 点「应用到 config.yaml」 |
| 5️⃣ | **运行管理** | 选项目、点运行按钮启动流程，页面实时刷新进度与日志 |
| 6️⃣ | **结果预览** | 跑完后在这里看 Peak 表格、注释饼图、信号热图 |

**第一次跑建议先用一个小样本试试**（1 个 IP + 1 个 Input，几十分钟就能看到结果）。

---

## 🛠 常用命令

装完后你的终端里多了 6 个命令：

```bash
chipseq-app            # 启动面板
chipseq-app-status     # 看当前运行状态
chipseq-app-logs       # 看实时日志（Ctrl+C 退出）
chipseq-app-stop       # 停止面板
chipseq-app-shell      # 进入环境的命令行（高级用户）
chipseq-app-update     # 升级到最新版本
```

启动时也可以指定端口或允许局域网访问：

```bash
chipseq-app --port 9000       # 换个端口
chipseq-app --public          # 让同网段的同事也能访问
```

---

## ❓ 遇到问题

| 现象 | 怎么办 |
|------|-------|
| `bootstrap.sh` 报「无可用运行时」 | 让管理员装 Apptainer/Singularity，或者你自己装 miniconda 后重跑 |
| `chipseq-app: command not found` | 上面第 2 步末尾那个 `export PATH=...` 没执行；重跑一次并 `source ~/.bashrc` |
| 浏览器打不开 `localhost:8501` | 服务器上用 `chipseq-app-status` 看是不是没启动；SSH 隧道断了重连一下 |
| 基因组下载很慢 | Ensembl/NCBI 国外源，可以「自定义物种」页面换成国内镜像 URL |
| 分析中途报错 | `chipseq-app-logs` 或到「🚀 运行管理」页看 Snakemake 日志，通常在 `logs/` 目录 |

其他问题：在项目 GitHub Issues 页面提问，或联系维护者。

---

## 🗑 卸载

```bash
bash scripts/uninstall.sh              # 只删程序，保留你的数据
bash scripts/uninstall.sh --purge-data # 连数据一起删（不可恢复！）
```

你的分析结果在 `~/.chipseq-pipeline/results/`，卸载前记得备份。
