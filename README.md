# ChIPseq Pipeline

ChIP-seq / CUT&Tag 数据分析流程。Snakemake + Streamlit，浏览器操作。

## 安装

前置：Linux 机器，装了 conda 或 mamba，能上网，≥ 20 GB 空闲磁盘，≥ 16 GB 内存。不需要 root。

```bash
git clone https://github.com/nculiujy/ChIPseq_Pipline.git
cd ChIPseq_Pipline
CHIPSEQ_RUNTIME=conda ./bootstrap.sh --full
```

一条命令完成：启动器 + CLI + 分析环境 + R 包。首次 10–30 分钟（下载几个 GB），后续幂等，已装部分秒过。

可选环境变量：

- `CHIPSEQ_SKIP_R=1`：只装命令行工具，跳过 R 包
- `CHIPSEQ_R_ANNO=1`：一并预装 TxDb/OrgDb 注释数据库（体积大）
- `CHIPSEQ_CRAN_MIRROR=<URL>`：换 CRAN 镜像

装完若 `chipseq-app` 命令找不到，把 `~/.local/bin` 加进 PATH：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

## 启动

```bash
chipseq-app
```

本机浏览器打开 <http://localhost:8501>。

远程服务器：本地机器执行 `ssh -L 8501:localhost:8501 用户@服务器`，本地浏览器打开 <http://localhost:8501>。

用 Xshell / PuTTY 请关掉 X11 转发（会话属性 → SSH → 隧道 → 取消勾选）。`chipseq-app` 已自动识别 SSH 会话不再拉远程浏览器，但关掉转发更保险。

## 页面

浏览器面板 6 个页面，按工作流顺序：

| 页面 | 用途 |
|---|---|
| 总览 | 项目状态、配置是否可运行、最近日志 |
| 配置编辑 | 填实验名、指定 FASTQ 目录、勾选分析步骤 |
| 物种注释 | 选物种 → 下载基因组 + 建 bowtie2 索引 → 应用到 config |
| 环境监测 | 只读；列出命令 / R 包 / 注释库就位情况，缺失时给修复命令 |
| 运行管理 | 启动 Snakemake 流程，看实时日志与进度 |
| 结果预览 | Peak 表、注释饼图、信号热图 |

第一次建议先用 1 IP + 1 Input 的小样本跑通。

## 命令

```bash
chipseq-app             启动面板
chipseq-app-logs        实时日志（Ctrl+C 退出）
chipseq-app-status      运行状态（含分析环境是否就绪）
chipseq-app-stop        停止面板
chipseq-app-shell       进入分析环境的 shell
chipseq-app-update      更新启动器；all/analysis/online/offline 一并建分析环境 + 补 R 包
chipseq-app-uninstall   卸载（默认保留数据目录）
```

启动选项：`chipseq-app --port 9000`、`chipseq-app --public`（允许局域网）、`chipseq-app --no-browser`。

## 卸载

```bash
chipseq-app-uninstall            预览要删什么，输 yes 确认
chipseq-app-uninstall --yes      跳过确认
chipseq-app-uninstall --data     连 ~/.chipseq-pipeline 数据一起删（不可逆）
```

## 目录位置

```
ChIPseq_Pipline/
├── bootstrap.sh
├── config/                  配置（浏览器里编辑）
├── workflow/anno/           基因组注释
└── workflow/resources/      FASTQ 原始数据

~/.chipseq-pipeline/         数据目录：结果、日志、marker
~/.local/chipseq-launch/     启动器环境（python + streamlit + pyyaml）
~/.local/chipseq-env/        分析环境（生信命令行工具，不含 R）
```

分析环境故意不含 R。`Rscript` 走系统 R（一般管理员装的 R 4.4.x），避免一个精简 `r-base` 遮蔽系统那个装满 ChIPseeker 等包的 R。缺包用系统 `Rscript workflow/env/install_R_packages.R` 补。

## 遇到问题

| 现象 | 处理 |
|---|---|
| `bootstrap.sh` 报「无可用运行时」 | 装 miniconda 或 miniforge 后重跑 |
| `chipseq-app: command not found` | 上面那个 `export PATH=...` 没生效；执行后 `source ~/.bashrc` |
| 打不开 `localhost:8501` | `chipseq-app-status` 看进程；SSH 隧道断了重连 |
| `chipseq-app-update all` 慢 | 经典 conda 求解慢。装 mamba（`conda install -n base -c conda-forge mamba`），或 `conda config --set solver libmamba`，或用离线包（见下） |
| 分析环境已建但监测显示缺 R 包 | 正常：分析环境不含 R。用系统 `Rscript workflow/env/install_R_packages.R` 补；`txdbmaker` 在 Bioc<3.21 是可选，不装不影响 |
| 启动后 Xshell 弹自带火狐、延迟高 | 关 Xshell 的 X11 转发；临时绕过 `chipseq-app --no-browser` |
| 分析中途报错 | `chipseq-app-logs`，或运行管理页看 Snakemake 日志（在 `logs/`） |

其他问题提 GitHub Issues。

## 进阶：离线包 / 多台机器部署

在一台已建好分析环境的机器上打包，其余机器秒级解压，完全跳过 conda 求解：

```bash
# 打包机
bash scripts/pack-analysis-env.sh                 # 产出 chipseq-env-<ver>.tar.gz

# 目标机
cp chipseq-env-<ver>.tar.gz <仓库目录>/           # 或设 CHIPSEQ_ENV_URL 指向 Release
CHIPSEQ_RUNTIME=conda ./bootstrap.sh --full       # 检测到本地 tar 自动走离线解压
# 或显式重装：chipseq-app-update offline
```

## 容器模式（可选）

`./bootstrap.sh` 不带 `CHIPSEQ_RUNTIME=conda` 时会自动探测 Apptainer / Podman / Docker。仓库默认的 `ghcr.io/chipseq-pipeline/...` 是占位地址、未发布，直接拉容器会 403。要用容器需自备镜像：

- 把 `chipseq-pipeline-image.tar.zst` 或 `chipseq-pipeline.sif` 放到项目根目录（自动识别加载）
- 或 `CHIPSEQ_IMAGE=<真实镜像> ./bootstrap.sh`

容器不可达时会自动降级到 conda。
