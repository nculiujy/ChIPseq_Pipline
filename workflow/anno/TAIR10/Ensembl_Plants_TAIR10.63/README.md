# Ensembl Plants — TAIR10.63 基因组注释

## 基本信息

| 属性 | 值 |
|------|-----|
| **物种** | *Arabidopsis thaliana* (thale cress) |
| **Assembly** | TAIR10 |
| **Ensembl 版本** | Release 63 |
| **Ensembl Genomes 发布日期** | 2026-04-06 |
| **基因注释来源** | Ensembl Plants gene annotation pipeline |

## 文件清单

| 文件名 | 大小 | 说明 |
|--------|------|------|
| `Arabidopsis_thaliana.TAIR10.63.gff3.gz` | 9.1 MB | 全基因组 GFF3 注释 |
| `README_Ensembl.txt` | 6.2 KB | Ensembl 官方 README |

## 下载来源

<https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/current/gff3/arabidopsis_thaliana/>

（注：`current/` 目录指向最新 Release，当前为 Release 63）

## GFF3 格式说明

- **gene** 类型: "gene" (protein-coding), "ncRNA_gene" (RNA genes), "pseudogene"
- **transcript** 类型: "mRNA", snoRNA, lnc_RNA, pseudogenic_transcript 等
- 基因 ID 示例: `gene:AT1G01010`，转录本 ID: `transcript:AT1G01010.1`
- biotype 属性标识基因类型（如 "protein_coding", "pseudogene"）

## 本版本特点

- 这是 Ensembl Plants 当前最新版本 (Release 63)
- 相对于上一版本 (62) 可能有基因模型的更新和新增基因

## 注意事项

- 基因 ID 与 TAIR/Araport 使用相同格式 (ATxGxxxxx)，但注释细节可能不同
- GFF3 文件使用 Ensembl 标准化格式，包含 chromosome、Mt (线粒体) 和 Pt (质体) 的注释
- 如有需要分染色体的注释文件，可从同一 FTP 目录下载
