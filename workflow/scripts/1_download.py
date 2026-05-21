
import os
import sys
import glob
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

def run_command(cmd):
    """执行外部命令"""
    print(f"[Command] {cmd}")
    status = os.system(cmd)
    if status != 0:
        raise RuntimeError(f"命令执行失败: {cmd}")

def download_sra(srr_id, download_dir):
    """
    使用 prefetch 下载 SRA 文件，并在下载前检查文件是否已存在。
    prefetch 会自动创建以 SRR_ID 命名的子目录，并将 .sra 文件下载到其中。
    如果 .sra 文件已存在，则跳过下载。
    """
    sra_dir = os.path.join(download_dir, srr_id)
    sra_file = os.path.join(sra_dir, f"{srr_id}.sra")
    
    # 检查 .sra 文件是否已经存在
    if os.path.exists(sra_file):
        print(f"[Skip] SRA 文件已存在，跳过下载: {sra_file}")
    else:
        print(f"[Process] 开始下载: {srr_id}")
        cmd_prefetch = f"prefetch {srr_id} --output-directory {download_dir} --max-size 100G"
        run_command(cmd_prefetch)
    
    return sra_file

def fastq_dump_sra(srr_id, download_dir, rawdata_dir):
    """
    使用 fasterq-dump 将下载的 .sra 文件转换为 FASTQ，并使用 pigz 压缩。
    同样在转换前检查结果文件是否已经存在。
    注意：这里假设转换出的是双端数据 (产生 _1.fastq.gz 和 _2.fastq.gz)。
    如果是单端数据，结果文件将没有 _1/_2 后缀，需根据实际情况调整。
    """
    sra_dir = os.path.join(download_dir, srr_id)
    sra_file = os.path.join(sra_dir, f"{srr_id}.sra")
    
    if not os.path.exists(sra_file):
        print(f"[Error] 未找到对应的 SRA 文件: {sra_file}，无法进行 fastq-dump。")
        return False

    # 定义预期的输出文件路径 (以双端测序为例)
    fq1 = os.path.join(rawdata_dir, f"{srr_id}_1.fastq.gz")
    fq2 = os.path.join(rawdata_dir, f"{srr_id}_2.fastq.gz")
    
    if os.path.exists(fq1) and os.path.exists(fq2):
        print(f"[Skip] FASTQ 文件已存在，跳过转换: {fq1}, {fq2}")
    else:
        print(f"[Process] 开始转换: {srr_id}")
        # 这里使用 --split-3 对双端数据进行拆分
        cmd_dump = f"fasterq-dump --split-3 {sra_file} -O {rawdata_dir} -e 8"
        run_command(cmd_dump)
        
        # 使用 pigz 进行多线程压缩
        print(f"[Process] 开始压缩: {srr_id}")
        cmd_pigz = f"pigz -p 8 {os.path.join(rawdata_dir, srr_id)}*.fastq"
        run_command(cmd_pigz)
    
    return True

def process_sample(srr_id, download_dir, rawdata_dir):
    """
    处理单个样本的完整流程：
    1. 下载 SRA 文件
    2. 将 SRA 文件转换为 FASTQ 并压缩
    """
    try:
        download_sra(srr_id, download_dir)
        fastq_dump_sra(srr_id, download_dir, rawdata_dir)
        return True
    except Exception as e:
        print(f"[Error] 样本 {srr_id} 处理失败: {e}")
        return False

def main():
    print("="*40)
    print("[Step] 开始执行下载流程")
    
    parser = argparse.ArgumentParser(description="Download and dump SRA files")
    parser.add_argument("--species", required=True, help="Species name")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    parser.add_argument("--outdir", required=True, help="Output directory")
    args = parser.parse_args()

    # 1. 尝试寻找这个 project 对应的 fastqfile 目录
    # 这里我们使用硬编码的相对路径（根据项目结构），也可以改为读取 config.yaml
    rawdata_dir = f"workflow/resources/{args.species}/{args.experiment}/fastqfile"
    
    if not os.path.exists(rawdata_dir):
        # 如果是TAIR等，路径可能稍有不同，这里做个 fallback
        possible_dirs = glob.glob(f"workflow/resources/*/{args.experiment}/fastqfile")
        if possible_dirs:
            rawdata_dir = possible_dirs[0]
        else:
            print(f"[Warning] 找不到对应的 fastqfile 目录用于推断样本 ({rawdata_dir})，此步骤将仅作为占位符成功退出。")
            sys.exit(0)

    print(f"[Info] 自动推断的数据目录为: {rawdata_dir}")
    print(f"[Info] 输出结果目录为: {args.outdir}")

    # 2. 检查 rawdata_dir 中是否已经存在压缩好的 FASTQ 文件
    # 我们根据现有的 .fastq.gz 文件推断样本 ID (假设命名格式为 SRRXXXXX_1.fastq.gz)
    fq_files = glob.glob(os.path.join(rawdata_dir, "*.fastq.gz"))
    
    if not fq_files:
        print(f"[Error] 目录 {rawdata_dir} 中未找到任何 .fastq.gz 文件。无法推断样本列表。")
        sys.exit(0)

    # 提取唯一的 SRR ID 列表
    srr_ids = set()
    for fq in fq_files:
        basename = os.path.basename(fq)
        srr_id = basename.split("_")[0]
        srr_ids.add(srr_id)
        
    srr_ids = list(srr_ids)
    print(f"[Info] 共找到 {len(srr_ids)} 个样本需要处理: {', '.join(srr_ids)}")
    
    # 由于文件可能已经存在于 rawdata_dir，我们的函数内部会有 skip 逻辑
    download_dir = args.outdir
    os.makedirs(download_dir, exist_ok=True)
    
    # 3. 使用进程池并发处理所有样本
    # max_workers 可以根据服务器配置进行调整
    max_workers = 4
    print(f"[Info] 启动多进程并发处理，最大进程数: {max_workers}")
    
    success_count = 0
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        futures = {executor.submit(process_sample, srr_id, download_dir, rawdata_dir): srr_id for srr_id in srr_ids}
        
        # 处理完成的任务
        for future in as_completed(futures):
            srr_id = futures[future]
            try:
                result = future.result()
                if result:
                    success_count += 1
            except Exception as e:
                print(f"[Error] 并发执行时样本 {srr_id} 出现异常: {e}")

    # 4. 总结
    print("="*40)
    print(f"[Summary] 总样本数: {len(srr_ids)}, 成功处理数: {success_count}")
    if success_count == len(srr_ids):
        print("[Step] 所有样本下载和转换均已完成！")
        sys.exit(0)
    else:
        print("[Step] 部分样本处理失败，请检查日志。")
        sys.exit(1)

if __name__ == "__main__":
    main()
