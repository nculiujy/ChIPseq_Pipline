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
  make_option("--promoter_only",     type = "logical",   default = FALSE,  action = "store_true",  help = "只用 Promoter 区域基因 [default: FALSE]"),
  # ---- 无官方 OrgDb 的物种：用 gene2go 生成的 TERM2GENE（enricher）----
  make_option("--term2gene_go",      type = "character", default = NULL,   help = "自定义 GO 基因集，形如 bp=/p/a.tsv,mf=/p/b.tsv（覆盖 config）"),
  make_option("--term2name_go",      type = "character", default = NULL,   help = "GO term 名称表，同 --term2gene_go 格式"),
  make_option("--id_strip",          type = "character", default = NULL,   help = "正则：去掉该前缀即得到 gene2go/KEGG 的 ID，如 ^(gene-)?LOC"),
  make_option("--universe",          type = "character", default = NULL,   help = "背景基因集文件（每行一个 ID，可选）")
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

# ============================================================
# 基因集来源 2：gene2go 生成的 TERM2GENE（无官方 OrgDb 的物种走这条）
# 项目级优先，其次 config$gsea 全局；路径可绝对可相对项目根
# ============================================================
term2gene_go <- list()
term2name_go <- list()
id_strip <- ""
universe_file <- ""
for (p in config$projects) {
  if (is.null(p$species) || p$species != opt$species) next
  if (!is.null(opt$experiment) && !is.null(p$experiment) && p$experiment != opt$experiment) next
  if (!is.null(p$term2gene_go)) term2gene_go <- p$term2gene_go
  if (!is.null(p$term2name_go)) term2name_go <- p$term2name_go
  if (!is.null(p$id_strip) && nzchar(p$id_strip)) id_strip <- p$id_strip
  if (!is.null(p$universe) && nzchar(p$universe)) universe_file <- p$universe
  break
}
if (length(term2gene_go) == 0 && !is.null(config$gsea$term2gene_go)) {
  term2gene_go <- config$gsea$term2gene_go
}
if (!nzchar(id_strip) && !is.null(config$gsea$id_strip)) id_strip <- config$gsea$id_strip
if (!nzchar(universe_file) && !is.null(config$gsea$universe)) universe_file <- config$gsea$universe

# 命令行覆盖（手动跑时用）
if (!is.null(opt$term2gene_go) && nzchar(opt$term2gene_go)) {
  term2gene_go <- list()
  for (kv in strsplit(opt$term2gene_go, ",")[[1]]) {
    parts <- strsplit(trimws(kv), "=")[[1]]
    if (length(parts) == 2 && nzchar(parts[1]) && nzchar(parts[2])) {
      term2gene_go[[tolower(parts[1])]] <- parts[2]
    }
  }
}
if (!is.null(opt$term2name_go) && nzchar(opt$term2name_go)) {
  term2name_go <- list()
  for (kv in strsplit(opt$term2name_go, ",")[[1]]) {
    parts <- strsplit(trimws(kv), "=")[[1]]
    if (length(parts) == 2 && nzchar(parts[1]) && nzchar(parts[2])) {
      term2name_go[[tolower(parts[1])]] <- parts[2]
    }
  }
}
if (!is.null(opt$id_strip)) id_strip <- opt$id_strip
if (!is.null(opt$universe) && nzchar(opt$universe)) universe_file <- opt$universe

if (length(term2gene_go) > 0) {
  names(term2gene_go) <- tolower(names(term2gene_go))
  term2name_go <- if (length(term2name_go) > 0) term2name_go else list()
  cat("自定义基因集(TERM2GENE):", paste(names(term2gene_go), collapse = ", "), "\n")
}
if (nzchar(id_strip)) cat("基因 ID 清洗正则:", id_strip, "\n")

# fallback (支持带版本号的物种名)
if (is.null(orgdb_name) || orgdb_name == "") {
  sp <- tolower(opt$species)
  orgdb_name <- if (startsWith(sp, "tair") || sp == "at" || sp == "arabidopsis") {
    "org.At.tair.db"
  } else if (startsWith(sp, "mm") || startsWith(sp, "mus")) {
    "org.Mm.eg.db"
  } else if (startsWith(sp, "hg") || startsWith(sp, "homo") || sp == "hs") {
    "org.Hs.eg.db"
  } else if (startsWith(sp, "dm") || startsWith(sp, "drosophila")) {
    "org.Dm.eg.db"
  } else if (startsWith(sp, "ce") || startsWith(sp, "caenorhabditis")) {
    "org.Ce.eg.db"
  } else if (startsWith(sp, "danio") || startsWith(sp, "dr")) {
    "org.Dr.eg.db"
  } else if (startsWith(sp, "sc") || startsWith(sp, "saccharomyces")) {
    "org.Sc.sgd.db"
  } else {
    # 大豆等非模式物种无官方 OrgDb 包：先去物种注释页自建包或生成 gene2go 基因集
    cat("提示: 未配置 orgdb 且无该物种的默认 OrgDb（大豆等需自建），",
        "可到「物种注释 → OrgDb 构建 / ID 映射」生成后用 enricher 跑 GO\n")
    NULL
  }
}

orgdb <- NULL
if (!is.null(orgdb_name) && nzchar(orgdb_name)) {
  cat("Using OrgDb:", orgdb_name, "\n")
  orgdb_ok <- tryCatch({
    suppressWarnings(library(orgdb_name, character.only = TRUE))
    TRUE
  }, error = function(e) {
    cat("警告: 无法加载 OrgDb 包", orgdb_name, ":", conditionMessage(e), "\n")
    FALSE
  })
  if (orgdb_ok) {
    orgdb <- get(orgdb_name)
  }
}
if (is.null(orgdb)) {
  if (length(term2gene_go) > 0) {
    cat("提示: 无 OrgDb，GO 富集将使用自建 TERM2GENE 基因集（enricher）\n")
  } else {
    cat("警告: OrgDb 不可用，且未配置 term2gene 基因集，GO 富集将被跳过\n")
  }
}

# 物种 KEGG 代码（支持带版本号的物种名，如 TAIR10、mm10 等）
species_lower <- tolower(opt$species)
kegg_org <- if (startsWith(species_lower, "tair") || species_lower == "at" || species_lower == "arabidopsis") {
  "ath"
} else if (startsWith(species_lower, "mm") || startsWith(species_lower, "mus")) {
  "mmu"
} else if (startsWith(species_lower, "hg") || startsWith(species_lower, "homo") || species_lower == "hs") {
  "hsa"
} else if (startsWith(species_lower, "dm") || startsWith(species_lower, "drosophila")) {
  "dme"
} else if (startsWith(species_lower, "ce") || startsWith(species_lower, "caenorhabditis")) {
  "cel"
} else if (startsWith(species_lower, "danio") || startsWith(species_lower, "dr")) {
  "dre"
} else if (startsWith(species_lower, "sc") || startsWith(species_lower, "saccharomyces")) {
  "sce"
} else if (startsWith(species_lower, "glyma") || startsWith(species_lower, "glycine") || species_lower == "gmax" || startsWith(species_lower, "soy")) {
  "gmx"
} else {
  cat("警告: 未知物种的 KEGG 代码，跳过 KEGG 分析\n")
  NULL
}
cat("KEGG organism code:", if (is.null(kegg_org)) "无" else kegg_org, "\n")

# enrichGO 的 keyType（TAIR 用 TAIR ID，其余默认 ENTREZID）
go_keytype <- if (startsWith(species_lower, "tair") || species_lower == "at" || species_lower == "arabidopsis") {
  "TAIR"
} else {
  "ENTREZID"
}

# ============================================================
# 读取 metadata，按 group 分组
# ============================================================
cat("读取 metadata:", opt$metadata, "\n")
metadata <- read_csv(opt$metadata, show_col_types = FALSE)

# metadata.csv 可带可选的 project 列（值形如 TAIR10/314Com5）。
# project 为空的行走“通用”语义，兼容老的两三列格式；写了 project 的行只属于
# 那一个项目，避免多项目共用一张表时把别人的样本也算进本项目。
if ("project" %in% colnames(metadata)) {
  this_project <- paste0(opt$species, "/", opt$experiment)
  project_col <- trimws(as.character(metadata$project))
  project_col[is.na(project_col)] <- ""
  metadata <- metadata[project_col == "" | project_col == this_project, ]
  cat("按项目过滤后保留", nrow(metadata), "行 (project =", this_project, ")\n")
}

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
# 辅助函数：ID 对齐 + 自建基因集读取
# ============================================================
# ChIPseeker 吐出的 geneId 与 gene2go / KEGG 用的 ID 常常差一个前缀：
#   LOC100808170 / gene-LOC100808170 / 100808170
# 靠 id_strip 这个正则对齐；缺了它，enrichKEGG/enricher 会“成功但全部为空”。
normalize_ids <- function(ids, pattern = id_strip) {
  ids <- unique(as.character(ids))
  ids <- ids[!is.na(ids) & ids != "" & ids != "NA"]
  if (!is.null(pattern) && length(pattern) == 1 && nzchar(pattern)) {
    ids <- unique(sub(pattern, "", ids))
  }
  ids[nzchar(ids)]
}

.t2g_cache <- new.env(parent = emptyenv())

get_term2gene <- function(ont) {
  key <- paste0("t2g_", tolower(ont))
  if (!is.null(.t2g_cache[[key]])) return(.t2g_cache[[key]])
  f <- term2gene_go[[tolower(ont)]]
  if (is.null(f) || !file.exists(f)) return(NULL)
  df <- read.delim(f, header = FALSE, stringsAsFactors = FALSE, quote = "",
                   comment.char = "", col.names = c("term", "gene"))
  df <- unique(df[nzchar(df$term) & nzchar(df$gene), , drop = FALSE])
  .t2g_cache[[key]] <- df
  df
}

get_term2name <- function(ont) {
  f <- term2name_go[[tolower(ont)]]
  if (is.null(f) || !file.exists(f)) return(NA)
  read.delim(f, header = FALSE, stringsAsFactors = FALSE, quote = "",
             comment.char = "", col.names = c("term", "name"))
}

# 背景基因集：有就用它收窄背景，没有就让 clusterProfiler 用全库
load_universe <- function() {
  if (!nzchar(universe_file) || !file.exists(universe_file)) return(NULL)
  u <- normalize_ids(readLines(universe_file, warn = FALSE))
  if (length(u) == 0) NULL else u
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
  # 对齐到 gene2go/KEGG 的 ID 形状后再跑富集
  gene_list_norm <- normalize_ids(gene_list)
  if (!identical(gene_list_norm, unique(gene_list))) {
    writeLines(gene_list_norm, file.path(group_outdir, "consensus_gene_ids_normalized.txt"))
    cat("  ID 清洗后:", length(gene_list_norm), "/", length(unique(gene_list)), "个（剩下的是被 strip 去重掉的）\n")
  }
  
  go_types <- strsplit(opt$go_types, ",")[[1]]
  go_types <- trimws(go_types)
  
  # ---- GO 富集 ----
  if (opt$run_go && length(go_types) > 0) {
    # 无 OrgDb 但有自建 TERM2GENE → 走 enricher；两者都没有才跳过
    use_custom_go <- is.null(orgdb) && length(term2gene_go) > 0
    if (is.null(orgdb) && !use_custom_go) {
      cat("  跳过 GO 分析: OrgDb 不可用，且未配置 term2gene 基因集\n")
      for (ont in go_types) {
        write.csv(data.frame(Message = "Skipped: no OrgDb and no TERM2GENE gene sets"),
                  file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
      }
    } else {
      cat(if (use_custom_go) "  运行 GO 分析（enricher + 自建基因集）...\n" else "  运行 GO 分析...\n")
      uni <- load_universe()
      for (ont in go_types) {
      tryCatch({
        go_result <- NULL
        if (use_custom_go) {
          t2g <- get_term2gene(ont)
          if (is.null(t2g)) {
            cat("    GO", ont, ": 未配置该类 term2gene，跳过\n")
            write.csv(data.frame(Message = paste("Skipped: no TERM2GENE for", ont)),
                      file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
          } else {
            go_result <- enricher(
              gene          = gene_list_norm,
              TERM2GENE     = t2g,
              TERM2NAME     = get_term2name(ont),
              universe      = uni,
              pAdjustMethod = "BH",
              pvalueCutoff  = opt$pvalue_cutoff,
              qvalueCutoff  = opt$qvalue_cutoff,
              minGSSize     = opt$min_gs_size,
              maxGSSize     = opt$max_gs_size
            )
          }
        } else {
          go_result <- enrichGO(
            gene          = gene_list_norm,
            OrgDb         = orgdb,
            keyType       = go_keytype,
            ont           = ont,
            pAdjustMethod = "BH",
            pvalueCutoff  = opt$pvalue_cutoff,
            qvalueCutoff  = opt$qvalue_cutoff,
            minGSSize     = opt$min_gs_size,
            maxGSSize     = opt$max_gs_size,
            readable      = FALSE
          )
        }
        
        if (!is.null(go_result) && nrow(as.data.frame(go_result)) > 0) {
          result_df <- as.data.frame(go_result)
          write.csv(result_df, file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv")), row.names = FALSE)
          
          n_show <- min(opt$top_n, nrow(result_df))
          p_bar <- barplot(go_result, showCategory = n_show, title = paste(group_name, "- GO", ont))
          ggsave(file.path(group_outdir, paste0("GO_", ont, "_barplot.pdf")), p_bar, width = 10, height = 8)
          p_dot <- dotplot(go_result, showCategory = n_show, title = paste(group_name, "- GO", ont))
          ggsave(file.path(group_outdir, paste0("GO_", ont, "_dotplot.pdf")), p_dot, width = 10, height = 8)
          
          cat("    GO", ont, ":", nrow(result_df), "个显著条目\n")
        } else if (!is.null(go_result)) {
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
  }
  
  # ---- GO 综合图（BP+MF+CC 合并在一张图，按 Term type 颜色区分）----
  if (opt$run_go && length(go_types) > 0) {
    tryCatch({
      combined_go_data <- data.frame()
      ont_label_map <- c("BP" = "Biological process", "MF" = "Molecular function", "CC" = "Cellular component")
      
      for (ont in go_types) {
        csv_path <- file.path(group_outdir, paste0("GO_", ont, "_enrichment.csv"))
        if (file.exists(csv_path)) {
          df <- read.csv(csv_path, stringsAsFactors = FALSE)
          # 过滤掉无实际数据的情况
          if (nrow(df) > 0 && "Description" %in% colnames(df)) {
            n_take <- min(10, nrow(df))  # 每种类型取 top 10
            df <- df[seq_len(n_take), ]
            df$Term_type <- ont_label_map[ont]
            combined_go_data <- rbind(combined_go_data, df)
          }
        }
      }
      
      if (nrow(combined_go_data) > 0 && "pvalue" %in% colnames(combined_go_data)) {
        combined_go_data$neg_log10_p <- -log10(combined_go_data$pvalue)
        combined_go_data$Term_type <- factor(combined_go_data$Term_type,
                                             levels = c("Biological process", "Molecular function", "Cellular component"))
        # 按 Term_type 和 neg_log10_p 排序
        combined_go_data <- combined_go_data[order(combined_go_data$Term_type, -combined_go_data$neg_log10_p), ]
        combined_go_data$Description <- factor(combined_go_data$Description,
                                               levels = rev(combined_go_data$Description))
        
        # 综合 barplot（类似截图风格：横向条形图按颜色区分 Term type）
        p_combined_bar <- ggplot(combined_go_data, aes(x = Description, y = neg_log10_p, fill = Term_type)) +
          geom_bar(stat = "identity", width = 0.7) +
          coord_flip() +
          scale_fill_manual(values = c("Biological process" = "#F5A623",
                                       "Molecular function" = "#7EC8E3",
                                       "Cellular component" = "#2ECC71")) +
          labs(x = NULL, y = expression(-log[10](italic(P)-values)),
               title = paste(group_name, "- GO Enrichment (Top terms)"),
               fill = "Term type") +
          theme_bw() +
          theme(axis.text.y = element_text(size = 8),
                legend.position = "right",
                plot.title = element_text(hjust = 0.5))
        
        plot_h <- max(6, nrow(combined_go_data) * 0.25)
        ggsave(file.path(group_outdir, "GO_barplot_combined.pdf"), p_combined_bar,
               width = 12, height = plot_h)
        ggsave(file.path(group_outdir, "GO_barplot_combined.png"), p_combined_bar,
               width = 12, height = plot_h, dpi = 200, device = grDevices::png, type = "cairo")
        
        # 综合 dotplot（气泡图：x = GeneRatio, size = Count, color = p.adjust）
        if ("GeneRatio" %in% colnames(combined_go_data) && "Count" %in% colnames(combined_go_data)) {
          # 解析 GeneRatio (格式 "12/200")
          combined_go_data$GeneRatio_num <- sapply(combined_go_data$GeneRatio, function(x) {
            parts <- strsplit(as.character(x), "/")[[1]]
            if (length(parts) == 2) as.numeric(parts[1]) / as.numeric(parts[2]) else NA
          })
          
          p_combined_dot <- ggplot(combined_go_data, aes(x = GeneRatio_num, y = Description,
                                                          size = Count, color = p.adjust)) +
            geom_point() +
            scale_color_gradient(low = "red", high = "blue") +
            scale_size_continuous(range = c(3, 10)) +
            facet_grid(Term_type ~ ., scales = "free_y", space = "free_y") +
            labs(x = "Gene Ratio", y = NULL, color = "p.adjust", size = "Count",
                 title = paste(group_name, "- GO Enrichment Dotplot")) +
            theme_bw() +
            theme(axis.text.y = element_text(size = 8),
                  strip.text.y = element_text(angle = 0),
                  plot.title = element_text(hjust = 0.5))
          
          plot_h2 <- max(6, nrow(combined_go_data) * 0.3)
          ggsave(file.path(group_outdir, "GO_dotplot_combined.pdf"), p_combined_dot,
                 width = 12, height = plot_h2)
          ggsave(file.path(group_outdir, "GO_dotplot_combined.png"), p_combined_dot,
                 width = 12, height = plot_h2, dpi = 200, device = grDevices::png, type = "cairo")
        }
        
        cat("    生成 GO 综合可视化: barplot_top100 + dotplot_combined\n")
      }
    }, error = function(e) {
      cat("    GO 综合图生成失败:", conditionMessage(e), "\n")
    })
  }
  
  # ---- KEGG 富集 ----
  if (opt$run_kegg) {
    # KEGG 只要 NCBI/Entrez ID，不依赖 OrgDb：无 OrgDb 的物种也能跑
    if (is.null(kegg_org)) {
      reason <- "未知物种的 KEGG 代码"
      cat("  跳过 KEGG 分析:", reason, "\n")
      write.csv(data.frame(Message = paste("Skipped:", reason)),
                file.path(group_outdir, "KEGG_pathway_enrichment.csv"), row.names = FALSE)
    } else {
      cat("  运行 KEGG 分析...\n")
    tryCatch({
      kegg_result <- enrichKEGG(
        gene          = gene_list_norm,
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
  
  # 优先从 consensus annotation 读取基因列表（更准确：基于合并后的 peaks 注释）
  consensus_anno_file <- file.path(opt$indir, paste0("consensus_", grp),
                                    paste0("consensus_", grp, "_peak_anno.csv"))
  
  consensus_genes <- character(0)
  
  if (file.exists(consensus_anno_file)) {
    cat("  使用 consensus annotation 文件:", basename(consensus_anno_file), "\n")
    consensus_genes <- extract_genes_from_anno(consensus_anno_file, promoter_only = opt$promoter_only)
    cat("  从 consensus peaks 注释中提取到", length(consensus_genes), "个基因\n")
  }
  
  # 如果 consensus annotation 不存在或提取到 0 个基因，回退到原始逻辑（从各单样本取交集）
  if (length(consensus_genes) == 0) {
    if (file.exists(consensus_anno_file)) {
      cat("  警告: consensus annotation 未提取到基因，回退到单样本交集模式\n")
    } else {
      cat("  consensus annotation 文件不存在，使用单样本交集模式\n")
    }
    
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
  paste("Gene set source:", if (!is.null(orgdb)) paste0("OrgDb:", orgdb_name) else
        if (length(term2gene_go) > 0) paste0("TERM2GENE:", paste(names(term2gene_go), collapse = "/")) else "none"),
  paste("ID strip:", if (nzchar(id_strip)) id_strip else "(none)"),
  paste("KEGG analyzed:", opt$run_kegg),
  paste("Promoter only:", opt$promoter_only)
)
writeLines(summary_lines, file.path(opt$outdir, "gsea_finished.txt"))

cat("\n=== GSEA 分析完成 ===\n")
cat("输出目录:", opt$outdir, "\n")
cat("分析了", total_groups, "个重复组\n")
