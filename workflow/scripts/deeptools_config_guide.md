# DeepTools 配置指南

## 概述

本文档说明如何通过 `config/config.yaml` 灵活配置 deepTools 分析流程。

## 配置项说明

### 1. computeMatrix 模式选择

deepTools 支持两种 `computeMatrix` 模式：

#### **reference-point 模式** (推荐用于 ChIP-seq peaks)

以某个参考点为中心进行分析，适用于：
- **Peak 中心分析**：查看信号在 peak 中心的分布
- **TSS 分析**：查看转录起始位点附近的信号
- **TES 分析**：查看转录终止位点附近的信号

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "center"    # center (peak中心), TSS, TES
    upstream: 2000     # 上游 2kb
    downstream: 2000   # 下游 2kb
```

#### **scale-regions 模式** (推荐用于基因/转录本分析)

将不同长度的区域缩放到统一长度，适用于：
- **基因体分析**：比较不同长度基因上的信号分布
- **增强子区域**：标准化不同大小的调控元件

```yaml
deeptools:
  matrix_mode: "scale-regions"
  scale_regions:
    region_length: 5000     # 缩放后统一长度
    upstream: 2000          # 区域上游延伸
    downstream: 2000        # 区域下游延伸
    unscaled_5prime: 0      # 5'端不缩放区域 (可选)
    unscaled_3prime: 0      # 3'端不缩放区域 (可选)
```

### 2. BED 文件来源配置

`bed_source` 参数控制分析的背景区域来源：

#### **选项 A: macs2_peaks** (默认)

使用 MACS2 peak calling 的输出作为背景区域，**每对样本使用自己的 peaks**。

```yaml
deeptools:
  bed_source: "macs2_peaks"
```

**来源**：`result/{species}/{experiment}/4_analyse/peakcalling/{pair}/{pair}_peaks_tab.bed`

**特点**：
- ✅ 针对性强：每对样本分析自己的差异区域
- ✅ 避免混淆：不同样本对的 peaks 独立
- ❌ 跨样本比较困难：不同样本对使用不同的区域集

这个文件由 [`workflow/scripts/4_1_peakcalling.pl`](4_1_peakcalling.pl:135-146) 生成：
1. MACS2 输出 `{pair}_peaks.narrowPeak` 或 `{pair}_peaks.broadPeak`
2. 提取前5列转换为标准 BED 格式
3. 转换空格为制表符生成 `_tab.bed`

#### **选项 B: union_peaks** (推荐用于跨样本比较)

合并所有样本对的 peaks，创建一个统一的区域集用于所有样本。

```yaml
deeptools:
  bed_source: "union_peaks"
```

**生成过程**：
1. 收集所有样本对的 `{pair}_peaks_tab.bed`
2. 使用 `bedtools merge` 合并重叠区域
3. 生成 `union_peaks.bed` 存放在输出目录

**特点**：
- ✅ 跨样本可比：所有样本使用相同的区域集
- ✅ 完整覆盖：包含任意样本对中检测到的所有 peaks
- ✅ 适合比较：便于观察不同样本在相同位置的信号差异
- ❌ 可能包含噪音：某些区域可能只在个别样本中显著

**适用场景**：
- 比较多个处理条件的效果
- 寻找共有和特异的结合位点
- 生成统一标准的可视化结果

#### **选项 C: custom** (自定义 BED 文件)

使用自己准备的 BED 文件作为分析区域。

```yaml
deeptools:
  bed_source: "custom"
  custom_bed_files:
    TAIR_314Com5: "workflow/anno/custom_regions.bed"
    homo_GSE103274: "workflow/anno/enhancers.bed"
```

**用途**：
- 分析特定的基因组区域（如增强子、启动子）
- 使用外部数据库的注释区域
- 聚焦感兴趣的候选区域

**BED 格式要求**：
```
chr1    1000    2000    peak1    100    +
chr1    3000    4000    peak2    200    -
```
至少需要前3列（染色体、起始、终止），建议包含名称和链信息。

#### **选项 D: genes** (基因注释)

从 GTF 文件提取基因区域进行分析。

```yaml
deeptools:
  bed_source: "genes"
  gtf_files:
    TAIR: "workflow/anno/TAIR10_GFF3_genes.gtf"
    homo: "workflow/anno/gencode.v38.annotation.gtf"
```

**用途**：
- 分析所有基因上的 ChIP 信号分布
- 比较不同基因类型的信号模式
- Gene body 覆盖度分析

**注意**：脚本会自动提取 GTF 中 `feature_type == "gene"` 的条目转换为 BED 格式。

### 3. 可视化参数

```yaml
deeptools:
  bin_size: 10              # bin 大小 (bp)，越小越精细但计算量越大
  missing_data_as_zero: true  # 缺失数据是否视为0
  
  plot:
    color_map: "RdYlBu_r"   # 热图配色
    z_min: 0                # 热图最小值 (或 "auto")
    z_max: "auto"           # 热图最大值 (或 "auto")
    interpolation: "bilinear"  # 插值方法
```

**常用配色方案**：
- `RdYlBu_r`: 红-黄-蓝 (reversed)
- `viridis`: 紫-绿-黄 (色盲友好)
- `plasma`: 紫-粉-黄
- `coolwarm`: 冷暖色对比
- `Reds`, `Blues`, `Greens`: 单色渐变

## 使用场景示例

### 场景1：分析每对样本各自的 peaks（默认）

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "center"
    upstream: 3000
    downstream: 3000
  bed_source: "macs2_peaks"
  bin_size: 50
```

**结果**：每对样本在自己的差异区域上独立分析

### 场景2：跨样本比较（使用 union peaks）

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "center"
    upstream: 2000
    downstream: 2000
  bed_source: "union_peaks"
```

**结果**：所有样本在统一的区域集上分析，便于横向比较

### 场景3：分析启动子区域 (-2kb ~ +1kb TSS)

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "TSS"
    upstream: 2000
    downstream: 1000
  bed_source: "genes"
  gtf_files:
    TAIR: "workflow/anno/TAIR10_GFF3_genes.gtf"
```

### 场景3：分析启动子区域 (-2kb ~ +1kb TSS)

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "TSS"
    upstream: 2000
    downstream: 1000
  bed_source: "genes"
  gtf_files:
    TAIR: "workflow/anno/TAIR10_GFF3_genes.gtf"
```

### 场景4：分析基因体覆盖度

```yaml
deeptools:
  matrix_mode: "scale-regions"
  scale_regions:
    region_length: 5000
    upstream: 2000
    downstream: 2000
  bed_source: "genes"
```

### 场景5：分析自定义增强子区域

```yaml
deeptools:
  matrix_mode: "reference-point"
  reference_point:
    point: "center"
    upstream: 5000
    downstream: 5000
  bed_source: "custom"
  custom_bed_files:
    TAIR_314Com5: "workflow/anno/enhancers_TAIR10.bed"
```

## 输出文件

每个样本对 (`{treat}_vs_{control}`) 生成以下文件：

```
result/{species}/{experiment}/4_analyse/deeptools/{pair}/
├── {treat}.bw                    # Treatment BigWig
├── {control}.bw                  # Control BigWig
├── matrix_{pair}.gz              # 信号矩阵
├── {pair}_plotProfile.pdf        # Profile 曲线图
└── {pair}_plotHeatmap.pdf        # 热图
```

## 技术细节

### BED 文件来源流程

1. **MACS2 Peaks** (pair-specific)：
   ```
   MACS2 callpeak (per pair)
   ↓
   {pair}_peaks.narrowPeak / broadPeak
   ↓
   提取前5列 → {pair}_peaks.bed
   ↓
   转换为tab分隔 → {pair}_peaks_tab.bed
   ↓
   每对样本使用自己的 peaks
   ```

2. **Union Peaks** (merged)：
   ```
   收集所有 {pair}_peaks_tab.bed
   ↓
   cat + sort -k1,1 -k2,2n
   ↓
   bedtools merge (合并重叠区域)
   ↓
   union_peaks.bed
   ↓
   所有样本共享同一个区域集
   ```

3. **Custom BED**：
   ```
   用户准备的 BED 文件
   ↓
   直接使用配置文件中指定的路径
   ↓
   按 {species}_{experiment} 键匹配
   ```

4. **Genes from GTF**：
   ```
   GTF 文件
   ↓
   提取 feature=="gene"
   ↓
   转换为 BED 格式
   ↓
   genes_from_gtf.bed
   ```

### computeMatrix 命令示例

**reference-point 模式**：
```bash
computeMatrix reference-point \
  -R peaks.bed \
  -S treat.bw control.bw \
  --referencePoint center \
  -b 2000 -a 2000 \
  --binSize 10 \
  -p 8 \
  --missingDataAsZero \
  -o matrix.gz
```

**scale-regions 模式**：
```bash
computeMatrix scale-regions \
  -R genes.bed \
  -S treat.bw control.bw \
  -m 5000 \
  -b 2000 -a 2000 \
  --binSize 10 \
  -p 8 \
  --missingDataAsZero \
  -o matrix.gz
```

## 常见问题

**Q: 如何选择 reference-point 还是 scale-regions？**
- 分析 peaks 或固定位点（TSS/TES）→ `reference-point`
- 分析基因或长度不一的区域 → `scale-regions`

**Q: macs2_peaks 和 union_peaks 有什么区别？**
- `macs2_peaks`：每对样本分析自己的 peaks，适合独立评估
- `union_peaks`：所有样本在同一区域集上分析，适合跨样本比较

**Q: 什么时候用 union_peaks？**
- 比较多个实验条件（如 WT vs KO1 vs KO2）
- 寻找共有和特异的结合位点
- 需要生成标准化的、可直接比较的热图

**Q: union_peaks 会增加噪音吗？**
- 是的，可能包含某些只在个别样本中显著的区域
- 建议：配合使用更严格的 MACS2 cutoff 减少假阳性

**Q: BED 文件为空或不存在怎么办？**
- MACS2 没有找到 peaks：检查 cutoff 参数是否太严格
- Custom BED 路径错误：确认文件路径正确且可访问
- GTF 解析失败：确认 GTF 格式正确且包含 gene 特征

**Q: 如何为不同项目使用不同的 BED 文件？**
使用 `custom_bed_files` 的键值对配置：
```yaml
custom_bed_files:
  TAIR_314Com5: "path/to/bed1.bed"
  TAIR_314OE: "path/to/bed2.bed"
  homo_GSE103274: "path/to/bed3.bed"
```

**Q: union_peaks 需要安装额外工具吗？**
- 需要 `bedtools` 命令行工具
- 已包含在项目的 conda 环境中

## 参考资源

- [deepTools 官方文档](https://deeptools.readthedocs.io/)
- [computeMatrix 手册](https://deeptools.readthedocs.io/en/latest/content/tools/computeMatrix.html)
- [plotHeatmap 参数](https://deeptools.readthedocs.io/en/latest/content/tools/plotHeatmap.html)
