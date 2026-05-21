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
| **R 4.0+** | ChIPseeker、clusterProfiler |
| **Python 3** | pandas、subprocess |
| **Perl** | 各步骤调度脚本 |

### 环境配置（一键安装）

项目根目录提供了完整的 [`environment.yml`](environment.yml)，可一键还原所有依赖：

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
