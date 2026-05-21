# Bowtie2 注释文件 (Index) 下载与配置指南

本目录 (`workflow/anno`) 用于统一存放 ChIP-seq 流程中 Bowtie2 比对步骤所需的参考基因组索引文件。

根据流程的设计，目前主要支持以下三类核心物种。本教程将指导您如何获取并配置这三个物种的注释文件。

---

## 1. 拟南芥 (TAIR / TAIR10)

官方并没有为拟南芥提供像人类和小鼠那样直接下载的预编译 Bowtie2 索引，因此我们需要从 Ensembl Plants 数据库下载基因组 FASTA 文件，并使用 `bowtie2-build` 命令自行构建。

### 下载与构建步骤
在终端中依次运行以下命令：
```bash
# 1. 进入注释文件存放目录
cd workflow/anno

# 2. 创建并进入专门存放拟南芥注释的文件夹
mkdir -p Bowtie2anno_TAIR10
cd Bowtie2anno_TAIR10

# 3. 从 Ensembl Plants 下载拟南芥 (TAIR10) 基因组的 FASTA 压缩文件
wget https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/release-56/fasta/arabidopsis_thaliana/dna/Arabidopsis_thaliana.TAIR10.dna.toplevel.fa.gz

# 4. 解压 FASTA 文件
gunzip Arabidopsis_thaliana.TAIR10.dna.toplevel.fa.gz

# 5. 使用 bowtie2-build 构建索引，这可能需要花费几分钟时间
# 格式: bowtie2-build <输入fasta> <输出索引前缀>
bowtie2-build Arabidopsis_thaliana.TAIR10.dna.toplevel.fa TAIR10

# 6. 构建完成后，可以删除原始的 FASTA 文件以节省空间（可选）
rm Arabidopsis_thaliana.TAIR10.dna.toplevel.fa
```
构建完成后，该目录下会生成以 `TAIR10` 为前缀的 `.bt2` 索引文件。

### 拟南芥基因组注释文件（GFF3）配置

ChIPseeker 注释步骤支持两种注释来源，在 `config.yaml` 的 `txdb` 字段中填写对应值即可自动切换：

#### 方式 1：使用 Bioconductor 注释包（推荐，无需下载文件）
在 R 环境中安装：
```r
BiocManager::install("TxDb.Athaliana.BioMart.plantsmart28")
BiocManager::install("org.At.tair.db")
```

`config.yaml` 配置：
```yaml
txdb: "TxDb.Athaliana.BioMart.plantsmart28"
orgdb: "org.At.tair.db"
```

#### 方式 2：使用 TAIR 官方 GFF3 文件（注释更新）
下载 GFF3 文件到 `workflow/anno/` 目录：
```bash
cd workflow/anno
wget "https://www.arabidopsis.org/api/download-files/download?filePath=Genes/TAIR10_genome_release/TAIR10_gff3/TAIR10_GFF3_genes.gff" -O TAIR10_GFF3_genes.gff
```

`config.yaml` 配置（填写文件路径即可，脚本自动识别）：
```yaml
txdb: "workflow/anno/TAIR10_GFF3_genes.gff"
orgdb: "org.At.tair.db"
```

> 脚本会自动判断 `txdb` 是包名还是文件路径：若文件存在则调用 `makeTxDbFromGFF()` 构建，否则按 Bioconductor 包名加载。

---

## 2. 人类 (homo / GRCh38)

对于人类基因组，我们推荐使用 Illumina 或 Johns Hopkins University 提供的官方预编译 Bowtie2 索引，这样可以节省大量的本地编译时间。

### 下载与解压步骤
在终端中依次运行以下命令：
```bash
# 进入注释文件存放目录
cd workflow/anno

# 下载官方提供的 GRCh38 (hg38) 预编译索引包
wget https://genome-idx.s3.amazonaws.com/bt/GRCh38_noalt_as.zip

# 解压文件 (解压后会生成 GRCh38_noalt_as 文件夹)
unzip GRCh38_noalt_as.zip

# 删除压缩包以节省空间
rm GRCh38_noalt_as.zip
```

- **`config.yaml` 配置**：
  ```yaml
  species: "homo"
  index_dir: "workflow/anno/GRCh38_noalt_as/GRCh38_noalt_as"
  ```

---

## 3. 小鼠 (mm / mm10)

与人类基因组类似，小鼠基因组（mm10）也提供了官方预编译的索引文件，可直接下载使用。

### 下载与解压步骤
在终端中依次运行以下命令：
```bash
# 进入注释文件存放目录
cd workflow/anno

# 下载官方提供的 mm10 预编译索引包
wget https://genome-idx.s3.amazonaws.com/bt/mm10.zip

# 解压文件 (解压后 .bt2 文件将直接释放到当前目录)
unzip mm10.zip

# 删除压缩包以节省空间
rm mm10.zip
```

- **`config.yaml` 配置**：
  ```yaml
  species: "mm"
  index_dir: "workflow/anno/mm10"
  ```

---

## 总结

无论您分析的是哪种物种，都需要在 `config/config.yaml` 中正确设置 `species` 和 `index_dir` 两个参数：
1. `species` 决定了下游分析（如 Peak Calling、注释等）所调用的物种特异性数据库。
2. `index_dir` 是 Bowtie2 索引文件的**绝对或相对路径前缀**（注意：**不要**包含 `.1.bt2` 等后缀）。