"""
6_1_motif.py — MEME-ChIP Motif 富集分析
=========================================
对每个重复组的 consensus peaks（或单样本 peaks）：
1. 从基因组中提取 peaks 中心 ±flank_size bp 的 FASTA 序列
2. 调用 meme-chip 进行 motif 富集分析
3. 输出 HTML 报告及各子工具结果 (MEME, STREME, CentriMo, TOMTOM)

依赖: meme-chip, bedtools, samtools (faidx)
"""

import os
import sys
import argparse
import subprocess
import yaml
import pandas as pd
import shutil


# ============================================================
# Motif 数据库自动下载
# ============================================================
MOTIF_DB_URL = "https://meme-suite.org/meme/meme-software/Databases/motifs/motif_databases.12.25.tgz"
MEME_DB_BASE = os.path.join(os.path.expanduser("~"), ".meme_db")

# 物种 -> 默认 motif 数据库映射
SPECIES_DB_MAP = {
    "tair":        "motif_databases/ARABD/ArabidopsisDAPv1.meme",
    "arabidopsis": "motif_databases/ARABD/ArabidopsisDAPv1.meme",
    "at":          "motif_databases/ARABD/ArabidopsisDAPv1.meme",
    "glyma":       "motif_databases/CIS-BP_2.00/Glycine_max.meme",
    "glycine":     "motif_databases/CIS-BP_2.00/Glycine_max.meme",
    "gmax":        "motif_databases/CIS-BP_2.00/Glycine_max.meme",
    "soy":         "motif_databases/CIS-BP_2.00/Glycine_max.meme",
    "homo":        "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme",
    "hg":          "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme",
    "hs":          "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme",
    "mm":          "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme",
    "mus":         "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme",
    "dm":          "motif_databases/FLY/fly_factor_survey.meme",
    "drosophila":  "motif_databases/FLY/fly_factor_survey.meme",
    "ce":          "motif_databases/WORM/worm_factor_survey.meme",
    "sc":          "motif_databases/YEAST/ScerTF.meme",
}


def run_cmd(cmd, allow_fail=False):
    """执行命令并打印日志"""
    print(f"[RUNNING] {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        if not allow_fail:
            print(f"[ERROR] exit code {result.returncode}")
            print(f"[STDERR] {result.stderr}")
            sys.exit(1)
        else:
            print(f"[WARNING] command failed (allow_fail=True): {result.stderr[:500]}")
    if result.stdout:
        print(result.stdout[:2000])
    return result


def ensure_motif_db(user_db_path, species):
    """
    确保 motif 数据库可用。
    优先使用用户指定路径；否则自动下载并根据物种选择默认数据库。
    """
    # 用户指定了有效路径
    if user_db_path and os.path.exists(user_db_path):
        print(f"[INFO] 使用用户指定的 motif 数据库: {user_db_path}")
        return user_db_path

    motif_db_dir = os.path.join(MEME_DB_BASE, "motif_databases")

    # 检查是否已下载
    if not os.path.isdir(motif_db_dir):
        print("[INFO] Motif 数据库不存在，开始自动下载...")
        print(f"[INFO] 下载地址: {MOTIF_DB_URL}")
        os.makedirs(MEME_DB_BASE, exist_ok=True)

        tmp_file = "/tmp/motif_databases.tgz"
        result = run_cmd(f"wget -q --show-progress -O {tmp_file} '{MOTIF_DB_URL}'", allow_fail=True)
        if result.returncode == 0 and os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 1000:
            run_cmd(f"tar -xzf {tmp_file} -C {MEME_DB_BASE}", allow_fail=True)
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            print("[INFO] Motif 数据库下载并解压完成")
        else:
            print("[WARNING] Motif 数据库下载失败（网络不可用？），将以无数据库模式运行（仅从头发现 motif）")
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            return None

    # 根据物种选择默认数据库
    species_lower = species.lower()
    for key, db_rel_path in SPECIES_DB_MAP.items():
        if species_lower.startswith(key):
            full_path = os.path.join(MEME_DB_BASE, db_rel_path)
            if os.path.exists(full_path):
                print(f"[INFO] 使用物种默认 motif 数据库: {full_path}")
                return full_path
            else:
                print(f"[WARNING] 默认数据库文件不存在: {full_path}")
                break

    # fallback: JASPAR vertebrates
    fallback = os.path.join(MEME_DB_BASE, "motif_databases/JASPAR/JASPAR2022_CORE_vertebrates_non-redundant_v2.meme")
    if os.path.exists(fallback):
        print(f"[INFO] 使用 fallback 数据库: {fallback}")
        return fallback

    # 如果仍然找不到，列出可用的 .meme 文件
    print("[WARNING] 未找到合适的 motif 数据库文件")
    available = []
    for root, dirs, files in os.walk(os.path.join(MEME_DB_BASE, "motif_databases")):
        for f in files:
            if f.endswith(".meme"):
                available.append(os.path.join(root, f))
    if available:
        print(f"[INFO] 可用数据库 ({len(available)} 个):")
        for a in available[:10]:
            print(f"  - {a}")
        return available[0]

    print("[ERROR] 无可用 motif 数据库，meme-chip 将以无数据库模式运行（仅从头发现）")
    return None


def ensure_genome_fasta(config, species, experiment):
    """
    获取基因组 FASTA 文件路径。
    优先使用 motif.genome_fasta 配置；否则从 index_dir 推断。
    """
    motif_cfg = config.get("motif", {})
    genome_fasta = motif_cfg.get("genome_fasta", "")

    if genome_fasta and os.path.exists(genome_fasta):
        return genome_fasta

    # 从 projects 中查找 index_dir，推断 FASTA 路径
    for proj in config.get("projects", []):
        if proj["species"] == species and proj["experiment"] == experiment:
            index_dir = proj.get("index_dir", "")
            # index_dir 格式如: workflow/anno/TAIR10/bowtie2_index_TAIR/TAIR10_Chr
            # 对应 FASTA: workflow/anno/TAIR10/bowtie2_index_TAIR/TAIR10_Chr.fa
            if index_dir:
                # 尝试 index_dir + .fa
                fa_path = index_dir + ".fa"
                if os.path.exists(fa_path):
                    return fa_path
                # 尝试在同目录下找 .fa 文件
                parent_dir = os.path.dirname(index_dir)
                if os.path.isdir(parent_dir):
                    for f in os.listdir(parent_dir):
                        if f.endswith(".fa") or f.endswith(".fasta"):
                            return os.path.join(parent_dir, f)
            break

    return None


def prepare_peak_fasta(bed_file, genome_fasta, output_fasta, flank_size, max_peaks=1000):
    """
    从基因组中提取 peaks 中心 ±flank_size 的 FASTA 序列。
    对 peaks 数量做上限截断（按文件顺序取前 max_peaks 个）。
    """
    # 确保基因组有 .fai 索引
    fai_file = genome_fasta + ".fai"
    if not os.path.exists(fai_file):
        print("[INFO] 创建基因组 FASTA 索引...")
        run_cmd(f"samtools faidx {genome_fasta}")

    # 读取 BED，计算中心 ±flank
    flanked_bed = output_fasta + ".tmp.bed"
    peak_count = 0
    with open(bed_file, 'r') as fin, open(flanked_bed, 'w') as fout:
        for line in fin:
            if line.startswith('#') or not line.strip():
                continue
            fields = line.strip().split('\t')
            if len(fields) < 3:
                continue
            chrom = fields[0]
            start = int(fields[1])
            end = int(fields[2])
            mid = (start + end) // 2
            new_start = max(0, mid - flank_size)
            new_end = mid + flank_size
            fout.write(f"{chrom}\t{new_start}\t{new_end}\n")
            peak_count += 1
            if peak_count >= max_peaks:
                break

    print(f"[INFO] 准备 {peak_count} 个 peaks 的 FASTA 序列 (±{flank_size}bp)")

    # bedtools getfasta
    run_cmd(f"bedtools getfasta -fi {genome_fasta} -bed {flanked_bed} -fo {output_fasta}")

    # 清理临时文件
    if os.path.exists(flanked_bed):
        os.remove(flanked_bed)

    # 验证输出
    if not os.path.exists(output_fasta) or os.path.getsize(output_fasta) == 0:
        print(f"[ERROR] FASTA 文件生成失败: {output_fasta}")
        return False

    return True


def run_meme_chip(fasta_file, output_dir, motif_db, motif_cfg, threads=8):
    """
    运行 meme-chip 进行 motif 富集分析。
    完成后将 motif logos 从 PNG 转为 PDF（发表质量）。
    """
    meme_nmotifs = motif_cfg.get("meme_nmotifs", 5)
    meme_minw = motif_cfg.get("meme_minw", 6)
    meme_maxw = motif_cfg.get("meme_maxw", 15)
    meme_mod = motif_cfg.get("meme_mod", "zoops")
    streme_pvt = motif_cfg.get("streme_pvt", 0.05)

    # 构建 meme-chip 命令
    cmd_parts = [
        "meme-chip",
        "-dna",
        f"-meme-mod {meme_mod}",
        f"-meme-nmotifs {meme_nmotifs}",
        f"-minw {meme_minw}",
        f"-maxw {meme_maxw}",
        f"-streme-pvt {streme_pvt}",
        f"-meme-p {threads}",
    ]

    # 数据库参数
    if motif_db and os.path.exists(motif_db):
        cmd_parts.append(f"-db {motif_db}")

    # 输出目录（meme-chip 要求目录不存在或用 -oc 覆盖）
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    cmd_parts.append(f"-o {output_dir}")

    # 输入 FASTA
    cmd_parts.append(fasta_file)

    cmd = " ".join(cmd_parts)
    result = run_cmd(cmd, allow_fail=True)

    # 检查是否生成了报告
    report_html = os.path.join(output_dir, "meme-chip.html")
    if os.path.exists(report_html):
        print(f"[SUCCESS] meme-chip 完成，报告: {report_html}")
        # 后处理：将 motif logos 从 PNG 转为 PDF（发表质量矢量图）
        convert_logos_to_pdf(output_dir)
        return True
    else:
        print(f"[WARNING] meme-chip 可能未完全成功（未找到 meme-chip.html）")
        return False


def convert_logos_to_pdf(meme_chip_outdir):
    """
    将 meme-chip 输出的 PNG logos 转换为 PDF 格式。
    使用 ceqlogo（MEME Suite 自带）从 MEME XML 重新生成 EPS，然后转 PDF。
    如果 ceqlogo 不可用，则尝试用 Python 的 PIL 或 cairosvg 转换。
    """
    import glob as glob_module

    meme_xml = os.path.join(meme_chip_outdir, "meme_out", "meme.xml")
    streme_xml = os.path.join(meme_chip_outdir, "streme_out", "streme.xml")
    pdf_dir = os.path.join(meme_chip_outdir, "logos_pdf")
    os.makedirs(pdf_dir, exist_ok=True)

    converted = 0

    # 方法 1: 使用 ceqlogo 从 MEME XML 生成 EPS → PDF
    if os.path.exists(meme_xml):
        # 解析 MEME XML 获取 motif 数量
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(meme_xml)
            root = tree.getroot()
            # MEME XML namespace
            ns = {'meme': 'http://meme-suite.org/schemas/meme'}
            motifs = root.findall('.//motif') or root.findall('.//{http://meme-suite.org/schemas/meme}motif')
            if not motifs:
                # fallback: 找所有带 id 属性的 motif 标签
                motifs = [elem for elem in root.iter() if 'motif' in elem.tag.lower() and 'id' in elem.attrib]

            for i, motif in enumerate(motifs, 1):
                motif_id = motif.get('id', f'motif_{i}')
                eps_file = os.path.join(pdf_dir, f"{motif_id}.eps")
                pdf_file = os.path.join(pdf_dir, f"{motif_id}.pdf")

                # 尝试用 ceqlogo 生成 EPS
                ceqlogo_cmd = f"ceqlogo -i1 {meme_xml} -m {i} -o {eps_file} -f EPS"
                result = run_cmd(ceqlogo_cmd, allow_fail=True)
                if result.returncode == 0 and os.path.exists(eps_file):
                    # EPS → PDF (使用 epstopdf 或 ps2pdf)
                    ps2pdf_cmd = f"epstopdf {eps_file} --outfile={pdf_file} 2>/dev/null || ps2pdf -dEPSCrop {eps_file} {pdf_file}"
                    run_cmd(ps2pdf_cmd, allow_fail=True)
                    if os.path.exists(pdf_file):
                        converted += 1
                        if os.path.exists(eps_file):
                            os.remove(eps_file)  # 清理中间 EPS 文件
                    elif os.path.exists(eps_file):
                        # 保留 EPS 作为替代
                        converted += 1

        except Exception as e:
            print(f"[WARNING] ceqlogo 方法失败: {e}")

    # 方法 2: 如果 ceqlogo 不成功，使用 Python 将 PNG 转为 PDF
    if converted == 0:
        print("[INFO] ceqlogo 不可用，尝试使用 Python 将 PNG 转为 PDF...")
        try:
            from PIL import Image
            png_files = glob_module.glob(os.path.join(meme_chip_outdir, "meme_out", "logo*.png"))
            png_files += glob_module.glob(os.path.join(meme_chip_outdir, "streme_out", "logo*.png"))
            png_files.sort()

            for png_path in png_files:
                fname = os.path.splitext(os.path.basename(png_path))[0]
                pdf_file = os.path.join(pdf_dir, f"{fname}.pdf")
                try:
                    img = Image.open(png_path)
                    # 转为 RGB（PDF 不支持 RGBA）
                    if img.mode == 'RGBA':
                        bg = Image.new('RGB', img.size, (255, 255, 255))
                        bg.paste(img, mask=img.split()[3])
                        img = bg
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.save(pdf_file, 'PDF', resolution=300)
                    converted += 1
                except Exception as e:
                    print(f"  [WARNING] 转换 {png_path} 失败: {e}")

        except ImportError:
            print("[WARNING] PIL (Pillow) 不可用，无法转换 PNG 为 PDF")

    if converted > 0:
        print(f"[INFO] 已生成 {converted} 个 PDF logos → {pdf_dir}")
    else:
        print("[WARNING] 未能生成任何 PDF logos（PNG 文件仍可用）")


def main():
    parser = argparse.ArgumentParser(description="MEME-ChIP Motif Enrichment Analysis")
    parser.add_argument("--metadata", required=True, help="Path to metadata.csv")
    parser.add_argument("--peakdir", required=True, help="Peak calling results directory")
    parser.add_argument("--consensus_dir", required=True, help="Consensus peaks directory")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--species", required=True, help="Species name")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    parser.add_argument("--threads", type=int, default=8, help="Number of threads")

    args = parser.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    # 设置 MEME Suite 环境变量（在任何 meme 调用之前）
    # 优先使用 conda 环境中的 meme（完整安装），再 fallback 到系统路径
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    meme_prefix = ""

    # 1. 优先检测 conda 环境（通常包含完整的 libexec 和 share）
    if conda_prefix and os.path.exists(os.path.join(conda_prefix, "bin/meme-chip")):
        meme_prefix = conda_prefix
    # 2. 检查用户设置的环境变量
    elif os.environ.get("MEME_BIN_DIR") and os.environ["MEME_BIN_DIR"] != "/usr/local/meme/bin":
        meme_bin_env = os.environ["MEME_BIN_DIR"]
        meme_prefix = os.path.dirname(meme_bin_env) if meme_bin_env.endswith("/bin") else os.path.dirname(meme_bin_env)
    # 3. Fallback 到 /usr/local/meme（源码编译安装）
    elif os.path.isdir("/usr/local/meme/bin"):
        meme_prefix = "/usr/local/meme"
    # 4. 从 PATH 中搜索
    else:
        meme_path = shutil.which("meme-chip") or shutil.which("meme")
        if meme_path:
            meme_prefix = os.path.dirname(os.path.dirname(os.path.realpath(meme_path)))

    if not meme_prefix:
        print("[ERROR] 无法找到 MEME Suite 安装路径")
        sys.exit(1)

    print(f"[INFO] MEME prefix: {meme_prefix}")

    meme_bin = os.path.join(meme_prefix, "bin")
    # 自动检测 meme 版本来确定 libexec 路径
    meme_libexec = ""
    for ver in ["5.5.9", "5.5.7", "5.5.8", "5.5.6", "5.5.5"]:
        candidate = os.path.join(meme_prefix, f"libexec/meme-{ver}")
        if os.path.isdir(candidate):
            meme_libexec = candidate
            break
    if not meme_libexec:
        meme_libexec = os.path.join(meme_prefix, "libexec/meme-5.5.7")

    # 自动检测 MEME perl lib 路径
    meme_perl_lib = ""
    for ver in ["5.5.9", "5.5.7", "5.5.8", "5.5.6", "5.5.5"]:
        candidate = os.path.join(meme_prefix, f"lib/meme-{ver}/perl")
        if os.path.isdir(candidate):
            meme_perl_lib = candidate
            break

    os.environ["MEME_BIN_DIR"] = meme_bin
    os.environ["MEME_SECONDARY_BIN_DIR"] = meme_libexec
    os.environ["MEME_SCRIPT_DIR"] = meme_libexec
    # 自动检测 share 目录
    meme_data_dir = ""
    for ver in ["5.5.9", "5.5.7", "5.5.8", "5.5.6", "5.5.5"]:
        candidate = os.path.join(meme_prefix, f"share/meme-{ver}")
        if os.path.isdir(candidate):
            meme_data_dir = candidate
            break
    if meme_data_dir:
        os.environ["MEME_DATA_DIR"] = meme_data_dir

    # 确保 PATH 包含 meme 工具和 conda 环境
    current_path = os.environ.get("PATH", "")
    extra_paths = [meme_bin, meme_libexec]
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        extra_paths.insert(0, os.path.join(conda_prefix, "bin"))
    for p in extra_paths:
        if p not in current_path:
            current_path = p + ":" + current_path
    os.environ["PATH"] = current_path

    # 设置 PERL5LIB 确保系统 perl 能找到所有必需模块
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    perl5lib_paths = []
    if meme_perl_lib:
        perl5lib_paths.append(meme_perl_lib)  # MEME Suite 自带模块 (Globals.pm)
    if conda_prefix:
        # 动态查找 conda 环境中所有可能的 perl 库路径（兼容不同 perl 版本）
        import glob as _glob
        # 标准 perl5 路径
        perl5lib_paths.extend([
            os.path.join(conda_prefix, "lib/perl5/5.32/site_perl"),
            os.path.join(conda_prefix, "lib/perl5/site_perl"),
            os.path.join(conda_prefix, "lib/perl5/5.32/vendor_perl"),
            os.path.join(conda_prefix, "lib/perl5/vendor_perl"),
            os.path.join(conda_prefix, "lib/perl5/5.32/core_perl"),
            os.path.join(conda_prefix, "lib/perl5/core_perl"),
        ])
        # 搜索 site_perl 中所有版本+架构子目录（解决 perl-xml-parser 版本不匹配问题）
        for pattern in [
            os.path.join(conda_prefix, "lib/site_perl/*/x86_64-*"),
            os.path.join(conda_prefix, "lib/site_perl/*"),
            os.path.join(conda_prefix, "lib/perl5/*/x86_64-*"),
        ]:
            for p in _glob.glob(pattern):
                if os.path.isdir(p) and p not in perl5lib_paths:
                    perl5lib_paths.append(p)
    existing_perl5lib = os.environ.get("PERL5LIB", "")
    os.environ["PERL5LIB"] = ":".join(perl5lib_paths) + (":" + existing_perl5lib if existing_perl5lib else "")
    print(f"[INFO] PERL5LIB set to: {os.environ['PERL5LIB'][:200]}...")
    print(f"[INFO] MEME_BIN_DIR={os.environ['MEME_BIN_DIR']}")
    print(f"[INFO] MEME_SECONDARY_BIN_DIR={os.environ['MEME_SECONDARY_BIN_DIR']}")

    # 读取配置
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    motif_cfg = config.get("motif", {})
    flank_size = motif_cfg.get("flank_size", 100)
    max_peaks = motif_cfg.get("max_peaks", 1000)
    use_consensus = motif_cfg.get("use_consensus_peaks", True)

    # 确保基因组 FASTA 可用
    genome_fasta = ensure_genome_fasta(config, args.species, args.experiment)
    if not genome_fasta:
        print("[ERROR] 无法找到基因组 FASTA 文件，请在 config.yaml 的 motif.genome_fasta 中指定")
        sys.exit(1)
    print(f"[INFO] 基因组 FASTA: {genome_fasta}")

    # 确保 motif 数据库可用
    user_db = motif_cfg.get("meme_db", "")
    motif_db = ensure_motif_db(user_db, args.species)

    # 读取 metadata，确定分析目标
    df = pd.read_csv(args.metadata)
    # metadata.csv 可带可选的 project 列（值形如 TAIR10/314Com5）。
    # project 为空的行走“通用”语义，兼容老的两三列格式；写了 project 的行只属于
    # 那一个项目，避免多项目共用一张表时把别人的样本也算进本项目。
    if "project" in df.columns and args.species:
        proj = df["project"].fillna("").astype(str).str.strip()
        this_project = f"{args.species}/{args.experiment}"
        df = df[(proj == "") | (proj == this_project)]
        print(f"[INFO] 按项目过滤后保留 {len(df)} 行 (project = {this_project})")
    if "group" not in df.columns:
        df["group"] = "all"

    # 过滤空 group
    df_with_group = df[df["group"].notna() & (df["group"] != "")]

    if use_consensus and len(df_with_group) > 0:
        # 使用 consensus peaks
        print("\n[MODE] 基于 Consensus Peaks 进行 Motif 分析")
        groups = df_with_group["group"].unique()

        for group_name in groups:
            print(f"\n{'='*60}")
            print(f"[GROUP] {group_name}")
            print(f"{'='*60}")

            consensus_bed = os.path.join(args.consensus_dir, f"consensus_{group_name}.bed")
            if not os.path.exists(consensus_bed):
                print(f"  [SKIP] Consensus BED 不存在: {consensus_bed}")
                continue
            if os.path.getsize(consensus_bed) == 0:
                print(f"  [SKIP] Consensus BED 为空: {consensus_bed}")
                continue

            # 准备输出目录
            group_outdir = os.path.join(args.outdir, f"consensus_{group_name}")
            os.makedirs(group_outdir, exist_ok=True)

            # 提取 FASTA
            fasta_file = os.path.join(group_outdir, f"consensus_{group_name}.fasta")
            success = prepare_peak_fasta(consensus_bed, genome_fasta, fasta_file, flank_size, max_peaks)
            if not success:
                print(f"  [ERROR] FASTA 提取失败，跳过")
                continue

            # 运行 meme-chip
            meme_outdir = os.path.join(group_outdir, "meme_chip_out")
            run_meme_chip(fasta_file, meme_outdir, motif_db, motif_cfg, args.threads)

    else:
        # 对每个样本对独立分析
        print("\n[MODE] 基于单样本 Peaks 进行 Motif 分析")
        for _, row in df.iterrows():
            treat = str(row["IP sample"]).strip()
            control = str(row["Input"]).strip()
            if not treat or not control:
                continue
            pair_name = f"{treat}_vs_{control}"

            print(f"\n{'='*60}")
            print(f"[PAIR] {pair_name}")
            print(f"{'='*60}")

            # 查找 peaks 文件
            peak_type = config.get("macs2_peak_type", "narrow")
            peak_ext = "broadPeak" if peak_type == "broad" else "narrowPeak"
            peak_file = os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks.{peak_ext}")

            if not os.path.exists(peak_file):
                # 尝试使用 tab.bed 格式
                peak_file = os.path.join(args.peakdir, pair_name, f"{pair_name}_peaks_tab.bed")

            if not os.path.exists(peak_file) or os.path.getsize(peak_file) == 0:
                print(f"  [SKIP] Peak 文件不存在或为空: {peak_file}")
                continue

            # 准备输出目录
            pair_outdir = os.path.join(args.outdir, pair_name)
            os.makedirs(pair_outdir, exist_ok=True)

            # 提取 FASTA
            fasta_file = os.path.join(pair_outdir, f"{pair_name}.fasta")
            success = prepare_peak_fasta(peak_file, genome_fasta, fasta_file, flank_size, max_peaks)
            if not success:
                print(f"  [ERROR] FASTA 提取失败，跳过")
                continue

            # 运行 meme-chip
            meme_outdir = os.path.join(pair_outdir, "meme_chip_out")
            run_meme_chip(fasta_file, meme_outdir, motif_db, motif_cfg, args.threads)

    # 汇总
    print(f"\n{'='*60}")
    print("[DONE] Motif 分析完成！")
    print(f"输出目录: {args.outdir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
