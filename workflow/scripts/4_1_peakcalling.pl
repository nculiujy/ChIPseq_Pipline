#!/usr/bin/perl
BEGIN { $ENV{PATH} = "/home/jyliu/miniconda3/envs/ChIPseq_Pipline/bin:$ENV{PATH}"; }
use Getopt::Long;
use File::Path qw(make_path);
use File::Basename;
use Time::Piece;

# 参数变量
my ($metadata_file, $bamdir, $outdir, $picture_dir, $genome, $qval, $threads, $cutoff_type, $norm_method, $peak_type, $broad_cutoff, $help);
GetOptions(
    'metadata=s'     => \$metadata_file,
    'bamdir=s'       => \$bamdir,
    'outdir=s'       => \$outdir,
    'picdir=s'       => \$picture_dir,
    'genome=s'       => \$genome,
    'qval=f'         => \$qval,
    'cutoff_type=s'  => \$cutoff_type,
    'norm_method=s'  => \$norm_method,
    'peak_type=s'    => \$peak_type,
    'broad_cutoff=f' => \$broad_cutoff,
    'threads=i'      => \$threads,
    'help!'          => \$help,
) or die "参数格式错误。\n";

# 参数检查
if ($help || !$metadata_file || !$bamdir || !$outdir || !$picture_dir || !$genome) {
    die "用法: perl 4_1_peakcalling.pl --metadata <file> --bamdir <dir> --outdir <dir> --picdir <dir> --genome <str> --qval <float> --cutoff_type <qvalue|pvalue> --norm_method <BPM|TPM|none> --peak_type <narrow|broad> --broad_cutoff <float> --threads <num>\n";
}

$qval //= 0.05;
$cutoff_type //= "qvalue";
$norm_method //= "none";
$peak_type //= "narrow";
$broad_cutoff //= 0.1;
$threads //= 8;

make_path($outdir) unless -d $outdir;
make_path($picture_dir) unless -d $picture_dir;

sub log_message {
    my ($message) = @_;
    my $time = localtime->strftime('%Y-%m-%d %H:%M:%S');
    print "[$time] $message\n";
}

log_message("ChIP-seq 步骤4: Peak Calling 开始");
log_message("参数: cutoff_type=$cutoff_type, value=$qval, normalization=$norm_method");

# 读取 metadata.csv
open(my $IN, '<', $metadata_file) or die "无法打开文件 $metadata_file: $!";
my $header = <$IN>; # Skip header

my $processed_count = 0;

while (my $line = <$IN>) {
    chomp $line;
    next if $line =~ /^\s*$/;
    my ($treat_sample, $control_sample) = split(/,/, $line);
    
    # 去除可能存在的空白字符
    $treat_sample =~ s/^\s+|\s+$//g;
    $control_sample =~ s/^\s+|\s+$//g;

    my $treat_bam = "$bamdir/$treat_sample/accepted_hits.sorted.unique.bam";
    my $control_bam = "$bamdir/$control_sample/accepted_hits.sorted.unique.bam";

    unless (-e $treat_bam) {
        log_message("警告: 实验组 BAM 文件不存在: $treat_bam，跳过 $treat_sample vs $control_sample");
        next;
    }
    unless (-e $control_bam) {
        log_message("警告: 对照组 BAM 文件不存在: $control_bam，跳过 $treat_sample vs $control_sample");
        next;
    }

    $processed_count++;
    my $pair_name = "${treat_sample}_vs_${control_sample}";
    log_message(">>> 正在处理样本对: $pair_name | genome: $genome");

    my $sample_dir = "$outdir/$pair_name";
    make_path($sample_dir);

    #### ===[Step 0: SPP分析]===
    my $bam_control_dir = "$sample_dir/bam_control";
    make_path($bam_control_dir);

    my $q_display = $qval;
    $q_display =~ s/^0\.//;

    my $spp_pdf = "$picture_dir/${pair_name}_bam_control.q${q_display}.pdf";
    my $spp_out = "$bam_control_dir/${pair_name}_cross.txt";

    # 注意：spp包或者phantompeakqualtools需要正确安装，并且run_spp.R在环境变量中
    my $spp_cmd = "run_spp.R -rf " .
                  "-c=$treat_bam " .
                  "-i=$control_bam " .
                  "-p=$threads " .
                  "-odir=$bam_control_dir " .
                  "-savp=$spp_pdf " .
                  "-out=$spp_out";

    log_message(">>> [SPP] 执行命令: $spp_cmd");
    system($spp_cmd) == 0 or log_message("警告: [SPP] $pair_name 运行失败 (如果未安装 SPP 可忽略)");

    #### ===[Step 1: MACS2 peak calling]===
    log_message(">>> [MACS2] 执行 Peak Calling: $pair_name");
    
    # 构建 MACS2 命令
    my $cutoff_arg = ($cutoff_type eq "pvalue") ? "-p $qval" : "-q $qval";
    
    # 归一化处理 (MACS2 本身主要通过 --SPMR 输出归一化信号，或者 bdgcmp)
    # 此处仅演示参数传递，如果需要更复杂的归一化，需要结合 macs2 bdgcmp 使用
    my $extra_args = "";
    if ($norm_method eq "BPM" || $norm_method eq "RPKM") {
        $extra_args .= " --SPMR"; # 生成每百万reads的信号轨道
    }

    if ($peak_type eq "broad") {
        $extra_args .= " --broad --broad-cutoff $broad_cutoff";
    }
    
    my $macs2_genome = $genome;
    if (lc($genome) eq "tair") {
        $macs2_genome = "1.2e8";
    } elsif (lc($genome) eq "homo") {
        $macs2_genome = "hs";
    } elsif (lc($genome) eq "mm") {
        $macs2_genome = "mm";
    }

    my $macs2_cmd = "macs2 callpeak -t $treat_bam -c $control_bam -g $macs2_genome -n $pair_name --keep-dup all $cutoff_arg $extra_args --outdir $sample_dir";
    log_message(">>> [MACS2] Cmd: $macs2_cmd");
    system($macs2_cmd) == 0 or die "错误: MACS2 运行失败 ($pair_name)\n";

    #### ===[Step 2: 处理 narrowPeak->bed->tab.bed 或 broadPeak->bed->tab.bed]===
    my $peak_ext = ($peak_type eq "broad") ? "broadPeak" : "narrowPeak";
    my $peak_file = "$sample_dir/${pair_name}_peaks.${peak_ext}";
    my $bed       = "$sample_dir/${pair_name}_peaks.bed";
    my $tab_bed   = "$sample_dir/${pair_name}_peaks_tab.bed";

    if (-e $peak_file) {
        system("awk '{print \$1, \$2, \$3, \$4, \$5}' $peak_file > $bed");
        system("sed 's/ \\+/\t/g' $bed > $tab_bed");
    } else {
        log_message("警告: 未找到 MACS2 输出文件 $peak_file");
    }
}

close($IN);

if ($processed_count == 0) {
    log_message("错误: 所有样本都因找不到 BAM 文件被跳过。Peakcalling 失败！");
    die "请检查前面的比对步骤是否成功生成了 BAM 文件。\n";
}

log_message("所有样本 Peak Calling 处理完成！\n");