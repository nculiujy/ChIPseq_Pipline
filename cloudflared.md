# 通过 cloudflared 隧道访问远程 Streamlit 应用

## 适用场景

当你的服务器在多层网络后面（例如：本地 → 堡垒机 Web 终端 → 远程服务器），无法直接使用 SSH 端口转发时，用 cloudflared 把远程端口临时暴露到公网，本地浏览器即可访问。

**优点：** 免费、无需注册、一条命令搞定、用完即弃。

---

## 1. 下载 cloudflared

在远程服务器上执行：

```bash
# 下载 Linux amd64 版本
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64

# 赋予执行权限
chmod +x cloudflared-linux-amd64
```

> 其他平台（macOS / Windows / ARM）请到 [GitHub Releases](https://github.com/cloudflare/cloudflared/releases) 下载对应版本。

---

## 2. 启动你的 Web 服务

以 Streamlit 为例，先在远程服务器上启动应用：

```bash
streamlit run streamlit_app/app.py --server.port 8501 &
```

> `&` 让进程在后台运行。确保端口（这里用 `8501`）没有被占用，可用 `lsof -i :8501` 检查。

---

## 3. 启动 cloudflared 隧道

```bash
./cloudflared-linux-amd64 tunnel --url http://localhost:8501
```

运行后会输出类似以下内容：

```
+--------------------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
|  https://pas-barely-stylish-creative.trycloudflare.com                                     |
+--------------------------------------------------------------------------------------------+
```

这个 `https://xxx.trycloudflare.com` 就是你的临时公网地址。

---

## 4. 本地访问

在你自己电脑的浏览器里打开上一步生成的 URL（如 `https://pas-barely-stylish-creative.trycloudflare.com`），即可像操作本地应用一样使用远程 Streamlit。

---

## 原理

```
你的浏览器
    ↕
https://xxx.trycloudflare.com  ← Cloudflare 中转
    ↕
远程服务器的 localhost:8501  ← 实际运行的应用
```

cloudflared 只是一个**网络管道**，数据是实时转发的。你在浏览器里的任何操作都直接作用于远程服务器，**不存在中间副本**。

| 操作 | 实际效果 |
|------|---------|
| 上传文件 | 存入远程服务器磁盘 |
| 修改参数 | 直接改远程服务器上的文件 |
| 运行分析 | 使用远程服务器的 CPU/内存 |
| 下载结果 | 从远程服务器读取传输 |

---

## 5. 防止断线：保持隧道持久运行

堡垒机 Web 终端有空闲超时，一断连接 cloudflared 就停了。下面两种方案可以解决这个问题。

### 方案 A：tmux（推荐，一劳永逸）

`tmux` 是一个终端复用器——你的进程跑在 tmux 会话里，终端断了进程不会停，随时可以重连回来。

```bash
# 安装 tmux（仅首次）
sudo apt install tmux -y

# 创建一个名为 tunnel 的会话
tmux new -s tunnel

# 在会话里依次启动 Streamlit 和 cloudflared
streamlit run streamlit_app/app.py --server.port 8501 &
./cloudflared-linux-amd64 tunnel --url http://localhost:8501
```

**日常使用：**

| 操作 | 命令 |
|------|------|
| 安全离开（进程继续跑） | 按 `Ctrl+B` 然后按 `D` |
| 断线后重新连接 | `tmux attach -t tunnel` |
| 查看所有会话 | `tmux ls` |
| 杀掉会话 | `tmux kill-session -t tunnel` |

> 堡垒机超时断开后，重新登录 dell，执行 `tmux attach -t tunnel` 就能回到原来的隧道会话，不用重新启动任何东西。

### 方案 B：nohup（简单但不够灵活）

不需要装东西，一条命令让进程跑在后台：

```bash
# Streamlit 后台
streamlit run streamlit_app/app.py --server.port 8501 &

# cloudflared 后台，日志写入文件
nohup ./cloudflared-linux-amd64 tunnel --url http://localhost:8501 > ~/cloudflared.log 2>&1 &
```

需要查看当前隧道 URL 时：

```bash
grep -o 'https://.*\.trycloudflare\.com' ~/cloudflared.log
```

> `nohup` 的缺点是断了之后没法重新"接回去"看实时日志，只能读日志文件。需要重启隧道的话要先 `pkill -f cloudflared` 杀掉旧进程。

### URL 不是固定的

每次运行 `cloudflared tunnel` 都会生成**新的随机 URL**。如果之前拿到了一个 URL 然后重跑了隧道，旧地址会失效，需要用新地址。

### 免费隧道限制

- 无账号隧道（Quick Tunnel）没有 SLA 保证，仅供临时测试
- 如需稳定长期使用，建议注册 Cloudflare 账号并创建命名隧道：[官方文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps)

### 安全提醒

生成的 `*.trycloudflare.com` URL 是**公网可访问**的，知道这个 URL 的人都能访问你的应用。使用完毕后建议直接 `Ctrl+C` 关闭隧道。

---
## 6. 附加：Filebrowser 远程文件管理

堡垒机 + Xshell 不像 WinSCP 能直接看文件结构。`filebrowser` 是一个 Web 文件管理器，通过 cloudflared 隧道暴露出来，就能在浏览器里像操作本地文件夹一样管理远程文件（上传、下载、重命名、删除、编辑等）。

### 安装

dell 无法直连 GitHub，需要先从本地下载再传上去。

**步骤 1：本地下载**

浏览器打开以下镜像地址，下载 `linux-amd64-filebrowser.tar.gz`：

```
https://ghproxy.net/https://github.com/filebrowser/filebrowser/releases/download/v2.31.2/linux-amd64-filebrowser.tar.gz
```

**步骤 2：上传到 dell**

通过已有的 Streamlit 页面上传该文件到远程服务器。

**步骤 3：解压并启动**

```bash
tar -xzf linux-amd64-filebrowser.tar.gz
chmod +x filebrowser
./filebrowser -r /media/dell/1A-CJJ20260521 -p 8502 &
```

**步骤 4：开通隧道**

```bash
./cloudflared-linux-amd64 tunnel --url http://localhost:8502
```

然后浏览器打开新的 `*.trycloudflare.com` 地址即可。

### 登录

| 字段 | 值 |
|------|-----|
| 用户名 | `admin` |
| 密码 | `admin` |

> 首次登录后建议在「Settings → User Management」里修改密码。

### 常用操作

| 操作 | 方法 |
|------|------|
| 上传文件 | 点右上角 ↑ 按钮，或拖拽到网页 |
| 下载文件 | 勾选文件 → 点 ⬇ 下载按钮 |
| 新建文件夹 | 点右上角 📁+ 按钮 |
| 重命名 | 点文件名 → 点上方 ✏️ 图标 |
| 删除 | 勾选 → 点 🗑 按钮 |
| 编辑文本 | 点文件直接在线编辑（支持语法高亮） |
| 多选 | 勾选第一个后，按住 Shift 勾最后一个 |

### Python 简易替代方案

如果暂时装不了 filebrowser，Python 自带一个只读文件列表：

```bash
cd /media/dell/1A-CJJ20260521
python3 -m http.server 8502 &
```

只能浏览和下载，不能上传/删除。

---
## 常见问题

### Q: 端口被占用

```
Port 8501 is already in use
```

解决：

```bash
# 查看占用进程
lsof -i :8501
# 杀掉旧进程
kill -9 <PID>
# 或者换个端口
streamlit run streamlit_app/app.py --server.port 8502
```

### Q: cloudflared 提示权限警告

```
failed to sufficiently increase receive buffer size
WRN The user running cloudflared process has a GID ...
```

这些是 ICMP/ping 相关的警告，**不影响隧道功能**，可以忽略。

### Q: 修改了 Python 源码，浏览器没变化

cloudflared 只管转发，改了代码需要**重启应用**：

```bash
pkill -f streamlit
streamlit run streamlit_app/app.py --server.port 8501 &
```

cloudflared 不需要重启，会自动连上新的 Streamlit 进程。

---

## 快速参考（一页纸）

```bash
# ===== 首次设置 =====
# 下载 cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64

# 安装 tmux（推荐）
sudo apt install tmux -y

# ===== 每次使用（tmux 方式，推荐） =====
tmux new -s tunnel                              # 创建会话
streamlit run streamlit_app/app.py --server.port 8501 &   # 启动应用
./cloudflared-linux-amd64 tunnel --url http://localhost:8501  # 启动隧道
# 按 Ctrl+B 然后 D 安全离开
# 断线重连：tmux attach -t tunnel

# ===== 每次使用（nohup 方式） =====
streamlit run streamlit_app/app.py --server.port 8501 &
nohup ./cloudflared-linux-amd64 tunnel --url http://localhost:8501 > ~/cloudflared.log 2>&1 &
grep -o 'https://.*\.trycloudflare\.com' ~/cloudflared.log   # 查看 URL
```
