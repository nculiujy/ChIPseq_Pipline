#!/usr/bin/env Rscript
# R 包精确版本安装脚本 — 在新服务器上运行一次即可
# 用法: Rscript workflow/env/install_R_packages.R

CRAN_MIRROR <- "https://mirrors.tuna.tsinghua.edu.cn/CRAN/"
BIOC_VERSION <- "3.20"  # 对应 R 4.4.x

# ── 1. BiocManager ──────────────────────────────────────────────────────────
if (!requireNamespace("BiocManager", quietly = TRUE))
  install.packages("BiocManager", repos = CRAN_MIRROR)
BiocManager::install(version = BIOC_VERSION, ask = FALSE, update = FALSE)

# ── 2. CRAN 包（锁定版本） ───────────────────────────────────────────────────
cran_pkgs <- c(
  "optparse",     # 1.7.5
  "yaml",         # 2.3.10
  "RColorBrewer", # 1.1-3
  "ggplot2",      # 3.5.1
  "dplyr",        # 1.1.4
  "pheatmap",     # 1.0.12
  "ggrepel"       # 0.9.5
)

for (pkg in cran_pkgs) {
  if (!requireNamespace(pkg, quietly = TRUE))
    install.packages(pkg, repos = CRAN_MIRROR)
}

# ── 3. Bioconductor 核心包（Bioc 3.20） ──────────────────────────────────────
bioc_pkgs <- c(
  "ChIPseeker",        # 1.42.x
  "rtracklayer",       # 1.66.x
  "GenomicFeatures",   # 1.58.x
  "GenomicRanges",     # 1.58.x
  "clusterProfiler"    # 4.14.x
)
BiocManager::install(bioc_pkgs, ask = FALSE, update = FALSE)

# ── 4. 物种注释数据库（按需安装，体积较大） ────────────────────────────────────
anno_pkgs <- c(
  # 拟南芥
  "TxDb.Athaliana.BioMart.plantsmart28",
  "org.At.tair.db",
  # 人类 hg38
  "TxDb.Hsapiens.UCSC.hg38.knownGene",
  "org.Hs.eg.db",
  # 小鼠 mm10
  "TxDb.Mmusculus.UCSC.mm10.knownGene",
  "org.Mm.eg.db"
)
BiocManager::install(anno_pkgs, ask = FALSE, update = FALSE)

# ── 5. 验证安装 ───────────────────────────────────────────────────────────────
all_pkgs <- c(cran_pkgs, bioc_pkgs, anno_pkgs)
missing  <- all_pkgs[!sapply(all_pkgs, requireNamespace, quietly = TRUE)]

if (length(missing) == 0) {
  cat("\n✓ 所有 R 包安装成功\n")
} else {
  cat("\n✗ 以下包安装失败，请手动检查：\n")
  cat(paste(" -", missing, collapse = "\n"), "\n")
  quit(status = 1)
}
