# TAIR 官方原始 TAIR10 基因组注释

## 基本信息

| 属性 | 值 |
|------|-----|
| **物种** | *Arabidopsis thaliana* (thale cress) |
| **生态型** | Columbia (Col-0) |
| **Assembly** | TAIR10 (原始版本) |
| **GenBank Accession** | GCA_000001735.1 |
| **提交者** | The Arabidopsis Information Resource (TAIR) |
| **NCBI 收录日期** | 2017-12-24 |
| **BioProject** | PRJNA10719 |

## 说明

这是 TAIR 官方提交到 NCBI GenBank 的**原始 TAIR10 注释**（非 RefSeq 版本）。该版本代表了 TAIR 在 2010 年前后发布的原始基因组注释，包含约 **27,416 protein-coding genes** 和约 **4,827 non-coding RNA genes**，总计约 32,000 个基因。

> ⚠️ **注意**：TAIR 官方网站 (arabidopsis.org) 已全面迁移至 JavaScript SPA 架构，直接下载链接均返回 403 Forbidden，无法通过程序自动爬取。此处的文件来自 NCBI GenBank FTP 存档，是 TAIR 提交到 NCBI 的**原始版本**。

## 文件清单

| 文件名 | 大小 | 说明 |
|--------|------|------|
| `GCA_000001735.1_TAIR10_genomic.gff.gz` | 18.3 MB | GFF3 格式基因组注释 |
| `GCA_000001735.1_TAIR10_protein.faa.gz` | 9.4 MB | 蛋白序列 (FASTA) |
| `GCA_000001735.1_TAIR10_cds_from_genomic.fna.gz` | 14.0 MB | CDS 序列 (从基因组提取) |
| `GCA_000001735.1_TAIR10_rna_from_genomic.fna.gz` | 23.5 MB | RNA 序列 |
| `GCA_000001735.1_TAIR10_feature_table.txt.gz` | 2.5 MB | NCBI feature table 格式注释 |
| `GCA_000001735.1_TAIR10_assembly_report.txt` | 1.5 KB | Assembly 报告 |
| `README_NCBI_GCA.txt` | 55 KB | NCBI 官方 README |

## 下载来源

<https://ftp.ncbi.nlm.nih.gov/genomes/all/GCA/000/001/735/GCA_000001735.1_TAIR10/>

## GCA vs GCF 对比

| 属性 | GCA_000001735.1 | GCA_000001735.2 | GCF_000001735.4 |
|------|----------------|-----------------|-----------------|
| 名称 | TAIR10 | TAIR10.1 | TAIR10.1 |
| 级别 | GenBank assembly | GenBank assembly | **RefSeq** assembly |
| 注释 | TAIR 原始 | TAIR 更新 | TAIR + Araport 融合 |
| Protein-coding | ~27,416 | ~27,416 | 27,562 |
| 总基因 | ~32,000 | ~32,000 | 38,312 |
| 提交日期 | ~2010 | ~2018 | 2022-10-20 |

## 注意事项

- 该版本是 TAIR 在 2010 年发布的原始注释，基因模型未经 Araport11 重注释
- 基因 ID 使用 AT1Gxxxxx 格式 (locus_tag)
- 对于需要与经典文献保持一致的分析，建议使用此版本
- 对于需要最新基因模型的数据分析，推荐使用 NCBI RefSeq (GCF_000001735.4) 或 Ensembl Plants
