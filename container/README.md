# ChIPseq Pipeline — 容器构建与分发

本目录用于**维护者侧**构建三种发布产物，供无 root 用户在 `bootstrap.sh` 中下载。

## 产物清单

| 产物 | 构建脚本 | 发布位置 | 大小 | 对应后端 |
|------|---------|---------|------|---------|
| OCI 镜像（amd64 / arm64） | `build.sh` | GHCR / Docker Hub | ≈ 4 GB 压缩 | docker, podman, apptainer |
| `chipseq-pipeline.sif` | `build-sif.sh` | GitHub Release | ≈ 4 GB | apptainer |
| `chipseq-env-<ver>.tar.gz` | `build-conda-pack.sh` | GitHub Release | 3–5 GB | conda-pack |
| `chipseq-pipeline-image.tar.zst`（可选） | `build.sh` + `docker save` | GitHub Release / 网盘 | ≈ 4 GB | 离线 docker/podman |

## 文件说明

```
container/
├── Dockerfile              # OCI 镜像构建（三种后端共用）
├── entrypoint.sh           # 容器入口；支持 PORT / SERVER_ADDRESS 环境变量
├── build.sh                # 构建 + 推送 OCI 镜像（多架构 buildx）
├── build-sif.sh            # 从 OCI 镜像构建 Apptainer .sif
├── build-conda-pack.sh     # 构建 conda-pack tarball（无容器环境兜底）
└── README.md               # 本文件
```

## 快速开始（维护者）

### 1. 构建 OCI 镜像

```bash
# 本地构建（默认单架构 amd64，--load 到本地）
bash container/build.sh

# 多架构 + 推送到 GHCR
PLATFORMS=linux/amd64,linux/arm64 PUSH=1 \
    IMAGE=ghcr.io/<your-org>/chipseq-pipeline:latest \
    bash container/build.sh
```

前置：`docker buildx`（Docker Desktop / docker-buildx-plugin）+ `docker login ghcr.io`。

### 2. 构建 Apptainer .sif

```bash
# 从已推送的 OCI 镜像构建（非 root 时用 --fakeroot）
bash container/build-sif.sh

# 上传到 GitHub Release
gh release upload v1.0.0 chipseq-pipeline.sif
```

### 3. 构建 conda-pack tarball（无 root 兜底）

```bash
# 建议在 condaforge/miniforge3 容器内构建，以获得 glibc 2.17 兼容性
docker run --rm -it -v "$PWD":/work -w /work condaforge/miniforge3:24.9.0 \
    bash container/build-conda-pack.sh 1.0.0

# 上传到 GitHub Release
gh release upload v1.0.0 chipseq-env-1.0.0.tar.gz
```

**关键**：在 `condaforge/miniforge3` 镜像内构建 → 产物兼容 glibc ≥ 2.17（CentOS 7+/RHEL 7+/Ubuntu 16.04+/Debian 9+）。

### 4. 导出离线镜像包（可选）

```bash
docker pull ghcr.io/<your-org>/chipseq-pipeline:latest
docker save ghcr.io/<your-org>/chipseq-pipeline:latest | zstd -T0 -19 \
    -o chipseq-pipeline-image.tar.zst
gh release upload v1.0.0 chipseq-pipeline-image.tar.zst
```

用户端 `runtime/_oci.sh` 会自动探测当前目录下的 `chipseq-pipeline-image.tar.zst` / `.tar` 并 `docker load`。

## 用户侧下载源配置

三种后端的下载地址均可用环境变量覆盖：

```bash
export CHIPSEQ_IMAGE=ghcr.io/<your-org>/chipseq-pipeline:latest
export CHIPSEQ_SIF_URI=https://github.com/<your-org>/ChIPseq_Pipline/releases/latest/download/chipseq-pipeline.sif
export CHIPSEQ_ENV_URL=https://github.com/<your-org>/ChIPseq_Pipline/releases/latest/download/chipseq-env-1.0.0.tar.gz
./bootstrap.sh
```

**离线场景**：把产物文件（同名）放到当前工作目录，`bootstrap.sh` 会优先使用本地文件而不联网下载。

## Dockerfile 设计要点

- **基础镜像**：`condaforge/miniforge3:24.9.0`（含 mamba，CentOS 7 glibc）
- **分层缓存**：先 `COPY environment.yml` → `mamba env create`，后 `COPY` 项目代码；改代码不重建环境
- **renv 预恢复**：构建时 `renv::restore` 缓存 R 包到镜像层；失败不阻断（用户挂载 `renv/` 会覆盖）
- **Streamlit 全局配置**：写入 `/root/.streamlit/config.toml`，headless + 关闭遥测
- **Apptainer 兼容**：`entrypoint.sh` 接受 `PORT` / `SERVER_ADDRESS` 环境变量（Apptainer 无端口映射，直接绑宿主）
- **数据外挂**：`.dockerignore` 排除 `result/`、`logs/`、`.snakemake/`、`renv/library/`、大文件类型；数据由用户挂载到 `/app/{config,data,results,logs,renv,.snakemake}` 与 `/app/workflow/{resources,anno}`

## 容器内目录约定

| 容器内路径 | 用户数据目录（默认 `~/.chipseq-pipeline/`） |
|-----------|-------------------------------------------|
| `/app/config`             | `config/` |
| `/app/data`               | `data/` |
| `/app/workflow/resources` | `references/` |
| `/app/workflow/anno`      | `annotations/` |
| `/app/result`             | `results/` |
| `/app/logs`               | `logs/` |
| `/app/renv`               | `renv/` |
| `/app/.snakemake`         | `.snakemake/` |

挂载逻辑见 `scripts/lib/common.sh::build_binds()`。

## CI 集成建议

`.github/workflows/release.yml`（未包含，需自行添加）：

- 触发：push tag `v*`
- Job 1：`docker buildx build --platform linux/amd64,linux/arm64 --push`
- Job 2：`apptainer build` → upload to Release
- Job 3：在 `condaforge/miniforge3` container 中 `conda pack` → upload to Release

## 常见构建问题

**Q: `mamba env create` 卡在 solving**
A: `environment.yml` 里有清华镜像源；境外构建可移除 `channels:` 中的镜像行，只保留 `conda-forge`、`bioconda`、`defaults`。

**Q: `docker buildx` 报 `multiple platforms feature is currently not supported`**
A: 用 `docker buildx create --use --name mybuilder && docker buildx inspect --bootstrap` 创建支持多架构的 builder。

**Q: `apptainer build` 报权限错**
A: 非 root 用户加 `--fakeroot`（`build-sif.sh` 已默认启用）；仍失败说明宿主未开 unprivileged userns，改用 root 用户或 CI runner。

**Q: `conda pack` 报 `Editable packages found`**
A: `build-conda-pack.sh` 已加 `--ignore-editable-packages`；如果仍失败，检查 environment.yml 里是否有 `pip: -e ...`。

**Q: 镜像体积过大（> 15 GB）**
A: `mamba clean -afy` 已在 Dockerfile 里；仍大可考虑 `--squash`（buildx 需实验特性）或拆多阶段构建。
