"""
genome_browser.py — 结果预览页「基因组浏览器」的数据层与绘图层
================================================================

把 3_ChIPseq 产出的 bigWig（bamCoverage --binSize 5 --normalizeUsing BPM）按任意
基因组区间重采样成曲线，叠加基因模型与 MACS2 peak，交给 Streamlit 页直接画。

为什么不做成交接文档里的独立服务
--------------------------------
《ChIP信号浏览器_实现交接文档》给的是 FastAPI + 原生 Canvas 单页：自建
/api/signal 端点、前端 fetch、HTTP Range 拉 bw。那套方案成立的前提是**浏览器
独立成站**。本项目的 UI 是 Streamlit，一页就是一次 rerun，塞不进 iframe 里跑
fetch——components.html 是单向的，前端缩放后的视野回传不给 Python，硬凑就得再
起一个常驻服务 + 端口 + 鉴权，为了一个 tab 不值得。

这里改用 plotly：同一份 Python，原生滚轮缩放、拖拽平移、十字线、tooltip、
导出 PNG 全部现成，不引入任何新进程。取舍是「缩放只在已加载窗口内是即时的，
换个视野要重新取数」——而这本来就是 IGV 的行为，换 locus 必然重读 bigWig。

口径（与交接文档 §5.3 一致）
-----------------------------
* ``nBins`` = 画布宽度级（默认 1000），**不是** ``nBins=1``；
* ``nan → 0`` 再参与统计（无覆盖的 bin 按 0 处理）；
* 平滑只影响显示，窗口统计一律用 raw；
* 不混基因组版本：bw 与注释必须共用一套染色体命名，解析注释时按此校验。

实测（本机、binSize=5 的 TAIR10 bw）：``stats()`` 约 110–140 ms / 1000 bins，
与窗口跨度基本无关，所以优化点在 bins 而不在「换个粗 bw」。
"""

from __future__ import annotations

import gzip
import hashlib
import math
import os
import re
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:  # UI 侧可选依赖：没装就优雅降级，页面提示跑 chipseq-app-update
    import pyBigWig
except ImportError:  # pragma: no cover - 取决于部署环境
    pyBigWig = None

try:  # 包内导入（streamlit 页里 from utils import genome_browser）
    from utils import env_paths
except ImportError:  # pragma: no cover
    import env_paths  # type: ignore


# ===========================================================================
# 配色（沿用交接文档 §3 的两层色：系列色管轨道，类别色管注释）
# ===========================================================================

# 样本系列色：按出图顺序取，取完循环。第一个是 ChIP 主色。
SERIES_COLORS = (
    "#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
)
INPUT_COLOR = "#8a8a8a"        # 对照轨统一灰，避免和任何 ChIP 系列色混淆
GENE_PLUS = "#1f3d5c"          # 正链基因
GENE_MINUS = "#5a5a5a"         # 负链基因
# 底带颜色必须够深才能看见。第一版用了 #f6d7e6 / #cfe2f3 配 0.35 透明度，
# 和白色混完分别只剩 (252,241,246) 和 (234,245,252)——白底上等于没有。
# 这里取的是「混合后正好落在浅粉/浅蓝」的值，反推出来的饱和度。
QUERY_BAND = "#a8cdec"         # 目标基因高亮带
PEAK_BAND = "#f17eb2"          # MACS2 peak 底带
GRID = "#ececec"

# 常见 gzip 注释后缀；顺序影响预览时的排序（非压缩优先）
GFF_EXTS = (".gff", ".gff3", ".gtf", ".gff.gz", ".gff3.gz", ".gtf.gz")

# 输出缓存放在 runtime 下，不污染仓库
CACHE_SUBDIR = os.path.join("cache", "browser")

DEFAULT_BINS = 1000

# 注释里用来声明「整条染色体」的 feature 类型，用来提取长度做同源校验
_REGION_FEATS = frozenset({"chromosome", "region", "supercontig", "contig"})
MAX_BINS = 2000
MIN_SPAN = 200                 # bp，最小视野
MAX_SPAN = 2e8                 # bp，最大视野（≈整条染色体）


# ===========================================================================
# 一、bigWig 层
# ===========================================================================

def have_pybigwig() -> bool:
    """启动器环境里有没有 pyBigWig。没装时页面给一句「跑 chipseq-app-update」。"""
    return pyBigWig is not None


def open_check(path: str) -> Optional[str]:
    """试探一个 bw 能不能打开，返回错误文本或 None。"""
    if pyBigWig is None:
        return "pyBigWig 未安装"
    try:
        bw = pyBigWig.open(path)
        try:
            if not bw.chroms():
                return "文件里没有染色体记录"
        finally:
            bw.close()
    except Exception as e:  # pyBigWig 抛的是 RuntimeError，不是 OSError
        return f"{type(e).__name__}: {e}"
    return None


def _mt(path: str) -> float:
    """mtime，作为 lru_cache 的隐式失效键（文件重跑后自动换新）。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@lru_cache(maxsize=64)
def _chroms_cached(path: str, _mtime: float) -> Tuple[Tuple[str, int], ...]:
    if pyBigWig is None:
        return ()
    bw = pyBigWig.open(path)
    try:
        items = bw.chroms() or {}
        return tuple(sorted(items.items()))
    finally:
        bw.close()


def chrom_sizes(bw_path: str) -> Dict[str, int]:
    """bw 的染色体长度表。不同 bw 之间用它做基因组版本一致性校验。"""
    return dict(_chroms_cached(bw_path, _mt(bw_path)))


def read_signal(path: str, chrom: str, start: int, end: int, bins: int) -> np.ndarray:
    """把 [start, end) 重采样成 ``bins`` 个等宽箱的均值（raw，不平滑）。

    ``nan``（该 bin 无任何覆盖）按 0 处理，这是交接文档 §5.3 的口径，也是
    ChIP 信号图的通行做法：把「没读段」画成 0 而不是断线，峰形才连得起来。

    染色体不在该 bw 里时返回全 0，并让调用方另外提示——不能抛异常把整页带崩，
    一个样本错配不该让其余样本也看不见。
    """
    bins = max(1, int(bins))
    empty = np.zeros(bins, dtype=np.float32)
    if pyBigWig is None or not os.path.isfile(path):
        return empty
    return np.asarray(_stats_cached(path, chrom, int(start), int(end), bins, _mt(path)),
                      dtype=np.float32)


@lru_cache(maxsize=512)
def _stats_cached(path: str, chrom: str, start: int, end: int, bins: int,
                  _mtime: float) -> Tuple[float, ...]:
    bw = pyBigWig.open(path)
    try:
        vals = bw.stats(chrom, start, end, nBins=bins)
    except Exception:
        # 染色体名对不上 / 区间越界都会走到这里；退化成全 0 而不是让页面报错
        return (0.0,) * bins
    finally:
        bw.close()
    if not vals:
        return (0.0,) * bins
    # nan 必须先变 0 再参与任何均值（文档 §9 坑 5）
    return tuple(0.0 if (v is None or not math.isfinite(v)) else float(v) for v in vals)


def smooth(values: np.ndarray, bin_bp: float, smooth_bp: float) -> np.ndarray:
    """显示层滑动平均。``kernel = smooth_bp / bin_bp``，不是按点数拍脑袋。

    与交接文档 §5.3 的写法有一处刻意不同：那边用 ``convolve(v, ones/k, 'same')``，
    窗口在两端被补零，曲线首尾会「往下塌」一截，看着像信号衰减。这里除以实际的
    参与计数（卷积一个全 1 序列），两端不衰减，视觉上就不会误导。
    """
    k = int(round(max(0.0, smooth_bp) / max(bin_bp, 1e-9)))
    if k <= 1 or values.size < 2:
        return values
    k = min(k, values.size)
    num = np.convolve(values, np.ones(k, dtype=np.float64), mode="same")
    den = np.convolve(np.ones_like(values, dtype=np.float64), np.ones(k), mode="same")
    return (num / np.maximum(den, 1e-9)).astype(np.float32)


def nice_max(value: float) -> float:
    """把最大值向上取整到 1/2/2.5/5×10^n，y 轴刻度才不会出现 0.00371 这种数。"""
    if not math.isfinite(value) or value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = 10.0 ** exp
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= m * base:
            return m * base
    return 10.0 * base


# ===========================================================================
# 二、样本层：从结果目录里把 bw 收出来，并认一下谁是 INPUT
# ===========================================================================

def _looks_input(name: str) -> bool:
    """名字里带 input/igg 或结尾是 ck 的，按对照处理。

    只是兜底：有 metadata.csv 时以它为准（那份表是用户自己填的），没有才靠猜。
    """
    n = name.lower()
    return ("input" in n) or ("igg" in n) or n.endswith(("_ck", "-ck"))


def read_metadata(metadata_path: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """读 config/metadata.csv → (样本→对照, 样本→重复组)。

    表头约定：``IP sample, Input, group``。列名容忍大小写与空格，
    因为这张表是用户手写的，不应为一个空格就整块失效。
    """
    pair: Dict[str, str] = {}
    group: Dict[str, str] = {}
    if not metadata_path or not os.path.isfile(metadata_path):
        return pair, group
    try:
        df = pd.read_csv(metadata_path)
    except Exception:
        return pair, group
    cols = {c.strip().lower(): c for c in df.columns}
    ip_col = cols.get("ip sample") or cols.get("ip") or cols.get("sample")
    in_col = cols.get("input")
    gr_col = cols.get("group")
    if ip_col is None:
        return pair, group
    for _, row in df.iterrows():
        ip = str(row.get(ip_col, "")).strip()
        if not ip or ip == "nan":
            continue
        if in_col is not None:
            inp = str(row.get(in_col, "")).strip()
            if inp and inp != "nan":
                pair[ip] = inp
        if gr_col is not None:
            g = str(row.get(gr_col, "")).strip()
            if g and g != "nan":
                group[ip] = g
    return pair, group


def list_samples(result_dir: str, metadata_path: Optional[str] = None) -> List[Dict]:
    """列出项目产出的样本 bigWig。

    只认 3_ChIPseq 下的样本级 bw：那是 bamCoverage --binSize 5 --normalizeUsing BPM
    的直接产物，口径单一、可解释。4_analyse 下的 bigwig 目录是 bamCompare 的派生
    结果（可能已按 log2 或 --scaleFactorsMethod 处理过），混进来会让人以为两条
    曲线可以直接比高低，所以不放进样本列表。
    """
    chip_dir = os.path.join(result_dir, "3_ChIPseq")
    if not os.path.isdir(chip_dir):
        return []

    pair, group = read_metadata(metadata_path)
    # 「被当成对照引用的那些名字」才是 INPUT。直接判断 pair.get(name) 非空会把
    # 每个 IP 也误判成 INPUT——IP2 有配对值 INPUT2，但它本身是实验组。
    input_names = {v for v in pair.values() if v}
    out: List[Dict] = []
    for name in sorted(os.listdir(chip_dir)):
        d = os.path.join(chip_dir, name)
        if not os.path.isdir(d):
            continue
        bw = os.path.join(d, f"{name}.bw")
        if not os.path.isfile(bw):
            cands = sorted(f for f in os.listdir(d) if f.endswith(".bw"))
            if not cands:
                continue
            bw = os.path.join(d, cands[0])
        is_input = name in input_names or _looks_input(name)
        role = "INPUT" if is_input else "CHIP"
        out.append({
            "id": name,
            "label": name,
            "role": role,
            "pair": "" if is_input else pair.get(name, ""),   # CHIP → 它的对照名
            "group": group.get(name, ""),
            "path": bw,
            "size_mb": round(os.path.getsize(bw) / 1e6, 1) if os.path.isfile(bw) else 0.0,
            "mtime": _mt(bw),
        })

    # 排序：CHIP 在前（按名字），INPUT 在后。图上先看实验组更符合直觉。
    out.sort(key=lambda s: (s["role"] != "CHIP", s["id"]))
    for i, s in enumerate(out):
        s["color"] = INPUT_COLOR if s["role"] == "INPUT" else SERIES_COLORS[
            sum(1 for x in out[:i] if x["role"] != "INPUT") % len(SERIES_COLORS)
        ]
    return out


def common_contigs(samples: Sequence[Dict]) -> Tuple[Dict[str, int], List[str]]:
    """取所有样本共有的染色体长度表，顺带报告谁不合群。

    一个样本换了参考基因组（比如 hg19 混进 hg38）时，长度对不上会直接表现为
    「峰位错位」而不是报错，肉眼很难发现，所以在入口处就拦一次。
    """
    warnings: List[str] = []
    if not samples:
        return {}, warnings
    base = chrom_sizes(samples[0]["path"])
    for s in samples[1:]:
        cur = chrom_sizes(s["path"])
        bad = [c for c, n in cur.items() if c in base and base[c] != n]
        extra = [c for c in base if c not in cur]
        if bad:
            warnings.append(f"{s['id']} 的染色体长度与其他样本不一致（{', '.join(bad[:3])}）")
        elif extra and len(extra) >= len(base) // 2:
            warnings.append(f"{s['id']} 的染色体命名与其他样本不同（缺 {', '.join(extra[:3])}）")
    return base, warnings


# ===========================================================================
# 三、注释层：GFF/GTF → 基因/外显子索引（带缓存）
# ===========================================================================

def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _scan_gffs(folder: str) -> List[str]:
    if not folder or not os.path.isdir(folder):
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = [os.path.join(folder, n) for n in names
             if n.lower().endswith(GFF_EXTS) and os.path.isfile(os.path.join(folder, n))]
    # 非压缩排前：解析快一截，且 gzip 损坏时不会只有一个候选可试
    found.sort(key=lambda p: (p.lower().endswith(".gz"), os.path.basename(p).lower()))
    return found


def _index_contigs(index_dir: str, repo_root: str) -> Tuple[Dict[str, int], str]:
    """从比对索引反推这套数据库的 seqid 与长度，返回 (染色体表, fai 路径)。

    ``index_dir`` 指向 bowtie2 索引前缀（…/bowtie2_index_TAIR/TAIR10_Chr），
    同目录的 ``<prefix>.fa.fai`` 前两列就是建索引那份 FASTA 的染色体名与长度。

    为什么拿它当锚点：bigWig 的 header 本来就是 aligner 从这份 FASTA 抄过去的，
    而「注释是不是同一套基因组」从此有了硬判据——名字可能巧合（哪个物种都有
    Chr1），长度对不上就一定不是同一套。找不到 .fai 时返回空表，调用方退回
    只用染色体名判断。
    """
    if not index_dir:
        return {}, ""
    base = index_dir if os.path.isabs(index_dir) else os.path.join(repo_root, index_dir)
    folder, prefix = os.path.dirname(base), os.path.basename(base)
    if not prefix or not os.path.isdir(folder):
        return {}, ""

    fai = ""
    for ext in (".fa.fai", ".fasta.fai", ".fna.fai", ".fai"):
        cand = os.path.join(folder, prefix + ext)
        if os.path.isfile(cand):
            fai = cand
            break
    if not fai:
        # 前缀未必带扩展名（老索引叫 TAIR10_Chr，FASTA 却可能是 TAIR10.fa），
        # 索引目录里通常只有一份 FASTA，退而取唯一的那个 .fai
        found = [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                 if n.endswith(".fai")]
        fai = found[0] if len(found) == 1 else ""

    contigs: Dict[str, int] = {}
    if fai:
        try:
            with open(fai, "rt", errors="replace") as fh:
                for line in fh:
                    f = line.split("\t")
                    if len(f) >= 2 and f[1].isdigit():
                        contigs[f[0]] = int(f[1])
        except OSError:
            contigs = {}
    return contigs, fai


@lru_cache(maxsize=64)
def _peek_seqids(path: str, _mtime: float, limit: int = 3000) -> Tuple[str, ...]:
    """扫 GFF 前若干行，收集 seqid。用来判断这份注释和 bw 是不是同一套命名。"""
    seqids: List[str] = []
    seen = set()
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as fh:
            for i, line in enumerate(fh):
                if line.startswith("##FASTA"):
                    break
                if line.startswith("#") or not line.strip():
                    continue
                sid = line.split("\t", 1)[0]
                if sid not in seen:
                    seen.add(sid)
                    seqids.append(sid)
                if i >= limit or len(seqids) >= 40:
                    break
    except (OSError, EOFError):
        pass
    return tuple(seqids)


def _chrom_score(seqids: Sequence[str], chroms: Iterable[str]) -> float:
    """注释的 seqid 有多少比例能对上 bw 的染色体名。1.0 = 完全一致。"""
    if not seqids:
        return 0.0
    pool = set(chroms)
    return sum(1 for s in seqids if s in pool) / len(seqids)


@lru_cache(maxsize=64)
def _peek_regions(path: str, _mtime: float, limit: int = 500) -> Dict[str, int]:
    """从注释里读出它声明的染色体长度，返回 seqid → 长度。

    这是判断「这份注释和比对数据库是不是同一套基因组」的唯一硬证据：光看
    seqid 名字不够——Ensembl 哪天把 1/2/3 改成 Chr1/Chr2，照样能和 TAIR 官方
    的 Chr1/Chr2 撞名，但两者基因模型差一个版本，基因坐标就会系统性偏差。

    两种写法都认：GFF3 的 ``##sequence-region <id> <start> <end>``（头行），
    以及 GFF/GTF 常见的 ``chromosome``/``region`` 特征行（带起止坐标）。
    都没有时返回空表，调用方退回只按名字判断。
    """
    out: Dict[str, int] = {}
    opener = gzip.open if path.endswith(".gz") else open
    # 这里必须扫完，不能“拿到几条就停”：标注是按基因排的，TAIR 官方这份
    # GFF 写完 Chr1 一行 chromosome 就是七万行基因，Chr2 的头行还在后面。
    # 提前收手只会拿到 1 条，虽然照样能算出 1.0，但那是运气，不是证据。
    # 代价（实测 44 MB / 55 万行 ≈ 300 ms）由 lru_cache 和调用方的
    # “名字先对上才查长度”抵消，实际每个项目只扫一份。
    try:
        with opener(path, "rt", errors="replace") as fh:
            for line in fh:
                if line.startswith("##FASTA"):
                    break
                if line.startswith("##sequence-region"):
                    parts = line.split()
                    if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
                        out.setdefault(parts[1], int(parts[3]) - int(parts[2]) + 1)
                    continue
                if line.startswith("#") or not line.strip():
                    continue
                f = line.rstrip("\n").split("\t")
                if len(f) >= 5 and f[2].lower() in _REGION_FEATS \
                        and f[3].isdigit() and f[4].isdigit():
                    out.setdefault(f[0], int(f[4]) - int(f[3]) + 1)
                if len(out) >= limit:
                    break
    except (OSError, EOFError):
        pass
    return out


def _length_score(regions: Dict[str, int], index_contigs: Dict[str, int]) -> float:
    """注释里能对上索引长度的染色体比例。1.0 = 就是同一套基因组。"""
    if not regions or not index_contigs:
        return 0.0
    hit = sum(1 for c, n in regions.items() if index_contigs.get(c) == n)
    return hit / len(regions)


def resolve_annotation(repo_root: str, species: str, index_dir: str, txdb: str,
                       chroms: Iterable[str],
                       override: Optional[str] = None) -> Dict:
    """挑一份与**比对数据库**同源的 GFF/GTF 当背景注释。

    「比对数据库」就是本项目 3_ChIPseq 用的 bowtie2 索引：bigWig 的染色体名和
    长度都来自建索引那份 FASTA。背景注释必须和它描的是同一套基因组，否则基因
    模型会整体错位——而这种错位在图上很隐蔽，曲线照样好看，只是基因对不上号。

    判定用两道，缺一不可：
      * ``score``：seqid 名字能不能对上 bigWig 的染色体名；
      * ``length_score``：注释声明的染色体长度与索引 ``.fai`` 是否逐条相等。
    名字可能巧合（哪个物种都有 Chr1），长度几乎不可能。

    扫描由内向外，越近越可信：
      1. 手动 override / 配置里 txdb 就是本地 GFF（用户意图直接算数）；
      2. 索引目录本身，以及新结构``<物种>__<源>/``里与 bowtie2 并列的注释；
      3. 物种目录 ``workflow/anno/<物种>/*/`` 下的历史注释。

    老结构（本项目 TAIR10 就是）索引目录里没放 GFF，注定落在第 3 层；
    靠长度校验确认它确实是同一套基因组后，会把它标成「比对数据库同源」。
    """
    pool = list(chroms)
    cands: List[Dict] = []
    # 比对数据库的指纹：建索引那份 FASTA 的 seqid 与长度
    idx_contigs, idx_fai = _index_contigs(index_dir, repo_root)

    def _add(path: str, source: str, near: int = 2) -> None:
        if not path:
            return
        p = path if os.path.isabs(path) else os.path.join(repo_root, path)
        if not os.path.isfile(p):
            return
        if any(c["path"] == p for c in cands):
            return
        seqs = _peek_seqids(p, _mt(p))
        score = _chrom_score(seqs, pool)
        # 名字都对不上的候选没资格谈同源，跳过全文件扫——否则十份历史的
        # Ensembl/NCBI 注释每次冷启动都要各解一遍 gz，白白多花一秒
        regions = _peek_regions(p, _mt(p)) if (score > 0 and idx_contigs) else {}
        len_score = _length_score(regions, idx_contigs)
        cands.append({
            "path": p,
            "rel": _rel(p, repo_root),
            "source": source,
            "near": near,
            "score": round(score, 4),
            "length_score": round(len_score, 4),
            # 长度逐条对上才算同一套基因组；注释里没写长度时不敢断言
            "same_genome": bool(len_score >= 0.99),
            "seqid_sample": ", ".join(seqs[:4]),
            "n_regions": len(regions),
        })

    if override:
        _add(override, "手动指定", near=-1)
    if txdb and txdb.lower().endswith(GFF_EXTS):
        _add(txdb, "配置 txdb", near=-1)

    idx = index_dir or ""
    if idx:
        idx_abs = idx if os.path.isabs(idx) else os.path.join(repo_root, idx)
        # index_dir 形如 .../bowtie2_index_TAIR/TAIR10_Chr，注释通常和索引前缀同层
        idx_dir = os.path.dirname(idx_abs) or repo_root
        for p in _scan_gffs(idx_dir):
            _add(p, "索引同层", near=0)
        # 新结构 <物种>__<源>/：注释和 bowtie2_index_* 并列，而不是塞在索引目录里
        parent = os.path.dirname(idx_dir) or repo_root
        if "__" in os.path.basename(parent):
            for p in _scan_gffs(parent):
                _add(p, "变体目录", near=0)

    sp_dir = os.path.join(repo_root, "workflow", "anno", species) if species else ""
    if sp_dir and os.path.isdir(sp_dir):
        for name in sorted(os.listdir(sp_dir)):
            sub = os.path.join(sp_dir, name)
            if os.path.isdir(sub):
                for p in _scan_gffs(sub):
                    _add(p, f"物种目录 · {name}", near=1)

    if not cands:
        return {"path": None, "rel": None, "source": None, "score": 0.0,
                "candidates": [], "index_contigs": idx_contigs, "index_fai": idx_fai,
                "warning": "没找到任何 GFF/GTF，基因模型与基因名定位不可用"}

    # 近的优先 → 能证明同一套基因组的优先 → 长度/名字匹配率高的优先。
    # near 放在最前面：索引旁边那份天然和比对数据库同源，没理由让一个
    # 名字碰巧对得上的远房注释盖过它。
    cands.sort(key=lambda c: (c["near"], not c["same_genome"],
                              -c["length_score"], -c["score"],
                              c["path"].lower().endswith(".gz"), c["rel"]))
    best = cands[0]

    # 来源文案：能在长度上证明同源的，直接说清楚，别让用户去猜目录名的含义
    if best["near"] >= 1 and best["same_genome"]:
        best["source"] = "比对数据库同源 · " + best["source"].split(" · ")[-1]

    warning = None
    if best["score"] < 0.5:
        warning = (
            f"最匹配的注释 `{best['rel']}` 只有 {best['score']:.0%} 的染色体名能对上 bigWig"
            f"（注释用 {best['seqid_sample']}）。基因模型可能整体错位，建议换一份与索引同源的注释。"
        )
    # 没拿到索引指纹时（有人只留了 bt2 把 .fai 删了），长度校验无从谈起，
    # 这时候不能反过来说注释“不同源”，得明确退化成只按名字判断
    elif best["n_regions"] and not best["same_genome"] and idx_contigs:
        warning = (
            f"注释 `{best['rel']}` 声明的染色体长度与比对数据库只有 "
            f"{best['length_score']:.0%} 能对上，可能是同物种的另一个版本，"
            "基因坐标会有系统偏差；最好换成建索引时配套的那一份。"
        )
    return {**best, "candidates": cands, "warning": warning,
            "index_contigs": idx_contigs, "index_fai": idx_fai}


def _parse_attrs(text: str) -> Dict[str, str]:
    """GFF3（k=v;）与 GTF（k "v";）通吃。"""
    out: Dict[str, str] = {}
    if "=" in text and not re.match(r'^\s*\w+\s+"', text):
        for kv in text.split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k.strip()] = v.strip()
    else:
        for m in re.finditer(r'(\w+)\s+"([^"]*)"', text):
            out[m.group(1)] = m.group(2)
    return out


def _is_transcript(feat: str) -> bool:
    """判断 GFF 的 feature 列是不是「转录本」层。

    GFF3/GTF 的写法各家不统一：mRNA / transcript / ncRNA / lnc_RNA /
    pseudogenic_transcript 都有。外显子要靠它才能挂回基因，漏一种就会让那批
    基因只剩一根光秃的内参线。用后缀判比穷举类型名稳。
    """
    f = feat.lower()
    return f in ("mrna", "transcript") or f.endswith("rna") or f.endswith("transcript")


def _first(d: Dict[str, str], keys: Sequence[str]) -> str:
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


def anno_cache_paths(gff_path: str) -> Tuple[str, str]:
    """按「路径 + 大小 + mtime」派生缓存文件名。

    只按路径算不行：注释换了版本但文件名没变（很常见，都是 annotation.gff3.gz）
    时会一直吃到旧索引，基因坐标全错且毫无提示。
    """
    try:
        st = os.stat(gff_path)
        sig = f"{os.path.abspath(gff_path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(gff_path)
    key = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
    d = os.path.join(str(env_paths.runtime_dir()), CACHE_SUBDIR)
    return (os.path.join(d, f"{key}.genes.tsv.gz"),
            os.path.join(d, f"{key}.exons.tsv.gz"))


def build_anno_cache(gff_path: str, force: bool = False) -> Tuple[str, str]:
    """把 GFF/GTF 压成两张小表（genes / exons），供页面毫秒级查询。

    原文件动辄几十万行（拟南芥 59 万行），每开一次页面都重解析会卡十几秒，
    所以只解析一次，结果按签名缓存到 runtime 下。``##FASTA`` 之后是序列，
    必须停下——否则会把碱基当成 feature 行，白白多读几十 MB。
    """
    genes_out, exons_out = anno_cache_paths(gff_path)
    if not force and os.path.isfile(genes_out) and os.path.isfile(exons_out):
        return genes_out, exons_out
    os.makedirs(os.path.dirname(genes_out), exist_ok=True)

    genes: List[Tuple] = []
    exons: List[Tuple] = []
    gene_by_id: Dict[str, str] = {}      # 基因 ID → 显示名
    tx2gene: Dict[str, str] = {}         # 转录本 ID → 基因 ID

    opener = gzip.open if gff_path.endswith(".gz") else open
    with opener(gff_path, "rt", errors="replace") as fh:
        for line in fh:
            if line.startswith("##FASTA"):
                break
            if line.startswith("#") or not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9:
                continue
            chrom, feat, start1, end1, strand, attr = f[0], f[2], f[3], f[4], f[6], f[8]
            if not start1.isdigit() or not end1.isdigit():
                continue
            start, end = int(start1) - 1, int(end1)      # GFF 1-based → 0-based 半开
            a = _parse_attrs(attr)

            if feat == "gene" or feat.endswith("_gene") or feat == "pseudogene":
                gid = _first(a, ("ID", "gene_id")) or f"{chrom}:{start}-{end}"
                name = _first(a, ("gene_name", "Name", "gene", "gene_id", "locus_tag")) or gid
                biotype = _first(a, ("gene_biotype", "gene_type", "biotype", "Note"))
                gene_by_id[gid] = name
                genes.append((chrom, start, end, strand if strand in "+-" else ".", name, gid, biotype))
            elif _is_transcript(feat):
                tid = _first(a, ("ID", "transcript_id"))
                parent = _first(a, ("Parent", "gene_id")).split(",")[0]
                if tid and parent:
                    tx2gene[tid] = parent
            elif feat == "exon":
                parent = _first(a, ("Parent", "transcript_id")).split(",")[0]
                if parent:
                    exons.append((chrom, start, end, parent))

    # 外显子挂在转录本上，落到基因层；找不到父基因的丢弃（scaffold 上的孤儿注释）
    exon_rows = []
    for chrom, start, end, parent in exons:
        gid = tx2gene.get(parent, parent)
        if gid in gene_by_id:
            exon_rows.append((chrom, start, end, gid))

    genes.sort(key=lambda r: (r[0], r[1]))
    exon_rows.sort()
    with gzip.open(genes_out, "wt") as out:
        out.write("chrom\tstart\tend\tstrand\tname\tgid\tbiotype\n")
        for r in genes:
            out.write("\t".join(str(x) for x in r) + "\n")
    with gzip.open(exons_out, "wt") as out:
        out.write("chrom\tstart\tend\tgid\n")
        for r in exon_rows:
            out.write("\t".join(str(x) for x in r) + "\n")
    return genes_out, exons_out


class Annotation:
    """基因/外显子索引的查询视图。只读，实例本身按缓存路径复用。"""

    def __init__(self, genes_path: str, exons_path: str):
        self.genes = pd.read_csv(genes_path, sep="\t", dtype={
            "chrom": str, "strand": str, "name": str, "gid": str, "biotype": str,
        })
        for col in ("start", "end"):
            self.genes[col] = self.genes[col].astype("int64")
        self.exons = pd.read_csv(exons_path, sep="\t", dtype={"chrom": str, "gid": str})
        for col in ("start", "end"):
            self.exons[col] = self.exons[col].astype("int64")
        self._name_idx: Dict[str, List[int]] = {}
        for i, (nm, gid) in enumerate(zip(self.genes["name"], self.genes["gid"])):
            for key in {str(nm).lower(), str(gid).lower()}:
                self._name_idx.setdefault(key, []).append(i)

    # ---- 基因查询 -----------------------------------------------------
    def genes_in(self, chrom: str, start: int, end: int) -> pd.DataFrame:
        g = self.genes
        m = (g["chrom"] == chrom) & (g["start"] < end) & (g["end"] > start)
        sub = g[m]
        return sub.sort_values("start")

    def find_gene(self, name: str) -> pd.DataFrame:
        idx = self._name_idx.get(name.strip().lower(), [])
        if not idx:
            return self.genes.iloc[0:0]
        return self.genes.iloc[idx].sort_values("start")

    def search_names(self, prefix: str, limit: int = 20) -> List[str]:
        """基因名联想。前缀优先、子串兜底（交接文档 §5.2 的排序口径）。

        _name_idx 的键是小写（为了大小写不敏感），但返回的必须是注释里的
        原始写法：TAIR 全大写、小鼠首字母大写，直接回小写键用户会认不出来。
        """
        p = prefix.strip().lower()
        if not p:
            return []
        hit = [k for k in self._name_idx if k.startswith(p)]
        if len(hit) < limit:
            hit += [k for k in self._name_idx if p in k and not k.startswith(p)]
        out: List[str] = []
        for k in hit:
            name = str(self.genes.iloc[self._name_idx[k][0]]["name"])
            if name not in out:
                out.append(name)
            if len(out) >= limit:
                break
        return out

    def exons_in(self, chrom: str, start: int, end: int) -> Dict[str, List[Tuple[int, int]]]:
        e = self.exons
        m = (e["chrom"] == chrom) & (e["start"] < end) & (e["end"] > start)
        out: Dict[str, List[Tuple[int, int]]] = {}
        for s, en, gid in zip(e.loc[m, "start"], e.loc[m, "end"], e.loc[m, "gid"]):
            out.setdefault(gid, []).append((int(s), int(en)))
        return out


@lru_cache(maxsize=8)
def _load_annotation(genes_path: str, exons_path: str, _sig: str) -> Annotation:
    return Annotation(genes_path, exons_path)


def load_annotation(gff_path: str) -> Annotation:
    genes_path, exons_path = build_anno_cache(gff_path)
    sig = f"{_mt(genes_path)}|{_mt(exons_path)}"
    return _load_annotation(genes_path, exons_path, sig)


# ===========================================================================
# 四、locus 解析
# ===========================================================================

_RE_INTERVAL = re.compile(r"^([\w.]+)\s*:\s*([\d,]+)\s*(?:-|\.\.)\s*([\d,]+)$")
_RE_POINT = re.compile(r"^([\w.]+)\s*:\s*([\d,]+)$")
_RE_GENE_WIN = re.compile(r"^(.+?)\s*:\s*(up|down)\s*=\s*([\d.]+\s*[kKmMgG]?b?)"
                          r"(?:\s*,\s*(up|down)\s*=\s*([\d.]+\s*[kKmMgG]?b?))?$", re.I)
_RE_GENE_BODY = re.compile(r"^(.+?)\s*:\s*(?:body|gene)$", re.I)
_RE_SIZE = re.compile(r"^([\d.]+)\s*([kKmMgG]?)b?$")


def parse_size(text: str, default: int = 2000) -> int:
    """``5k`` / ``2.5kb`` / ``1M`` / ``5000`` → bp。"""
    m = _RE_SIZE.match((text or "").strip())
    if not m:
        return default
    mult = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def resolve_chrom(name: str, chrom_sizes_map: Dict[str, int]) -> Optional[str]:
    """容错地把用户写的染色体名映射到 bw 里的名字（chr1 / 1 / Chr1 都能认）。"""
    if name in chrom_sizes_map:
        return name
    low = {c.lower(): c for c in chrom_sizes_map}
    for cand in (name.lower(), name.lower().replace("chr", ""), "chr" + name.lower().lstrip("chr")):
        if cand in low:
            return low[cand]
    return None


def parse_locus(query: str, anno: Optional[Annotation],
                chrom_sizes_map: Dict[str, int]) -> Dict:
    """把用户输入解析成 ``{chrom, start, end, anchor, label, gene}``。

    支持（与交接文档 §4.4 对齐）：
      * ``AT1G01010``                → 基因体 ± 5 kb
      * ``AT1G01010:up=2k,down=5k``  → TSS 上游/下游（**按链方向**，负链会自动翻）
      * ``AT1G01010:body``           → 基因体 + 2 kb
      * ``Chr1:3630-5899``           → 绝对区间（容忍千分位逗号）
      * ``Chr1:5000000``             → 单位点 ± 10 kb

    抛 ``ValueError`` 时页面直接把消息显示给用户，所以文案要写成人话。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("请输入基因名或区间，例如 AT1G01010 / Chr1:3630-5899")

    m = _RE_INTERVAL.match(q)
    if m:
        chrom = resolve_chrom(m.group(1), chrom_sizes_map)
        if not chrom:
            raise ValueError(f"染色体 `{m.group(1)}` 不在当前样本的 bigWig 里")
        s, e = int(m.group(2).replace(",", "")), int(m.group(3).replace(",", ""))
        s, e = min(s, e), max(s, e)
        s, e = max(0, s), min(e, chrom_sizes_map[chrom])
        if e - s < MIN_SPAN:
            c = (s + e) // 2
            s, e = max(0, c - 100), min(c + 100, chrom_sizes_map[chrom])
        return {"chrom": chrom, "start": s, "end": e, "anchor": None,
                "label": f"{chrom}:{s:,}-{e:,}", "gene": None}

    m = _RE_POINT.match(q)
    if m:
        chrom = resolve_chrom(m.group(1), chrom_sizes_map)
        if not chrom:
            raise ValueError(f"染色体 `{m.group(1)}` 不在当前样本的 bigWig 里")
        c = int(m.group(2).replace(",", ""))
        s, e = max(0, c - 10000), min(c + 10000, chrom_sizes_map[chrom])
        return {"chrom": chrom, "start": s, "end": e, "anchor": c,
                "label": f"{chrom}:{s:,}-{e:,}", "gene": None}

    if anno is None:
        raise ValueError("基因名定位需要 GFF/GTF 注释，但当前项目没找到可用的注释文件")

    body_only = False
    m = _RE_GENE_BODY.match(q)
    if m:
        gene_name, body_only = m.group(1), True
    else:
        m = _RE_GENE_WIN.match(q)
        gene_name = m.group(1) if m else q

    hits = anno.find_gene(gene_name)
    if hits.empty:
        raise ValueError(f"注释里找不到基因 `{gene_name}`")
    g = hits.iloc[0]
    chrom = resolve_chrom(str(g["chrom"]), chrom_sizes_map)
    if not chrom:
        raise ValueError(f"基因 `{gene_name}` 在染色体 `{g['chrom']}` 上，但该染色体不在 bigWig 里")

    g_start, g_end = int(g["start"]), int(g["end"])
    strand = str(g["strand"])
    gene_info = {"name": str(g["name"]), "gid": str(g["gid"]), "chrom": chrom,
                 "start": g_start, "end": g_end, "strand": strand,
                 "biotype": str(g.get("biotype", "") or ""),
                 "n_hits": int(len(hits))}

    if body_only:
        pad = 2000
        s, e = g_start - pad, g_end + pad
        anchor = None
    elif m is not None and not body_only:
        up = down = 2000
        groups = m.groups()
        # 只有用户真写了 up=/down= 才覆盖默认值。不能按位置硬套：
        # `AT1G01010:down=2k` 只有一个参数，按位置套会把 down 当成 up。
        if groups[1]:
            if groups[1].lower() == "up":
                up = parse_size(groups[2])
            else:
                down = parse_size(groups[2])
        if groups[3] and groups[4]:
            if groups[3].lower() == "up":
                up = parse_size(groups[4])
            else:
                down = parse_size(groups[4])
        tss = g_start if strand != "-" else g_end - 1
        # 负链的 up 是坐标增大方向——这是最容易写错的地方，必须按 strand 翻
        if strand == "-":
            s, e = tss - down, tss + up
        else:
            s, e = tss - up, tss + down
        anchor = tss
    else:
        pad = 5000
        s, e = g_start - pad, g_end + pad
        anchor = None

    size = chrom_sizes_map[chrom]
    s, e = max(0, s), min(e, size)
    if e - s < MIN_SPAN:
        e = min(s + MIN_SPAN, size)
    return {"chrom": chrom, "start": int(s), "end": int(e), "anchor": anchor,
            "label": f"{gene_info['name']} ({chrom}:{int(s):,}-{int(e):,})",
            "gene": gene_info}


# ===========================================================================
# 五、MACS2 peak：跳转入口与底带
# ===========================================================================

@lru_cache(maxsize=64)
def _read_narrowpeak(path: str, _mtime: float) -> Tuple[Tuple[str, int, int, float], ...]:
    rows: List[Tuple[str, int, int, float]] = []
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) < 9:
                    continue
                try:
                    rows.append((f[0], int(f[1]), int(f[2]), float(f[8] or 0)))
                except ValueError:
                    continue
    except OSError:
        return ()
    return tuple(rows)


def list_peak_files(result_dir: str) -> List[str]:
    base = os.path.join(result_dir, "4_analyse", "peakcalling")
    out: List[str] = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".narrowPeak"):
                out.append(os.path.join(d, f))
    return out


def top_peaks(result_dir: str, limit: int = 20) -> List[Dict]:
    """全项目最强的若干个峰，给「跳到最强峰」下拉用。

    有了这个，用户打开 tab 就有东西可看，不用先想一个基因名——这是和纯
    IGV 比最实用的一处差别：peak 是这条流程自己 call 出来的，直接就是关注点。
    """
    scored: List[Dict] = []
    for path in list_peak_files(result_dir):
        sample = os.path.basename(os.path.dirname(path))
        for chrom, start, end, score in _read_narrowpeak(path, _mt(path)):
            scored.append({"sample": sample, "chrom": chrom, "start": start,
                           "end": end, "score": score})
    scored.sort(key=lambda p: -p["score"])
    return scored[:limit]


def peaks_in(result_dir: str, chrom: str, start: int, end: int,
             limit: int = 40, merge: bool = True) -> List[Dict]:
    """视野内的峰，按显著性取前若干个画底带。

    ``merge=True`` 把重叠区间并成一段。同一个位点在 IP2/IP3/IP4 各自的
    narrowPeak 里都有记录，不合并的话底带会在同一处叠 3~4 层半透明色，
    结果是一条发紫的实心块——既看不出是个峰，还把信号的填充色带偏。
    """
    hit: List[Dict] = []
    for path in list_peak_files(result_dir):
        sample = os.path.basename(os.path.dirname(path))
        for c, s, e, score in _read_narrowpeak(path, _mt(path)):
            if c == chrom and s < end and e > start:
                hit.append({"sample": sample, "start": s, "end": e, "score": score})
    hit.sort(key=lambda p: -p["score"])

    if merge:
        # 先按坐标排序再贪心合并；score 取区间内最大值，用来决定撞 limit 时谁留下
        span = sorted(hit, key=lambda p: p["start"])
        merged: List[Dict] = []
        for p in span:
            if merged and p["start"] <= merged[-1]["end"]:
                last = merged[-1]
                last["end"] = max(last["end"], p["end"])
                last["score"] = max(last["score"], p["score"])
                last["sample"] += "," + p["sample"]
            else:
                merged.append(dict(p))
        # 合并时是直接拼字符串的，同一个样本的峰如果分两次并进来就会重复，
        # 这里按原顺序去重，''.join(dict.fromkeys(...)) 是老写法里最少代码的
        for p in merged:
            p["sample"] = ",".join(dict.fromkeys(p["sample"].split(",")))
        hit = sorted(merged, key=lambda p: -p["score"])

    return hit[:limit]


# ===========================================================================
# 六、绘图
# ===========================================================================

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _layout_lanes(genes: pd.DataFrame, view_start: int, view_end: int,
                  max_lanes: int = 3) -> List[Tuple[Dict, int, bool]]:
    """给基因模型分行，避免相邻基因的方块叠在一起。

    贪心：按起点排序，放进第一条「已经空出来的」泳道；三条泳道全占满时
    丢到最后一条并标 overflow，绘图时画成截断箭头（与交接文档 §6.3 一致）。
    """
    rows: List[Tuple[Dict, int, bool]] = []
    lane_end = [-1] * max_lanes
    for _, g in genes.iterrows():
        s, e = int(g["start"]), int(g["end"])
        if e <= s:
            continue
        lane = next((i for i, en in enumerate(lane_end) if en <= s), None)
        overflow = lane is None
        if lane is None:
            lane = max_lanes - 1
        lane_end[lane] = max(lane_end[lane], e)
        rows.append(({
            "name": str(g["name"]), "gid": str(g["gid"]), "start": s, "end": e,
            "strand": str(g["strand"]), "clipped_l": s < view_start,
            "clipped_r": e > view_end,
        }, lane, overflow))
    return rows


def build_figure(*, locus: Dict, rows: Sequence[Dict], anno: Optional[Annotation],
                 smooth_bp: int = 200, bins: int = DEFAULT_BINS,
                 peaks: Optional[Sequence[Dict]] = None,
                 show_gene: bool = True, row_height: int = 92,
                 overlay: bool = False, shared_y: bool = True,
                 max_genes: int = 40) -> "object":
    """组装多行信号图。``rows`` 每项：label / color / values(已平滑) / raw / role。

    ``shared_y=True``（默认）让所有轨道共用一套 y 范围，顶 = 全场最高信号向上
    取整到 1/2/2.5/5×10^n。这是堆叠模式下能「看出谁高」的前提：各自 autoscale
    时每行的峰都顶到行顶，2.0 和 0.02 长得一模一样。代价是低信号轨会被压扁，
    真需要看它的细节时再关掉。

    ``overlay=True`` 把所有样本叠到一行、共用一根 y 轴：想看“谁更高”时用这个。

    图层顺序按交接文档 §6.3：底带 → 网格/y=0 → 填充 → 线 → 基因模型 → 文字。
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    chrom, start, end = locus["chrom"], int(locus["start"]), int(locus["end"])
    span = max(1, end - start)
    bin_bp = span / max(1, bins)

    # 基因模型行的去留要在这里定下来，因为它决定了 nrows 和总高。先查一遍：
    # 视野里一个基因都没有时（基因间隔区、或者选了个 peak 但附近没有注释）
    # 还占着一整行空白，白浪费 130px，看着像渲染坏了。
    genes = None
    has_gene = bool(show_gene and anno is not None)
    if has_gene:
        genes = anno.genes_in(chrom, start, end)
        if len(genes) > max_genes:
            genes = genes.assign(_w=(genes["end"] - genes["start"])).nlargest(
                max_genes, "_w").sort_values("start")
        has_gene = len(genes) > 0

    sig_rows = 1 if overlay else max(1, len(rows))
    nrows = sig_rows + (1 if has_gene else 0)

    # 高度必须按「行高→总高」反解，不能拍一个数字了事。make_subplots 会先扣掉
    # 所有 vertical_spacing 再按 row_heights 比例分剩下的，直接给 height 的话行高
    # 会随行数缩水——4 个样本时每行只剩 55px，标题就只能压在曲线上（第一版截
    # 图就是这么错的）。这里反过来：先定死 row_height，再解出总高。
    spacing = 0.055
    heights = [1.0] * sig_rows + ([1.40] if has_gene else [])
    units = float(sum(heights))
    frac = 1.0 - spacing * (nrows - 1)
    # 叠加只有一行，用信号轨的行高会扁得像一条缝（而叠加本来就是为了看高低差）
    if overlay:
        row_height = max(row_height, 200)
    avail = row_height * units / frac
    margin_t, margin_b = 40, 38
    height = int(round(avail + margin_t + margin_b))

    fig = make_subplots(rows=nrows, cols=1, shared_xaxes=True,
                        vertical_spacing=spacing, row_heights=heights)

    # 轨道名不用 subplot_titles：它居中且贴着行顶边画，行一矮就和数据打架。
    # 这里左对齐写进每行上方的空隙里（IGV 的轨道名做法）。
    # 注意 yref="yN domain" 是**该行内部**的 0-1 坐标，不是全局的——第一版按全局
    # 累加算 y，越往下偏得越多，最后一行直接掉到行底。在行内坐标系里每行的顶
    # 都是 1.0，靠 yanchor="bottom" 让文字向上长进间隙即可。
    labels = (["样本叠加"] if overlay else [r["label"] for r in rows])
    if has_gene:
        labels = labels + ["基因模型"]
    for i, text in enumerate(labels, start=1):
        fig.add_annotation(xref="x domain", yref="y domain",
                           x=0.0, y=1.0,
                           xanchor="left", yanchor="bottom",
                           text=f"<b>{text}</b>", showarrow=False,
                           font=dict(size=11, color="#333"),
                           bgcolor="rgba(255,255,255,0.85)", borderpad=2,
                           row=i, col=1)

    # 目标基因的高亮带：先画，压在数据下面。
    # exclude_empty_subplots=False 不能省：add_vrect(row="all") 默认会跳过还没有
    # trace 的空子图，而底带又是在所有信号轨之前画的——不加这个参数时整段 vrect
    # 会被默默吞掉，一个 shape 都不生成（第一版截图里 peak 带完全看不见就是这回事）。
    qgene = (locus.get("gene") or {}) if locus else {}
    if qgene and qgene.get("chrom") == chrom:
        fig.add_vrect(x0=qgene["start"], x1=qgene["end"], fillcolor=QUERY_BAND,
                      opacity=0.55, line_width=0, layer="below",
                      row="all", col=1, exclude_empty_subplots=False)

    # MACS2 peak 底带：只铺在信号轨上，不盖基因模型行——基因行有自己的内容，
    # 再叠一层底色只会让人看不清外显子。
    # 每行一份 shape，所以按行数反推上限，避免勾很多样本时 shape 数量失控
    # （几百个 shape 会让 plotly 在浏览器里明显地卡）。
    peak_cap = max(1, 120 // max(1, sig_rows))
    for ri in range(1, sig_rows + 1):
        for pk in (peaks or [])[:peak_cap]:
            fig.add_vrect(x0=max(pk["start"], start), x1=min(pk["end"], end),
                          fillcolor=PEAK_BAND, opacity=0.22, line_width=0,
                          layer="below", row=ri, col=1,
                          exclude_empty_subplots=False)

    # ---- 信号轨 ----
    glob_lo, glob_hi = 0.0, 0.0
    for i, r in enumerate(rows, start=1):
        vals = np.asarray(r["values"], dtype=float)
        raw = np.asarray(r.get("raw", vals), dtype=float)
        xs = np.linspace(start, end, vals.size, endpoint=False) + bin_bp / 2
        color = r.get("color", SERIES_COLORS[0])
        row_i = 1 if overlay else i
        fig.add_trace(go.Scatter(
            x=xs, y=vals, name=r["label"], mode="lines",
            # 叠加时线要粗一号：四条叠在一起，0.9px 的细线碰在一起就看不出是四条了
            line=dict(color=color, width=0.9 if not overlay else 1.5),
            # 叠加只画线不填充。四条 toself 半透明叠起来会混成一块脏兮兮的灰色，
            # 后面画的还把前面画的盖住，反而看不出各自的轮廓。
            fill="tozeroy" if not overlay else "none",
            fillcolor=_rgba(color, 0.32),
            customdata=raw,
            hovertemplate=("%{customdata:.3f} raw" if smooth_bp > 0 else "%{y:.3f}")
                          + "<extra>" + r["label"] + "</extra>",
            # 堆叠时每行标题已经写了样本名，图例是冗余的；叠加时全靠图例分辨
            showlegend=overlay,
        ), row=row_i, col=1)
        glob_lo = min(glob_lo, float(vals.min()))
        glob_hi = max(glob_hi, float(vals.max()))

    # ---- y 轴 ----
    # 默认所有轨道共用一套范围：以全场最高信号向上取整到 1/2/2.5/5×10^n 当顶。
    # 各轨各自 autoscale 的话，0.002 和 2.0 会被画成一样高——而「谁更高」正是
    # 这个 tab 要回答的问题，所以统一是默认，独立缩放反而需要显式关掉。
    shared_lo = float(min(0.0, glob_lo))
    shared_hi = float(nice_max(max(glob_hi, 1e-9)))

    if overlay:
        fig.update_yaxes(range=[shared_lo, shared_hi * 1.02], row=1, col=1,
                         showgrid=True, gridcolor=GRID, gridwidth=0.5,
                         zeroline=shared_lo < 0, zerolinecolor="#888", zerolinewidth=0.7,
                         tickfont=dict(size=9), nticks=4)
    else:
        for i, r in enumerate(rows, start=1):
            if shared_y:
                lo, hi = shared_lo, shared_hi
            else:
                vals = np.asarray(r["values"], dtype=float)
                lo = min(0.0, float(vals.min())) if r.get("signed") else 0.0
                hi = nice_max(float(max(vals.max(), 1e-9)))
            fig.update_yaxes(range=[lo, hi * 1.02], row=i, col=1,
                             showgrid=True, gridcolor=GRID, gridwidth=0.5,
                             zeroline=lo < 0,
                             zerolinecolor="#888", zerolinewidth=0.7,
                             tickfont=dict(size=9), nticks=3)

    # ---- 基因模型 ----
    if has_gene:
        gene_row = nrows
        lanes = _layout_lanes(genes, start, end)
        # 视野太宽时外显子会糊成一片，干脆只画基因体（交接文档 §6.3 的抽稀思路）
        want_exons = span <= 300_000
        exon_map = anno.exons_in(chrom, start, end) if want_exons else {}
        show_names = len(lanes) <= 16

        lane_ids = sorted({lane for _, lane, _ in lanes}) or [0]
        lane_span = 1.0 / (max(lane_ids) + 2.0)
        # 每个基因一个 marker trace 会多出几十条 trace，合并成两条（正链/负链各一）
        tips = {True: [], False: []}
        for g, lane, overflow in lanes:
            color = GENE_MINUS if g["strand"] == "-" else GENE_PLUS
            # 基线从 0.72 起，不是 0.82：基因名写在外显子顶边之上，写 0.82 时
            # y + 偏移 已经 >1，超出 y domain [0,1] 被裁掉（第一版截图里
            # AT1G01010 的名字就是这么没的）。降到 0.58 是为了在行内垂直居中。
            y = 0.58 - lane * lane_span
            xs_, xe_ = max(g["start"], start), min(g["end"], end)
            fig.add_shape(type="line", x0=xs_, x1=xe_, y0=y, y1=y,
                          line=dict(color=color, width=1.3), row=gene_row, col=1)
            for ex_s, ex_e in exon_map.get(g["gid"], [])[:40]:
                fig.add_shape(type="rect", x0=max(ex_s, start), x1=min(ex_e, end),
                              y0=y - lane_span * 0.30, y1=y + lane_span * 0.30,
                              fillcolor=color, line_width=0, row=gene_row, col=1)
            # 链方向：在 3' 端放一个小三角（+ 链在右、− 链在左），
            # 比 arrow 注释轻，缩到整条染色体时也不会消失
            neg = g["strand"] == "-"
            tips[neg].append((xs_ if neg else xe_, y))
            if show_names:
                fig.add_annotation(
                    x=xs_, y=y + lane_span * 0.30, text=g["name"], showarrow=False,
                    xanchor="left", yanchor="bottom", row=gene_row, col=1,
                    font=dict(size=9, color=color), opacity=0.9,
                )
        for neg, pts in tips.items():
            if not pts:
                continue
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts],
                mode="markers", showlegend=False, hoverinfo="skip",
                marker=dict(symbol="triangle-left" if neg else "triangle-right",
                            size=6, color=GENE_MINUS if neg else GENE_PLUS),
            ), row=gene_row, col=1)
        fig.update_yaxes(range=[0, 1], visible=False, row=gene_row, col=1)
        fig.update_xaxes(showline=True, linecolor="#bbb", row=gene_row, col=1)

    # ---- 全局坐标轴与交互 ----
    for r in range(1, nrows + 1):
        fig.update_xaxes(range=[start, end], row=r, col=1,
                         showgrid=r == nrows, gridcolor=GRID,
                         color="#666", tickfont=dict(size=9))
    fig.update_layout(
        height=height,
        margin=dict(l=68, r=18, t=margin_t, b=margin_b),
        hovermode="x unified",
        dragmode="zoom",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family='system-ui, "Noto Sans SC", sans-serif', size=10),
        hoverlabel=dict(bgcolor="rgba(255,255,255,0.94)", bordercolor="#ccc",
                        font=dict(size=10)),
        showlegend=overlay,
        # 图例不能贴 paper 顶部：右上角被 plotly 的工具栏（下载/缩放按钮）占了，
        # y=1.0 会和它叠在一起。绘图区顶边约在 paper 的 0.856 处，压到它下方就错开了。
        legend=dict(orientation="h", x=0.995, y=0.85, xanchor="right", yanchor="top",
                    font=dict(size=10), bgcolor="rgba(255,255,255,0.82)",
                    bordercolor="rgba(0,0,0,0.08)", borderwidth=1),
    )
    # 十字线画在每一行上，鼠标一过就是一条贯穿全图的位置线（IGV 的手感）
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor",
                     spikethickness=1, spikecolor="#999", spikedash="solid",
                     showline=True, linecolor="#bbb")
    return fig


# ===========================================================================
# 七、窗口统计（导出用）
# ===========================================================================

def window_stats(rows: Sequence[Dict], bin_bp: float) -> pd.DataFrame:
    """每个样本在当前视野内的汇总。**一律用 raw**，不碰平滑后的显示值。

    均值按 bin 等权：bin_w 就是 bin 的 bp 宽度，这里所有 bin 等宽，所以等权
    即长度加权，和交接文档 §5.3 的 win_mean 口径一致。
    """
    recs = []
    for r in rows:
        raw = np.asarray(r.get("raw", r["values"]), dtype=float)
        if raw.size == 0:
            continue
        recs.append({
            "样本": r["label"],
            "类型": r.get("role", ""),
            "均值 (BPM)": round(float(raw.mean()), 4),
            "最大值 (BPM)": round(float(raw.max()), 4),
            "总和 (BPM·bin)": round(float(raw.sum()), 2),
            "非零 bin 占比": round(float((raw > 0).mean()), 3),
        })
    return pd.DataFrame(recs)


def build_rows(samples: Sequence[Dict], *, chrom: str, start: int, end: int,
               bins: int, smooth_bp: int, mode: str,
               all_samples: Optional[Sequence[Dict]] = None,
               warnings: Optional[List[str]] = None) -> List[Dict]:
    """按显示模式把样本读成待画的 ``rows``。

    mode:
      * ``stacked`` / ``overlay`` — 每个样本一条；
      * ``delta``               — 每个 CHIP 减去它在 metadata 里配对的 INPUT，
                                   同 binSize、同 BPM 才能相减，这里只对配好对的
                                   样本出图，配不上的明确报出来而不是静默用 0 代替。

    ``all_samples`` 是「全部样本」，差值模式下用来找配对对照：用户只勾了 IP2 时
    不能因此报「没有配对的 INPUT」——配对关系写在 metadata.csv 里，与当前勾了谁
    无关，把 INPUT2 带进来算差值才是符合预期的。
    """
    warnings = warnings if warnings is not None else []
    bin_bp = max(1e-9, (end - start) / max(1, bins))
    cache: Dict[str, np.ndarray] = {}

    def sig(s: Dict) -> np.ndarray:
        if s["id"] not in cache:
            cache[s["id"]] = read_signal(s["path"], chrom, start, end, bins)
        return cache[s["id"]]

    pool = {s["id"]: s for s in (all_samples if all_samples is not None else samples)}
    out: List[Dict] = []

    if mode == "delta":
        for s in samples:
            if s["role"] != "CHIP":
                continue
            inp = pool.get(s.get("pair") or "")
            if inp is None:
                warnings.append(f"{s['id']} 没有配对的 INPUT，差值轨已跳过")
                continue
            raw = sig(s) - sig(inp)
            out.append({
                "label": f"{s['id']} − {inp['id']}", "color": s["color"],
                "role": "Δ(CHIP−INPUT)", "raw": raw, "signed": True,
                "values": smooth(raw, bin_bp, smooth_bp),
            })
    else:
        for s in samples:
            raw = sig(s)
            out.append({
                "label": s["id"], "color": s["color"], "role": s["role"],
                "raw": raw, "signed": False,
                "values": smooth(raw, bin_bp, smooth_bp),
            })
    return out
