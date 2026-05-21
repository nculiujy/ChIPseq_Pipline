APIARC: Automated Pipeline for Integrated Analysis of RNA-seq and ChIP-seq
===========================================================================

Introduction
----------------
**APIARC** is a modular, flexible, and fully automated workflow based on **Snakemake** designed for the comprehensive and integrated analysis of RNA-seq and ChIP-seq data. 

While there are many pipelines that analyze RNA-seq or ChIP-seq individually, APIARC bridges the gap by not only processing raw data from both sequencing types but also performing deep biological integration. It identifies co-regulated genes, links Enhancer and Promoter signals, performs functional enrichment (KEGG/GO), and infers Transcription Factor (TF) - Gene regulatory networks, generating Cytoscape-ready files for network visualization.

Workflow
------------
![Workflow Overview](Figure1.png)

The APIARC workflow consists of three main parallel and sequential branches:

1. **RNA-seq Pipeline**: From raw data download (SRA) $\rightarrow$ QC (FastQC/fastp) $\rightarrow$ Alignment & Quantification (Hisat2/StringTie) $\rightarrow$ Differential Expression Analysis (DESeq2).
2. **ChIP-seq Pipeline**: From raw data download $\rightarrow$ QC (Trim Galore) $\rightarrow$ Alignment & Filtering (Bowtie2/Picard) $\rightarrow$ Peak Calling (MACS2) & Signal Track Generation (deepTools).
3. **Integrated Analysis**:
   - **Promoter Module**: Integrates ChIP-seq peaks at promoter regions with RNA-seq DEGs.
   - **Enhancer Module**: Associates distal Enhancer peaks with target genes using correlation analysis.
   - **Network Module**: Generates GO/KEGG functional enrichment networks and TF-Gene regulatory networks (exportable to Cytoscape).

📂 Input and Output
-------------------
### Input format
1. **Config file (`config.yaml`)**: The main configuration file to set up reference genomes, thread counts, and general pipeline switches.
2. **Metadata (`config/RNAseq_metadata.csv` & `ChIPseq_metadata.csv`)**: Tabular data defining the sample names, their corresponding SRR IDs, and experimental groups (e.g., Treatment vs Control).
3. **Raw Data (Optional)**: The pipeline can automatically download raw reads from NCBI SRA using the SRR IDs provided. Alternatively, users can place local `.fastq.gz` files in the raw data directories.

### Output directories
All results are structured in the `result/` directory:
1. `RNAseq_pipline/`: Contains `1_Rawdata`, `2_Cleandata`, `3_RC_pipline` (BAM/GTF/Counts), and `4_DEseq` (Differential expression tables and plots).
2. `ChIPseq_pipline/`: Contains `1_Rawdata`, `2_Cleandata`, `3_CC_pipline` (BAM/BigWig), and `4_peak_result` (MACS2 peaks and deepTools matrix).
3. `Integrated/`: 
   - **Promoter Module**: Identifies common genes between DEGs and ChIP promoter peaks, plotting expression vs. ChIP signal correlations. Performs KEGG/GO enrichment (Gene-KEGG/GO networks) and TF motif binding analysis (TF-Gene networks).
   - **Enhancer Module**: Identifies common genes between DEGs and ChIP enhancer peaks, mapping correlations. Generates corresponding KEGG/GO enrichment networks and TF-Gene regulatory networks.

⚙️ Installation
----------------
### Dependencies
The pipeline relies on Conda for environment management. The core software dependencies include:
- `snakemake`
- `fastp`, `trim_galore`, `fastqc`
- `hisat2`, `bowtie2`, `samtools`, `picard`
- `stringtie`, `macs2`, `deeptools`
- **Python 3**: `pandas`, `numpy`, `concurrent.futures`
- **R 4.0+**: `DESeq2`, `ChIPseeker`, `ComplexHeatmap`, `clusterProfiler`

### Clone the repository
```bash
git clone https://github.com/nculiujy/APIARC.git
cd APIARC
```

### Setup Environments
APIARC uses Conda environments defined in `workflow/envs/`. Snakemake will automatically create and use these environments when you run the pipeline with the `--use-conda` flag (if configured).

Alternatively, you can manually build the core environments:
```bash
conda env create -f environment.yml
conda activate APIARC
```

### Resource Preparation
The `workflow/resources/` directory contains necessary external scripts and genomic annotations (Promoter, Enhancer, Motif, etc.) used for the Integrated Analysis modules.
Because some annotation files are too large to be hosted on GitHub, we provide a setup script to automatically fetch and configure them.
**Before running the pipeline, you must prepare the annotation environment:**
```bash
bash workflow/resources/anno/setup_anno_env.sh
```
This script will download and format the required reference annotations (like GTF, Bed files, etc.) for your target species (Human/Mouse) and place them in the correct `workflow/resources/anno` subdirectories.

🚀 Usage
---------
The `config/` directory serves as the control center of the APIARC pipeline. Before running the pipeline, users must carefully configure the following three files according to their experimental design:

### 1. Main Configuration (`config/config.yaml`)
This YAML file defines global parameters and module switches. It controls the overall behavior of the pipeline.
```yaml
# Select species for reference genome ("mm" for Mouse, "homo" for Human)
species: "mm"

# Define project/experiment ID (used for SRA download folder naming)
experiment: "GSE140552"

# Toggle pipeline modules on/off (True/False)
RNAseq_modules:
  1_download: True
  2_QC: True
  3_RC: True
  4_DEseq: True

# Enable/Disable ChIP-seq modules
ChIPseq_modules: ...
```

### 2. RNA-seq Metadata (`config/RNAseq_metadata.csv`)
A comma-separated file describing your RNA-seq samples. This file is critical for downloading data and setting up DESeq2 comparisons.
**Format rules:**
- **`sample`**: The unique identifier (e.g., SRA accession like `SRR10485905`). If using local data, place your files in `result/RNAseq_pipline/1_Rawdata/{experiment}/` and ensure the filename starts with this ID (e.g., `SRR10485905.fastq.gz`).
- **`sample_name`**: A readable biological name. **Must be unique** for every row. For biological replicates, use suffixes like `_1`, `_2` (e.g., `Control_1`, `Control_2`).
- **`group`**: The experimental condition for Differential Expression Analysis (DESeq2). 
  - Use `"T"` for the Control/Baseline group.
  - Use `"P"` for the Treatment/Experimental group.

*Example:*
```csv
sample,sample_name,group
SRR10485905,Control_1,T
SRR10485906,Control_2,T
SRR10485907,Treatment_1,P
```

### 3. ChIP-seq Metadata (`config/ChIPseq_metadata.csv`)
A comma-separated file detailing the ChIP-seq sample pairings for Peak Calling.
**Format rules:**
- **`IP sample`**: The SRR ID or prefix of the IP (treatment) raw data file.
- **`Input`**: The SRR ID or prefix of the corresponding Input (background control) raw data file.
- **`IP_name`**: The target name or group label (e.g., `H3K4me1_rep1`). 
  - **Important:** Replicates should be indicated by suffixes like `_rep1` and `_rep2` (e.g., `H3K27ac_rep1`). The integrated analysis modules will automatically group and merge them based on the prefix before the `_rep`.

*Example:*
```csv
IP sample,Input,IP_name
SRR10485892,SRR10485880,H3K4me1_rep1
SRR10485893,SRR10485880,H3K4me1_rep2
SRR10485886,SRR10485880,H3K27ac_rep1
```

### Running the Pipeline
Once the configuration is correctly set, execute the pipeline from the project root.

To run the complete integrated pipeline locally with 30 cores:
```bash
snakemake -c 30 --rerun-incomplete
```

To run a dry-run (to check which rules will be executed without actually running them):
```bash
snakemake -n
```

If you encounter network instability during data download, the pipeline has built-in retry mechanisms and will fail safely if the data cannot be acquired.

### Project Architecture
```text
APIARC/
├── Snakefile                 # Project entry point
├── README.md                 # Project description and user manual
├── environment.yml           # Conda base environment dependencies
├── Figure1.png               # Workflow diagram
│
├── config/                   # Core configuration center (user modification area)
│   ├── config.yaml           # Global parameter switches (species selection, experiment ID, module on/off)
│   ├── RNAseq_metadata.csv   # RNA-seq sample control and grouping information
│   └── ChIPseq_metadata.csv  # ChIP-seq IP and Input sample pairing and merging information
│
└── workflow/                 # Core workflow logic
    ├── rules/                # Snakemake rule files (define input/output for each step)
    │   ├── RNAseq_pipline/   # Rules for RNAseq analysis pipeline (Download -> QC -> RC -> DESeq2)
    │   ├── ChIPseq_pipline/  # Rules for ChIPseq analysis pipeline (Download -> QC -> CC -> Peak Calling)
    │   └── Integrated/       # Integrated analysis rules (divided into Promoter module and Enhancer module)
    │
    ├── scripts/              # External execution scripts (Python, R, Perl)
    │   ├── RNAseq_pipline/   # e.g., download scripts, DESeq2 analysis scripts
    │   ├── ChIPseq_pipline/  # e.g., quality control Perl scripts, format conversion scripts
    │   └── Integrated/       # Co-expression network, KEGG/GO, TF motif analysis R scripts
    │
    ├── envs/                 # Conda isolated environment configuration files (.yaml)
    │   └── *.yaml            # Exclusive software environments defined for different modules (e.g., separate environments for MACS2, DESeq2)
    │
    └── resources/            # Static data and external resources (genome annotation, Java tools, etc.)
        ├── anno/             # Basic genome annotation (GTF, chromosome size, etc., automatically generated by setup_anno_env.sh)
        ├── Enhancer_anno/    # Enhancer feature annotation data
        ├── Motif_tf_anno/    # TF Motif JASPAR database
        └── picard-2.18.2/    # Picard Java executable files
```