"""项目版本的单一来源。

界面（侧栏品牌、浏览器标签）、CLI（`chipseq-app --version`）都从这里取版本，
发新版本时只改本文件，避免到处改漏。

注意：`cli/chipseq-app` 用 sed 从本文件提取 `APP_VERSION = "..."` 这一行，
所以该行的写法（四个空格、双引号、行尾无注释）要保持不变。
"""

APP_NAME = "ChIPseq_Pipline"
APP_VERSION = "1.0"

# 完整项目名，例如 ChIPseq_Pipline_v1.0
APP_FULL_NAME = f"{APP_NAME}_v{APP_VERSION}"

__version__ = APP_VERSION
