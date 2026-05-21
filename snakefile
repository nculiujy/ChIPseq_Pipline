import os

configfile: "config/config.yaml"

TARGET_FILES = []

# 解析 projects
PROJECTS = config.get("projects", [])

# 先将需要的 rules include 进来
include: "workflow/rules/1_download.smk"
include: "workflow/rules/2_QC.smk"
include: "workflow/rules/3_ChIPseq.smk"
include: "workflow/rules/4_analyse.smk"

# 根据每个 project 的独立 modules 开关来添加 TARGET_FILES
for proj in PROJECTS:
    species = proj["species"]
    experiment = proj["experiment"]
    modules = proj.get("modules", {})

    if modules.get("1_download", False):
        TARGET_FILES.append(os.path.join("result", species, experiment, "1_download", "finished.txt"))

    if modules.get("2_QC", False):
        TARGET_FILES.append(os.path.join("result", species, experiment, "2_QC", "QC_finished.txt"))

    if modules.get("3_ChIPseq", False):
        TARGET_FILES.extend([
            os.path.join("result", species, experiment, "3_ChIPseq", "step1_finished.txt"),
            os.path.join("result", species, experiment, "3_ChIPseq", "step2_finished.txt")
        ])

    if modules.get("4_analyse", False):
        TARGET_FILES.extend([
            os.path.join("result", species, experiment, "4_analyse", "peakcalling_finished.txt"),
            os.path.join("result", species, experiment, "4_analyse", "annoChIPPeaks_finished.txt"),
            os.path.join("result", species, experiment, "4_analyse", "deeptools_finished.txt")
        ])

rule all:
    input:
        TARGET_FILES