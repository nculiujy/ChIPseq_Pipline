#!/usr/bin/env Rscript

# 载入所需的包
if (!require("optparse")) install.packages("optparse", repos="https://mirrors.tuna.tsinghua.edu.cn/CRAN/")
if (!requireNamespace("BiocManager", quietly = TRUE)) 
  install.packages("BiocManager", repos="https://mirrors.tuna.tsinghua.edu.cn/CRAN/")
if (!require("yaml")) install.packages("yaml", repos="https://mirrors.tuna.tsinghua.edu.cn/CRAN/")
if (!require("RColorBrewer")) install.packages("RColorBrewer", repos="https://mirrors.tuna.tsinghua.edu.cn/CRAN/")
if (!require("ChIPseeker")) BiocManager::install("ChIPseeker")
if (!require("rtracklayer")) BiocManager::install("rtracklayer")

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

if (!file.exists(opt$peakfile)) {
  cat("Warning: The input Peak file does not exist. MACS2 may have been skipped or failed for this sample.\n")
  
  # 创建空的输出文件以满足 Snakemake 期望
  peak_anno_file <- file.path(opt$output, paste0(opt$name, "_peak_anno.csv"))
  write.csv(data.frame(Message="Peak file not found"), peak_anno_file, row.names = FALSE)
  
  pdf_file <- file.path(opt$output, paste0(opt$name, "_pie_bp±2000.pdf"))
  pdf(pdf_file, width = 7.5, height = 7.5)
  plot.new()
  text(0.5, 0.5, "Peak file not found")
  dev.off()
  
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
cat("Using OrgDb:", orgdb_name, "\n")

# 动态加载并安装数据库
if (!require(txdb_name, character.only = TRUE)) {
  BiocManager::install(txdb_name, ask = FALSE)
  library(txdb_name, character.only = TRUE)
}
if (!require(orgdb_name, character.only = TRUE)) {
  BiocManager::install(orgdb_name, ask = FALSE)
  library(orgdb_name, character.only = TRUE)
}

txdb <- get(txdb_name)
annoDb_name <- orgdb_name

# 忽略第一个外显子和内含子的分类
options(ChIPseeker.ignore_1st_exon = TRUE)
options(ChIPseeker.ignore_1st_intron = TRUE)

# 读取 Peak 文件
if (file.info(opt$peakfile)$size == 0) {
  cat("Warning: The input Peak file is empty (size 0). MACS2 may not have found any peaks under the current cutoff.\n")
  
  # 创建空的输出文件以满足 Snakemake 期望
  peak_anno_file <- file.path(opt$output, paste0(opt$name, "_peak_anno.csv"))
  write.csv(data.frame(Message="No peaks found"), peak_anno_file, row.names = FALSE)
  
  pdf_file <- file.path(opt$output, paste0(opt$name, "_pie_bp±2000.pdf"))
  pdf(pdf_file, width = 7.5, height = 7.5)
  plot.new()
  text(0.5, 0.5, "No peaks found by MACS2")
  dev.off()
  
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

# 注释 peaks
peakAnno <- annotatePeak(
  peak,
  tssRegion = c(-2000, 2000),  # 定义上下游范围
  TxDb = txdb,                 # 基因注释数据库对象
  annoDb = annoDb_name         # 用于基因注释的数据库
)

# 提取注释数据结果
annotationData <- as.data.frame(peakAnno)

# 统计 peaks 总数
total_peaks <- nrow(annotationData)
cat("Total peaks:", total_peaks, "\n")

# 重新分类注释
annotationData$annotation_category <- ifelse(grepl("Promoter", annotationData$annotation), "Promoter",
                                    ifelse(grepl("Intron", annotationData$annotation), "Intron",
                                           ifelse(grepl("Exon", annotationData$annotation), "Exon",
                                                  ifelse(grepl("3' UTR", annotationData$annotation), "3' UTR",
                                                         ifelse(grepl("Downstream", annotationData$annotation) | grepl("Distal Intergenic", annotationData$annotation), "Intergenic", annotationData$annotation)))))

# 统计每个注释类别的频率
annotationCounts <- table(annotationData$annotation_category)
annotationCounts <- as.data.frame(annotationCounts)
colnames(annotationCounts) <- c("Category", "Count")

# 选择特定的类别
annotationCounts <- annotationCounts[annotationCounts$Category %in% c("Promoter", "Intergenic", "Exon", "Intron", "3' UTR"), ]

# 计算百分比
if(sum(annotationCounts$Count) > 0) {
  annotationCounts$Percentage <- annotationCounts$Count / sum(annotationCounts$Count) * 100
} else {
  annotationCounts$Percentage <- 0
}

# 导出 PDF 饼图
pdf_file <- file.path(opt$output, paste0(opt$name, "_pie_bp±2000.pdf"))
pdf(pdf_file, width = 7.5, height = 7.5)

if (nrow(annotationCounts) > 0 && sum(annotationCounts$Count) > 0) {
  # 动态生成标签
  labels <- annotationCounts$Category
  values <- annotationCounts$Count
  percentages <- paste(format(round(annotationCounts$Percentage, 2), nsmall = 2), "%", sep = "")
  label_text <- paste(labels, "\n(", values, ", ", percentages, ")", sep = "")
  colors <- brewer.pal(max(3, length(annotationCounts$Category)), "Set3")[1:length(annotationCounts$Category)]

  pie(values, labels = label_text, col = colors, border = "white", main = paste(opt$name, "- Total peaks:", total_peaks))
} else {
  plot.new()
  text(0.5, 0.5, "No peaks matched required categories")
}
dev.off()

# 导出完整的注释 CSV
peak_anno_file <- file.path(opt$output, paste0(opt$name, "_peak_anno.csv"))
write.csv(annotationData, peak_anno_file, row.names = FALSE)

# 过滤 Promoter peaks 并导出 CSV
promoter_peaks <- annotationData[grepl("Promoter", annotationData$annotation_category), ]
promoter_file <- file.path(opt$output, paste0(opt$name, "_promoter_peaks.csv"))
write.csv(promoter_peaks, promoter_file, row.names = FALSE)

cat("Annotation finished successfully. Output saved to:", opt$output, "\n")