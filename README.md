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
├── bootstrap.sh         ← 装启动器 + CLI（分析环境靠 chipseq-app-update all，见第3步）
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
> - **conda 模式（默认推荐，无需容器）** 分两步：
>   - **① `CHIPSEQ_RUNTIME=conda ./bootstrap.sh`** 只创建一个**秒级精简启动器** `~/.local/chipseq-launch`（python + streamlit + pyyaml）把面板拉起来，并写标记、装 CLI——**不下载几个 GB、不建分析环境**。
>   - **② 命令行构建分析环境** `~/.local/chipseq-env`（全部生信**命令行工具**；**R 不在这里装**，走系统/管理员基线那个）：最省事 `chipseq-app-update all`（有离线包则秒级解压，否则在线 mamba/conda 构建）；或直接 `conda env create -p ~/.local/chipseq-env -f environment.yml`。嫌慢见下方「⚡ 更快的环境安装」。
>   - 浏览器「环境监测」页**只读**检测分析环境是否满足要求，**不再触发安装**（旧的在线「环境配置」页已移除）。
> - **容器模式（可选，仅当有真实镜像时）**：Apptainer / Podman / Docker 一次性给全量环境。⚠️ 仓库默认的 `ghcr.io/chipseq-pipeline/...` 是**从未发布的占位地址**，直接 `./bootstrap.sh` 走容器会报 **403 Forbidden**。要用容器需自备镜像：把离线包 `chipseq-pipeline-image.tar.zst` 放项目根目录（自动识别加载），或用 `CHIPSEQ_IMAGE=<真实可访问镜像>` 指定。
>
> 开发态直接 `streamlit run streamlit_app/app.py`（已激活含工具的 conda 环境）时，「环境监测」页会回落到当前环境检测，照常可用。

### 第 2 步：一键安装（启动器 + CLI，数秒）

```bash
CHIPSEQ_RUNTIME=conda ./bootstrap.sh
```

**这一步会做什么**（conda 方案仅数秒，不下载大环境）：

1. 创建**秒级精简启动器** `~/.local/chipseq-launch`（python + streamlit + pyyaml），负责把面板拉起来
2. 写好运行时/分析环境标记，把 `chipseq-app*` 命令装到 `~/.local/bin/`
3. **不构建分析环境**——分析环境（生信命令行工具）在第 3 步用命令行完成

装完后按提示把 `~/.local/bin` 加到 PATH：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

### 第 3 步：构建分析环境（全部生信命令行工具）

这是唯一“重”的一步（要下几个 GB）。**推荐一条命令**：

> ℹ️ 分析环境**只含命令行工具**，**故意不含 R**：流程与「环境监测」里的 `Rscript` 会直接解析到**系统 R**（管理员基线，已装满 ChIPseeker 等包），避免一个精简 `r-base` 遮蔽它。缺 R 包时补齐的是系统 R，不是分析环境。

```bash
chipseq-app-update all
```

它会：本地有离线包 `chipseq-env*.tar.gz` 则**秒级解压**；否则用 **mamba/conda 在线** `env create -p ~/.local/chipseq-env -f environment.yml`（已存在则 `update` 补缺）。等价手动命令：

```bash
conda env create -p ~/.local/chipseq-env -f environment.yml   # 首次
conda env update  -p ~/.local/chipseq-env -f environment.yml   # 已存在→补缺
# R 不在分析环境里；若「环境监测」显示缺 R 包，用流程实际调用的那个（系统）Rscript 补：
Rscript workflow/env/install_R_packages.R
```

> ⏩ **嫌 conda 求解慢？** 看下方「⚡ 更快的环境安装」：用 mamba / libmamba，或**一次打包、多台秒级解压**。

> 想走容器模式（需自备真实镜像，见上方说明）直接 `./bootstrap.sh`，会自动探测 Apptainer / Podman / Docker；无容器或默认镜像 403 会自动降级 conda。全程不需 `sudo`、不碰系统文件。

### 第 4 步：一键启动

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

### 第 5 步：打开浏览器

- **在自己电脑上跑**：直接打开 <http://localhost:8501>
- **通过 SSH 连到服务器跑**：在**本地电脑**新开一个终端，按上面提示执行 `ssh -L 8501:localhost:8501 用户名@服务器地址`，然后本地浏览器打开 <http://localhost:8501>

### 第 6 步：在浏览器里配置和运行

进入面板后，左侧导航按工作流分为「开始 / 配置 / 运行 / 结果」4 组、共 6 个页面，首次使用建议按下面顺序：

| 顺序 | 页面 | 做什么 |
|:---:|------|-------|
| 1️⃣ | **总览** | 看当前项目、配置是否可运行、管线进度与最近日志 |
| 2️⃣ | **配置编辑** | 填实验名、把 FASTQ 数据目录指过去、勾选要跑的分析模块 |
| 3️⃣ | **环境监测** | **只读**检测当前环境是否满足分析要求：逐项列出命令 / R 包 / 注释库的就位与版本，缺失时给出命令行修复指引（不触发任何安装） |
| 4️⃣ | **物种注释** | 选择物种（如 TAIR10 拟南芥）→ 下载基因组 + 建 bowtie2 索引 → 点「应用到 config.yaml」 |
| 5️⃣ | **运行管理** | 选项目、点运行按钮启动流程，页面实时刷新进度与日志 |
| 6️⃣ | **结果预览** | 跑完后在这里看 Peak 表格、注释饼图、信号热图 |

**第一次跑建议先用一个小样本试试**（1 个 IP + 1 个 Input，几十分钟就能看到结果）。

---

## ⚡ 更快的环境安装

第 3 步的“慢”几乎全来自经典 conda 的 **Solving environment**（30 个 bioconda 依赖回溯求解）。按由快到慢：

**方案 A（最快，多台/重装秒级）：conda-pack 离线包 —— 零求解**
在任意一台已建好分析环境的机器上打一次包，之后所有机器解压即用：
```bash
# 维护者机器（已建好 ~/.local/chipseq-env）
bash scripts/pack-analysis-env.sh              # 产出 chipseq-env-1.0.0.tar.gz（放仓库根 / 传 GitHub Release）
# 新机器
cp chipseq-env-1.0.0.tar.gz <仓库目录>/        # 或设 CHIPSEQ_ENV_URL 指向 Release 资产
chipseq-app-update offline                     # 秒级解压 + 自动 conda-unpack
```

**方案 B（在线提速）：mamba** —— 求解 + 并行下载快数倍
`chipseq-app-update all/online` 会自动优先用 mamba（若已安装）。装 mamba：
```bash
conda install -n base -c conda-forge mamba     # 或直接用自带 mamba 的 Miniforge
```

**方案 C（就地提速）：libmamba solver** —— conda ≥23.10 可换默认求解器，无需换工具
```bash
conda config --set solver libmamba             # conda 25.x 已默认；老版本先装 libmamba-solver
```

> 环境已建好时再次构建会走 `update`（补缺）而非推倒重来；只有 `offline` 或首次才全量。
> `environment.yml` 的包（bowtie2/samtools/macs2/bedtools/fastqc/trim-galore/picard/snakemake/deeptools…）与流程实际调用对齐；新增分析步骤用到新工具时，记得同步更新 `environment.yml` 与 `utils/env_check.py` 的监测基线。

---

## 🛠 常用命令

装完后你的终端里多了 7 个命令：

```bash
chipseq-app            # 启动面板
chipseq-app-status     # 看运行状态（含分析环境是否就绪）
chipseq-app-logs       # 看实时日志（Ctrl+C 退出）
chipseq-app-stop       # 停止面板
chipseq-app-shell      # 进入环境的命令行（高级用户）
chipseq-app-update     # 更新启动器；all/online/offline 同时构建分析环境（见第 3 步）
chipseq-app-uninstall  # 卸载 bootstrap 安装的启动器/分析环境/CLI（默认保留数据）
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
| `conda env create` / `chipseq-app-update all` 慢 | 多半是经典 conda 求解慢：装 mamba、或 `conda config --set solver libmamba`，或提前 `pack-analysis-env.sh` 打包走离线秒级解压（见「⚡ 更快的环境安装」） |
| 分析环境已建但监测仍显示缺 R 包 | 正常：分析环境故意不含 R，`Rscript` 走系统 R。缺的是系统 R 的包，用系统 `Rscript workflow/env/install_R_packages.R` 补，或让管理员补（`txdbmaker` 在 Bioc<3.21 为可选，不装不影响） |
| 分析中途报错 | `chipseq-app-logs` 或到「🚀 运行管理」页看 Snakemake 日志，通常在 `logs/` 目录 |
| 启动后 Xshell 自动弹出内置火狐、延迟很高 | 这是远程 X11 转发误拉远程浏览器的经典坑。新版 `chipseq-app` 已自动识别 SSH 会话不再弹；你仍可以在 Xshell 里“会话属性 > SSH > 隧道 > 取消 X11”关掉转发；或在本地新开终端 `ssh -L 8501:localhost:8501 用户@服务器` 后本地浏览器打开 http://localhost:8501。临时绕过：`chipseq-app --no-browser` |

其他问题：在项目 GitHub Issues 页面提问，或联系维护者。

---

## 🗑 卸载

优先用安装时自带的命令（默认删启动器 + 分析环境 + CLI + 后端副本，**保留数据**）：

```bash
chipseq-app-uninstall            # 先预览要删什么，输入 yes 确认
chipseq-app-uninstall --yes      # 跳过确认
chipseq-app-uninstall --data     # 连数据目录 ~/.chipseq-pipeline 一起删（不可逆！）
```

> 没装命令、或想直接从仓库删：`bash scripts/uninstall.sh`（加 `--purge-data` 连数据删）。

你的参考基因组/结果/配置都在 `~/.chipseq-pipeline/`，上面默认不会动它；只删程序、保留数据后，重跑 `bootstrap.sh` 即可恢复。
