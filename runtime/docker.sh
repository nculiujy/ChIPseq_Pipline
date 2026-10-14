# shellcheck shell=bash
# ChIPseq Pipeline — Docker 后端
# 薄封装：设置 ENGINE=docker 后 source _oci.sh

ENGINE="docker"
_OCI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_oci.sh
source "$_OCI_DIR/_oci.sh"
