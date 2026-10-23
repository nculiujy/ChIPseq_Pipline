#!/usr/bin/env Rscript

# 优先使用 conda 环境的 R 库，如果不存在则回退到 renv/系统库
# 这解决了 "undefined symbol: R_getRegisteredNamespace" 等版本不兼容错误
conda_prefix <- Sys.getenv("CONDA_PREFIX")
if (nchar(conda_prefix) > 0) {
  conda_r_lib <- file.path(conda_prefix, "lib", "R", "library")
  if (dir.exists(conda_r_lib)) {
    # 将 conda 库添加到搜索路径首位，但保留其他路径作为回退
    .libPaths(c(conda_r_lib, .libPaths()))
    cat("R library path prioritized conda env:", conda_r_lib, "\n")
  }
}

# 所有包由 conda 环境或 workflow/env/install_R_packages.R 预先安装
# 不在运行时动态安装，确保跨服务器版本一致
library(optparse)
library(yaml)
library(ChIPseeker)
library(ggplot2)
library(GenomicFeatures)
library(GenomicRanges)
library(rtracklayer)
library(graphics)
library(RColorBrewer)

rm(list = ls())

# 定义命令行选项
option_list <- list(
  make_option(c("-p", "--peakfile"), type = "character", help = "Path to the input Peak file (.narrowPeak or .bed)"),
  make_option(c("-o", "--output"), type = "character", help = "Path to the output directory", default = NULL),
  make_option(c("-n", "--name"), type = "character", help = "Prefix name for output files", default = "PeakAnno"),
  make_option(c("-c", "--config"), type = "character", help = "Path to the config.yaml file", default = "config/config.yaml")
)

# 解析命令行参数
opt_parser <- OptionParser(option_list = option_list)
opt <- parse_args(opt_parser)

# 参数校验
if (is.null(opt$peakfile)) {
  stop("--peakfile must be provided.")
}

# 自动生成输出路径
if (is.null(opt$output)) {
  opt$output <- getwd()
  cat("No output path provided, using current working directory:", opt$output, "\n")
}

if (!dir.exists(opt$output)) {
  dir.create(opt$output, recursive = TRUE)
}

# ============================================================
# 读取 ChIPseeker 配置节（若不存在则使用与旧版本一致的默认值）
# ============================================================
cs_cfg <- list()
if (!is.null(opt$config) && file.exists(opt$config)) {
  full_cfg <- tryCatch(yaml.load_file(opt$config), error = function(e) list())
  if (is.list(full_cfg$chipseeker)) cs_cfg <- full_cfg$chipseeker
}

# 默认值（与 ChIPseeker::annotatePeak() 官方默认对齐；旧版本本脚本曾硬编码为±2000）
DEFAULT_TSS_UP      <- -3000
DEFAULT_TSS_DOWN    <-  3000
DEFAULT_FLANK       <-  3000
DEFAULT_PRIORITY    <- c("Promoter", "5UTR", "3UTR", "Exon", "Intron", "Downstream", "Intergenic")
DEFAULT_LEVEL       <- "gene"

# 兼容读取：同时接受 ChIPseeker 官方 camelCase（UI 当前保存的命名）
# 与旧版 snake_case（历史 config），按 keys 顺序优先匹配。
cfg_get <- function(cfg, keys, default = NULL) {
  for (k in keys) {
    v <- cfg[[k]]
    if (!is.null(v)) return(v)
  }
  return(default)
}

# tssRegion 支持两种写法：
#   1) camelCase 一维数组: tssRegion: [-3000, 3000]
#   2) snake_case 嵌套:    tss_region: {upstream: -3000, downstream: 3000}
tss_region_raw <- cfg_get(cs_cfg, c("tssRegion", "tss_region"), NULL)
if (is.list(tss_region_raw) && !is.null(tss_region_raw$upstream)) {
  tss_up   <- as.numeric(tss_region_raw$upstream)
  tss_down <- as.numeric(tss_region_raw$downstream)
} else if (is.numeric(tss_region_raw) || is.list(tss_region_raw)) {
  tss_vec  <- as.numeric(unlist(tss_region_raw))
  tss_up   <- if (length(tss_vec) >= 1) tss_vec[1] else DEFAULT_TSS_UP
  tss_down <- if (length(tss_vec) >= 2) tss_vec[2] else DEFAULT_TSS_DOWN
} else {
  tss_up   <- DEFAULT_TSS_UP
  tss_down <- DEFAULT_TSS_DOWN
}

flank_dist    <- as.numeric(cfg_get(cs_cfg, c("flankDistance", "flank_distance"), DEFAULT_FLANK))
anno_level    <- as.character(cfg_get(cs_cfg, c("level"), DEFAULT_LEVEL))
# addFlankGeneInfo: 为落在基因附近的 peak 额外附加 flank 基因信息（ChIPseeker v1.42 新增参数）
add_flank_info <- isTRUE(cfg_get(cs_cfg, c("addFlankGeneInfo", "add_flank_gene_info"), FALSE))

anno_priority_raw <- cfg_get(cs_cfg, c("genomicAnnotationPriority", "annotation_priority"), DEFAULT_PRIORITY)
anno_priority <- if (length(anno_priority_raw) > 0) {
  as.character(unlist(anno_priority_raw))
} else {
  DEFAULT_PRIORITY
}
# 过滤掉非法项
anno_priority <- anno_priority[anno_priority %in% DEFAULT_PRIORITY]
if (length(anno_priority) == 0) anno_priority <- DEFAULT_PRIORITY

verbose_flag   <- isTRUE(cfg_get(cs_cfg, c("verbose"), TRUE))
ignore_overlap <- isTRUE(cfg_get(cs_cfg, c("ignoreOverlap", "ignore_overlap"), FALSE))

# 输出可视化开关
# 注意：anno_csv 与 pie_chart 是 Snakemake 规则硬性依赖的产物（下游 GSEA/结果预览使用），
#       即使用户在 UI 中取消勾选，脚本仍会强制生成以保证流水线稳定。
out_cfg <- cs_cfg$outputs
if (!is.list(out_cfg)) out_cfg <- list()
want_anno_csv     <- TRUE   # 契约输出，强制启用
want_pie          <- TRUE   # 契约输出，强制启用
want_bar          <- if (!is.null(out_cfg$bar_chart))    isTRUE(out_cfg$bar_chart)    else FALSE
want_dist_tss     <- if (!is.null(out_cfg$dist_to_tss))  isTRUE(out_cfg$dist_to_tss)  else FALSE
want_avg_prof     <- if (!is.null(out_cfg$avg_profile))  isTRUE(out_cfg$avg_profile)  else FALSE
want_covplot      <- if (!is.null(out_cfg$covplot))      isTRUE(out_cfg$covplot)      else FALSE
want_promoter_csv <- if (!is.null(out_cfg$promoter_csv)) isTRUE(out_cfg$promoter_csv) else TRUE

cat("=== ChIPseeker config ===\n")
cat("  tssRegion              :", sprintf("[%d, %d]", tss_up, tss_down), "\n")
cat("  flankDistance          :", flank_dist, "\n")
cat("  level                  :", anno_level, "\n")
cat("  addFlankGeneInfo       :", add_flank_info, "\n")
cat("  genomicAnnoPriority    :", paste(anno_priority, collapse = " > "), "\n")
cat("  ignoreOverlap          :", ignore_overlap, "\n")
cat("  verbose                :", verbose_flag, "\n")
cat("  outputs                :",
    paste(names(which(c(anno_csv=want_anno_csv, pie=want_pie, bar=want_bar,
                        distToTSS=want_dist_tss, avgProf=want_avg_prof,
                        covplot=want_covplot, promoter_csv=want_promoter_csv))),
          collapse = ","), "\n")

# 应用 ChIPseeker options（仅设置 v1.42 确实存在的选项）
# 可用选项：ignore_1st_exon / ignore_1st_intron / ignore_downstream / ignore_promoter_subcategory / downstreamDistance
options(ChIPseeker.ignore_1st_exon      = if (!is.null(cs_cfg$ignore_1st_exon))       isTRUE(cs_cfg$ignore_1st_exon)       else TRUE)
options(ChIPseeker.ignore_1st_intron    = if (!is.null(cs_cfg$ignore_1st_intron))     isTRUE(cs_cfg$ignore_1st_intron)     else TRUE)
options(ChIPseeker.ignore_downstream    = if (!is.null(cs_cfg$ignore_downstream))     isTRUE(cs_cfg$ignore_downstream)     else FALSE)
options(ChIPseeker.ignore_promoter_subcategory = if (!is.null(cs_cfg$ignore_promoter_subcategory)) isTRUE(cs_cfg$ignore_promoter_subcategory) else FALSE)
if (!is.null(cs_cfg$downstream_distance)) {
  options(ChIPseeker.downstreamDistance = as.numeric(cs_cfg$downstream_distance))
}

# 统一的空输出兜底函数：当 peak 文件缺失或为空时，Snakemake 仍需要所有期望产物存在
write_empty_outputs <- function(reason_msg) {
  if (want_anno_csv) {
    f <- file.path(opt$output, paste0(opt$name, "_peak_anno.csv"))
    write.csv(data.frame(Message = reason_msg), f, row.names = FALSE)
  }
  if (want_pie) {
    f <- file.path(opt$output, paste0(opt$name, "_annoPie.pdf"))
    pdf(f, width = 7.5, height = 7.5); plot.new(); text(0.5, 0.5, reason_msg); dev.off()
  }
  if (want_bar) {
    f <- file.path(opt$output, paste0(opt$name, "_annoBar.pdf"))
    pdf(f, width = 7.5, height = 5); plot.new(); text(0.5, 0.5, reason_msg); dev.off()
  }
  if (want_dist_tss) {
    f <- file.path(opt$output, paste0(opt$name, "_distToTSS.pdf"))
    pdf(f, width = 7.5, height = 5); plot.new(); text(0.5, 0.5, reason_msg); dev.off()
  }
  if (want_avg_prof) {
    f <- file.path(opt$output, paste0(opt$name, "_avgProfTSS.pdf"))
    pdf(f, width = 7.5, height = 5); plot.new(); text(0.5, 0.5, reason_msg); dev.off()
  }
  if (want_covplot) {
    f <- file.path(opt$output, paste0(opt$name, "_covplot.pdf"))
    pdf(f, width = 10, height = 6); plot.new(); text(0.5, 0.5, reason_msg); dev.off()
  }
  if (want_promoter_csv) {
    f <- file.path(opt$output, paste0(opt$name, "_promoter_peaks.csv"))
    write.csv(data.frame(Message = reason_msg), f, row.names = FALSE)
  }
}

if (!file.exists(opt$peakfile)) {
  cat("Warning: The input Peak file does not exist. MACS2 may have been skipped or failed for this sample.\n")
  write_empty_outputs("Peak file not found")
  cat("Annotation skipped due to missing peak file. Empty outputs generated.\n")
  quit(save = "no", status = 0)
}

if (!file.exists(opt$config)) {
  stop("The config file does not exist.")
}

# 读取 yaml 配置，获取物种信息
config <- yaml.load_file(opt$config)

# 根据传入的路径判断当前属于哪个 project
# 假设 peakfile 路径格式为: result/homo/GSE103274/4_analyse/peakcalling/...
path_parts <- strsplit(opt$peakfile, "/")[[1]]
species <- "TAIR" # 默认值
experiment <- ""

if (length(path_parts) >= 3 && path_parts[1] == "result") {
  species <- path_parts[2]
  experiment <- path_parts[3]
}

cat("Detected species:", species, "experiment:", experiment, "\n")

# 从 config 中查找对应的 txdb 和 orgdb
txdb_name <- NULL
orgdb_name <- NULL

if (!is.null(config$projects)) {
  for (p in config$projects) {
    if (p$species == species && p$experiment == experiment) {
      txdb_name <- p$txdb
      orgdb_name <- p$orgdb
      break
    }
  }
}

# 如果没有在 config 里找到配置，做一些默认 fallback
if (is.null(txdb_name) || is.null(orgdb_name)) {
  if (species == "TAIR") {
    txdb_name <- "TxDb.Athaliana.BioMart.plantsmart28"
    orgdb_name <- "org.At.tair.db"
  } else if (species == "mm") {
    txdb_name <- "TxDb.Mmusculus.UCSC.mm10.knownGene"
    orgdb_name <- "org.Mm.eg.db"
  } else if (species == "homo") {
    txdb_name <- "TxDb.Hsapiens.UCSC.hg38.knownGene"
    orgdb_name <- "org.Hs.eg.db"
  } else {
    stop("Unsupported species and no txdb/orgdb provided in config.yaml for:", species)
  }
}

cat("Using TxDb:", txdb_name, "\n")

# OrgDb 可选：部分非模式物种（如大豆）无官方 OrgDb 包，
# 未配置或加载失败时跳过基因符号注释，ChIPseeker 仍会基于 TxDb 输出 geneId
annoDb_name <- NULL
if (!is.null(orgdb_name) && nzchar(as.character(orgdb_name))) {
  orgdb_ok <- tryCatch({
    suppressWarnings(library(orgdb_name, character.only = TRUE))
    TRUE
  }, error = function(e) {
    cat("Warning: 无法加载 OrgDb 包", orgdb_name, ":", conditionMessage(e), "\n")
    FALSE
  })
  if (orgdb_ok) {
    annoDb_name <- orgdb_name
    cat("Using OrgDb:", orgdb_name, "\n")
  } else {
    cat("Warning: OrgDb 不可用，跳过基因符号注释 (annoDb = NULL)\n")
  }
} else {
  cat("未配置 OrgDb，跳过基因符号注释 (annoDb = NULL)\n")
}

# 判断 txdb 是文件路径还是 Bioconductor 包名
if (file.exists(txdb_name)) {
  cat("Loading TxDb from GFF/GTF file:", txdb_name, "\n")
  # Bioc 3.19 起 makeTxDbFromGFF 的实现搬进了 txdbmaker，GenomicFeatures 里只剩
  # 一个转发壳。缺包时它只报一句 "Could not load package txdbmaker"，看不出是
  # 环境缺依赖，所以这里接住错误并给出修法。
  txdb <- tryCatch({
    if (requireNamespace("txdbmaker", quietly = TRUE)) {
      txdbmaker::makeTxDbFromGFF(txdb_name)
    } else {
      GenomicFeatures::makeTxDbFromGFF(txdb_name)
    }
  }, error = function(e) {
    stop("无法从 GFF/GTF 构建 TxDb：", conditionMessage(e), "\n",
         "  Bioc 3.19 起 makeTxDbFromGFF 由 txdbmaker 提供，当前环境没装：\n",
         "    BiocManager::install(\"txdbmaker\")\n",
         "    conda install -c bioconda bioconductor-txdbmaker\n",
         "  或把 config.yaml 里该项目的 txdb 换成 Bioconductor 包名。",
         call. = FALSE)
  })
} else {
  library(txdb_name, character.only = TRUE)
  txdb <- get(txdb_name)
}

# 读取 Peak 文件
if (file.info(opt$peakfile)$size == 0) {
  cat("Warning: The input Peak file is empty (size 0). MACS2 may not have found any peaks under the current cutoff.\n")
  write_empty_outputs("No peaks found")
  cat("Annotation skipped due to empty peak file. Empty outputs generated.\n")
  quit(save = "no", status = 0)
}

peak <- readPeakFile(opt$peakfile)

# 标准化染色体名称：如果 peak 文件的染色体名有 "Chr" 前缀而 TxDb 没有，则去掉
peak_seqlevels <- seqlevels(peak)
txdb_seqlevels <- seqlevels(txdb)
peak_has_chr <- any(grepl("^Chr", peak_seqlevels))
txdb_has_chr <- any(grepl("^Chr", txdb_seqlevels))
if (peak_has_chr && !txdb_has_chr) {
  seqlevels(peak) <- gsub("^Chr", "", seqlevels(peak))
  cat("Stripped 'Chr' prefix from peak chromosome names to match TxDb.\n")
}
if (!peak_has_chr && txdb_has_chr) {
  seqlevels(peak) <- paste0("Chr", seqlevels(peak))
  cat("Added 'Chr' prefix to peak chromosome names to match TxDb.\n")
}

# ============================================================
# ChIPseeker 注释（参数来自 config.yaml -> chipseeker 节）
# ============================================================
peakAnno <- annotatePeak(
  peak,
  tssRegion                  = c(tss_up, tss_down),
  TxDb                       = txdb,
  annoDb                     = annoDb_name,
  level                      = anno_level,
  genomicAnnotationPriority  = anno_priority,
  addFlankGeneInfo           = add_flank_info,
  flankDistance              = flank_dist,
  ignoreOverlap              = ignore_overlap,
  verbose                    = verbose_flag
)

# 提取注释数据结果
annotationData <- as.data.frame(peakAnno)

# 统计 peaks 总数
total_peaks <- nrow(annotationData)
cat("Total peaks:", total_peaks, "\n")

# 保留 ChIPseeker 原始 annotation 作为 annotation_category（1:1 映射，不再做有损的二次分类）
# ChIPseeker 原始 annotation 列已经是标准分类，包含：
#   "Promoter (<=1kb)", "Promoter (1-2kb)", "Promoter (2-3kb)",
#   "5' UTR", "3' UTR", "Exon", "Intron",
#   "Downstream", "Distal Intergenic"
# 旧版代码硬编码的 ifelse 重分类会丢弃 5' UTR，导致饼图看不到 5'UTR/3'UTR 独立分类。
# 保留 annotation_category 列名是为了兼容下游 GSEA / 结果预览页（它们读的是这个列名）。
annotationData$annotation_category <- annotationData$annotation

# 统计每个注释类别的频率（用于饼图）
# 不再做白名单过滤，直接展示 ChIPseeker 实际输出的所有类别，
# 这样 5' UTR / 3' UTR / Promoter 子类 / Downstream / Distal Intergenic 都会出现。
annotationCounts <- as.data.frame(table(annotationData$annotation_category), stringsAsFactors = FALSE)
colnames(annotationCounts) <- c("Category", "Count")
# 按 ChIPseeker 官方 priority 顺序排列（让饼图图例顺序稳定、可读）
PRIORITY_ORDER <- c(
  "Promoter (<=1kb)", "Promoter (1-2kb)", "Promoter (2-3kb)",
  "5' UTR", "3' UTR", "Exon", "Intron",
  "Downstream", "Distal Intergenic"
)
# 使用 factor + order 安全排序（不在 PRIORITY_ORDER 中的类别排在最后）
all_levels <- c(PRIORITY_ORDER, setdiff(as.character(annotationCounts$Category), PRIORITY_ORDER))
annotationCounts$Category <- factor(annotationCounts$Category, levels = all_levels)
annotationCounts <- annotationCounts[order(annotationCounts$Category), , drop = FALSE]
annotationCounts$Category <- as.character(annotationCounts$Category)
rownames(annotationCounts) <- NULL
# 去掉 Count == 0 的行（table() 不会产生 0，但防御性处理）
annotationCounts <- annotationCounts[annotationCounts$Count > 0, , drop = FALSE]
if (nrow(annotationCounts) > 0 && sum(annotationCounts$Count) > 0) {
  annotationCounts$Percentage <- annotationCounts$Count / sum(annotationCounts$Count) * 100
} else {
  annotationCounts$Percentage <- 0
}
cat("Annotation categories detected:", paste(annotationCounts$Category, collapse = ", "), "\n")

# ---- 输出 1: 完整注释 CSV ----
if (want_anno_csv) {
  peak_anno_file <- file.path(opt$output, paste0(opt$name, "_peak_anno.csv"))
  write.csv(annotationData, peak_anno_file, row.names = FALSE)
  cat("Wrote:", peak_anno_file, "\n")
}

# ---- 输出 2: 饼图 (ChIPseeker::plotAnnoPie，自动引线布局 + 图例分离，避免标签重叠) ----
# 旧版使用 R base pie()，9+ 个类别时标签会重叠；plotAnnoPie 是 ChIPseeker 官方推荐方式。
# 注意：plotAnnoPie 是 base R 绘图（直接画到当前 device），不返回 ggplot 对象，
#       所以必须先 pdf() 打开 device，再调用 plotAnnoPie，最后 dev.off()。
if (want_pie) {
  pdf_file <- file.path(opt$output, paste0(opt$name, "_annoPie.pdf"))
  tryCatch({
    pdf(pdf_file, width = 10, height = 7.5)
    plotAnnoPie(
      peakAnno,
      ndigit = 2,
      cex = 0.75,
      radius = 0.7,
      legend.position = "rightside"
    )
    title(main = sprintf("%s - Total peaks: %d (TSS: %d~%d)", opt$name, total_peaks, tss_up, tss_down),
          line = 2.2, cex.main = 1.0)
    dev.off()
    cat("Wrote:", pdf_file, "\n")
  }, error = function(e) {
    cat("Warning: plotAnnoPie failed:", conditionMessage(e), "\n")
    # 兜底：确保 device 关闭
    try(dev.off(), silent = TRUE)
    # 再兜底：用 base pie + 图例分离（标签放图例里，不放饼图上）
    pdf(pdf_file, width = 10, height = 7.5)
    if (nrow(annotationCounts) > 0 && sum(annotationCounts$Count) > 0) {
      values <- annotationCounts$Count
      colors <- brewer.pal(max(3, min(12, length(values))), "Set3")[1:length(values)]
      pie(values, labels = rep("", length(values)), col = colors, border = "white",
          main = sprintf("%s - Total peaks: %d", opt$name, total_peaks))
      legend_text <- sprintf("%s: %d (%.2f%%)",
                             annotationCounts$Category,
                             annotationCounts$Count,
                             annotationCounts$Percentage)
      legend("right", legend = legend_text, fill = colors, bty = "n", cex = 0.7)
    } else {
      plot.new()
      text(0.5, 0.5, "No peaks matched required categories")
    }
    dev.off()
    cat("Wrote (fallback):", pdf_file, "\n")
  })
}

# ---- 输出 3: 柱状图 (ChIPseeker::plotAnnoBar) ----
if (want_bar) {
  bar_file <- file.path(opt$output, paste0(opt$name, "_annoBar.pdf"))
  tryCatch({
    p <- plotAnnoBar(peakAnno, xlab = opt$name)
    ggsave(bar_file, plot = p, width = 8, height = 5, limitsize = FALSE)
    cat("Wrote:", bar_file, "\n")
  }, error = function(e) {
    cat("Warning: plotAnnoBar failed:", conditionMessage(e), "\n")
    pdf(bar_file, width = 8, height = 5); plot.new()
    text(0.5, 0.5, paste("plotAnnoBar failed:", conditionMessage(e)))
    dev.off()
  })
}

# ---- 输出 4: TSS 距离分布 (ChIPseeker::plotDistToTSS) ----
if (want_dist_tss) {
  dist_file <- file.path(opt$output, paste0(opt$name, "_distToTSS.pdf"))
  tryCatch({
    p <- plotDistToTSS(peakAnno, xlab = opt$name)
    ggsave(dist_file, plot = p, width = 8, height = 5, limitsize = FALSE)
    cat("Wrote:", dist_file, "\n")
  }, error = function(e) {
    cat("Warning: plotDistToTSS failed:", conditionMessage(e), "\n")
    pdf(dist_file, width = 8, height = 5); plot.new()
    text(0.5, 0.5, paste("plotDistToTSS failed:", conditionMessage(e)))
    dev.off()
  })
}

# ---- 输出 5: TSS 平均信号曲线 (ChIPseeker::plotAvgProf) ----
# plotAvgProf 需要 tagMatrix；getTagMatrix(type='start_site') 需显式传 by='gene'，否则报
# "argument 'by' is missing"（v1.42 行为）
if (want_avg_prof) {
  avg_file <- file.path(opt$output, paste0(opt$name, "_avgProfTSS.pdf"))
  tryCatch({
    tagMatrix <- getTagMatrix(
      peak, TxDb = txdb, type = "start_site", by = "gene",
      upstream = abs(tss_up), downstream = tss_down
    )
    p <- plotAvgProf(tagMatrix, xlim = c(tss_up, tss_down), conf = 0.95, resample = 500, facet = "row")
    ggplot2::ggsave(avg_file, plot = p, width = 8, height = 6, limitsize = FALSE)
    cat("Wrote:", avg_file, "\n")
  }, error = function(e) {
    cat("Warning: plotAvgProf failed:", conditionMessage(e), "\n")
    pdf(avg_file, width = 8, height = 6); plot.new()
    text(0.5, 0.5, paste("plotAvgProf failed:", conditionMessage(e)))
    dev.off()
  })
}

# ---- 输出 6: 染色体覆盖图 (ChIPseeker::covplot) ----
if (want_covplot) {
  cov_file <- file.path(opt$output, paste0(opt$name, "_covplot.pdf"))
  tryCatch({
    p <- covplot(peak, title = paste0(opt$name, " - Peak coverage"))
    ggsave(cov_file, plot = p, width = 12, height = 7, limitsize = FALSE)
    cat("Wrote:", cov_file, "\n")
  }, error = function(e) {
    cat("Warning: covplot failed:", conditionMessage(e), "\n")
    pdf(cov_file, width = 12, height = 7); plot.new()
    text(0.5, 0.5, paste("covplot failed:", conditionMessage(e)))
    dev.off()
  })
}

# ---- 输出 7: Promoter peaks CSV ----
if (want_promoter_csv) {
  promoter_peaks <- annotationData[grepl("Promoter", annotationData$annotation_category), ]
  promoter_file <- file.path(opt$output, paste0(opt$name, "_promoter_peaks.csv"))
  write.csv(promoter_peaks, promoter_file, row.names = FALSE)
  cat("Wrote:", promoter_file, "\n")
}

cat("Annotation finished successfully. Output saved to:", opt$output, "\n")
