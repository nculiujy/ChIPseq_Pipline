# ChIPseq_Pipline 环境配置指南

本目录 (`workflow/env`) 包含了运行该 ChIP-seq 分析流程所需的所有依赖环境配置文件。为了确保流程在不同计算节点或新系统中能够稳定重现，我们提供了完整的 Conda 环境导出文件 `ChIPseq_Pipline_env.yml`。

## 1. 环境依赖说明

该环境主要包含了以下关键的生物信息学工具和数据分析包：
- **基础流程控制**: Python 3.x, Snakemake, Perl
- **质控与比对**: FastQC, Trim Galore, Bowtie2, Samtools
- **Peak Calling**: MACS2
- **信号可视化**: Deeptools
- **下游注释 (R包)**: ChIPseeker, clusterProfiler, TxDb 系列等

## 2. 在新系统中恢复环境

如果您在一台新的服务器上部署此流程，请按照以下步骤使用 `.yml` 文件恢复 Conda 环境。

### 前提条件
请确保您的系统中已经安装了 **Miniconda3** 或 **Anaconda3**，并且 `conda` 命令可以正常使用。如果未安装，请参考 [Conda 官方安装指南](https://docs.conda.io/en/latest/miniconda.html)。

### 安装步骤

1. **进入环境目录**
   在终端中进入当前目录：
   ```bash
   cd workflow/env
   ```

2. **创建并恢复环境**
   使用 `conda env create` 命令，基于 `ChIPseq_Pipline_env.yml` 文件创建一个完全相同的环境：
   ```bash
   conda env create -f ChIPseq_Pipline_env.yml
   ```
   *注意：这一步会下载并安装所有依赖包，可能需要花费较长时间（取决于网络情况），请耐心等待。*

3. **激活环境**
   安装完成后，激活该环境：
   ```bash
   conda activate ChIPseq_Pipline
   ```

4. **验证安装**
   激活环境后，您可以尝试运行以下命令来验证关键工具是否安装成功：
   ```bash
   snakemake --version
   macs2 --version
   bowtie2 --version
   deeptools --version
   ```
   如果不报错并能正确输出版本号，说明环境配置成功！

## 3. 跨平台注意事项 (可选)
如果在新系统上使用上述命令安装遇到 "未找到包 (PackagesNotFoundError)" 等操作系统差异引起的冲突，您可以尝试使用 `--no-builds` 参数导出当前环境，或者直接使用更为宽松的跨平台安装方式：
```bash
# 如果遇到严格的依赖冲突，可以尝试让 conda 仅根据包名解析依赖
conda env create -f ChIPseq_Pipline_env.yml --force
```