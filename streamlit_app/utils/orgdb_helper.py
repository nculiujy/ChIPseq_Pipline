"""
orgdb_helper.py — 无官方 OrgDb 物种的"富集补给站"
=================================================
物种注释页的「OrgDb 构建 / ID 映射」tab 全部逻辑都落在这里，只做三件"轻"事：

  1. 体检：R 环境有没有 AnnotationForge / clusterProfiler，装了哪些 org.*.db
  2. 体检：peak_anno.csv 里的 geneId 到底长什么样（LOC / gene-LOC / 纯数字 / 带版本号）
  3. 预览：这批 geneId 套上某套 TERM2GENE 后能命中多少（决定 id_strip 该怎么写）

耗时活（建包、下 gene2go、装 R 包）不在这里做，一律交给 annotation_worker 的
后台任务；本模块只在 UI 线程里跑秒级操作，避免把 Streamlit 卡死。

为什么需要"预览命中率"：大豆这类物种有三套 ID 同时存在——
  NCBI GTF  exon gene_id   = LOC100808170
  NCBI GFF3 gene ID        = gene-LOC100808170
  NCBI GeneID / KEGG gmx   = 100808170
而 gene2go 与 KEGG 只认最后一种。strip 规则写错一个字符，富集结果就会静默为空，
所以必须先在 UI 上把命中率打出来。
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常用物种 taxid：只做输入框的默认值，最终以用户填的为准
# ---------------------------------------------------------------------------
COMMON_TAXID: Dict[str, str] = {
    "glycine max": "3847", "soybean": "3847", "gmax": "3847", "glyma": "3847",
    "zea mays": "4577", "maize": "4577", "corn": "4577",
    "oryza sativa": "39947", "rice": "39947",
    "triticum aestivum": "4565", "wheat": "4565",
    "hordeum vulgare": "4513", "barley": "4513",
    "sorghum bicolor": "4558", "sorghum": "4558",
    "arabidopsis thaliana": "3702", "arabidopsis": "3702",
    "solanum lycopersicum": "4081", "tomato": "4081",
    "solanum tuberosum": "4113", "potato": "4113",
    "capsicum annuum": "4072", "pepper": "4072",
    "gossypium hirsutum": "3635", "cotton": "3635",
    "brassica napus": "3708", "rapeseed": "3708",
    "brassica oleracea": "3712",
    "vitis vinifera": "29760", "grape": "29760",
    "malus domestica": "3750", "apple": "3750",
    "arachis hypogaea": "3818", "peanut": "3818",
    "cucumis sativus": "3659", "cucumber": "3659",
    "nicotiana tabacum": "4097", "tobacco": "4097",
    "populus trichocarpa": "3694", "poplar": "3694",
    "medicago truncatula": "3880",
    "fragaria vesca": "101020", "strawberry": "101020",
    "helianthus annuus": "4232", "sunflower": "4232",
    "sesamum indicum": "28529", "sesame": "28529",
    "ricinus communis": "3988", "castor": "3988",
    "musa acuminata": "4641", "banana": "4641",
    "homo sapiens": "9606", "human": "9606",
    "mus musculus": "10090", "mouse": "10090",
    "danio rerio": "7955", "zebrafish": "7955",
    "drosophila melanogaster": "7227", "drosophila": "7227",
    "caenorhabditis elegans": "6239",
    "saccharomyces cerevisiae": "4932", "yeast": "4932",
    "sus scrofa": "9823", "pig": "9823",
    "bos taurus": "9913", "cattle": "9913",
    "gallus gallus": "9031", "chicken": "9031",
}

# ---------------------------------------------------------------------------
# 常见物种模板：选一下就把 taxid / 属 / 种 / 包名都填好，不用去 NCBI 查
# ---------------------------------------------------------------------------
# official 非空 = Bioconductor 有官方 OrgDb 包，装包几分钟，不必 AnnotationForge 自建；
# 为空 = 没有官方包，只能自建（这正是「OrgDb 构建」tab 存在的理由）。
# 顺序：用户点名要的那几个在前，其余按「作物 → 模式动物」排。
SPECIES_TEMPLATES: List[Dict[str, str]] = [
    {"label": "小鼠 Mus musculus", "taxid": "10090", "genus": "Mus",
     "species": "musculus", "official": "org.Mm.eg.db"},
    {"label": "人 Homo sapiens", "taxid": "9606", "genus": "Homo",
     "species": "sapiens", "official": "org.Hs.eg.db"},
    {"label": "拟南芥 Arabidopsis thaliana", "taxid": "3702", "genus": "Arabidopsis",
     "species": "thaliana", "official": "org.At.tair.db"},
    {"label": "大豆 Glycine max", "taxid": "3847", "genus": "Glycine",
     "species": "max", "official": ""},
    {"label": "水稻 Oryza sativa", "taxid": "39947", "genus": "Oryza",
     "species": "sativa", "official": ""},
    {"label": "玉米 Zea mays", "taxid": "4577", "genus": "Zea",
     "species": "mays", "official": ""},
    {"label": "小麦 Triticum aestivum", "taxid": "4565", "genus": "Triticum",
     "species": "aestivum", "official": ""},
    {"label": "番茄 Solanum lycopersicum", "taxid": "4081", "genus": "Solanum",
     "species": "lycopersicum", "official": ""},
    {"label": "烟草 Nicotiana tabacum", "taxid": "4097", "genus": "Nicotiana",
     "species": "tabacum", "official": ""},
    {"label": "油菜 Brassica napus", "taxid": "3708", "genus": "Brassica",
     "species": "napus", "official": ""},
    {"label": "蒺藜苜蓿 Medicago truncatula", "taxid": "3880", "genus": "Medicago",
     "species": "truncatula", "official": ""},
    {"label": "大鼠 Rattus norvegicus", "taxid": "10116", "genus": "Rattus",
     "species": "norvegicus", "official": "org.Rn.eg.db"},
    {"label": "斑马鱼 Danio rerio", "taxid": "7955", "genus": "Danio",
     "species": "rerio", "official": "org.Dr.eg.db"},
    {"label": "果蝇 Drosophila melanogaster", "taxid": "7227", "genus": "Drosophila",
     "species": "melanogaster", "official": "org.Dm.eg.db"},
    {"label": "线虫 Caenorhabditis elegans", "taxid": "6239", "genus": "Caenorhabditis",
     "species": "elegans", "official": "org.Ce.eg.db"},
    {"label": "酵母 Saccharomyces cerevisiae", "taxid": "4932", "genus": "Saccharomyces",
     "species": "cerevisiae", "official": "org.Sc.sd.eg.db"},
    {"label": "猪 Sus scrofa", "taxid": "9823", "genus": "Sus",
     "species": "scrofa", "official": "org.Ss.eg.db"},
    {"label": "牛 Bos taurus", "taxid": "9913", "genus": "Bos",
     "species": "taurus", "official": "org.Bt.eg.db"},
    {"label": "鸡 Gallus gallus", "taxid": "9031", "genus": "Gallus",
     "species": "gallus", "official": "org.Gg.eg.db"},
]


def template_by_label(label: str) -> Optional[Dict[str, str]]:
    for t in SPECIES_TEMPLATES:
        if t["label"] == label:
            return t
    return None


def find_species_template(*texts: str) -> Optional[Dict[str, str]]:
    """从 species ID / display_name 认出模板条目（Glycine_max_v4 -> 大豆那条）。"""
    for text in texts:
        t = re.sub(r"[_\-.,/]+", " ", (text or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            continue
        for tpl in SPECIES_TEMPLATES:
            if f"{tpl['genus']} {tpl['species']}".lower() in t:
                return tpl
    return None


def template_orgdb(tpl: Dict[str, str]) -> str:
    """这条模板最终该用的包名：有官方包用官方的，没有就按 AnnotationForge 规则生成。"""
    return tpl.get("official") or suggest_orgdb_pkg(tpl["genus"], tpl["species"])


# 建包/装包默认用的 CRAN 镜像（BiocManager 会据此推 Bioconductor 版本）
CRAN_MIRRORS: List[str] = [
    "https://mirrors.tuna.tsinghua.edu.cn/CRAN",
    "https://mirrors.ustc.edu.cn/CRAN",
    "https://mirrors.aliyun.com/CRAN",
    "https://cloud.r-project.org",
]

# 自建 OrgDb / gene2go 产物默认放在 <ANNO_DIR>/<species>/orgdb
RDEPS_DEFAULT = "AnnotationForge,AnnotationDbi,GO.db"


# ---------------------------------------------------------------------------
# R 环境探测
# ---------------------------------------------------------------------------
def rscript_path() -> Optional[str]:
    """分析环境里的 Rscript（优先 env_paths 定向的 analysis prefix）。"""
    try:
        from utils import env_paths as _ep  # 页面里已把 streamlit_app 加进 sys.path
        p = _ep.resolved_tool_path("Rscript")
        if p:
            return p
    except Exception:
        pass
    return shutil.which("Rscript")


def r_lib_dir() -> str:
    """由 Rscript 实际位置反推 <prefix>/lib/R/library。"""
    rs = rscript_path()
    if rs:
        lib = Path(rs).resolve().parents[1] / "lib" / "R" / "library"
        if lib.is_dir():
            return str(lib)
    prefix = os.environ.get("CONDA_PREFIX") or ""
    if prefix:
        lib = Path(prefix) / "lib" / "R" / "library"
        if lib.is_dir():
            return str(lib)
    return ""


def run_r(code: str, timeout: int = 120, r_lib: str = "") -> Tuple[int, str]:
    """同步跑一小段 Rscript，返回 (exit_code, 输出)。只用于秒级探测。"""
    rs = rscript_path()
    if not rs:
        return 127, "未找到 Rscript（分析环境里没有 R？）"
    env = dict(os.environ)
    lib = r_lib or r_lib_dir()
    if lib:
        env["R_LIBS"] = lib + (os.pathsep + env["R_LIBS"] if env.get("R_LIBS") else "")
    try:
        # --vanilla 会忽略 .Renviron（代理/镜像常写在那里），只跳 .Rprofile 就够了
        proc = subprocess.run(
            [rs, "--no-init-file", "-e", code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, env=env,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, f"R 探测超时（>{timeout}s）"
    except OSError as e:
        return 126, f"Rscript 执行失败：{e}"


def list_orgdb_packages(lib: str = "") -> List[Dict[str, Any]]:
    """扫描 R 库里的 org.*.db 包。纯文件系统操作，不启 R。"""
    libs: List[Path] = []
    if lib:
        libs.append(Path(lib))
    else:
        cand = r_lib_dir()
        if cand:
            libs.append(Path(cand))
        for extra in (os.environ.get("R_LIBS_USER"), os.environ.get("R_LIBS")):
            if extra:
                libs.extend(Path(x) for x in extra.split(os.pathsep) if x)
        home_lib = Path.home() / "R"
        if home_lib.is_dir():
            libs.extend(sorted(home_lib.glob("*/library")))
    seen, out = set(), []
    for L in libs:
        if not L.is_dir():
            continue
        for d in sorted(L.glob("org.*.db")):
            key = (str(d.resolve()), d.name)
            if key in seen or not d.is_dir():
                continue
            seen.add(key)
            desc = d / "DESCRIPTION"
            ver, title = "", ""
            if desc.is_file():
                for ln in desc.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.startswith("Version:"):
                        ver = ln.split(":", 1)[1].strip()
                    elif ln.startswith("Title:"):
                        title = ln.split(":", 1)[1].strip()
            out.append({"pkg": d.name, "lib": str(L), "version": ver,
                        "title": title, "size_mb": _dir_size_mb(d)})
    return out


def r_packages_status(pkgs: Sequence[str], lib: str = "") -> Dict[str, Any]:
    """一次 Rscript 问清：这些包在不在、版本号多少。"""
    pk = ", ".join(f'"{p}"' for p in pkgs)
    lib_lit = (lib or r_lib_dir()).replace("\\", "/")
    code = (
        (f'.libPaths(c("{lib_lit}", .libPaths())); ' if lib_lit else "") +
        f'pk <- c({pk}); '
        'ok <- vapply(pk, function(p) requireNamespace(p, quietly=TRUE), logical(1)); '
        'ver <- vapply(pk, function(p) if (requireNamespace(p, quietly=TRUE)) '
        'as.character(packageVersion(p)) else "", character(1)); '
        'cat("STATUS", paste(pk, ok, ver, sep="=", collapse=";"), "\\n")'
    )
    rc, out = run_r(code, timeout=90, r_lib=lib)
    status = {p: {"ok": False, "version": ""} for p in pkgs}
    if rc == 0:
        for chunk in out.split("STATUS", 1)[-1].split(";"):
            parts = chunk.strip().split("=")
            if len(parts) >= 2 and parts[0] in status:
                status[parts[0]] = {"ok": parts[1].strip().upper() == "TRUE",
                                    "version": parts[2] if len(parts) > 2 else ""}
    return {"rscript": rscript_path() or "", "lib": lib or r_lib_dir(),
            "rc": rc, "raw": out, "packages": status}


# ---------------------------------------------------------------------------
# 产物目录 / 清单
# ---------------------------------------------------------------------------
def species_orgdb_dir(anno_root: str | Path, species_id: str) -> Path:
    return Path(anno_root) / str(species_id) / "orgdb"


def scan_term2gene(outdir: str | Path) -> Optional[Dict[str, Any]]:
    """读 *_manifest.json；有 manifest 才算产物完整。"""
    d = Path(outdir)
    if not d.is_dir():
        return None
    mans = sorted(d.glob("*_manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mans:
        return None
    try:
        data = json.loads(mans[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["_manifest"] = str(mans[0])
    t2g = {k: str(d / f"{data.get('prefix', '')}_go_{k}_term2gene.tsv")
           for k in (data.get("n_go_terms") or {})}
    data["term2gene_go"] = {k: v for k, v in t2g.items() if Path(v).is_file()}
    data["term2name_go"] = {k: str(d / f"{data.get('prefix', '')}_go_{k}_term2name.tsv")
                            for k in data["term2gene_go"]}
    uni = d / f"{data.get('prefix', '')}_all_genes.txt"
    data["universe"] = str(uni) if uni.is_file() else ""
    return data


def scan_orgdb_outdir(outdir: str | Path) -> List[Dict[str, Any]]:
    """列出 outdir 下已构建好的 org.*.db 源码目录。"""
    d = Path(outdir)
    out = []
    if d.is_dir():
        for p in sorted(d.glob("org.*.db")):
            if p.is_dir():
                out.append({"pkg": p.name, "dir": str(p), "size_mb": _dir_size_mb(p)})
    return out


# ---------------------------------------------------------------------------
# peak_anno geneId 体检
# ---------------------------------------------------------------------------
GENE_COLUMN_CANDIDATES = ("geneId", "gene_id", "GENEID", "geneid")
_GOOD_ID_SPLIT = re.compile(r"[/,;]")


def find_peak_anno(result_root: str | Path, species: str = "", experiment: str = "",
                   limit: int = 5) -> List[Path]:
    """找 4_analyse/annoChIPPeaks 下的 *_peak_anno.csv（优先当前项目）。"""
    root = Path(result_root)
    if not root.is_dir():
        return []
    if species and experiment:
        # 指定了项目就只认它：跨项目兜底会把别的物种（比如拟南芥）的 geneId 当成本项目的，
        # 后面算出来的 strip 规则和命中率就全是假的
        base = root / species / experiment / "4_analyse" / "annoChIPPeaks"
        if not base.is_dir():
            return []
        return sorted(base.rglob("*_peak_anno.csv"))[:limit]
    return sorted(root.glob("*/*/4_analyse/annoChIPPeaks/**/*_peak_anno.csv"))[:limit]


def read_gene_ids(paths: Iterable[str | Path], promoter_only: bool = False,
                  max_rows: int = 200000) -> Tuple[List[str], str, int]:
    """从若干 peak_anno.csv 读 geneId 列，返回 (去重后的 ID 列表, 列名, 行数)。

    只读这一列：这些文件动辄几十万行，pandas 全读会把页面拖死。
    一个 cell 里可能有多个基因（"a/b"），按 / , ; 拆开。
    """
    ids: List[str] = []
    used_col = ""
    n_rows = 0
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.reader(fh)
                try:
                    header = next(reader)
                except StopIteration:
                    continue
                if not used_col:
                    for cand in GENE_COLUMN_CANDIDATES:
                        if cand in header:
                            used_col = cand
                            break
                    if not used_col:
                        continue
                idx = header.index(used_col)
                ann_idx = header.index("annotation_category") if "annotation_category" in header else -1
                for row in reader:
                    n_rows += 1
                    if n_rows > max_rows:
                        break
                    if len(row) <= idx:
                        continue
                    cell = (row[idx] or "").strip().strip('"')
                    if not cell or cell in ("NA", "None", "nan"):
                        continue
                    if promoter_only and ann_idx >= 0:
                        ann = (row[ann_idx] or "").lower()
                        if "promoter" not in ann:
                            continue
                    ids.extend(x.strip() for x in _GOOD_ID_SPLIT.split(cell) if x.strip())
        except OSError:
            continue
    return sorted(set(ids)), used_col, n_rows


def parse_id_text(text: str) -> List[str]:
    """把用户粘进来的一坨 ID（换行/逗号/分号/斜杠分隔）拆成去重列表。

    给「模块 4 还没跑完，但想先验证 strip 规则」的场景用。
    """
    out: List[str] = []
    for line in (text or "").splitlines():
        for tok in _GOOD_ID_SPLIT.split(line):
            tok = tok.strip().strip('"').strip("'")
            if tok and tok not in ("NA", "None", "nan"):
                out.append(tok)
    return sorted(set(out))


def summarize_ids(ids: Sequence[str]) -> Dict[str, Any]:
    """给 geneId 拍个 X 光：前缀分布、版本号、纯数字比例、LOC 比例。"""
    total = len(ids)
    n_numeric = sum(1 for i in ids if i.isdigit())
    n_loc = sum(1 for i in ids if re.fullmatch(r"LOC[0-9]+", i))
    n_gene_loc = sum(1 for i in ids if re.fullmatch(r"gene-LOC[0-9]+", i))
    n_ver = sum(1 for i in ids if re.search(r"\.[0-9]+$", i))
    prefixes = Counter()
    for i in ids:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*[-_:]?)", i)
        prefixes[m.group(1) if m else "(数字开头)"] += 1
    return {
        "n_total": total,
        "n_numeric": n_numeric,
        "n_loc": n_loc,
        "n_gene_loc": n_gene_loc,
        "n_with_version": n_ver,
        "prefix_top": prefixes.most_common(8),
        "samples": list(ids[:5]),
    }


def suggest_id_strip(ids: Sequence[str]) -> str:
    """按多数派推荐 strip 正则（同时兼容 Python re 与 R 的 TRE）。

    只用 [[:alnum:]] 安全的写法：R 的 sub() 默认引擎不支持 \\d。
    """
    if not ids:
        return ""
    n = len(ids)
    n_gene_loc = sum(1 for i in ids if re.fullmatch(r"gene-LOC[0-9]+", i))
    n_loc = sum(1 for i in ids if re.fullmatch(r"LOC[0-9]+", i))
    n_num = sum(1 for i in ids if i.isdigit())
    # "^(gene-)?LOC" 对 LOC123 和 gene-LOC123 都成立（gene- 是可选的），
    # 所以两种形态混着出现时也用它，不必分两档
    if n_gene_loc + n_loc >= 0.4 * n:
        return "^(gene-)?LOC"
    if n_num >= 0.8 * n:
        return ""
    return ""


def apply_id_strip(ids: Iterable[str], pattern: str) -> List[str]:
    if not pattern:
        return list(ids)
    try:
        rx = re.compile(pattern)
    except re.error:
        return list(ids)
    return sorted({rx.sub("", str(i)) for i in ids})


# ---------------------------------------------------------------------------
# 映射预览
# ---------------------------------------------------------------------------
def read_term2gene_genes(path: str | Path, limit_terms: int = 100000) -> Dict[str, List[str]]:
    """TERM2GENE 两列 TSV → {term: [gene, ...]}。"""
    out: Dict[str, List[str]] = {}
    p = Path(path)
    if not p.is_file():
        return out
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh):
            if i >= limit_terms:
                break
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            out.setdefault(parts[0], []).append(parts[1])
    return out


def preview_mapping(ids: Sequence[str], term2gene_paths: Dict[str, str],
                    id_strip: str = "") -> Dict[str, Any]:
    """算这套 geneId 在某套 TERM2GENE 下的命中率（GO 富集能不能跑起来的硬指标）。"""
    stripped = apply_id_strip(ids, id_strip)
    input_set = set(stripped)
    per_ont: Dict[str, Any] = {}
    for ont, path in (term2gene_paths or {}).items():
        t2g = read_term2gene_genes(path)
        universe = {g for genes in t2g.values() for g in genes}
        hit = input_set & universe
        per_ont[ont.upper()] = {
            "path": path,
            "n_terms": len(t2g),
            "n_genes_in_db": len(universe),
            "n_hit": len(hit),
            "hit_rate": (len(hit) / len(input_set)) if input_set else 0.0,
            "samples_hit": sorted(hit)[:5],
            "samples_miss": sorted(input_set - hit)[:5],
        }
    return {"n_input": len(ids), "n_input_unique": len(input_set),
            "id_strip": id_strip, "per_ont": per_ont}


# ---------------------------------------------------------------------------
# config.yaml 读写（写回项目条目）
# ---------------------------------------------------------------------------
def project_list(config_path: str | Path) -> List[Dict[str, Any]]:
    import yaml
    try:
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except (OSError, Exception):
        return []
    return cfg.get("projects") or []


def update_project(config_path: str | Path, species: str, experiment: str,
                   fields: Dict[str, Any], create_missing: bool = False,
                   template: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """把 fields 合并进 config.projects 里 (species, experiment) 那一条。

    空字符串不覆盖已有值（与 annotation_manager.apply_to_config 同一约定），
    想清空某字段请显式传 None。
    """
    import yaml
    path = Path(config_path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as e:
        return False, f"读不到 {path}：{e}"
    projects = cfg.get("projects") or []
    hit = None
    for p in projects:
        if p.get("species") == species and p.get("experiment") == experiment:
            hit = p
            break
    if hit is None:
        if not create_missing:
            return False, f"config.yaml 里没有 {species}/{experiment} 这一条，请先在配置页保存项目"
        hit = dict(template or {})
        hit.update({"species": species, "experiment": experiment})
        projects.append(hit)
    for k, v in (fields or {}).items():
        if v == "" or v is None:
            if v is None:
                hit.pop(k, None)
            continue
        hit[k] = v
    cfg["projects"] = projects
    try:
        path.write_text(
            yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as e:
        return False, f"写入失败：{e}"
    return True, f"已写入 {species}/{experiment}"


def enable_gsea_module(config_path: str | Path, species: str, experiment: str,
                       enable: bool = True) -> Tuple[bool, str]:
    """顺手把该项目的 5_gsea 开关打开（默认关着的话，配好了也不会跑）。"""
    import yaml
    path = Path(config_path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as e:
        return False, f"读不到 {path}：{e}"
    for p in cfg.get("projects") or []:
        if p.get("species") == species and p.get("experiment") == experiment:
            mods = dict(p.get("modules") or {})
            mods["5_gsea"] = enable
            p["modules"] = mods
            path.write_text(
                yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            return True, f"5_gsea 已{'启用' if enable else '关闭'}"
    return False, "未找到该项目"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _dir_size_mb(p: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return round(total / 1024 / 1024, 1)


def guess_taxid(species_id: str, display_name: str = "") -> str:
    """按物种名猜 taxid（只提供默认值，不强制）。"""
    for text in (species_id, display_name):
        # 物种 ID 常写成 Glycine_max_v4 / Glycine-max：先归一成空格，否则 "glycine max" 匹配不上
        t = re.sub(r"[_\-.,/]+", " ", (text or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            continue
        for key, tax in COMMON_TAXID.items():
            if key in t:
                return tax
    return ""


def guess_genus_species(species_id: str, display_name: str = "") -> Tuple[str, str]:
    """从 'Glycine_max_v4' 这类 ID 里拆 (Glycine, max)，给建包表单做默认值。"""
    raw = (species_id or display_name or "").strip()
    raw = re.sub(r"[_\-.]?(v[0-9]+|genome|refseq|ncbi|ensembl).*$", "", raw, flags=re.I)
    parts = [x for x in re.split(r"[_\-.\s]+", raw) if x]
    if len(parts) >= 2:
        return parts[0].capitalize(), parts[1].lower()
    return (parts[0].capitalize(), "") if parts else ("", "")


def suggest_orgdb_pkg(genus: str, species: str) -> str:
    """AnnotationForge 的包名规则：org.<属首字母大写><种名前 3 字母小写>.eg.db。"""
    if not genus or not species:
        return ""
    return f"org.{genus[0].upper()}{species[:3].lower()}.eg.db"


def read_text_head(path: str | Path, max_bytes: int = 4096) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""
