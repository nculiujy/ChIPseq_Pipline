# ChIP-seq 流程「可搬运发行包」方案（CentOS Stream 9 / x86_64）

> 目标：把生信软件做成**用户目录内、版本明确、可验证、离线可运行**的发行包。
> 普通用户无需管理员权限、无需在安装时联网求解 Conda 依赖；
> 核心不是复制本机整个环境，而是**构建 + 锁定 + 校验 + 实测**出一个可信的发行包。

---

## 0. 为什么现在必须做这件事（真实教训）

本项目在开发机与目标服务器之间已经多次因版本漂移踩坑：

| 现象 | 根因 | 版本证据 |
|---|---|---|
| `makeTxDbFromGFF() ... formally defunct` | Bioconductor 3.21 起 `makeTxDbFromGFF` 从 `GenomicFeatures` 拆分到 `txdbmaker` | 开发机 GenomicFeatures 1.58，目标机 ≥1.61 |
| ChIPseeker 选项/行为不一致 | 注释脚本按 1.42 写，目标机装到 1.46 | `ChIPseeker v1.46.1` |
| picard 到底用哪个 jar | `config.yaml` 指向系统 `picard-2.18.2`，而 conda 环境是 `picard=3.4.0` | 两套并存 |
| R 包"是用 R 4.5.3 建造的"告警 | 目标机 R 与 conda env R 前缀混用 | `R_HOME` 冲突 |

结论：**只要安装时让 conda/Bioc 现解依赖，两台机器就不会等价。** 本方案的本质是把"求解 + 安装"提前到构建阶段一次完成，交付给用户的只是一个已验证的、解压即用的二进制发行包。

---

## 1. 设计原则

1. **构建即锁定**：所有依赖的精确版本、来源渠道、内容哈希在构建阶段固化，用户侧不再联网求解。
2. **可搬运**：运行时（Python / R / Java / 编译程序）随包走；系统层只依赖 CentOS Stream 9 基础库（glibc 2.34 等）。
3. **不可搬运就早报错**：安装前自检架构 / glibc / 动态库 / 磁盘 / 写权限，绝不把缺库问题拖到流程跑一半才暴露。
4. **程序与数据分离**：数据库、参考基因组、索引、用户输入输出**不进发行包**，由配置文件指定路径。
5. **版本三元独立记录**：流程代码版本、参数配置版本、数据库版本分别记录、分别升级。
6. **可复现**：每次运行留一份 run manifest（输入校验值、软件版本、提交号、数据库版本、关键参数、日志）。
7. **资源友好**：线程、内存、临时目录由用户设定，默认不占满共享服务器。

---

## 2. 发行包目录结构

以本项目为例，发行包命名 `chipseq`（`mybio` 的实例化）。包内**不含**任何数据与数据库。

```
chipseq/
  bin/                      # 启动器 / CLI（PATH 入口，全部为薄封装脚本）
    chipseq                 # 总入口：子命令 run / ui / selfcheck / config ...
    chipseq-run             # 调用 snakemake
    chipseq-ui              # 调用 streamlit run app.py
    chipseq-selfcheck       # 环境自检
  env/                      # conda-pack 产出的固定运行时（解压后由 conda-unpack 修正前缀）
    <打包的 python3.9 + R4.4 + bioconda 工具链>
  app/                      # 流程与分析代码（本项目源码）
    snakefile
    workflow/               # rules / scripts / anno(仅模板，不含大索引)
    config/                 # 配置模板（schemas 的实体默认值）
    streamlit_app/
  share/
    schemas/                # 参数定义与配置模板
      config.schema.yaml    # 校验 config.yaml 的字段/类型/枚举
      config.defaults.yaml
    versions/               # 锁定文件
      conda-lock.yml        # 全部 conda 包的精确版本+哈希+渠道
      renv.lock             # R 包精确版本
    licenses/               # 许可与来源清单（可选，合规用）
  scripts/                  # 生命周期脚本
    install.sh              # 用户级安装（解压 + conda-unpack + 建入口）
    selfcheck.sh            # 轻量自检（架构/glibc/库/磁盘/DB/写权限）
    upgrade.sh              # 并存版本安装 + 校验 + 原子切换默认
    uninstall.sh            # 移除某个版本目录与入口，不动用户数据
    mk-release.sh           # 构建阶段：生成发行包 tar（仅维护者用）
  manifest.json             # 版本、架构、依赖、校验信息（见 §6）
```

用户侧目录约定（**程序 / 配置 / 数据三者分离**）：

```
~/.local/opt/chipseq/<版本>/     # 程序与运行时（install.sh 解压到这里，可并存多版本）
~/.config/chipseq/               # 用户配置（config.yaml、metadata.csv 的生效副本）
~/.local/share/chipseq/          # 默认数据目录（输出、日志、benchmarks 的兜底位置）
/data/reference/                 # 用户指定的参考数据库（bowtie2 索引、GTF、genome fasta、MEME 库）
/work/project-a/                 # 某次分析的输入、输出、结果
```

> 参考现有约定：`workflow/anno/<物种>/bowtie2_index_*`（索引）、`config/config.yaml` 里
> `index_dir` / `txdb` / `rawdata_dir` / `motif.genome_fasta` / `picard_dir` 都指向 `$HOME`/`/data` 下的路径——
> 这些属于"数据"，**保持不随包升级覆盖**。

---

## 3. 运行时环境如何交付（核心）

### 3.1 用 conda-lock 锁定，而不是 environment.yml 现解

现状 `environment.yml` 里 `snakemake / samtools / deeptools / picard / bowtie2 …` **大多没钉版本**（只有 `macs2=2.2.7.1`），这正是版本漂移的源头。

构建阶段（在**与目标兼容**的环境里）生成锁定文件：

```bash
# 构建机 / 构建容器内，一次求解、锁定全部传递依赖
conda-lock lock -f environment.yml -p linux-64 \
  --lockfile share/versions/conda-lock.yml
```

`conda-lock.yml` 记录每个包的 **名称、精确版本、build 串、渠道 URL、sha256**。交付与复现都以它为准，不再用裸 `environment.yml`。

> 建议在 `environment.yml` 里先补钉关键版本（对照项目实际用到、且已记录的版本）：
> `bowtie2=2.5.5`、`samtools=1.23`、`picard=3.4.0`、`macs2=2.2.7.1`、`deeptools=3.5.6`、
> `snakemake`（固定到当前用版本，如 7.32）、`r-base=4.4`、以及**显式加入 `bioconductor-txdbmaker`**
> （上一轮 `makeTxDbFromGFF` 的修复依赖它，必须进锁定文件，不能靠脚本运行时现装）。

### 3.2 用 conda-pack 打包成可搬运 tar

```bash
# 1) 依据 lock 精确安装到一个独立前缀 env/
conda create --prefix ./env --file share/versions/conda-lock.yml   # 或 mamba/conda-lock install
# 2) R 包补齐：优先用 conda 渠道的 bioconductor-* / r-* 包（已在 lock 内）；
#    若沿用 install_R_packages.R / renv，则把 renv 库一并装进 env/lib/R/library 后再打包
conda pack -p ./env -o chipseq-env.tar.gz
```

用户 `install.sh` 解压 `chipseq-env.tar.gz` 到 `~/.local/opt/chipseq/<版本>/env/`，
然后运行包内 `bin/conda-unpack` 修正硬编码前缀。**conda-pack 不是跨平台万能工具**：
必须为 **linux-64** 构建，并在 **CentOS Stream 9 实测**（见 §10）。

### 3.3 三类运行时的特殊处理

- **Python 3.9**：随 conda-pack 走，无需系统 Python；启动器绝不改系统 PATH，只在自身目录内定位解释器。
- **R 4.4 + Bioconductor**：
  - 全部注释/富集相关包（ChIPseeker、clusterProfiler、GenomicFeatures、**txdbmaker**、org.*.db 等）尽量从 conda 渠道纳入 lock，避免运行时 `install.packages()` 联网；
  - `renv.lock` 作为**第二重锁定**保留，`Rprofile`/`.libPaths()` 指向包内 `env/lib/R/library`；
  - 消除历史遗留：脚本头部 `conda_prefix` 逻辑保留，但发行包内 R 前缀唯一，不再有"R 4.5.3 建造"告警。
- **Java（Picard）**：bioconda `picard` 依赖 conda 渠道的 `openjdk`，随包走，**不再依赖系统 `java-1.8.0-openjdk`**。
  因此 `config.yaml` 的 `picard_dir` 应改为留空/走 `picard` 命令（`3_1_ChIPseq.pl` 已有"找不到 jar 就回退 `picard` 命令"的分支，PATH 指向 env/bin 即可命中）。
- **MEME 套件与 motif 库**：`6_motif` 依赖 `meme-chip`（environment.yml 未列，需补进锁定），
  motif 数据库（`~/.meme_db/.../Glycine_max.meme` 等）属**数据**，走只读共享目录，不打包进程序。

### 3.4 不可携带库的检测策略

系统层只假设 CentOS Stream 9 base 已有：`glibc 2.34`、`libstdc++`、`zlib`、`bzip2-libs`、`xz-libs`、
`libcurl`、`libxml2`、`pcre2`、`ncurses-libs`、`libzstd`（均在现有 `system_packages_baseline.txt` 中）。
构建后用 `ldd` 扫描 `env/bin`、`env/lib` 下所有 ELF，凡出现"base 白名单之外且非包内提供"的 `.so` 依赖，
`selfcheck.sh` 在安装前明确报错并列出缺失项——**不允许在流程运行中才暴露**。

---

## 4. 数据库与用户数据的交付

- 大型参考数据库（基因组 FASTA、bowtie2 索引、GTF/GFF、TxDb/orgDb、MEME 库）**不进发行包**。
- 两种获取方式：
  1. **管理员统一只读共享**：`/data/reference/<物种>/…`，用户配置里引用（推荐，省空间、版本一致）；
  2. **独立下载脚本**：`scripts/fetch-db.sh <物种> <版本>`，下载后写入校验值，不与程序包耦合。
- 发行包用 `manifest.json` **记录所测数据库的版本与校验值**，用户数据库版本不符时 `selfcheck` 给出告警。
- 用户输入 / 输出 / 临时目录一律由 `config.yaml` 与命令行指定，支持断点续跑、失败保留日志。

---

## 5. 安装与启动体验（用户视角）

```bash
# 1) 下载发行包（一个离线 tar + 校验文件）
sha256sum -c chipseq-1.3.0-linux64.tar.gz.sha256
# 2) 用户级安装到 ~/.local/opt/chipseq/1.3.0，并生成入口
bash <(tar -xOzf chipseq-1.3.0-linux64.tar.gz chipseq/scripts/install.sh)
# 3) 安装即自检
chipseq selfcheck
# 4) 运行
chipseq run    --project TAIR10/314Com5            # 等价于内部 snakemake
chipseq ui                                          # 启动 Streamlit 管理面板
```

- **启动器定位运行时的方式**：`bin/chipseq` 用 `SCRIPT_DIR=$(dirname $(readlink -f $0))` 反查包根，
  激活 `env`（`source env/bin/activate` 的等价前缀激活），再 `exec` 真正的 snakemake/streamlit。
  **绝不修改系统 Python、全局 PATH 或 shell 配置文件。**
- `install.sh` 做的轻量检查（对齐 §3.4）：
  架构=x86_64、`getconf GNU_LIBC_VERSION` ≥2.34、磁盘空间（预估 env 解压体积）、
  运行时 ELF 的 `ldd` 完整、`~/.config`/`~/.local/share` 可写、参考数据库路径存在且可读。

### 升级（并存 + 原子切换）

```
~/.local/opt/chipseq/1.2.0/     # 旧版本保留
~/.local/opt/chipseq/1.3.0/     # 新版本先装这里，跑 selfcheck + 迷你测试
```
通过后再把入口软链从 1.2.0 切到 1.3.0（改 `~/.local/bin/chipseq` 指向）。
**用户数据（`~/.config`、`~/.local/share`、`/data`、`/work`）不随程序升级覆盖。**

---

## 6. `manifest.json` 规范

```json
{
  "name": "chipseq",
  "version": "1.3.0",
  "arch": "linux-64",
  "built_at": "2026-10-19T10:00:00Z",
  "targets": {
    "os": "CentOS Stream 9",
    "glibc_min": "2.34",
    "cpu": "x86_64"
  },
  "pipeline": {
    "git_commit": "a1b2c3d",
    "snakefile_sha256": "…",
    "rules_sha256": { "4_analyse.smk": "…", "5_gsea.smk": "…" }
  },
  "runtime": {
    "conda_lock_sha256": "…",
    "renv_lock_sha256": "…",
    "tools": {
      "bowtie2": "2.5.5", "samtools": "1.23", "picard": "3.4.0",
      "macs2": "2.2.7.1", "deeptools": "3.5.6", "snakemake": "7.32",
      "R": "4.4", "ChIPseeker": "1.42.x", "txdbmaker": "…", "meme": "…"
    }
  },
  "databases": [
    { "id": "TAIR10_bt2", "version": "TAIR10", "path_hint": "/data/reference/TAIR10",
      "sha256": "…", "required": true },
    { "id": "Glycine_max_v4_gtf", "version": "v4", "sha256": "…", "required": false }
  ],
  "artifacts": [
    { "file": "chipseq-env.tar.gz", "sha256": "…", "size_bytes": 0 }
  ]
}
```

要点：流程版本、运行时（conda-lock/renv.lock）哈希、工具精确版本、数据库版本+校验、发行包各部件哈希，**一处齐全**，`selfcheck` 与 `run manifest` 都从这里取基准。

---

## 7. 生信工作流要额外固定的内容

1. **三层版本各自独立**：
   - 流程代码版本 → `manifest.pipeline.git_commit` + 各 rule 文件哈希；
   - 参数配置版本 → `config.yaml` 里加 `config_version:` 字段，`share/schemas/config.schema.yaml` 校验字段；
   - 数据库版本 → `manifest.databases[].version/sha256`。
2. **每次运行的可复现清单**（`run_manifest.json`，落在该次输出目录）：
   - 输入文件校验值（fastq md5/sha256）；
   - 软件版本快照（`snakemake --software-deployment-file share/versions/conda-lock.yml` 对应的锁定；外加 `chipseq selfcheck --versions` 采集的 bowtie2/samtools/picard/macs2/deeptools/R/Bioc 实际打印版本）；
   - 流程提交号（Slurm job id / 本地 PID + snakemake 目录指纹）；
   - 数据库版本（本次用到的 index_dir / txdb / motif 库路径 + 其 `manifest` 校验值）；
   - 关键参数（genome size、`--fdr`、`--shift`、扩展大小、线程数、内存上限、tmpdir）；
   - 各步骤日志路径与 `.snakemake/incomplete/` 状态摘要。
3. **断点恢复与失败留痕**：保留 Snakemake 的 checkpoint/`--restart-times` 语义，
   临时目录与结果目录由用户指定；失败时不清空日志，`run_manifest.json` 记录失败步骤与原因。
4. **运行清单与发行包解耦**：`run_manifest.json` 写进 `/work/<project>/`，不写进 `~/.local/opt`，
   程序升级不影响历史运行记录的可追溯性。

---

## 8. 资源限制与并行（共享服务器友好）

多进程并行必须让用户可控，默认不占满机器：

- **线程数**：`config.yaml` 的 `threads: 10`（现为 10）映射到 `snakemake --cores`；
  各规则内 `params.threads` / `--nodup java -Xmx` 上限受此约束。
- **内存**：`snakemake --max-memory`（支持 `--resources mem_mb=…`）；Picard/MACS2 的 `-Xmx`、`--mem` 从配置读取，不硬编码 `15g`。
- **临时目录**：`TMPDIR` / Snakemake `--tmpdir-prefix` 指向用户可写、大容量、非家目录的 scratch，避免 `/tmp` 撑爆。
- **提交方式**：本机直跑用 `--cores N`；集群用 `--profile slurm`（partition / cpus-per-task / mem 由 profile 固定），二者都从 `config.yaml` 取值。
- **默认保守**：安装后首次运行给的是"低默认"（如 `--cores 4`），用户按需上调，而非默认吃满所有核。

---

## 9. 配置校验（schemas）

- `share/schemas/config.schema.yaml` 用 Snakemake `--config-Schema` / `validate` 校验 `config.yaml`：
  字段类型、枚举（如 `species` ∈ {TAIR10, Glycine_max_v4, human…}）、必填路径。
- **路径类字段在安装/运行前做存在性与可读性检查**（`index_dir`、`rawdata_dir`、`txdb`、`picard_dir`、`motif.genome_fasta`）——
  直接针对上一台服务器踩过的坑：`rawdata_dir` 相对路径多前缀导致"秒失败"、`picard_dir` 误指 `workflow/env`。
- 缺字段/错类型/路径不存在 → **启动即报错并指出该行**，不拖到规则执行中途。

---

## 10. 验证清单（先在这台 CentOS Stream 9 上跑通再扩展）

发布前逐项打勾，任一不过不得打包：

1. **干净用户目录安装**：新建测试用户 / 空 `$HOME`，仅 `install.sh` + 解压包，无管理员介入。
2. **无网络启动**：安装与 `chipseq selfcheck` 全程断网（`unset http_proxy`、屏蔽 conda/CRAN 源）仍能通过；确认没有隐藏的 `install.packages()` / `conda install` 联网调用。
3. **架构与 glibc 校验**：`selfcheck` 检出非 x86_64 / glibc<2.34 时明确报错。
4. **`ldd` 全绿**：`env/bin`、`env/lib` 下 ELF 无白名单外缺失库。
5. **完整小型测试流程**：用极小的数据集跑通 step1→peakcalling→anno→deeptools→consensus→GSEA→motif 全链路，
   重点回归 `makeTxDbFromGFF`（txdbmaker 分支）、OrgDb 缺失时的占位输出、大豆 genome size=1.0e9。
6. **run_manifest 生成正确**：测试输出目录含 `run_manifest.json` 且字段齐全、可被人读回溯。
7. **升级并存**：装 1.2.0→1.3.0，两目录并存、入口原子切换、用户数据未被覆盖。
8. **卸载**：`uninstall.sh` 删版本目录与入口，保留 `~/.config`、`~/.local/share`、`/data`、`/work`。

全部通过后再考虑扩展到其他发行版（Rocky/Alma 9、Ubuntu 等），且需针对各目标重新构建+实测——**conda-pack 不保证跨发行版**。

---

## 11. 与现有文件的映射（迁移落地步骤）

| 现有资产 | 在发行包中的归宿 | 迁移动作 |
|---|---|---|
| `environment.yml`（多未钉版本） | `share/versions/conda-lock.yml` | 补钉关键版本 + 加 `bioconductor-txdbmaker`、`meme`，用 `conda-lock` 生成锁定 |
| `renv.lock` + `renv/library` | `env/lib/R/library` + `share/versions/renv.lock` | R 包优先并入 conda lock；renv 作第二重锁定，`.libPaths()` 指向包内 |
| `install_R_packages.R` | 构建阶段脚本 | 已加 `txdbmaker`；打包时预装，运行期不再联网安装 |
| `setup_new_server.sh`（conda create + R 装包 + 验证） | `scripts/install.sh` | 改为"解压 conda-pack + `conda-unpack` + 建入口 + selfcheck"，去掉联网求解 |
| `setup_admin_baseline.sh`（系统包+miniconda+condarc 镜像） | 构建机/管理员侧一次性 | 保留为**构建**用途；用户侧安装不再需要它 |
| `system_packages_baseline.txt` | `selfcheck.sh` 的系统库白名单来源 | 据此定义"可假设存在的 base 库"，`ldd` 缺项即报错 |
| `config/config.yaml` + `metadata.csv` | `app/config/` 模板 + `~/.config/chipseq/` 生效副本 | 补 `config_version`、`threads`、`max_memory`、`tmpdir`；路径字段纳入 schema 校验 |
| `streamlit_app/` | `app/streamlit_app/` + `bin/chipseq-ui` | 启动器相对包根定位运行时，不改全局环境 |
| `workflow/anno/<物种>/`（含大索引） | 索引/数据库外移到 `/data/reference/<物种>` | 包内只留模板与说明（如 `Glycine_max_v4/README.md`），二进制索引走只读共享 |

落地顺序建议：
1. 先补钉 `environment.yml` 版本并加 `txdbmaker`/`meme` → 生成 `conda-lock.yml`；
2. 构建机 `conda create --prefix ./env` → `conda pack` → 得 `chipseq-env.tar.gz`；
3. 写 `install.sh`/`selfcheck.sh`/`upgrade.sh`/`uninstall.sh` + `manifest.json`；
4. 在本 CentOS Stream 9 过 §10 全清单；
5. 发布 tar + `.sha256`，用户级安装试用。

---

## 12. 风险与注意事项

- **conda-pack 的边界**：修正的是硬编码前缀，不解决 glibc/系统库差异；跨发行版必须重新构建+实测。
- **R/Bioc 混装**：conda 的 `bioconductor-*` 与 CRAN/renv 装的同名包会打架；发行包内**只保留一种来源**，二选一。
- **picard 双份**：系统 `2.18.2` 与 conda `3.4.0` 并存易混淆；发行包只带 conda 版，`picard_dir` 留空走 `picard` 命令。
- **数据库版本漂移**：orgDb/KEGG/MEME 库随时间更新，`manifest.databases` 锁版本+校验，`selfcheck` 不符即告警，避免"流程版本没变但结果变了"。
- **体积**：全量离线包可能数 GB；`install.sh` 绝不默认在线拉依赖，大数据一律走只读共享或独立 `fetch-db.sh`。
- **合规**：`share/licenses/` 记录各组件许可，便于内部分发审计。

---

## 附：最小交付组合

> 一个 **linux/amd64 离线发行包**（`chipseq-<版本>-linux64.tar.gz` + `.sha256`）
> + **用户级安装/自检脚本**（`install.sh` / `selfcheck.sh` / `upgrade.sh` / `uninstall.sh`）
> + **独立的流程版本与数据库版本记录**（`manifest.json` + 每次运行的 `run_manifest.json`）。

先在本 CentOS Stream 9 服务器验证"干净目录安装 + 无网络启动 + 完整小型测试流程"，
确认 §10 全绿后，再考虑扩展到其他发行版。