#!/usr/bin/perl -w  
use strict;
use warnings;
use Getopt::Long;
use File::Path qw(make_path);
use POSIX qw(waitpid);
use Time::Piece;
use File::Basename;

my ($inputdir, $threads, $help);
GetOptions(
    "inputdir|i=s"   => \$inputdir,
    "threads|t=i"    => \$threads,
    "help!"          => \$help,
);

die "用法: perl 3_2_bamtobwfile.pl --inputdir <dir> --threads <num>\n"
    if $help || !$inputdir || !$threads;

sub log_message {
    my ($message) = @_;
    my $time = localtime->strftime('%Y-%m-%d %H:%M:%S');
    print "[$time] $message\n";
}

log_message("ChIP-seq 步骤2: Bam 转 BigWig 开始");

my @bam_files = glob("$inputdir/*/accepted_hits.sorted.unique.bam");
chomp @bam_files;

if (!@bam_files) {
    log_message("错误: 未找到任何 BAM 文件！");
    die "未找到 BAM 文件，请检查输入路径: $inputdir\n";
}

foreach my $bam (@bam_files) {
    my $sample_dir = dirname($bam);
    my $sample_id = basename($sample_dir);
    my $bigwig_output = "$sample_dir/${sample_id}.bw";

    log_message("开始处理样本: $sample_id");
    my $pid = fork();
    if (!defined $pid) {
        die "无法创建子进程: $!\n";
    } elsif ($pid == 0) {
        log_message("生成 bigWig 文件: $sample_id");
        # 让 bamCoverage 的输出直接打印到标准输出/标准错误，由 Snakemake 捕获
        system("bamCoverage -b $bam -of bigwig --binSize 5 --ignoreDuplicates --normalizeUsing BPM --numberOfProcessors $threads -o $bigwig_output") == 0 or log_message("错误: 生成 bigWig 失败 ($sample_id)");
        exit(0);
    }
}

while (wait() != -1) {}
log_message("所有样本 步骤2 处理完成！\n");