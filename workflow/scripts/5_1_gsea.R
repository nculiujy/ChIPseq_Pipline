#!/usr/bin/env Rscript

# 强制只使用 conda 环境的 R 库，防止混入其他用户库路径
conda_prefix <- Sys.getenv("CONDA_PREFIX")
if (nchar(conda_prefix) > 0) {
  conda_r_lib <- file.path(conda_prefix, "lib", "R", "library")
  if (dir.exists(conda_r_lib)) {
    .libPaths(conda_r_lib)
    cat("R library path set to conda env:", conda_r_lib, "\n")
  }
}

suppressPackageStartupMessages({
  library(optparse)
  library(yaml)
  library(clusterProfiler)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(stringr)
})

# ============================================================
# 命令行参数
# ============================================================
option_list <- list(
  make_option(c("-i", "--indir"),    type = "character", help = "annotation 结果目录（含 *_peak_anno.csv）"),
  make_option(c("-o", "--outdir"),   type = "character", help = "GSEA 输出目录"),
  make_option(c("-c", "--config"),   type = "character", help = "config.yaml 路径", default = "config/config.yaml"),
  make_option(c("-s", "--species"),  type = "character", help = "物种名，如 TAIR、mm、homo"),
  make_option(c("-e", "--experiment"), type = "character", help = "实验名"),
  make_option("--pvalue_cutoff",     type = "double",    default = 0.05,   help = "富集分析 p-value 截断值 [default: 0.05]"),
  make_option("--qvalue_cutoff",     type = "double",    default = 0.2,    help = "富集分析 q-value 截断值 [default: 0.2]"),
  make_option("--go_types",          type = "character", default = "BP,MF,CC", help = "GO 类型，逗号分隔 [default: BP,MF,CC]"),
  make_option("--run_kegg",          type = "logical",   default = TRUE,   action = "store_true",  help = "是否运行 KEGG 分析 [default: TRUE]"),
  make_option("--run_go",            type = "logical",   default = TRUE,   action = "store_true",  help = "是否运行 GO 分析 [default: TRUE]"),
  make_option("--min_gs_size",       type = "integer",   default = 10,     help = "基因集最小大小 [default: 10]"),
  make_option("--max_gs_size",       type = "integer",   default = 500,    help = "基因集最大大小 [default: 500]"),
  make_option("--top_n",             type = "integer",   default = 20,     help = "显示 top N 条目 [default: 20]"),
  make_option("--promoter_only",     type = "logical",   default = FALSE,  action = "store_true",  help = "只使用 Promoter 区域的基因 [default: FALSE]")
)

opt_parser <- OptionParser(option_list = option_list)
opt <- parse_args(opt_parser)

if (is.null(opt$indir) || is.null(opt$outdir) || is.null(opt$species)) {
  stop("必须提供 --indir, --outdir, --species 参数")
}

dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)

# ============================================================
# 读取 config 获取 orgdb
# ============================================================
config <- yaml.load_file(opt$config)
orgdb_name <- NULL

for (p in config$projects) {
  if (!is.null(p$species) && p$species == opt$species) {
    if (!is.null(opt$experiment) && !is.null(p$experiment) && p$experiment == opt$experiment) {
      orgdb_name <- p$orgdb
      break
    }
    if (is.null(opt$experiment)) {
      orgdb_name <- p$orgdb
      break
    }
  }
}

# fallback
if (is.null(orgdb_name)) {
  orgdb_name <- switch(tolower(opt$species),
    "tair"  = "org.At.tair.db",
    "mm"    = "org.Mm.eg.db",
    "homo"  = "org.Hs.eg.db",
    "hs"    = "org.Hs.eg.db",
    stop("不支持的物种，请在 config.yaml 中配置 orgdb")
  )
}

cat("Using OrgDb:", orgdb_name, "\n")
library(orgdb_name, character.only = TRUE)
orgdb <- get(orgdb_name)

# 物种 KEGG 代码
kegg_org <- switch(tolower(opt$species),
  "tair"  = "ath",
  "mm"    = "mmu",
  "homo"  = "hsa",
  "hs"    = "hsa",
  {
    cat("警告: 未知物种的 KEGG 代码，尝试使用 KEGG 'hsa' 作为测试，请在 config.yaml 中配置\n")
    "hsa"
  }
)

# ============================================================
# 整合注释结果，提取并去重 gene_id
# ============================================================
cat("扫描注释结果目录:", opt$indir, "\n")
anno_files <- list.files(opt$indir, pattern = "_peak_anno\\.csv$", full.names = TRUE, recursive = TRUE)

if (length(anno_files) == 0) {
  stop("未找到任何 *_peak_anno.csv 文件，请先运行 4_annotation 步骤")
}

cat("找到", length(anno_files), "个注释文件\n")

all_genes <- c()
for (f in anno_files) {
  tryCatch({
    df <- read_csv(f, show_col_types = FALSE)
    
    # 根据是否只用 Promoter 过滤
    if (opt$promoter_only && "annotation_category" %in% colnames(df)) {
      df <- df[grepl("Promoter", df$annotation_category, ignore.case = TRUE), ]
    }
    
    # 提取 gene_id 列（ChIPseeker 输出的字段名）
    gene_col <- NULL
    for (col in c("geneId", "gene_id", "GENEID", "geneid")) {
      if (col %in% colnames(df)) {
        gene_col <- col
        break
      }
    }
    
    if (!is.null(gene_col)) {
      ids <- df[[gene_col]]
      ids <- ids[!is.na(ids) & ids != "" & ids != "NA"]
      all_genes <- c(all_genes, ids)
      cat("  从", basename(f), "提取到", length(ids), "个 gene_id\n")
    } else {
      cat("  警告:", basename(f), "中未找到 gene_id 列（尝试了: geneId, gene_id, GENEID, geneid）\n")
    }
  }, error = function(e) {
    cat("  读取", basename(f), "失败:", conditionMessage(e), "\n")
  })
}

# 去重
all_genes <- unique(as.character(all_genes))
cat("整合后去重 gene_id 共:", length(all_genes), "个\n")

if (length(all_genes) == 0) {
  stop("没有提取到任何 gene_id，请检查注释结果文件")
}

# 保存整合基因列表
gene_list_file <- file.path(opt$outdir, "integrated_gene_ids.txt")
writeLines(all_genes, gene_list_file)
cat("整合基因列表保存至:", gene_list_file, "\n")

# ============================================================
# GO 富集分析
# ============================================================
go_types <- strsplit(opt$go_types, ",")[[1]]
go_types <- trimws(go_types)

if (opt$run_go && length(go_types) > 0) {
  cat("\n--- 开始 GO 富集分析 ---\n")
  
  for (ont in go_types) {
    cat("GO Ontology:", ont, "\n")
    
    tryCatch({
      go_result <- enrichGO(
        gene          = all_genes,
        OrgDb         = orgdb,
        keyType       = "TAIR",   # 会根据物种自动调整
        ont           = ont,
        pAdjustMethod = "BH",
        pvalueCutoff  = opt$pvalue_cutoff,
        qvalueCutoff  = opt$qvalue_cutoff,
        minGSSize     = opt$min_gs_size,
        maxGSSize     = opt$max_gs_size,
        readable      = FALSE
      )
      
      if (!is.null(go_result) && nrow(as.data.frame(go_result)) > 0) {
        # 保存结果 CSV
        result_df <- as.data.frame(go_result)
        csv_file <- file.path(opt$outdir, paste0("GO_", ont, "_enrichment.csv"))
        write.csv(result_df, csv_file, row.names = FALSE)
        cat("  GO", ont, "结果保存至:", csv_file, "\n")
        
        # 柱状图
        n_show <- min(opt$top_n, nrow(result_df))
        p_bar <- barplot(go_result, showCategory = n_show, title = paste("GO", ont, "Enrichment"))
        ggsave(file.path(opt$outdir, paste0("GO_", ont, "_barplot.pdf")), p_bar, width = 10, height = 8)
        
        # 点图
        p_dot <- dotplot(go_result, showCategory = n_show, title = paste("GO", ont, "Enrichment"))
        ggsave(file.path(opt$outdir, paste0("GO_", ont, "_dotplot.pdf")), p_dot, width = 10, height = 8)
        
        cat("  GO", ont, "图表已生成\n")
      } else {
        cat("  GO", ont, ": 无显著富集结果（p <", opt$pvalue_cutoff, "）\n")
        # 生成空白占位文件
        write.csv(data.frame(Message = "No significant enrichment"), 
                  file.path(opt$outdir, paste0("GO_", ont, "_enrichment.csv")),
                  row.names = FALSE)
      }
    }, error = function(e) {
      cat("  GO", ont, "分析失败:", conditionMessage(e), "\n")
      cat("  提示: 确认 OrgDb 中的 keyType 与 gene_id 格式匹配\n")
      write.csv(data.frame(Error = conditionMessage(e)),
                file.path(opt$outdir, paste0("GO_", ont, "_enrichment.csv")),
                row.names = FALSE)
    })
  }
}

# ============================================================
# KEGG 通路富集分析
# ============================================================
if (opt$run_kegg) {
  cat("\n--- 开始 KEGG 通路富集分析 ---\n")
  
  tryCatch({
    # 对于拟南芥，gene_id 已是 TAIR ID，KEGG 直接支持
    kegg_result <- enrichKEGG(
      gene          = all_genes,
      organism      = kegg_org,
      pAdjustMethod = "BH",
      pvalueCutoff  = opt$pvalue_cutoff,
      qvalueCutoff  = opt$qvalue_cutoff,
      minGSSize     = opt$min_gs_size,
      maxGSSize     = opt$max_gs_size
    )
    
    if (!is.null(kegg_result) && nrow(as.data.frame(kegg_result)) > 0) {
      result_df <- as.data.frame(kegg_result)
      csv_file <- file.path(opt$outdir, "KEGG_pathway_enrichment.csv")
      write.csv(result_df, csv_file, row.names = FALSE)
      cat("KEGG 结果保存至:", csv_file, "\n")
      
      n_show <- min(opt$top_n, nrow(result_df))
      p_bar <- barplot(kegg_result, showCategory = n_show, title = "KEGG Pathway Enrichment")
      ggsave(file.path(opt$outdir, "KEGG_barplot.pdf"), p_bar, width = 10, height = 8)
      
      p_dot <- dotplot(kegg_result, showCategory = n_show, title = "KEGG Pathway Enrichment")
      ggsave(file.path(opt$outdir, "KEGG_dotplot.pdf"), p_dot, width = 10, height = 8)
      
      cat("KEGG 图表已生成\n")
    } else {
      cat("KEGG: 无显著富集结果（p <", opt$pvalue_cutoff, "）\n")
      write.csv(data.frame(Message = "No significant enrichment"),
                file.path(opt$outdir, "KEGG_pathway_enrichment.csv"),
                row.names = FALSE)
    }
  }, error = function(e) {
    cat("KEGG 分析失败:", conditionMessage(e), "\n")
    write.csv(data.frame(Error = conditionMessage(e)),
              file.path(opt$outdir, "KEGG_pathway_enrichment.csv"),
              row.names = FALSE)
  })
}

# ============================================================
# 写入完成标记
# ============================================================
writeLines(c(
  paste("Completed:", Sys.time()),
  paste("Total unique genes:", length(all_genes)),
  paste("GO types analyzed:", opt$go_types),
  paste("KEGG analyzed:", opt$run_kegg)
), file.path(opt$outdir, "gsea_finished.txt"))

cat("\n=== GSEA 分析完成 ===\n")
cat("输出目录:", opt$outdir, "\n")
