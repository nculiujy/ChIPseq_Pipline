# NCBI RefSeq — TAIR10.1 基因组注释

## 基本信息

| 属性 | 值 |
|------|-----|
| **物种** | *Arabidopsis thaliana* (thale cress) |
| **生态型** | Columbia |
| **Taxonomy ID** | 3702 |
| **Assembly 名称** | TAIR10.1 |
| **RefSeq Accession** | GCF_000001735.4 |
| **GenBank Accession** | GCA_000001735.2 |
| **注释来源** | TAIR + Araport |
| **注释日期** | 2022-10-20 |
| **注释方法** | NCBI Eukaryotic Annotation Propagation Pipeline |
| **RefSeq 类别** | reference genome |
| **BioProject** | PRJNA10719 — Arabidopsis assembly project |

## 基因组统计

| 指标 | 数值 |
|------|------|
| 总序列长度 | 119,146,348 bp |
| 无 gap 长度 | 118,960,704 bp |
| 染色体数 | 5 |
| Contig 数 | 100 |
| Contig N50 | 11,194,537 bp |
| Scaffold N50 | 23,459,830 bp |
| GC 含量 | 36% |
| 细胞器 | 叶绿体 (154,478 bp) + 线粒体 (367,808 bp) |

## 注释统计

| 类型 | 数量 |
|------|------|
| 总基因 (Genes) | 38,312 |
| Protein-coding | 27,562 |
| Non-coding | 5,884 |
| Pseudogenes | 4,851 |
| Other | 15 |

## 文件清单

| 文件名 | 大小 | 说明 |
|--------|------|------|
| `GCF_000001735.4_TAIR10.1_genomic.gff.gz` | 21 MB | GFF3 格式基因组注释 |
| `GCF_000001735.4_TAIR10.1_genomic.gtf.gz` | 37 MB | GTF 格式基因组注释（推荐用于 RNA-seq） |
| `GCF_000001735.4_TAIR10.1_protein.faa.gz` | 12 MB | 蛋白序列 (FASTA) |
| `GCF_000001735.4_TAIR10.1_cds_from_genomic.fna.gz` | 15 MB | CDS 序列 (从基因组提取) |
| `GCF_000001735.4_TAIR10.1_rna.fna.gz` | 24 MB | RNA 序列 |
| `README_NCBI.txt` | 56 KB | NCBI 官方 README |

## 下载来源

<https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/735/GCF_000001735.4_TAIR10.1/>

## 注意事项

- 基因 ID 使用 AT1Gxxxxx 格式（locus_tag），与 TAIR 原始注释兼容
- 该注释融合了 TAIR 原始注释和 Araport11 再注释的基因模型
- 文件中的染色体编号为 NC_003070.9 (Chr1), NC_003071.7 (Chr2), NC_003074.8 (Chr3), NC_003075.7 (Chr4), NC_003076.8 (Chr5)
- 叶绿体: NC_000932.1；线粒体: NC_037304.1
