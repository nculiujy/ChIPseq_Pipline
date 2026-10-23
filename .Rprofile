# ---------------------------------------------------------------------------
# renv 已停用（保留 renv/ 目录与 renv.lock 仅作历史参考，不再参与加载）
#
# 本项目的 R 包由 conda 环境提供：<runtime>/analysis/lib/R/library，
# 由 environment.yml 一次性装齐（实测 193 个包，含 ChIPseeker、clusterProfiler、
# rtracklayer、GenomicFeatures、pheatmap 等全部依赖）。
#
# 为什么必须停用 renv：
#   1. 一旦 renv 装好并被激活，它会把 .libPaths() 改写成只剩
#      renv/library/<platform>/<R-ver>/<arch>。该目录实测只有 2 个条目，基本是空的，
#      于是 conda 里装着的 193 个包全部不可见，library(ChIPseeker) 直接报
#      “there is no package called 'ChIPseeker'”。实测：renv 激活时
#      ChIPseeker 可用 = FALSE，--vanilla 跳过 .Rprofile 时 = TRUE。
#   2. 若 renv 启动时因 00LOCK 残留而 bootstrap 失败，它又完全不激活，
#      此时反而能正常用 conda 库。也就是说，能不能跑取决于 renv 当时装没装成功，
#      同一份代码两种结果，不可复现。
#
# 两种状态都不可接受，所以直接不加载。如需恢复 renv，把最后一行取消注释即可。
# ---------------------------------------------------------------------------

# source("renv/activate.R")
