ChIPseq_Pipline：基于 Snakemake 的 ChIP-seq / CUT&Tag 自动化分析流程
=======================================================================

## 简介

**ChIPseq_Pipline** 是一套基于 **Snakemake** 构建的模块化、可配置的 ChIP-seq / CUT&Tag 数据分析流程。该流程支持多物种（拟南芥 TAIR10、人类 GRCh38 等）、多实验项目的并行处理，只需修改配置文件即可灵活切换分析模式。

### 主要功能

- 自动化比对：基于 Bowtie2 完成双端测序数据比对，Picard 去重
- BAM 转换：自动将 BAM 文件转换为 BigWig 信号轨道文件
- Peak 鉴定：调用 MACS2 进行 narrow/broad peak calling，支持 pvalue/qvalue 阈值切换
- Peak 注释：使用 ChIPseeker（R）进行基因组区域注释与可视化
- 信号热图：基于 deepTools 绘制各样本的信号热图和 Profile 图
- 多项目支持：单个配置文件中可同时定义多个物种/实验项目，独立控制各模块开关

---

## 分析流程

```
原始 FASTQ 数据
      │
      ▼
  [可选] 质控 (2_QC)
   fastp / Trim Galore
      │
      ▼
  Step1: 比对 & 过滤 (3_ChIPseq)
   Bowtie2 → SAMtools → Picard 去重
      │
      ▼
  Step2: BAM → BigWig (3_ChIPseq)
   bamCoverage (deepTools)
      │
      ├──────────────────────┐
      ▼                      ▼
Peak 鉴定 (4_analyse)   deepTools 热图 (4_analyse)
  MACS2 broad/narrow      computeMatrix + plotHeatmap
      │
      ▼
Peak 注释 (4_analyse)
  ChIPseeker (R)
  饼图 + 注释表格
```

---

## 目录结构

```
ChIPseq_Pipline/
├── snakefile                     # 流程入口，读取 config 并汇总所有目标文件
├── README.md                     # 项目说明文档
├── .gitignore                    # Git 忽略规则（大文件、结果目录等）
│
├── config/                       # 配置中心（用户修改区域）
│   ├── config.yaml               # 全局参数与各项目/模块开关
│   └── metadata.csv              # IP 与 Input 样本配对表
│
├── workflow/                     # 核心流程逻辑
│   ├── rules/                    # Snakemake 规则文件
│   │   ├── 1_download.smk        # 数据下载模块
│   │   ├── 2_QC.smk              # 质控模块
│   │   ├── 3_ChIPseq.smk         # 比对、去重、BAM→BigWig
│   │   └── 4_analyse.smk         # Peak calling、注释、deepTools
│   │
│   ├── scripts/                  # 各步骤执行脚本
│   │   ├── 1_download.py         # SRA 数据下载
│   │   ├── 2_1_QC.pl             # 质控脚本
│   │   ├── 3_1_ChIPseq.pl        # 比对与去重
│   │   ├── 3_2_bamtobwfile.pl    # BAM 转 BigWig
│   │   ├── 4_1_peakcalling.pl    # MACS2 peak calling
│   │   ├── 4_2_annoChIPPeaks.R   # ChIPseeker 注释
│   │   └── 4_3_deeptools.py      # deepTools 热图
│   │
│   ├── anno/                     # 基因组注释文件（本地准备，不上传）
│   │   ├── Bowtie2anno_TAIR10/       # 拟南芥 TAIR10 Bowtie2 索引
│   │   ├── Bowtie2anno_TAIR10_arabidopsis/
│   │   └── Bowtie2anno_GRCh38/       # 人类 GRCh38 Bowtie2 索引
│   │
│   └── resources/                # 原始测序数据（本地准备，不上传）
│       ├── TAIR10/               # 拟南芥实验数据
│       └── homo/                 # 人类实验数据
│
├── result/                       # 分析结果输出（自动生成，不上传）
│   └── {species}/{experiment}/
│       ├── 3_ChIPseq/            # BAM、去重结果、BigWig
│       └── 4_analyse/
│           ├── peakcalling/      # MACS2 peak 文件
│           ├── annoChIPPeaks/    # 注释 CSV 和饼图
│           └── deeptools/        # 热图、Profile 图
│
└── logs/                         # 各步骤日志（自动生成，不上传）
```

---

## 安装与依赖

### 软件依赖

| 软件 | 用途 |
|------|------|
| Snakemake | 流程管理 |
| Bowtie2 | 短序列比对 |
| SAMtools | BAM 文件处理 |
| Picard | 去重 |
| MACS2 | Peak calling |
| deepTools | BigWig 生成与热图 |
| fastp / Trim Galore | 质控（可选） |
| **R 4.4 + Bioc 3.20** | ChIPseeker、TxDb.*、org.*.db |
| **Python 3** | pandas、subprocess |
| **Perl** | 各步骤调度脚本 |

### 新服务器一键部署

```bash
git clone https://github.com/YOUR_USERNAME/ChIPseq_Pipline.git
cd ChIPseq_Pipline
bash workflow/env/setup_new_server.sh
```

该脚本依次执行：

1. `conda env create -f environment.yml` — 还原 Python/Perl/bioinformatics 工具
2. `Rscript workflow/env/install_R_packages.R` — 安装锁定版本的 R/Bioconductor 包
3. 验证 bowtie2、samtools、macs2、snakemake、deeptools 是否可用

### R 包版本说明

R 包不在 `environment.yml` 中管理（Bioconductor 包与 conda 适配性差），
改由 [`workflow/env/install_R_packages.R`](workflow/env/install_R_packages.R) 集中锁定版本：

| 包 | 版本 | 来源 |
|----|------|------|
| ggplot2 | 3.5.1 | CRAN |
| dplyr | 1.1.4 | CRAN |
| pheatmap | 1.0.12 | CRAN |
| ggrepel | 0.9.5 | CRAN |
| ChIPseeker | 1.42.x (Bioc 3.20) | Bioconductor |
| rtracklayer | 1.66.x | Bioconductor |
| GenomicFeatures | 1.58.x | Bioconductor |
| TxDb.Athaliana.BioMart.plantsmart28 | — | Bioconductor |
| TxDb.Hsapiens.UCSC.hg38.knownGene | — | Bioconductor |
| org.At.tair.db / org.Hs.eg.db | — | Bioconductor |

> 物种注释数据库体积较大（各 ~200–500 MB），首次安装需要时间，之后 conda 环境内缓存复用。

### 仅重建 conda 环境（不重装 R 包）

```bash
conda env create -f environment.yml
conda activate ChIPseq_Pipline
```

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/ChIPseq_Pipline.git
cd ChIPseq_Pipline
```

### 2. 准备参考基因组索引

将 Bowtie2 索引文件放置在 `workflow/anno/` 对应子目录下：

```
workflow/anno/Bowtie2anno_TAIR10/TAIR10.*.bt2
workflow/anno/Bowtie2anno_GRCh38/GRCh38_noalt_as.*.bt2
```

### 3. 准备原始数据

将 `.fastq.gz` 双端测序文件放置在 `workflow/resources/` 对应物种和实验目录下，命名格式为：

```
{样本名}_1.clean.fq.gz
{样本名}_2.clean.fq.gz
```

### 4. 修改配置文件

**`config/config.yaml`** — 全局参数与项目列表：

```yaml
picard_dir: "/path/to/picard-2.18.2"   # Picard 安装路径
threads: 8                              # 并行线程数

macs2_peak_type: "broad"               # narrow 或 broad
macs2_cutoff_type: "pvalue"            # pvalue 或 qvalue
macs2_cutoff_value: 0.05
macs2_broad_cutoff: 0.1
normalization_method: "BPM"

projects:
  - species: "TAIR"
    experiment: "MyExperiment"
    rawdata_dir: "workflow/resources/TAIR10/MyExperiment"
    index_dir: "workflow/anno/Bowtie2anno_TAIR10/TAIR10"
    txdb: "TxDb.Athaliana.BioMart.plantsmart28"
    orgdb: "org.At.tair.db"
    modules:
      1_download: false
      2_QC: false
      3_ChIPseq: true
      4_analyse: true
```

**`config/metadata.csv`** — IP 与 Input 样本配对：

```csv
IP sample,Input
IP_rep1,INPUT_1
IP_rep2,INPUT_1
IP_rep3,INPUT_2
```

> `IP sample` 和 `Input` 列填写样本文件名前缀（不含 `_1.clean.fq.gz` 后缀）。

### 5. 运行流程

```bash
# 试运行（查看将执行哪些规则，不实际运行）
snakemake -n

# 正式运行（使用 8 个核心）
snakemake -c 8 --rerun-incomplete

# 强制重新运行所有步骤
snakemake -c 8 --forceall
```

---

## 配置说明

### 模块开关

每个 project 可以独立控制各分析模块：

| 模块键 | 功能 | 说明 |
|--------|------|------|
| `1_download` | 数据下载 | 从 SRA 下载原始数据 |
| `2_QC` | 质量控制 | fastp / Trim Galore |
| `3_ChIPseq` | 比对分析 | Bowtie2 比对 + Picard 去重 + BigWig |
| `4_analyse` | Peak 分析 | MACS2 + ChIPseeker + deepTools |

设为 `true` 开启，`false` 关闭。

### 支持物种

| `species` 配置值 | 对应生物 | MACS2 基因组参数 |
|-----------------|---------|----------------|
| `TAIR` | 拟南芥 | `1.2e8` |
| `homo` | 人类 | `hs` |
| `mm` | 小鼠 | `mm` |

### deepTools 配置

deepTools 模块提供灵活的可视化配置，详细说明见 [`workflow/scripts/deeptools_config_guide.md`](workflow/scripts/deeptools_config_guide.md)。

#### computeMatrix 模式

**reference-point 模式**（推荐用于 ChIP-seq peaks）：

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "center"    # center (peak中心), TSS, TES
    upstream: 2000     # 上游距离 (bp)
    downstream: 2000   # 下游距离 (bp)
```

**scale-regions 模式**（推荐用于基因体分析）：

```yaml
deeptools:
  matrix_mode: "scale-regions"
  scale_regions:
    region_length: 5000     # 缩放后的统一长度
    upstream: 2000          # 区域上游延伸
    downstream: 2000        # 区域下游延伸
```

#### BED 文件来源

控制分析的背景区域来源：

| 选项 | 说明 | 适用场景 |
|------|------|---------|
| `macs2_peaks` | 使用 MACS2 输出的 peaks（每对样本用自己的） | 独立评估每对样本 |
| `union_peaks` | 合并所有样本的 peaks 作为统一区域集 | 跨样本比较 |
| `custom` | 使用自定义 BED 文件 | 分析特定区域（增强子、启动子等） |
| `genes` | 从 GTF 文件提取基因区域 | 全基因组系统性分析 |

**示例：跨样本比较配置**

```yaml
deeptools:
  matrix_mode: "reference-point"
  bed_source: "union_peaks"  # 所有样本使用统一的 peaks 合集
  reference_point:
    point: "center"
    upstream: 2000
    downstream: 2000
```

**示例：分析启动子区域**

```yaml
deeptools:
  matrix_mode: "reference-point"
  bed_source: "genes"
  reference_point:
    point: "TSS"
    upstream: 2000
    downstream: 1000
  gtf_files:
    TAIR: "workflow/anno/TAIR10_GFF3_genes.gtf"
    homo: "workflow/anno/gencode.v38.annotation.gtf"
```

#### 可视化参数

```yaml
deeptools:
  bin_size: 10              # bin 大小 (bp)
  missing_data_as_zero: true
  
  plot:
    color_map: "RdYlBu_r"   # 热图配色方案
    z_min: 0                # 最小值 (或 "auto")
    z_max: "auto"           # 最大值
    interpolation: "bilinear"
```

常用配色方案：`RdYlBu_r`, `viridis`, `plasma`, `coolwarm`, `Reds`, `Blues`

---

## 输出结果

### 3_ChIPseq 目录

| 文件类型 | 说明 |
|---------|------|
| `*.sorted.bam` | 排序后的 BAM 文件 |
| `*.sorted.rmdup.bam` | 去重后的 BAM 文件 |
| `*.bw` | BigWig 信号轨道文件（可在 IGV 中可视化） |

### 4_analyse 目录

| 子目录/文件 | 说明 |
|-----------|------|
| `peakcalling/{pair}/` | MACS2 输出的 peak 文件（`.broadPeak` 或 `.narrowPeak`） |
| `annoChIPPeaks/{pair}/*_peak_anno.csv` | Peak 基因组注释表格 |
| `annoChIPPeaks/{pair}/*_pie_bp±2000.pdf` | Peak 分布饼图 |
| `deeptools/` | 热图（heatmap）与 Profile 图 |

---

## Streamlit 管理面板

本项目集成了基于 **Streamlit** 的 Web 管理面板，无需记忆命令行即可完成配置、运行和结果查看。

### 文件结构

```
streamlit_app/
├── app.py                    # 主页（项目概览、系统信息）
├── .streamlit/
│   └── config.toml           # Streamlit 主题配置
└── pages/
    ├── 1_⚙️_配置编辑.py      # 可视化编辑 config.yaml / metadata.csv
    ├── 2_🚀_运行管理.py      # 流程状态监控、启动/停止 Snakemake
    └── 3_📊_结果预览.py      # 查看分析产出（表格、图片、PDF）
```

### 启动面板

```bash
# 1. 激活 conda 环境
conda activate ChIPseq_Pipline

# 2. 启动（默认监听 8501 端口）
streamlit run streamlit_app/app.py --server.port 8501
```

> Streamlit 已包含在 `environment.yml` 中，`conda env create` 后无需额外安装。

### 功能详解

#### ⚙️ 配置编辑

- 以表单形式编辑 `config/config.yaml` 中的全局参数（线程数、MACS2 模式、deepTools 选项等）
- 可视化管理 **项目列表**：添加/删除项目，配置物种、实验名、数据路径、模块开关
- 编辑 `config/metadata.csv` 中的 IP/Input 样本配对
- 内置 **deepTools 高级配置**：矩阵模式（reference-point / scale-regions）、BED 来源、配色方案等
- 保存后立即写入文件，下次 `snakemake` 调用时生效

#### 🚀 运行管理

- **流程状态总览**：实时检测各模块的 `*_finished.txt` 标记文件，显示已完成 / 运行中 / 待运行 / 未启用
- **进程检测**：自动识别系统中是否有 snakemake 进程在运行，状态随任务推进更新
- **自动刷新**：支持 15 秒轮询刷新，或手动点击刷新
- **一键操作**：dry-run 预览、正式运行、强制全部重跑
- **日志查看**：按时间排序列出所有日志文件，可查看最后 N 行内容
- **常用命令速查**：列出常见 Snakemake 操作命令

#### 📊 结果预览

- **Peak 注释表格**：展示 ChIPseeker 输出的 `*_peak_anno.csv`，支持搜索和下载
- **图片浏览**：预览 deepTools 热图、Profile 图、饼图等 PNG/PDF 文件
- **PDF 下载**：对于 PDF 格式图片提供直接下载链接

### 远程服务器访问

面板通常运行在没有图形界面的远程服务器上，通过 SSH 隧道即可在本地浏览器中使用：

```bash
# 本地终端执行
ssh -L 8501:localhost:8501 user@server_ip

# 然后在本地浏览器打开
# http://localhost:8501
```

### 注意事项

- Streamlit 面板是 **只读监控 + 配置编辑** 工具，不会自动触发 snakemake（需要你手动点击运行按钮）
- 面板通过 `pgrep` 检测 snakemake 进程状态，如果流程在其他 session/用户运行，也会被检测到
- 配置保存后会直接覆盖 `config/config.yaml`，建议在保存前用 `git diff` 确认变更

---

## 注意事项

1. **大文件不上传**：原始 FASTQ 数据、Bowtie2 索引（`.bt2`）、基因组 FASTA 文件体积过大，已在 `.gitignore` 中排除，需本地自行准备。
2. **Picard 路径**：请在 `config.yaml` 中修改 `picard_dir` 为本机实际安装路径。
3. **路径中文字符**：如果 `rawdata_dir` 包含中文字符，请确保系统 locale 支持 UTF-8（`export LANG=zh_CN.UTF-8`）。
4. **多项目并行**：在 `projects` 列表中添加多个条目，Snakemake 会自动并行处理，互不干扰。

---

## 修改日志

详见 [修改日志.md](修改日志.md)。

---

## 引用软件

- [Snakemake](https://snakemake.readthedocs.io/)
- [Bowtie2](http://bowtie-bio.sourceforge.net/bowtie2/)
- [Picard](https://broadinstitute.github.io/picard/)
- [MACS2](https://github.com/macs3-project/MACS)
- [deepTools](https://deeptools.readthedocs.io/)
- [ChIPseeker](https://bioconductor.org/packages/ChIPseeker/)
