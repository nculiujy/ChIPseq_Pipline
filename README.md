# ChIPseq Pipeline

ChIP-seq / CUT&Tag 数据分析流程。Snakemake + Streamlit，浏览器操作。所有安装产物（conda 环境、包缓存、R 库、数据）都封装在项目仓库内的 `.chipseq-runtime/`，不污染你 HOME 里的 conda / R / pip。

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

**默认监听 `0.0.0.0`（对整个局域网开放）**，所以从同一局域网内的自己 Windows/Mac 浏览器直接开启动日志里打印的「局域网访问」那条地址（`http://<服务器IP>:8501`，不是 localhost）即可。只想绑回环（仅本机/隧道）时用 `chipseq-app --private`。

服务器在公网上 / 不想暴露局域网时，改用 SSH 隧道：本地机器执行 `ssh -L 8501:localhost:8501 用户@服务器`，本地浏览器再开 <http://localhost:8501>。

用 Xshell / PuTTY 请关掉 X11 转发（会话属性 → SSH → 隧道 → 取消勾选）。`chipseq-app` 已自动识别 SSH 会话不再拉远程浏览器，但关掉转发更保险。

### 云服务器 / 堡垒机：cloudflared 公网临时隧道（断网不死）

拿不到局域网 IP、SSH 隧道也不方便时，加 `--tunnel`，面板就绪后会自动在后台拉起一个 cloudflared 快隧道，并打印一个 `https://xxx.trycloudflare.com` 公网地址，任何地方浏览器直接打开：

```bash
# 首次：下载 cloudflared 到仓库根目录（或放 PATH，或用 CHIPSEQ_CLOUDFLARED 指定绝对路径）
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64

# 每次：
chipseq-app --tunnel
```

Streamlit 和 cloudflared 都以 `setsid nohup` 脱离终端后台运行，**断网 / 关 Xshell 都不会停**；`chipseq-app-status` 会显示当前隧道 URL，`chipseq-app-stop` 会把面板和隧道一起关掉。注意：快隧道的 URL 每次重启会变（要固定域名需 Cloudflare 命名隧道），且该地址**公网可访问**，用完记得关。


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

启动选项：`chipseq-app --port 9000`、`chipseq-app --private`（只绑 127.0.0.1，默认已局域网开放，`--public` 同义）、`chipseq-app --tunnel`（cloudflared 公网临时 URL）、`chipseq-app --no-browser`。

## 卸载

```bash
chipseq-app-uninstall              预览要删什么，输 yes 确认
chipseq-app-uninstall --yes        跳过确认
chipseq-app-uninstall --data       连项目内数据目录一起删（参考基因组/结果/配置，不可逆）
chipseq-app-uninstall --purge-legacy  一并删旧版本 HOME 侧残留（~/.local/chipseq-*、~/.chipseq-pipeline）
chipseq-app-uninstall --all        = --data + --purge-legacy
```

默认只删：`<repo>/.chipseq-runtime/` 下的 launch/analysis/pkgs/Rlib/cache/bin/lib/runtime/assets，`~/.local/bin/chipseq-*` 软链接，以及 `~/.conda/environments.txt` 中属于本项目的行。**数据目录** `<repo>/.chipseq-runtime/data/` 保留。

直接 `rm -rf <repo>` 是终极卸载（一切都在仓库内），但会连数据一起丢。

## 目录位置

本应用把安装的一切封装在项目仓库内，不会往你 HOME 的 conda / R / pip 里写东西：

```
<repo>/                             仓库本体
├── bootstrap.sh
├── config/                         开发用默认配置
├── workflow/anno/                  基因组注释 + bowtie2 索引
├── workflow/resources/             FASTQ 原始数据
├── result/                         分析结果
├── logs/                           Snakemake 日志
├── renv/library/                   R 包库（开发态）
├── .snakemake/                     Snakemake 内部缓存
└── .chipseq-runtime/               本应用安装的一切（.gitignore）
    ├── launch/                     启动器 prefix env（python + streamlit + pyyaml）
    ├── analysis/                   分析 prefix env（生信命令行 + r-base + compilers）
    │   └── lib/R/library/          R 包库（ChIPseeker/clusterProfiler/ggtree…）
    ├── data/                       用户数据（config / references / annotations / results / logs）
    ├── pkgs/                       conda 包缓存（不写 $CONDA_ROOT/pkgs）
    ├── cache/                      pip 缓存 + install.packages 临时目录
    └── bin/ lib/ runtime/ assets/  CLI 实体与共享脚本
```

HOME 侧只碰两个地方：

- `~/.local/bin/chipseq-app*` — 7 个软链接（卸载会自动删）
- `~/.conda/environments.txt` — conda `-p` 会静默追加；本项目在安装/卸载/更新后自动清除相关行

R 运行时和流程用到的 R 包（ChIPseeker / rtracklayer / GenomicFeatures / GenomicRanges / clusterProfiler / txdbmaker + 9 个 CRAN 包）**都写在 `environment.yml` 里**，走 bioconda/conda-forge 预编译二进制，`conda env create` 一条命令装齐，不需要编译器、也不用现编源码。`install_R_packages.R` 保留作兜底：环境已齐时秒过，只有真缺包时才回退到源码安装。

> 注：`bioconductor-*` 是按 r45 构建的，求解器会把 `r-base` 定在 **4.5.x（Bioc 3.22）**；`environment.yml` 里的 `compilers` 仅留给临时 `install.packages` 使用，非必需。

## 遇到问题

| 现象 | 处理 |
|---|---|
| `bootstrap.sh` 报「无可用运行时」 | 装 miniconda 或 miniforge 后重跑 |
| `chipseq-app: command not found` | 上面那个 `export PATH=...` 没生效；执行后 `source ~/.bashrc` |
| 打不开 `localhost:8501` | 先看启动时打印的「局域网访问」URL，用服务器 IP 而不是 localhost（localhost 永远指你自己本机）；`chipseq-app-status` 看进程；确认没跑 `--private`（那会只绑 127.0.0.1）；服务器在公网则重做 SSH 隧道 |
| `chipseq-app-update all` 慢 | 经典 conda 求解慢。装 mamba（`conda install -n base -c conda-forge mamba`），或 `conda config --set solver libmamba`，或用离线包（见下） |
| 监测页显示缺 R 包 | 先确认环境是**最新 `environment.yml`** 建的（`git pull` 后跑 `chipseq-app-update all`，R 包由 conda 一次性装齐）。若仍缺（旧环境/手动改动导致），可只补 R：`bash -c 'source scripts/lib/common.sh && _chipseq_export_isolation_env && source runtime/conda.sh && _install_R_packages_best_effort'`。若日志里出现 `x86_64-conda-linux-gnu-cc: command not found`，是环境缺编译器（源码回退路径），补 `conda env update -p <analysis> -f environment.yml` 即可 |
| 启动后 Xshell 弹自带火狐、延迟高 | 关 Xshell 的 X11 转发；临时绕过 `chipseq-app --no-browser` |
| `--tunnel` 报找不到 cloudflared | 按上面 `wget` 下载 `cloudflared-linux-amd64` 到仓库根目录并 `chmod +x`；或 `export CHIPSEQ_CLOUDFLARED=/绝对路径`。云服务器连不上 GitHub 时，本地下好再传上去 |
| 分析中途报错 | `chipseq-app-logs`，或运行管理页看 Snakemake 日志（在 `logs/`） |
| 从旧版本升级（环境在 `~/.local/chipseq-*` / 数据在 `~/.chipseq-pipeline`） | `bootstrap.sh` 会提示迁移。推荐：`mv ~/.local/chipseq-launch <repo>/.chipseq-runtime/launch && mv ~/.local/chipseq-env <repo>/.chipseq-runtime/analysis && mv ~/.chipseq-pipeline <repo>/.chipseq-runtime/data`；确认新版能跑后再 `chipseq-app-uninstall --purge-legacy` 收尾 |
| 不想污染 HOME，但项目所在盘空间不够 | 把仓库整个搬到大盘；或临时 `export CHIPSEQ_RUNTIME_DIR=/scratch/$USER/chipseq-runtime` 后再跑 bootstrap，一切写入（env/pkgs/R 库/data）都会落到该目录 |

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
