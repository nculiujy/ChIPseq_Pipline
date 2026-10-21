#!/usr/bin/env Rscript
# ChIPseq Pipeline — R 包幂等安装/补齐脚本
#
# 用法：
#   Rscript workflow/env/install_R_packages.R
#
# 支持的环境变量：
#   CHIPSEQ_CRAN_MIRROR   CRAN 镜像 URL，默认清华
#   CHIPSEQ_R_LIB         强制指定安装目录；默认自动挑一个可写 lib（系统不可写则 ~/R）
#   CHIPSEQ_R_ANNO=1      一并安装 6 个 TxDb/OrgDb 注释数据库（体积大，默认跳过，让 UI 按物种装）
#
# 设计：
#   - 全程非交互（NONINTERACTIVE=1）；被 bootstrap.sh / chipseq-app-update all 调用不挂起
#   - R 版本 → Bioc 版本自动映射（4.3/4.4/4.5/4.6）
#   - txdbmaker 仅在 Bioc ≥ 3.21 时装（旧版 GenomicFeatures 自带 makeTxDbFromGFF，脚本有回退）
#   - 已装包秒过（requireNamespace 判定）；可选包缺失不 fatal
#   - 只有"必需包"缺失才 exit 1；调用方 (bootstrap) 用 || warn 兜住即可
#   - 若系统 R lib 不可写，自动建 ~/R 作为 lib 目标

# ---------- 0. 全局非交互 ----------
Sys.setenv(NONINTERACTIVE = "1")
options(stringsAsFactors = FALSE, warn = 1)

CRAN_MIRROR <- Sys.getenv(
    "CHIPSEQ_CRAN_MIRROR",
    "https://mirrors.tuna.tsinghua.edu.cn/CRAN/"
)

# ---------- 1. 挑一个可写 lib ----------
pick_lib <- function() {
    forced <- Sys.getenv("CHIPSEQ_R_LIB", "")
    if (nzchar(forced)) {
        dir.create(forced, showWarnings = FALSE, recursive = TRUE)
        return(normalizePath(forced, mustWork = FALSE))
    }
    for (p in .libPaths()) {
        if (nzchar(p) && file.access(p, mode = 2L) == 0L) return(p)
    }
    user_lib <- path.expand("~/R")
    dir.create(user_lib, showWarnings = FALSE, recursive = TRUE)
    user_lib
}
LIB <- pick_lib()
.libPaths(c(LIB, .libPaths()))
cat(sprintf("[R] 安装目标 lib = %s\n", LIB))
cat(sprintf("[R] R 版本 = %s.%s\n", R.version$major, R.version$minor))

# ---------- 2. R 版本 → Bioc 版本自动映射 ----------
# 对应关系以 BiocManager 内置版本表为准（BiocManager:::.version_map）：
#   R 4.2 → 3.16, 4.3 → 3.18, 4.4 → 3.20, 4.5 → 3.22, 4.6 → 3.23(release)
# 注意 R 4.6 曾是 3.22，那是错的：BiocManager 会直接报
#   "Bioconductor version '3.22' requires R version '4.5'; use `version = '3.23'`"
rv <- getRversion()
BIOC_VERSION <- if (rv >= "4.7") {
    NA_character_            # 未知的未来版本 → 交给 BiocManager 自己选 release
} else if (rv >= "4.6") {
    "3.23"
} else if (rv >= "4.5") {
    "3.22"
} else if (rv >= "4.4") {
    "3.20"
} else if (rv >= "4.3") {
    "3.18"
} else if (rv >= "4.2") {
    "3.16"
} else {
    NA_character_
}
BIOC_VERSION_NUM <- if (is.na(BIOC_VERSION)) NA_real_ else as.numeric(BIOC_VERSION)
if (is.na(BIOC_VERSION)) {
    cat(sprintf("[R] 未识别 R %s，交给 BiocManager 用默认版本\n", format(rv)))
} else {
    cat(sprintf("[R] 对应 Bioconductor 版本 = %s\n", BIOC_VERSION))
}

# ---------- 3. 装 BiocManager 并锁定 Bioc 版本 ----------
if (!requireNamespace("BiocManager", quietly = TRUE)) {
    cat("[R] 安装 BiocManager …\n")
    install.packages("BiocManager", repos = CRAN_MIRROR, lib = LIB, quiet = TRUE)
}
if (!is.na(BIOC_VERSION)) {
    # 不要用 try(..., silent=TRUE) 静默吞掉：锁定失败会退化为「用当前 R 的默认版本」，
    # 之前的静默版本让人只看到「缺包」而看不到这一步已经报错。
    tryCatch(
        BiocManager::install(version = BIOC_VERSION, ask = FALSE, update = FALSE),
        error = function(e) {
            cat(sprintf("[R] [WARN] 锁定 Bioc %s 失败：%s\n", BIOC_VERSION,
                        conditionMessage(e)))
            cat("[R] [WARN] 回退为 BiocManager 按当前 R 自动选择的版本继续\n")
        }
    )
}

# ---------- 3.5 编译工具链自检 ----------
# conda 版 r-base 的 Makeconf 里 CC = x86_64-conda-linux-gnu-cc。若环境里只有
# gcc_impl_* 而没有 compilers/gcc 元包，这条命令根本不存在，于是**所有**含
# C/C++/Fortran 的包都会 "had non-zero exit status"，而日志里只有一堆编译失败，
# 看不出根因（Bioconductor 的 rtracklayer / GenomicFeatures / ChIPseeker /
# clusterProfiler 恰好全在这条链上）。这里提前显式告警。
.cc_of <- function(which = "CC") {
    out <- tryCatch(
        system2(file.path(R.home("bin"), "R"), c("CMD", "config", which),
                stdout = TRUE, stderr = FALSE),
        error = function(e) character(0)
    )
    if (!length(out)) return("")
    sub("\\s.*$", "", out[1])
}
.missing_cc <- character(0)
# 不只看 PATH：手动跑脚本时 analysis/bin 可能不在 PATH 里，也要看 conda prefix
.has_cmd <- function(cmd) {
    if (!nzchar(cmd)) return(TRUE)
    if (nzchar(Sys.which(cmd))) return(TRUE)
    cand <- c(
        file.path(Sys.getenv("CONDA_PREFIX"), "bin", cmd),
        file.path(R.home("bin"), cmd),
        file.path(dirname(dirname(R.home())), "bin", cmd)
    )
    any(nzchar(cand) & file.exists(cand))
}
for (wl in c("CC", "CXX", "FC")) {
    cc <- .cc_of(wl)
    if (!.has_cmd(cc)) .missing_cc <- c(.missing_cc, cc)
}
if (length(.missing_cc)) {
    cat("[R] [WARN] R 配置的编译器不存在:", paste(unique(.missing_cc), collapse = ", "), "\n")
    cat("[R] [WARN] 含 C/C++/Fortran 代码的包会全部安装失败（non-zero exit status）\n")
    cat("[R] [WARN] 补装编译器后重试（environment.yml 已含 compilers / make）:\n")
    cat("[R] [WARN]   conda env update -p <analysis prefix> -f <仓库>/environment.yml\n")
    cat("[R] [WARN]   Rscript workflow/env/install_R_packages.R\n")
} else if (.has_cmd(.cc_of("CC"))) {
    cat(sprintf("[R] 编译工具链就绪: %s\n", .cc_of("CC")))
}

has_pkg <- function(p) requireNamespace(p, quietly = TRUE)

# ---------- 4. CRAN 必需包 ----------
cran_required <- c(
    "optparse", "yaml", "RColorBrewer", "ggplot2",
    "dplyr", "pheatmap", "ggrepel", "readr", "stringr"
)
missing_cran <- cran_required[!vapply(cran_required, has_pkg, logical(1))]
if (length(missing_cran)) {
    cat("[R] 安装/补齐 CRAN 包:", paste(missing_cran, collapse = ", "), "\n")
    install.packages(missing_cran, repos = CRAN_MIRROR, lib = LIB, quiet = TRUE)
} else {
    cat("[R] CRAN 必需包全部已就位\n")
}

# ---------- 5. Bioconductor 必需包（含 txdbmaker 条件项） ----------
# 直接列上 ChIPseeker/clusterProfiler 的关键传递依赖：
#   BiocManager::install 对已装包不递归修依赖（renv/旧 site-lib 干扰时
#   会造成“ggtree 缺、但 enrichplot 看似已装”的假阳性），写到这里强制确认。
bioc_required <- c(
    "ChIPseeker", "rtracklayer", "GenomicFeatures",
    "GenomicRanges", "GenomeInfoDb", "S4Vectors", "IRanges",
    "clusterProfiler", "DOSE", "GOSemSim", "enrichplot", "ggtree",
    "aplot", "AnnotationDbi", "GO.db", "qvalue"
)
# txdbmaker 仅在 Bioc >= 3.21 (R >= 4.5) 存在；旧版 GenomicFeatures 自带 makeTxDbFromGFF
if (!is.na(BIOC_VERSION_NUM) && BIOC_VERSION_NUM >= 3.21) {
    bioc_required <- c(bioc_required, "txdbmaker")
} else {
    cat("[R] txdbmaker 在当前 Bioc 版本不存在，跳过（GenomicFeatures 自带 makeTxDbFromGFF）\n")
}
missing_bioc <- bioc_required[!vapply(bioc_required, has_pkg, logical(1))]
if (length(missing_bioc)) {
    cat("[R] 安装/补齐 Bioconductor 包:", paste(missing_bioc, collapse = ", "), "\n")
    BiocManager::install(missing_bioc, ask = FALSE, update = FALSE)
} else {
    cat("[R] Bioconductor 必需包全部已就位\n")
}

# ---------- 6. 物种注释数据库（体积大；默认跳过，UI 里按物种下载更合适） ----------
anno_pkgs <- c(
    "TxDb.Athaliana.BioMart.plantsmart28", "org.At.tair.db",
    "TxDb.Hsapiens.UCSC.hg19.knownGene", "TxDb.Hsapiens.UCSC.hg38.knownGene", "org.Hs.eg.db",
    "TxDb.Mmusculus.UCSC.mm10.knownGene", "TxDb.Mmusculus.UCSC.mm39.knownGene", "org.Mm.eg.db"
)
if (Sys.getenv("CHIPSEQ_R_ANNO", "0") == "1") {
    missing_anno <- anno_pkgs[!vapply(anno_pkgs, has_pkg, logical(1))]
    if (length(missing_anno)) {
        cat("[R] CHIPSEQ_R_ANNO=1 → 安装注释数据库:", paste(missing_anno, collapse = ", "), "\n")
        try(BiocManager::install(missing_anno, ask = FALSE, update = FALSE), silent = TRUE)
    } else {
        cat("[R] 注释数据库全部已就位\n")
    }
} else {
    cat("[R] 注释数据库默认跳过（体积大，UI『物种注释』页按需用）。加 CHIPSEQ_R_ANNO=1 一起装\n")
}

# ---------- 7. 验证：只看必需包，缺则 exit 1 ----------
required_all <- c(cran_required, bioc_required)
still_missing <- required_all[!vapply(required_all, has_pkg, logical(1))]
if (length(still_missing) == 0) {
    cat("\n[R] ✓ 所有必需 R 包就位\n")
    quit(status = 0)
} else {
    cat("\n[R] ✗ 必需包仍缺:", paste(still_missing, collapse = ", "), "\n")
    cat("[R]   常见原因：CRAN/Bioc 镜像不通、编译依赖缺（make/gcc/gfortran）、lib 权限被拒。\n")
    cat("[R]   可稍后手动重试：Rscript workflow/env/install_R_packages.R\n")
    quit(status = 1)
}
