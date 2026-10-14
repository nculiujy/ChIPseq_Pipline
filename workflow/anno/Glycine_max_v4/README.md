# 大豆 (Glycine max v4) 注释文件放置说明

请将准备好的大豆注释文件按以下结构放置（UI「项目管理」页会自动识别）：

```
Glycine_max_v4/
└── bowtie2_index_Glycine_max_v4/        # 目录名必须以 bowtie2 开头
    ├── <genome_prefix>.1.bt2 ...        # bowtie2-build 生成的索引文件
    ├── <genome_prefix>.rev.1.bt2 ...
    ├── Glycine_max_v4.fa                # 基因组 FASTA（模块6 motif 分析需要，
    │                                    #   脚本会在索引目录内自动查找 *.fa/*.fasta）
    └── Glycine_max_v4_v4.gff3           # 基因注释 GFF3/GTF（供 TxDb 使用）
```

## config.yaml 项目配置示例

```yaml
- species: Glycine_max_v4
  experiment: <实验名>
  rawdata_dir: <fastq 目录>
  index_dir: workflow/anno/Glycine_max_v4/bowtie2_index_Glycine_max_v4/<genome_prefix>
  txdb: workflow/anno/Glycine_max_v4/bowtie2_index_Glycine_max_v4/Glycine_max_v4_v4.gff3
  orgdb: ""     # 大豆无官方 OrgDb 包，留空即可（注释正常，GO/KEGG 富集自动跳过）
  modules:
    3_ChIPseq: true
    4_analyse: true
    5_gsea: false   # 无 OrgDb 时建议关闭；自建 OrgDb 后可开启
    6_motif: true
```

## 说明

- `species` 名以 `Glyma/Glycine/gmax/soy` 开头时，流程自动识别大豆：
  - MACS2 基因组大小 → 1.0e9（Glycine max v4 ~1.025 Gb）
  - KEGG 物种代码 → gmx
  - motif 数据库 → ~/.meme_db/motif_databases/CIS-BP_2.00/Glycine_max.meme
- 如需跑 GO/KEGG 富集，可用 AnnotationHub 的 `makeOrgDbFromNCBI(genome = "gmax")`
  或 `makeOrgPackageFromGFF()` 自建 OrgDb 包，安装后填入 `orgdb` 字段。
