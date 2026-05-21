#!/usr/bin/perl -w  
use strict;
use warnings;
use Getopt::Long;
use File::Path qw(make_path);
use POSIX qw(waitpid);
use Time::Piece;
use File::Basename;

my ($inputdir, $outputdir, $threads, $help);
GetOptions(
    "inputdir|i=s"   => \$inputdir,
    "outputdir|o=s"  => \$outputdir,
    "threads|t=i"    => \$threads,
    "help!"          => \$help,
);

die "用法: perl 2_1_QC.pl --inputdir <dir> --outputdir <dir> --threads <num>\n"
    if $help || !$inputdir || !$outputdir || !$threads;

make_path($outputdir) unless -d $outputdir;

sub log_message {
    my ($message) = @_;
    my $time = localtime->strftime('%Y-%m-%d %H:%M:%S');
    print "[$time] $message\n";
}

log_message("ChIP-seq 步骤2 QC: Trim Galore 开始");

my @samples = glob("$inputdir/*/*_1*.fq.gz");
if (!@samples) {
    @samples = glob("$inputdir/*_1*.fq.gz");
}
# 兼容未压缩的 fastq/fq
if (!@samples) {
    @samples = glob("$inputdir/*/*_1*.fastq");
    if (!@samples) {
        @samples = glob("$inputdir/*_1*.fastq");
    }
}
if (!@samples) {
    @samples = glob("$inputdir/*/*_1*.fq");
    if (!@samples) {
        @samples = glob("$inputdir/*_1*.fq");
    }
}
chomp @samples;

if (!@samples) {
    log_message("错误: 未找到任何 Fastq 文件！");
    die "未找到 Fastq 文件，请检查输入路径: $inputdir\n";
}

my %sample_info;
foreach my $fq1 (@samples) {
    if ($fq1 =~ /(.+)_1\.clean\.fq\.gz$/ || $fq1 =~ /(.+)_1\.fastq\.gz$/ || $fq1 =~ /(.+)_1\.fq\.gz$/ || $fq1 =~ /(.+)_1\.fastq$/ || $fq1 =~ /(.+)_1\.fq$/) {
        my $base_name = basename($1);
        my $fq2 = $fq1;
        $fq2 =~ s/_1\./_2\./;
        $sample_info{$base_name} = (-e $fq2) ? { fq1 => $fq1, fq2 => $fq2, paired => 1 } : { fq1 => $fq1, paired => 0 };
    }
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

while (wait() != -1) {}
log_message("所有样本 QC 处理完成！\n");

sub process_sample {
    my ($sample_id, $fq1, $fq2, $paired) = @_;
    my $sample_dir = "$outputdir/$sample_id";
    make_path($sample_dir) unless -d $sample_dir;

    log_message("执行 Trim Galore: $sample_id");
    # 让 trim_galore 的输出直接打印到标准输出/标准错误，由 Snakemake 捕获
    my $trim_cmd = $paired
        ? "trim_galore --paired --fastqc --cores $threads -o $sample_dir $fq1 $fq2"
        : "trim_galore --fastqc --cores $threads -o $sample_dir $fq1";

    system($trim_cmd) == 0 or die "Trim Galore 失败，请检查 logs。\n";
}