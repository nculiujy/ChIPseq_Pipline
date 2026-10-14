
**使用MEME-ChIP进行motif富集分析**

MEME-ChIP是一个用于在ChIP-seq中分析蛋白质与基因组中调控元件结合基序（motif，例如转录因子蛋白-基因启动子区序列的结合区域）的工具，包括motif鉴定、富集分析、位点可视化、结合亲和力分析、转录因子-靶基因预测等。

本篇简介使用MEME-ChIP，根据peaks数据识别蛋白质与基因组之间结合位点的可能motif。

peaks数据文件格式

MEME-ChIP的输入数据是一个fasta序列文件，即在peaks位点两侧截取一定长度的序列（例如位点两侧各100bp），如下所示的“peak_bind.fasta”。

![图片](https://mmbiz.qpic.cn/mmbiz_png/Canb3IJn7EAiclLOMgG15sNqcp58cusvU83rYWDYVWoI6SEIiaFeLIqTqb9OoFxCc3CuMreVfPib5ibB9GlbovXxwQ/640?wx_fmt=png&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=2)

## *** 根据bed文件中的peaks在基因组中提取位点两侧序列**

通常ChIP-seq或ATAC-seq的peaks以bed文件存储，如下所示的“Runx1.bed”，来自某人类细胞的ChIP-seq试验，获得了转录因子Runx1在基因组（本示例使用了人类基因组hg38）上的结合peaks。因此我使用Python写了个循环，依次读取bed文件中peaks位点信息，在人类基因组hg38中将Runx1结合位点两侧的DNA序列提取出来，最后输出一个fasta文件存放所有提取出的DNA序列。

```
import os

fas = open('peak_bind.fasta', 'w')  #输出序列，fasta 格式
bed = open('Runx1.bed', 'r')  #输入 bed 文件
for line in bed:
    line = line.split('\t')
    print(f'>{line[3]}', file = fas)
    #期间用到 samtools 在基因组中截取序列，这里我截取了结合位点两侧各 100bp 的长度，即提取的总序列长度 200bp
    os.system(f'samtools faidx hsGRCh38.fa {line[0]}:{int(line[1])-99}-{int(line[2])+99} > fas.tmp')

    fas_tmp = open('fas.tmp', 'r')
    fas_tmp.readline()
    for line1 in fas_tmp:
        fas.write(line1)

    fas_tmp.close()

fas.close()
bed.close()
os.system('rm fas.tmp')
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/Canb3IJn7EAiclLOMgG15sNqcp58cusvU8Nk29USrEia95qpEDk6dQzqXMPSuwibU3icmcII5RYyQr1k9yWsHQ8npQ/640?wx_fmt=png&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=3)


本地运行MEME-ChIP进行motif富集

下载motif数据库和安装MEME-ChIP

进入MEME-ChIP下载页面（**https://meme-suite.org/meme/doc/download.html**），下载motif数据库，并选择适合系统版本的MEME-ChIP安装包。

![图片](https://mmbiz.qpic.cn/mmbiz_png/Canb3IJn7EAiclLOMgG15sNqcp58cusvUyfamtLIdtZDhlvibssic3c1dcG7twUnWibIow1xgAgN9TLXF2k5Sjsllg/640?wx_fmt=png&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=5)

对于motif数据库，点击“Motif Databases”即可将motif数据库下载至本地。

![图片](https://mmbiz.qpic.cn/mmbiz_png/Canb3IJn7EAiclLOMgG15sNqcp58cusvUyD8aDC0AYRb3TTicuqJ6ncS1ibz6KtxoCLUZYtWdWXyvibT4iaKvBics2vQ/640?wx_fmt=png&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=6)

对于MEME-ChIP的安装，根据安装指引（http://meme-suite.org/doc/install.html），在Linux系统下使用命令行操作。

```
#可能需要预先配置 autoconf
#apt install autoconf

#下载编译 MEME 程序包，这里以最新的 5.5.0 版为例
tar zxf meme-5.5.0.tar.gz
cd meme-5.5.0
./configure --prefix=/home/proj/software/MEME/meme-5.5.0 --with-url=http://meme-suite.org/ --enable-build-libxml2 --enable-build-libxslt
make
make test
make install

#将 MEME 主程序所在路径添加至环境变量，例如我将 MEME 安装在“/home/proj/software/MEME/meme-5.5.0”
export PATH=/home/proj/software/MEME/meme-5.5.0:$PATH
```

运行MEME-ChIP进行motif富集

将记录了peaks结合位点两侧序列的fasta文件作为输入，并根据具体试验中的DNA结合蛋白类型指定motif数据库，以及其他运行参数后，运行MEME-ChIP进行motif富集。等待一段时间后运行结束，打开报告“meme-chip.html”查看本次motif分析结果。

```
#例如，本示例的 DNA 结合蛋白是一个转录因子，因此指定了 JASPAR 的转录因子-启动子 motif 数据库
#其他参数请参阅 MEME 的帮助文档
meme-chip -dna -meme-mod zoops -meme-nmotifs 5 -minw 6 -maxw 15 -streme-pvt 0.05 -centrimo-score 5 -centrimo-ethresh 10 \
    -db db/motif_databases/JASPAR/JASPAR2018_CORE_vertebrates_non-redundant.meme -o motif peak_bind.fasta
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/Canb3IJn7EAiclLOMgG15sNqcp58cusvU8mJibZicd6BgJwBTeJta9lQbJBv9M0DbVhGfQZff0jwn56F0ezeHaohw/640?wx_fmt=png&tp=wxpic&wxfrom=5&wx_lazy=1#imgIndex=7)
