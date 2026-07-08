# Miniconda 安装与 Conda 环境配置教程

适用于全新 Ubuntu 服务器。

---

## 1. 下载 Miniconda 安装脚本

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
```

> 若无 `wget`，使用 `curl`：
> ```bash
> curl -O https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
> ```

---

## 2. 运行安装脚本

```bash
bash Miniconda3-latest-Linux-x86_64.sh
```

安装过程交互说明：

| 提示 | 操作 |
|------|------|
| 查看许可协议 | 按 `Enter` 翻页，输入 `yes` 同意 |
| 安装路径 | 默认 `~/miniconda3`，直接回车确认 |
| 是否初始化 | 输入 `yes`（自动写入 `~/.bashrc`） |

---

## 3. 激活 conda

```bash
source ~/.bashrc
```

验证安装：

```bash
conda --version
```

---

## 4. 配置国内镜像源（推荐）

```bash
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/bioconda/
conda config --set show_channel_urls yes
```

---

## 5. 创建 Conda 环境

### 方式一：从 `environment.yml` 创建（推荐用于本项目）

```bash
conda env create -f environment.yml
```

### 方式二：手动创建

```bash
conda create -n chipseq python=3.10
```

---

## 6. 激活与管理环境

```bash
# 激活环境
conda activate chipseq

# 查看所有环境
conda env list

# 退出当前环境
conda deactivate

# 删除环境
conda env remove -n chipseq
```

---

## 7. 导出当前环境（便于复现）

```bash
conda env export > environment.yml
```

---

# CentOS 系统安装指南

## 1. 安装基础依赖

```bash
sudo yum install -y wget bzip2
```

## 2. 下载 Miniconda 安装脚本

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
```

## 3. 运行安装脚本

```bash
bash Miniconda3-latest-Linux-x86_64.sh
```

交互操作与 Ubuntu 相同（见上方表格）。

## 4. 激活 conda

```bash
source ~/.bashrc
conda --version
```

## 5. 配置国内镜像源

同 Ubuntu，执行相同的 `conda config` 命令（见上方第 4 节）。

## 6. 创建环境

同 Ubuntu（见上方第 5、6 节）。

> **CentOS 特别说明：**
> - CentOS 7 默认 Python 版本较旧，建议通过 conda 管理 Python 版本，不要修改系统 Python
> - 若遇到 `libGL` 等依赖缺失，可用 `sudo yum install -y mesa-libGL` 安装

---

# RStudio Server 安装与网页版使用教程

RStudio Server 允许通过浏览器访问 RStudio，无需在本地安装桌面版。

---

## Ubuntu 系统安装 RStudio Server

### 1. 安装 R

```bash
sudo apt update
sudo apt install -y r-base
```

### 2. 安装依赖

```bash
sudo apt install -y gdebi-core
```

### 3. 下载并安装 RStudio Server

前往 [https://posit.co/download/rstudio-server/](https://posit.co/download/rstudio-server/) 获取最新版本链接，或直接使用：

```bash
wget https://download2.rstudio.org/server/jammy/amd64/rstudio-server-2024.04.2-764-amd64.deb
sudo gdebi rstudio-server-2024.04.2-764-amd64.deb
```

> Ubuntu 20.04 将 `jammy` 替换为 `focal`；Ubuntu 18.04 替换为 `bionic`

### 4. 启动服务

```bash
sudo systemctl start rstudio-server
sudo systemctl enable rstudio-server  # 开机自启
sudo systemctl status rstudio-server  # 查看状态
```

---

## CentOS 系统安装 RStudio Server

### 1. 安装 R

```bash
sudo yum install -y epel-release
sudo yum install -y R
```

### 2. 下载并安装 RStudio Server

```bash
wget https://download2.rstudio.org/server/centos7/x86_64/rstudio-server-rhel-2024.04.2-764-x86_64.rpm
sudo yum install -y rstudio-server-rhel-2024.04.2-764-x86_64.rpm
```

### 3. 启动服务

```bash
sudo systemctl start rstudio-server
sudo systemctl enable rstudio-server
```

---

## 网页版访问

RStudio Server 默认监听 **8787** 端口。

在浏览器中访问：

```
http://<服务器IP>:8787
```

使用服务器的 **Linux 系统用户名和密码** 登录。

> **防火墙放行端口（如需要）：**
> ```bash
> # Ubuntu (ufw)
> sudo ufw allow 8787
>
> # CentOS (firewalld)
> sudo firewall-cmd --permanent --add-port=8787/tcp
> sudo firewall-cmd --reload
> ```

---

## 在 RStudio Server 中使用 Conda 环境的 R

若希望 RStudio Server 使用 conda 环境中的 R：

### 1. 在 conda 环境中安装 R

```bash
conda activate chipseq
conda install -c conda-forge r-base
```

### 2. 配置 RStudio Server 使用该 R

```bash
# 查找 conda 环境中 R 的路径
which R  # 激活环境后执行

# 编辑 RStudio Server 配置
sudo nano /etc/rstudio/rserver.conf
```

添加以下内容（替换为实际路径）：

```
rsession-which-r=/home/username/miniconda3/envs/chipseq/bin/R
```

重启服务生效：

```bash
sudo systemctl restart rstudio-server
```
