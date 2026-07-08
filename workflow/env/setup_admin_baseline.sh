#!/usr/bin/env bash
# 管理员基础环境配置脚本 — 跨发行版（CentOS/RHEL/Ubuntu/Debian）
# 用法: sudo bash workflow/env/setup_admin_baseline.sh
set -euo pipefail

# ── 1. 检测发行版并安装系统基础包 ─────────────────────────────────────────────
if command -v dnf &>/dev/null; then
  # CentOS / RHEL / Rocky Linux / Alma Linux / Fedora
  dnf groupinstall -y "Development Tools"
  dnf install -y \
    gcc-toolset-13 git curl wget make cmake \
    openssl openssl-devel \
    zlib zlib-devel \
    libcurl libcurl-devel \
    libxml2 libxml2-devel \
    libpng-devel libjpeg-devel cairo-devel \
    perl perl-devel \
    java-1.8.0-openjdk-headless \
    environment-modules

elif command -v apt-get &>/dev/null; then
  # Ubuntu / Debian
  apt-get update -y
  apt-get install -y \
    build-essential gfortran \
    git curl wget make cmake \
    libssl-dev \
    zlib1g zlib1g-dev \
    libcurl4-openssl-dev \
    libxml2 libxml2-dev \
    libpng-dev libjpeg-dev libcairo2-dev \
    perl \
    default-jre-headless \
    environment-modules

else
  echo "不支持的发行版，请手动安装: gcc gfortran git curl wget make openssl zlib libcurl libxml2 perl java"
  exit 1
fi

# ── 2. 安装 Miniconda（全局，供所有用户使用） ──────────────────────────────────
CONDA_PREFIX="/opt/miniconda3"
if [ ! -f "${CONDA_PREFIX}/bin/conda" ]; then
  INSTALLER="/tmp/miniconda.sh"
  curl -fsSL "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh" \
    -o "${INSTALLER}"
  bash "${INSTALLER}" -b -p "${CONDA_PREFIX}"
  rm -f "${INSTALLER}"
fi

# ── 3. 全局 conda 初始化 ───────────────────────────────────────────────────────
cat > /etc/profile.d/conda.sh << 'EOF'
if [ -f "/opt/miniconda3/etc/profile.d/conda.sh" ]; then
  . "/opt/miniconda3/etc/profile.d/conda.sh"
fi
EOF

# ── 4. 全局 conda 镜像配置 ─────────────────────────────────────────────────────
mkdir -p /etc/conda
cat > /etc/conda/condarc << 'EOF'
channels:
  - conda-forge
  - bioconda
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/
  - https://mirrors.bfsu.edu.cn/anaconda/cloud/bioconda/
  - defaults
show_channel_urls: true
auto_activate_base: false
EOF

echo ""
echo "✓ 基础环境配置完成（$(. /etc/os-release && echo $PRETTY_NAME)）"
echo "  重新登录后 conda 即可使用，然后以普通用户执行:"
echo "  bash workflow/env/setup_new_server.sh"
