"""
annotation_manager.py — 物种注释下载/构建的后台任务管理

设计目标：
  - Streamlit 页面不能被长时间任务阻塞（bowtie2-build 大基因组可能几十分钟）
  - 因此把下载/构建 fork 到独立 subprocess，通过 JSON 状态文件通信
  - UI 侧提供 start / status / cancel / list / clear 接口

状态目录：  <ANNO_DIR>/.download_tasks/
状态文件：  <ANNO_DIR>/.download_tasks/<task_id>.json
Worker：    streamlit_app/utils/annotation_worker.py（python 直接可执行）
日志文件：  <ANNO_DIR>/.download_tasks/<task_id>.log
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import runtime as _rt


# ---------- 常量 ----------
STAGES: List[str] = [
    "init",
    "download_fasta",
    "decompress_fasta",
    "samtools_faidx",
    "bowtie2_build",
    "download_gff",
    "verify",
    "done",
]

# 同一套后台任务框架服务三种活：建基因组索引 / 自建 OrgDb 包 / 生成 gene2go 基因集。
# 各 kind 的阶段数不同，进度条按 kind 取自己的 stage 列表，否则阶段索引会错位。
STAGES_BY_KIND: Dict[str, List[str]] = {
    "genome": STAGES,
    "orgdb": ["init", "check_env", "build_orgdb", "install_orgdb", "verify", "done"],
    "term2gene": ["init", "download_gene2go", "parse", "write", "verify", "done"],
    "rdeps": ["init", "check_env", "install", "verify", "done"],
}


def stages_for(spec: "TaskSpec") -> List[str]:
    """该任务对应的阶段名列表（UI 与 worker 必须一致）。"""
    return STAGES_BY_KIND.get(getattr(spec, "kind", "genome") or "genome", STAGES)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


# ---------- 数据类 ----------
@dataclass
class TaskSpec:
    """启动一个任务所需的全部参数（会序列化成 JSON 传给 worker）。"""
    task_id: str
    species_id: str                       # 例: TAIR10 / GRCh38 / custom_xxx
    display_name: str
    source_id: str                        # 例: Ensembl_Plants_63 / custom
    anno_root: str                        # workflow/anno 的绝对路径
    fasta_url: str
    fasta_filename: str                   # 保存文件名（含 .gz）
    gff_url: str
    gff_filename: str
    fasta_sha256: Optional[str] = None
    gff_sha256: Optional[str] = None
    bowtie2_threads: int = 4
    keep_gz: bool = True                  # 是否保留 .gz（不占多余空间）
    # 只补下注释文件（FASTA 和索引已存在时用），不会重下基因组、不重建索引
    gff_only: bool = False
    # 完成后写回 config.yaml 用（worker 不写，UI 侧调用 apply_to_config 时写）
    default_txdb_pkg: Optional[str] = None
    default_orgdb_pkg: Optional[str] = None
    genome_size_mb: Optional[int] = None
    estimated_time_min: Optional[int] = None

    # ---- 任务类型：genome(默认，旧的下载+索引) / orgdb / term2gene ----
    kind: str = "genome"

    # ---- kind=orgdb：AnnotationForge 自建 OrgDb（NCBI taxid 驱动）----
    tax_id: str = ""                 # 例: 3847 (Glycine max)
    ncbi_genus: str = ""             # 例: Glycine
    ncbi_species: str = ""           # 例: max
    orgdb_pkg: str = ""              # 目标包名，如 org.Gmax.eg.db（留空按属种推导）
    orgdb_lib: str = ""              # 安装到哪个 R library（分析环境 lib 路径）

    # ---- kind=term2gene：NCBI gene2go → clusterProfiler TERM2GENE ----
    term2gene_outdir: str = ""       # 输出目录（默认 <ANNO_DIR>/<species_id>/orgdb）
    term2gene_prefix: str = "gmax"   # 输出文件名前缀，如 gmax_go_bp_term2gene.tsv
    gene2go_url: str = "https://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2go.gz"

    # ---- kind=rdeps：在分析环境装 R 包（AnnotationForge 等）----
    r_packages: str = "AnnotationForge"
    cran_mirror: str = ""            # 留空=worker 默认（清华 TUNA）


def orgdb_task(species_id: str, display_name: str, anno_root: str, tax_id: str,
               genus: str, species: str, orgdb_pkg: str = "", orgdb_lib: str = "") -> TaskSpec:
    """自建 OrgDb 包的后台任务（kind=orgdb）。"""
    return TaskSpec(
        task_id=new_task_id(), species_id=species_id, display_name=display_name,
        source_id="orgdb", anno_root=anno_root,
        fasta_url="", fasta_filename="", gff_url="", gff_filename="",
        kind="orgdb", tax_id=str(tax_id).strip(), ncbi_genus=genus.strip(),
        ncbi_species=species.strip(), orgdb_pkg=orgdb_pkg.strip(),
        orgdb_lib=orgdb_lib.strip(),
    )


def term2gene_task(species_id: str, display_name: str, anno_root: str, tax_id: str,
                   prefix: str = "", outdir: str = "") -> TaskSpec:
    """NCBI gene2go → TERM2GENE/TERM2NAME 的后台任务（kind=term2gene）。"""
    return TaskSpec(
        task_id=new_task_id(), species_id=species_id, display_name=display_name,
        source_id="gene2go", anno_root=anno_root,
        fasta_url="", fasta_filename="", gff_url="", gff_filename="",
        kind="term2gene", tax_id=str(tax_id).strip(),
        term2gene_prefix=prefix.strip() or f"tax{str(tax_id).strip()}",
        term2gene_outdir=outdir.strip(),
    )


def rdeps_task(species_id: str, display_name: str, anno_root: str,
               packages: str = "AnnotationForge,AnnotationDbi,GO.db",
               orgdb_lib: str = "", cran_mirror: str = "") -> TaskSpec:
    """在分析环境装 R 依赖的后台任务（kind=rdeps）。"""
    return TaskSpec(
        task_id=new_task_id(), species_id=species_id, display_name=display_name,
        source_id="rdeps", anno_root=anno_root,
        fasta_url="", fasta_filename="", gff_url="", gff_filename="",
        kind="rdeps", r_packages=packages, orgdb_lib=orgdb_lib.strip(),
        cran_mirror=cran_mirror.strip(),
    )


# ---------- 内部工具 ----------
def _tasks_dir(anno_root: str | Path) -> Path:
    p = Path(anno_root) / ".download_tasks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(anno_root: str | Path, task_id: str) -> Path:
    return _tasks_dir(anno_root) / f"{task_id}.json"


def _log_path(anno_root: str | Path, task_id: str) -> Path:
    return _tasks_dir(anno_root) / f"{task_id}.log"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """原子写：先写 .tmp 再 rename，避免读到半个 JSON。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- 公共 API ----------
def new_task_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def start_task(spec: TaskSpec) -> Dict[str, Any]:
    """
    启动一个后台 worker 进程。返回初始状态字典。
    若目标物种已存在完整索引，会抛 FileExistsError（由 UI 侧决定是否 force）。
    """
    anno_root = Path(spec.anno_root)
    anno_root.mkdir(parents=True, exist_ok=True)

    state_file = _state_path(anno_root, spec.task_id)
    log_file = _log_path(anno_root, spec.task_id)

    initial_state = {
        "task_id": spec.task_id,
        "species_id": spec.species_id,
        "display_name": spec.display_name,
        "source_id": spec.source_id,
        "anno_root": str(anno_root),
        "status": STATUS_PENDING,
        "kind": getattr(spec, "kind", "genome") or "genome",
        "stage": "init",
        "stage_index": 0,
        "stage_total": len(stages_for(spec)),
        "progress": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0,
        "eta_sec": 0,
        "message": "任务已创建，等待 worker 启动",
        "started_at": time.time(),
        "updated_at": time.time(),
        "finished_at": None,
        "error": None,
        "target_dir": None,
        "spec": asdict(spec),
        "pid": None,
    }
    _atomic_write_json(state_file, initial_state)

    # 定位 worker 脚本
    worker_path = Path(__file__).resolve().parent / "annotation_worker.py"
    if not worker_path.is_file():
        initial_state["status"] = STATUS_ERROR
        initial_state["error"] = f"worker 脚本未找到: {worker_path}"
        _atomic_write_json(state_file, initial_state)
        return initial_state

    # 启动 subprocess（脱离父进程，即使 Streamlit 重跑也不中断）
    # spec 通过 stdin JSON 传递，避免命令行过长和转义问题
    # 把本 conda 环境的 bin 前置到 PATH，保证 worker 里 shutil.which / subprocess
    # 能解析 bowtie2-build、samtools、curl 等环境内工具（即使启动 shell 是 base）
    worker_env = dict(os.environ)
    worker_env["PATH"] = tool_search_path()
    with open(log_file, "ab", buffering=0) as logf:
        logf.write(
            f"\n==== task {spec.task_id} started at {time.strftime('%F %T')} ====\n".encode()
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", str(worker_path), "--state", str(state_file)],
            stdin=subprocess.PIPE,
            stdout=logf,
            stderr=logf,
            start_new_session=True,   # setsid，脱离父进程组
            close_fds=True,
            env=worker_env,
        )
        proc.stdin.write(json.dumps(asdict(spec), ensure_ascii=False).encode("utf-8"))
        proc.stdin.close()

    initial_state["pid"] = proc.pid
    initial_state["status"] = STATUS_RUNNING
    initial_state["message"] = f"worker 已启动 (PID {proc.pid})"
    initial_state["updated_at"] = time.time()
    _atomic_write_json(state_file, initial_state)

    return initial_state


def get_task(anno_root: str | Path, task_id: str) -> Optional[Dict[str, Any]]:
    """读取任务当前状态。"""
    p = _state_path(anno_root, task_id)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_tasks(anno_root: str | Path) -> List[Dict[str, Any]]:
    """列出所有任务（按 started_at 倒序）。"""
    tdir = _tasks_dir(anno_root)
    out: List[Dict[str, Any]] = []
    for p in tdir.glob("*.json"):
        st = get_task(anno_root, p.stem)
        if st:
            out.append(st)
    out.sort(key=lambda s: s.get("started_at") or 0, reverse=True)
    return out


def cancel_task(anno_root: str | Path, task_id: str) -> bool:
    """
    取消任务：向 worker 进程组发 SIGTERM。
    成功返回 True；进程不存在或已终止返回 False。
    """
    st = get_task(anno_root, task_id)
    if not st:
        return False
    if st.get("status") in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED):
        return False

    pid = st.get("pid")
    if pid:
        try:
            # 用 killpg 杀掉整个进程组（bowtie2-build 子进程也会被清理）
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    # 立即把状态改成 cancelled（worker 收到信号后也会自己更新一次，兜底）
    st["status"] = STATUS_CANCELLED
    st["message"] = "已取消"
    st["finished_at"] = time.time()
    st["updated_at"] = time.time()
    _atomic_write_json(_state_path(anno_root, task_id), st)
    return True


def clear_task(anno_root: str | Path, task_id: str) -> bool:
    """清除任务的状态与日志文件（不删已下载的产物）。"""
    s = _state_path(anno_root, task_id)
    l = _log_path(anno_root, task_id)
    ok = False
    if s.exists():
        s.unlink()
        ok = True
    if l.exists():
        l.unlink()
        ok = True
    return ok


def tail_log(anno_root: str | Path, task_id: str, n_lines: int = 40) -> str:
    """读日志尾部 n_lines 行。"""
    p = _log_path(anno_root, task_id)
    if not p.is_file():
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= n_lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n_lines:])
    except OSError:
        return ""


# ---------- 目录扫描（UI 侧用于展示当前状态） ----------
@dataclass
class SpeciesStatus:
    species_id: str
    anno_root: str
    exists: bool = False
    bowtie2_dir: Optional[str] = None     # 目录名，例 bowtie2_index_TAIR10
    index_prefix: Optional[str] = None    # 例 TAIR10_Chr
    fasta_path: Optional[str] = None
    fai_path: Optional[str] = None
    gff_path: Optional[str] = None
    bt2_files: List[str] = field(default_factory=list)
    all_bowtie2_dirs: List[str] = field(default_factory=list)
    size_bytes: int = 0
    complete: bool = False
    missing: List[str] = field(default_factory=list)
    # species_id 可能是个参考变体（TAIR10__Ensembl_Plants_63）：
    # species_base 记物种本名，source_id 记它用的是哪个数据库源
    species_base: str = ""
    source_id: str = ""


def scan_species(
    anno_root: str | Path,
    species_id: str,
    source_id: str = "",
    species_base: str = "",
) -> SpeciesStatus:
    """扫描某个物种/变体目录，判断 bowtie2 索引 + FASTA + GFF 是否齐全。"""
    root = Path(anno_root) / species_id
    st = SpeciesStatus(
        species_id=species_id,
        anno_root=str(anno_root),
        species_base=species_base or _rt.variant_species(species_id),
        source_id=source_id or _rt.split_variant(species_id)[1],
    )
    if not root.is_dir():
        st.missing = ["species_dir"]
        return st
    st.exists = True

    # 找 bowtie2 开头的子目录；可能有多个（例 TAIR10 下有 4 个），选“最完整”的：
    # 优先级：bt2 齐全 + 有 fa + 有 gff > bt2 齐全 + 有 fa > bt2 齐全 > 其他
    bt_dirs = sorted(
        [d for d in root.iterdir() if d.is_dir() and d.name.lower().startswith("bowtie2")],
        key=lambda d: d.name,
    )
    if not bt_dirs:
        st.missing.append("bowtie2_dir")
        return st

    def _score(d: Path) -> tuple[int, int, int, int]:
        has_bt2 = len(list(d.glob("*.bt2")))
        has_fa = len(list(d.glob("*.fa"))) + len(list(d.glob("*.fasta")))
        has_fai = len(list(d.glob("*.fa.fai"))) + len(list(d.glob("*.fasta.fai")))
        has_gff = (
            len(list(d.glob("*.gff"))) + len(list(d.glob("*.gff3"))) +
            len(list(d.glob("*.gtf"))) +
            len(list(d.glob("*.gff.gz"))) + len(list(d.glob("*.gff3.gz"))) +
            len(list(d.glob("*.gtf.gz")))
        )
        # 排序权重：bt2 个数 > fa > gff > fai
        return (has_bt2, has_fa, has_gff, has_fai)

    bt_dirs.sort(key=_score, reverse=True)
    bt_dir = bt_dirs[0]
    st.bowtie2_dir = bt_dir.name
    st.all_bowtie2_dirs = [d.name for d in bt_dirs]

    # 收集文件
    bt2 = sorted(bt_dir.glob("*.bt2"))
    st.bt2_files = [f.name for f in bt2]
    fa = list(bt_dir.glob("*.fa")) + list(bt_dir.glob("*.fasta"))
    fai = list(bt_dir.glob("*.fa.fai")) + list(bt_dir.glob("*.fasta.fai"))
    gff = (
        list(bt_dir.glob("*.gff")) + list(bt_dir.glob("*.gff3")) +
        list(bt_dir.glob("*.gtf")) +
        list(bt_dir.glob("*.gff.gz")) + list(bt_dir.glob("*.gff3.gz")) +
        list(bt_dir.glob("*.gtf.gz"))
    )

    # 推断 index_prefix：从 .1.bt2 反推
    prefix = None
    for f in bt2:
        name = f.name
        for suf in (".rev.1.bt2", ".rev.2.bt2", ".1.bt2", ".2.bt2", ".3.bt2", ".4.bt2"):
            if name.endswith(suf):
                prefix = name[: -len(suf)]
                break
        if prefix:
            break
    st.index_prefix = prefix
    if fa:
        st.fasta_path = str(fa[0])
    if fai:
        st.fai_path = str(fai[0])
    if gff:
        st.gff_path = str(gff[0])

    # 完整性判断
    needed_bt2 = {f"{prefix}{s}" for s in (".1.bt2", ".2.bt2", ".rev.1.bt2", ".rev.2.bt2")}
    have_bt2 = {f.name for f in bt2}
    if not needed_bt2.issubset(have_bt2):
        st.missing.append("bt2_index")
    if not st.fasta_path:
        st.missing.append("fasta")
    if not st.gff_path:
        st.missing.append("gff")

    # 目录大小
    try:
        st.size_bytes = sum(f.stat().st_size for f in bt_dir.rglob("*") if f.is_file())
    except OSError:
        pass

    st.complete = not st.missing
    return st


def scan_variant(anno_root: str | Path, species_id: str, source_id: str) -> SpeciesStatus:
    """扫某个「物种 + 数据库来源」变体。

    同一物种的多个源各建一个目录（TAIR10__Ensembl_Plants_63 /
    TAIR10__NCBI_RefSeq_TAIR10.1 …），互不覆盖，结果目录也自然分开。
    """
    vid = _rt.variant_id(species_id, source_id)
    return scan_species(anno_root, vid, source_id=source_id, species_base=species_id)


def list_legacy_index_dirs(anno_root: str | Path, species_id: str) -> List[str]:
    """老结构里挤在物种目录下的 bowtie2_index_* 子目录。

    早期版本没有变体概念，同一物种的几套索引都建在 workflow/anno/<物种>/ 下，
    目录名是手起的。把这些列出来给用户挂到对应源上迁移。
    """
    root = Path(anno_root) / species_id
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and d.name.lower().startswith("bowtie2")
    )


# 目录名关键词 → 源 ID 关键词。只是预选，用户可改：手起的目录名不可能全对得上。
_LEGACY_SOURCE_HINTS = (
    (("ensembl",), ("ensembl",)),
    (("refseq", "ncbi_ref"), ("refseq",)),
    (("genbank", "gca"), ("genbank",)),
    (("ucsc", "hg", "mm"), ("ucsc",)),
    (("legume", "soybase", "wm82"), ("legumeinfo",)),
)


def guess_source_for_dir(sources: Dict[str, Any], dir_name: str) -> str:
    """老索引目录名 → catalog 源 ID 的模糊匹配，对不上返回空串。"""
    low = str(dir_name or "").lower()
    if not low or not sources:
        return ""
    # 1. 目录名里直接出现源 ID
    for src_id in sources:
        if src_id.lower() in low:
            return src_id
    # 2. 靠关键词猜
    for dir_needles, src_needles in _LEGACY_SOURCE_HINTS:
        if not any(n in low for n in dir_needles):
            continue
        for src_id in sources:
            if any(n in src_id.lower() for n in src_needles):
                return src_id
    return ""


def migrate_legacy_index(
    anno_root: str | Path, species_id: str, legacy_dir: str, source_id: str
) -> Dict[str, str]:
    """把老的 workflow/anno/<物种>/<索引目录> 搬到变体目录下。

    只移动位置，不改名里面任何文件：老目录的索引前缀千奇百怪（TAIR10_Chr、
    TAIR10.1 …），但 scan_species 是从 *.1.bt2 反推前缀的，搬过去照样认得出来。
    也不复制：索引动辄几十 GB，搬比拷快。
    """
    root = Path(anno_root)
    src = root / species_id / legacy_dir
    if not src.is_dir():
        raise FileNotFoundError(f"源目录不存在: {src}")
    # 没有来源就没法拼变体 ID，目标会落回源目录内部，到时候 move 自己搬自己
    if not str(source_id or "").strip():
        raise ValueError("必须指定数据库来源")

    vid = _rt.variant_id(species_id, source_id)
    dst_root = root / vid
    dst = dst_root / f"bowtie2_index_{vid}"
    if dst.exists():
        raise FileExistsError(f"目标已存在，不覆盖: {dst}")

    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))

    # 老物种目录搬空后删掉，否则「已配置」里会挂着一个空壳物种
    legacy_root = root / species_id
    try:
        if legacy_root.is_dir() and not any(legacy_root.iterdir()):
            legacy_root.rmdir()
    except OSError:
        pass

    return {"variant_id": vid, "source_id": source_id, "from": str(src), "to": str(dst)}


def repoint_index_dir(config_path: str | Path, old_sub: str, new_sub: str) -> List[str]:
    """把 config.yaml 里 index_dir 含 old_sub 的项目改写成 new_sub。

    索引搬家后不同步改 config，项目就会指向一个不存在的路径，跑到 3_ChIPseq 才报错。
    返回被改到的项目（物种/实验），供 UI 提示。
    """
    import yaml  # 延迟导入

    cfg_path = Path(config_path)
    if not cfg_path.is_file():
        return []
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    changed: List[str] = []
    for p in cfg.get("projects") or []:
        idx = str(p.get("index_dir") or "")
        if old_sub and old_sub in idx:
            p["index_dir"] = idx.replace(old_sub, new_sub)
            changed.append(f"{p.get('species')}/{p.get('experiment')}")

    if changed:
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return changed


def human_size(n: int | float) -> str:
    """字节 → 人类可读。"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------- config.yaml 写入（应用注释到项目） ----------
def apply_to_config(
    config_path: str | Path,
    species_id: str,
    experiment: str,
    index_dir: str,        # 相对项目根的路径，如 workflow/anno/TAIR10/bowtie2_index_TAIR10/TAIR10_Chr
    txdb: str,             # Bioconductor 包名 或 GFF 相对路径
    orgdb: str = "",
    rawdata_dir: str = "",
    modules: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """
    在 config.yaml 的 projects 列表里插入或更新对应条目。
    返回更新后的 config dict（UI 侧可展示）。
    """
    import yaml  # 延迟导入

    cfg_path = Path(config_path)
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    projects = cfg.get("projects") or []
    entry = {
        "species": species_id,
        "experiment": experiment,
        "rawdata_dir": rawdata_dir,
        "index_dir": index_dir,
        "txdb": txdb,
        "orgdb": orgdb,
        "modules": modules or {
            "1_download": False,
            "2_QC": False,
            "3_ChIPseq": True,
            "4_analyse": True,
            "5_gsea": bool(orgdb),
            "6_motif": True,
        },
    }

    # 更新已有 (species, experiment) 组合，否则追加
    replaced = False
    for i, p in enumerate(projects):
        if p.get("species") == species_id and p.get("experiment") == experiment:
            # 保留原有 modules / rawdata_dir / orgdb（如用户已在旧条目里填过）
            merged = dict(p)
            for k, v in entry.items():
                if k == "modules":
                    continue   # modules 只在调用者显式传入时才覆盖
                if v in (None, "", {}):
                    continue   # 空值不覆盖
                merged[k] = v
            if modules is not None:
                merged["modules"] = modules
            projects[i] = merged
            replaced = True
            break
    if not replaced:
        projects.append(entry)

    cfg["projects"] = projects
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return cfg


# ---------- 依赖自检 ----------
# 工具定向统一委托 env_paths（launcher/analysis 双 prefix 解耦）：
#   <analysis_prefix>/bin : sys.prefix/bin : 系统 PATH
# 开发态（分析 prefix 不存在）自动回落 sys.prefix/bin，保持向后兼容。
from utils import env_paths as _env_paths  # noqa: E402


def conda_env_bin() -> str:
    """分析环境 bin；不存在时回落到当前 Python 环境 bin（供 UI 展示）。"""
    ab = _env_paths.analysis_bin()
    return ab if os.path.isdir(ab) else os.path.join(sys.prefix, "bin")


def tool_search_path() -> str:
    """命令搜索路径：优先分析环境 bin，再叠加当前环境 bin 与系统 PATH。"""
    return _env_paths.tool_search_path()


def resolved_tool_path(cmd: str) -> Optional[str]:
    """在分析环境 bin + 当前环境 bin + 系统 PATH 中查找命令，返回绝对路径或 None。"""
    return _env_paths.resolved_tool_path(cmd)


def check_dependencies() -> Dict[str, bool]:
    """检查 worker 依赖的外部命令是否可用（在 tool_search_path() 中查找）。"""
    deps: Dict[str, bool] = {}
    for cmd in ("curl", "bowtie2", "bowtie2-build", "samtools", "gunzip"):
        deps[cmd] = _env_paths.resolved_tool_path(cmd) is not None
    return deps
