# shellcheck shell=bash
# ChIPseq Pipeline — Podman (rootless) 后端
# 薄封装：设置 ENGINE=podman 后 source _oci.sh

ENGINE="podman"
_OCI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_oci.sh
source "$_OCI_DIR/_oci.sh"
