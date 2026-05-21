#!/usr/bin/perl -w  
use strict;
use warnings;
use Getopt::Long;
use File::Path qw(make_path);
use POSIX qw(waitpid);
use Time::Piece;
use File::Basename;

my ($inputdir, $outputdir, $indexdir, $picarddir, $threads, $help, $single_end);
GetOptions(
    "inputdir|i=s"   => \$inputdir,
    "outputdir|o=s"  => \$outputdir,
    "indexdir|x=s"   => \$indexdir,
    "picarddir|p=s"  => \$picarddir,
    "threads|t=i"    => \$threads,
    "single_end|s!"  => \$single_end,
    "help!"          => \$help,
);

die "用法: perl 3_1_ChIPseq.pl --inputdir <dir> --outputdir <dir> --indexdir <dir> --picarddir <dir> --threads <num> [--single_end]\n"
    if $help || !$inputdir || !$outputdir || !$indexdir || !$picarddir || !$threads;

make_path($outputdir) unless -d $outputdir;

sub log_message {
    my ($message) = @_;
    my $time = localtime->strftime('%Y-%m-%d %H:%M:%S');
    print "[$time] $message\n";
}

log_message("ChIP-seq 步骤1: 比对与去重 开始");

# 查找样本文件 (兼容不同 QC 输出或原始文件格式)
my @samples = glob('"' . $inputdir . '/*/*_1*.fq.gz"');
if (!@samples) {
    @samples = glob('"' . $inputdir . '/*_1*.fq.gz"');
}
# 兼容未压缩的 fastq/fq 以及 Trim Galore 产出的 _val_1.fq 等
if (!@samples) {
    @samples = glob('"' . $inputdir . '/*/*_1*.fq"');
    if (!@samples) {
        @samples = glob('"' . $inputdir . '/*_1*.fq"');
    }
}
if (!@samples) {
    @samples = glob('"' . $inputdir . '/*/*_1*.fastq"');
    if (!@samples) {
        @samples = glob('"' . $inputdir . '/*_1*.fastq"');
    }
}
chomp @samples;

if (!@samples) {
    log_message("错误: 未找到任何 Fastq 文件！");
    die "未找到 Fastq 文件，请检查输入路径: $inputdir\n";
}

my %sample_info;
foreach my $fq1 (@samples) {
    # 提取基本名，支持多种命名格式，包括 Trim Galore 产出的 val_1
    my $base_name;
    my $fq2 = $fq1;

    # 这里使用 basename() 仅获取文件名部分，不要保留目录结构
    my $filename = basename($fq1);
    
    if ($filename =~ /^(.+)_1_val_1\.fq(?:\.gz)?$/) {
        $base_name = $1;
        $fq2 =~ s/_1_val_1\.fq/_2_val_2.fq/;
    } elsif ($filename =~ /^(.+)_1_trimmed\.fq(?:\.gz)?$/) {
        $base_name = $1;
        $fq2 =~ s/_1_trimmed\.fq/_2_trimmed.fq/;
    } elsif ($filename =~ /^(.+)_1\.clean\.fq(?:\.gz)?$/) {
        $base_name = $1;
        $fq2 =~ s/_1\.clean\.fq/_2.clean.fq/;
    } elsif ($filename =~ /^(.+)_1\.fastq(?:\.gz)?$/) {
        $base_name = $1;
        $fq2 =~ s/_1\.fastq/_2.fastq/;
    } elsif ($filename =~ /^(.+)_1\.fq(?:\.gz)?$/) {
        $base_name = $1;
        $fq2 =~ s/_1\.fq/_2.fq/;
    } else {
        next; # 无法识别的文件格式跳过
    }
    
    $sample_info{$base_name} = (-e $fq2) ? { fq1 => $fq1, fq2 => $fq2, paired => 1 } : { fq1 => $fq1, paired => 0 };
}

foreach my $sample_id (keys %sample_info) {
    my $fq1 = $sample_info{$sample_id}{fq1};
    my $fq2 = $sample_info{$sample_id}{fq2} // "";
    my $paired = $sample_info{$sample_id}{paired};

    log_message("开始处理样本: $sample_id");
    my $pid = fork();
    if (!defined $pid) {
        die "无法创建子进程: $!\n";
    } elsif ($pid == 0) {
        process_sample($sample_id, $fq1, $fq2, $paired);
        exit(0);
    }
}

my $has_error = 0;
while (my $pid = wait()) {
    last if $pid == -1;
    if ($? != 0) {
        $has_error = 1;
        log_message("子进程 $pid 失败，退出码: " . ($? >> 8));
    }
}

if ($has_error) {
    die "错误: 部分样本处理失败！\n";
}

log_message("所有样本 步骤1 处理完成！\n");

sub process_sample {
    my ($sample_id, $fq1, $fq2, $paired) = @_;
    my $sample_dir = "$outputdir/$sample_id";
    make_path($sample_dir) unless -d $sample_dir;

    my $accepted_sam = "$sample_dir/accepted_hits.sam";
    my $accepted_sorted_bam = "$sample_dir/accepted_hits.sorted.bam";
    my $accepted_unique_bam = "$sample_dir/accepted_hits.sorted.unique.bam";
    my $metrics_file = "$sample_dir/${sample_id}.metricsFile";

    # Bowtie2 日志不直接重定向到文件，而是让其输出到 stderr，最终被 Snakemake 捕获到 log 中
    log_message("执行 Bowtie2 比对: $sample_id");
    my $bowtie_cmd = $paired 
        ? "bowtie2 -x \"$indexdir\" -p $threads -t -q -N 1 -L 25 --no-mixed --no-discordant --rg-id $sample_id --rg SM:$sample_id -1 \"$fq1\" -2 \"$fq2\" -S \"$accepted_sam\""
        : "bowtie2 -x \"$indexdir\" -p $threads -t -q -N 1 -L 25 --no-mixed --no-discordant --rg-id $sample_id --rg SM:$sample_id -U \"$fq1\" -S \"$accepted_sam\"";
    system($bowtie_cmd) == 0 or die "错误: Bowtie2 失败 ($sample_id)\n";

    log_message("转换并排序 BAM 文件: $sample_id");
    system("samtools view -bS \"$accepted_sam\" -o \"$accepted_sorted_bam\" && samtools sort -@ $threads -o \"$accepted_sorted_bam\" \"$accepted_sorted_bam\"") == 0 or die "错误: SAMtools 失败 ($sample_id)\n";

    log_message("去除 PCR 复制: $sample_id");
    my $picard_cmd = (-e "$picarddir/picard.jar") ? "java -Xmx15g -jar \"$picarddir/picard.jar\" MarkDuplicates" : "picard MarkDuplicates";
    system("$picard_cmd I=\"$accepted_sorted_bam\" O=\"$accepted_unique_bam\" METRICS_FILE=\"$metrics_file\" REMOVE_DUPLICATES=true") == 0 or die "错误: Picard 失败 ($sample_id)\n";

    system("samtools index \"$accepted_unique_bam\"") == 0 or die "错误: SAMtools index 失败 ($sample_id)\n";

    log_message("清理临时文件: $sample_id");
    unlink $accepted_sam, $accepted_sorted_bam, "$accepted_sorted_bam.bai";
}