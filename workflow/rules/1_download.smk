rule download_data_project:
    input:
        script="workflow/scripts/1_download.py",
        # 不需要 metadata.csv 因为 1_download.py 直接依赖于项目文件夹内的特定文件
    output:
        "result/{species}/{experiment}/1_download/finished.txt"
    log:
        "logs/{species}/{experiment}/1_download.log"
    params:
        outdir="result/{species}/{experiment}/1_download"
    shell:
        """
        python {input.script} \
            --species {wildcards.species} \
            --experiment {wildcards.experiment} \
            --outdir {params.outdir} > {log} 2>&1
        
        touch {output}
        """