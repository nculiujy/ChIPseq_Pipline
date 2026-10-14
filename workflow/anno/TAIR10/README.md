# TAIR10 基因组注释文件合集

> **物种**：*Arabidopsis thaliana* (拟南芥)  
> **生态型**：Columbia (Col-0)  
> **参考基因组**：TAIR10  
> **基因组大小**：~119.1 Mb (5条染色体 + 叶绿体 + 线粒体)  
> **下载日期**：2026-07-04

---

## 目录结构

```
F:\TAIR10\
├── README.md                          # 本文件
├── NCBI_RefSeq_TAIR10.1/              # NCBI RefSeq 注释 (推荐)
│   ├── README.md
│   ├── GCF_000001735.4_TAIR10.1_genomic.gff.gz     (21 MB)
│   ├── GCF_000001735.4_TAIR10.1_genomic.gtf.gz     (37 MB)
│   ├── GCF_000001735.4_TAIR10.1_protein.faa.gz     (12 MB)
│   ├── GCF_000001735.4_TAIR10.1_cds_from_genomic.fna.gz (15 MB)
│   ├── GCF_000001735.4_TAIR10.1_rna.fna.gz         (24 MB)
│   └── README_NCBI.txt
├── Ensembl_Plants_TAIR10.58/          # Ensembl Plants Release 58
│   ├── README.md
│   ├── Arabidopsis_thaliana.TAIR10.58.gff3.gz
│   └── README_Ensembl.txt
├── Ensembl_Plants_TAIR10.59/          # Ensembl Plants Release 59
├── Ensembl_Plants_TAIR10.60/          # Ensembl Plants Release 60
├── Ensembl_Plants_TAIR10.61/          # Ensembl Plants Release 61
├── Ensembl_Plants_TAIR10.62/          # Ensembl Plants Release 62
└── Ensembl_Plants_TAIR10.63/          # Ensembl Plants Release 63 (最新)
```

---

## 无法自动下载的版本

以下版本因服务器限制（403 Forbidden / JavaScript 渲染页面）无法自动下载，请手动获取：

| 版本 | 来源 | 获取方式 |
|------|------|----------|
| **TAIR10 原始注释** | TAIR (arabidopsis.org) | 浏览器访问 `https://www.arabidopsis.org/download/` |
| **Araport11** | TAIR / BAR | 已整合至 TAIR 下载页面；BAR ThaleMine: `https://bar.utoronto.ca/thalemine/` |
| **Phytozome v13** | JGI | `https://phytozome-next.jgi.doe.gov/info/Athaliana_TAIR10` (需浏览器) |

---

## 版本对比

| 属性 | NCBI RefSeq | Ensembl Plants | **TAIR 原始** | Araport11 |
|------|------------|---------------|-----------|-----------|
| Assembly | TAIR10.1 | TAIR10 | TAIR10 | TAIR10 |
| Accession | GCF_000001735.4 | — | GCA_000001735.1 | — |
| 发布时间 | 2022-10-20 | 持续更新 | ~2010 | 2016-06 |
| Protein-coding | 27,562 | — | ~27,416 | ~27,655 |
| 总基因数 | 38,312 | — | ~32,000 | ~48,000 |
| 基因 ID | AT1Gxxxxx | AT1Gxxxxx (Ensembl ID) | AT1Gxxxxx | AT1Gxxxxx |
| 数据来源 | NCBI FTP | Ensembl FTP | NCBI GenBank FTP | 需手动下载 |

---

## 使用建议

- **常规 RNA-seq / 差异表达分析**：推荐 NCBI RefSeq 的 GTF/GFF 文件
- **比较基因组 / 进化分析**：推荐 Ensembl Plants
- **最新文献引用标准**：Araport11（需手动下载）
- **跨数据库交叉验证**：可同时使用 NCBI + Ensembl
