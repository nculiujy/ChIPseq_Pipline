#!/usr/bin/env Rscript

# =============================================================================
# 5_1_gsea.R — 基于重复组 consensus peaks 的富集分析
# 
# 逻辑：
#   1. 读取 metadata.csv，按 group 列分组
#   2. 对每个 group，找到该组所有样本对的 peak_anno.csv
#   3. 取交集基因（在该组所有重复中都出现的 gene_id）作为 consensus 注释基因
#   4. 对每组的交集基因分别运行 GO + KEGG 富集分析
#   5. 输出每组的富集结果到各自子目录
# =============================================================================

# 强制只使用 conda 环境的 R 库
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
  make_option(c("-m", "--metadata"), type = "character", help = "metadata.csv 路径", default = "config/metadata.csv"),
  make_option(c("-c", "--config"),   type = "character", help = "config.yaml 路径", default = "config/config.yaml"),
  make_option(c("-s", "--species"),  type = "character", help = "物种名，如 TAIR、mm、homo"),
  make_option(c("-e", "--experiment"), type = "character", help = "实验名"),
  make_option("--pvalue_cutoff",     type = "double",    default = 0.05,   help = "p-value 截断值 [default: 0.05]"),
  make_option("--qvalue_cutoff",     type = "double",    default = 0.2,    help = "q-value 截断值 [default: 0.2]"),
  make_option("--go_types",          type = "character", default = "BP,MF,CC", help = "GO 类型，逗号分隔 [default: BP,MF,CC]"),
  make_option("--run_kegg",          type = "logical",   default = TRUE,   action = "store_true",  help = "运行 KEGG [default: TRUE]"),
  make_option("--run_go",            type = "logical",   default = TRUE,   action = "store_true",  help = "运行 GO [default: TRUE]"),
  make_option("--min_gs_size",       type = "integer",   default = 10,     help = "基因集最小大小 [default: 10]"),
  make_option("--max_gs_size",       type = "integer",   default = 500,    help = "基因集最大大小 [default: 500]"),
  make_option("--top_n",             type = "integer",   default = 20,     help = "显示 top N [default: 20]"),
  make_option("--promoter_only",     type = "logical",   default = FALSE,  action = "store_true",  help = "只用 Promoter 区域基因 [default: FALSE]")
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
    cat("警告: 未知物种的 KEGG 代码，使用 'hsa' 作为默认\n")
    "hsa"
  }
)

# ============================================================
# 读取 metadata，按 group 分组
# ============================================================
cat("读取 metadata:", opt$metadata, "\n")
metadata <- read_csv(opt$metadata, show_col_types = FALSE)

# 确保有 group 列
if (!"group" %in% colnames(metadata)) {
  cat("metadata 中无 group 列，将所有样本视为同一组\n")
  metadata$group <- "all"
}

# 过滤空 group
metadata <- metadata %>%
  filter(!is.na(group) & group != "")

if (nrow(metadata) == 0) {
  stop("metadata 中无有效的 group 信息")
}

# 构建 IP_vs_Input pair 名（与 annotation 输出的目录名一致）
metadata <- metadata %>%
  mutate(pair = paste0(`IP sample`, "_vs_", Input))

groups <- unique(metadata$group)
cat("检测到", length(groups), "个重复组:", paste(groups, collapse = ", "), "\n")

# ============================================================
# 辅助函数：从 annotation CSV 提取 gene_id
# ============================================================
extract_genes_from_anno <- function(anno_file, promoter_only = FALSE) {
  tryCatch({
    df <- read_csv(anno_file, show_col_types = FALSE)
    
    # Promoter 过滤
    if (promoter_only && "annotation_category" %in% colnames(df)) {
      df <- df[grepl("Promoter", df$annotation_category, ignore.case = TRUE), ]
    }
    
    # 查找 gene_id 列
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
      return(unique(as.character(ids)))
    } else {
      cat("    警告:", basename(anno_file), "中未找到 gene_id 列\n")
      return(character(0))
    }
  }, error = function(e) {
    cat("    读取", basename(anno_file), "失败:", conditionMessage(e), "\n")
    return(character(0))
  })
}

# ============================================================
# 辅助函数：运行富集分析并输出结果
# ============================================================
run_enrichment <- function(gene_list, group_name, group_outdir) {
  dir.create(group_outdir, recursive = TRUE, showWarnings = FALSE)
  
  cat("  基因数:", length(gene_list), "\n")
  
  if (length(gene_list) == 0) {
    cat("  跳过: 无基因可分析\n")
    write.csv(data.frame(Message = "No genes available for enrichment"),
              file.path(group_outdir, "no_genes.csv"), row.names = FALSE)
    return(invisible(NULL))
  }
  
  # 保存基因列表
  writeLines(gene_list, file.path(group_outdir, "consensus_gene_ids.txt"))
  
  go_types <- strsplit(opt$go_types, ",")[[1]]
  go_types <- trimws(go_types)
  
  # ---- GO 富集 ----
  if (opt$run_go && length(go_types) > 0) {
    cat("  运行 GO 分析...\n")
    for (ont in go_types) {
      tryCatch({
        go_result <- enrichGO(
          gene          = gene_list,
          OrgDb         = orgdb,
          keyType       = "TAIR",
          ont           = ont,
          pAdjustMethod = "BH",
          pvalueCutoff  = opt$pvalue_cutoff,
          qvalueCutoff  = opt$qvalue_cutoff,
          minGSSize     = opt$min_gs_size,
          maxGSSize     = opt$max_gs_size,
          readable      = FALSE
        )
        
        if (!is.null(go_result) && nrow(as.data.frame(go_result)) > 0) {
          result_df <- as.data.frame(go_result)
          write.csv(result_df, file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
          
          n_show <- min(opt$top_n, nrow(result_df))
          p_bar <- barplot(go_result, showCategory = n_show, title = paste(group_name, "- GO", ont))
          ggsave(file.path(group_outdir, paste0("GO_", ont, "_barplot.pdf")), p_bar, width = 10, height = 8)
          p_dot <- dotplot(go_result, showCategory = n_show, title = paste(group_name, "- GO", ont))
          ggsave(file.path(group_outdir, paste0("GO_", ont, "_dotplot.pdf")), p_dot, width = 10, height = 8)
          
          cat("    GO", ont, ":", nrow(result_df), "个显著条目\n")
        } else {
          cat("    GO", ont, ": 无显著富集\n")
          write.csv(data.frame(Message = "No significant enrichment"),
                    file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
        }
      }, error = function(e) {
        cat("    GO", ont, "失败:", conditionMessage(e), "\n")
        write.csv(data.frame(Error = conditionMessage(e)),
                  file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
      })
    }
  }
  
  # ---- KEGG 富集 ----
  if (opt$run_kegg) {
    cat("  运行 KEGG 分析...\n")
    tryCatch({
      kegg_result <- enrichKEGG(
        gene          = gene_list,
        organism      = kegg_org,
        pAdjustMethod = "BH",
        pvalueCutoff  = opt$pvalue_cutoff,
        qvalueCutoff  = opt$qvalue_cutoff,
        minGSSize     = opt$min_gs_size,
        maxGSSize     = opt$max_gs_size
      )
      
      if (!is.null(kegg_result) && nrow(as.data.frame(kegg_result)) > 0) {
        result_df <- as.data.frame(kegg_result)
        write.csv(result_df, file.path(group_outdir, "KEGG_pathway_enrichment.csv"), row.names = FALSE)
        
        n_show <- min(opt$top_n, nrow(result_df))
        p_bar <- barplot(kegg_result, showCategory = n_show, title = paste(group_name, "- KEGG Pathway"))
        ggsave(file.path(group_outdir, "KEGG_barplot.pdf"), p_bar, width = 10, height = 8)
        p_dot <- dotplot(kegg_result, showCategory = n_show, title = paste(group_name, "- KEGG Pathway"))
        ggsave(file.path(group_outdir, "KEGG_dotplot.pdf"), p_dot, width = 10, height = 8)
        
        cat("    KEGG:", nrow(result_df), "个显著通路\n")
      } else {
        cat("    KEGG: 无显著富集\n")
        write.csv(data.frame(Message = "No significant enrichment"),
                  file.path(group_outdir, "KEGG_pathway_enrichment.csv"), row.names = FALSE)
      }
    }, error = function(e) {
      cat("    KEGG 失败:", conditionMessage(e), "\n")
      write.csv(data.frame(Error = conditionMessage(e)),
                file.path(group_outdir, "KEGG_pathway_enrichment.csv"), row.names = FALSE)
    })
  }
}

# ============================================================
# 主逻辑：对每个 group 取交集基因并做富集
# ============================================================
cat("\n=== 开始按重复组进行 GSEA 分析 ===\n")
cat("注释结果目录:", opt$indir, "\n\n")

total_groups <- 0
total_genes_all <- c()

for (grp in groups) {
  cat("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
  cat("处理重复组:", grp, "\n")
  
  # 该组的所有样本对
  grp_pairs <- metadata %>% filter(group == grp) %>% pull(pair)
  cat("  包含样本对:", paste(grp_pairs, collapse = ", "), "\n")
  
  # 查找每个样本对的 annotation 文件
  gene_sets <- list()
  for (pair in grp_pairs) {
    # annotation 文件路径模式: indir/pair/pair_peak_anno.csv
    # 或递归搜索
    anno_pattern <- paste0(pair, "_peak_anno.csv")
    anno_files <- list.files(opt$indir, pattern = anno_pattern, full.names = TRUE, recursive = TRUE)
    
    if (length(anno_files) > 0) {
      genes <- extract_genes_from_anno(anno_files[1], promoter_only = opt$promoter_only)
      if (length(genes) > 0) {
        gene_sets[[pair]] <- genes
        cat("    ", pair, ":", length(genes), "个基因\n")
      } else {
        cat("    ", pair, ": 提取到0个基因（跳过）\n")
      }
    } else {
      cat("    ", pair, ": 未找到 annotation 文件\n")
    }
  }
  
  if (length(gene_sets) == 0) {
    cat("  跳过组", grp, ": 无有效 annotation 数据\n\n")
    next
  }
  
  # ---- 取交集基因（在所有重复中都出现的 gene_id）----
  consensus_genes <- Reduce(intersect, gene_sets)
  cat("  交集基因数:", length(consensus_genes),
      "（来自", length(gene_sets), "个重复）\n")
  
  if (length(consensus_genes) == 0) {
    cat("  警告: 交集为空，改用并集（union）\n")
    consensus_genes <- unique(unlist(gene_sets))
    cat("  并集基因数:", length(consensus_genes), "\n")
  }
  
  total_genes_all <- c(total_genes_all, consensus_genes)
  
  # 为该组创建子目录并运行富集
  group_outdir <- file.path(opt$outdir, grp)
  run_enrichment(consensus_genes, grp, group_outdir)
  
  total_groups <- total_groups + 1
  cat("\n")
}

# ============================================================
# 汇总：所有组的并集基因也做一次整体富集
# ============================================================
if (total_groups > 1) {
  cat("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
  cat("运行所有组合并的整体富集分析...\n")
  all_unique_genes <- unique(total_genes_all)
  cat("  所有组并集基因数:", length(all_unique_genes), "\n")
  
  all_outdir <- file.path(opt$outdir, "all_groups_combined")
  run_enrichment(all_unique_genes, "All Groups", all_outdir)
}

# ============================================================
# 写入完成标记
# ============================================================
summary_lines <- c(
  paste("Completed:", Sys.time()),
  paste("Groups analyzed:", total_groups),
  paste("Group names:", paste(groups, collapse = ", ")),
  paste("Total unique genes (union):", length(unique(total_genes_all))),
  paste("GO types:", opt$go_types),
  paste("KEGG analyzed:", opt$run_kegg),
  paste("Promoter only:", opt$promoter_only)
)
writeLines(summary_lines, file.path(opt$outdir, "gsea_finished.txt"))

cat("\n=== GSEA 分析完成 ===\n")
cat("输出目录:", opt$outdir, "\n")
cat("分析了", total_groups, "个重复组\n")
