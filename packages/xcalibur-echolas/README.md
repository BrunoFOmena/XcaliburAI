# xcalibur-echolas

XcaliburAI domain package for Leishmania population genomics (ECHOLAS). Install it next to `xcalibur-ai` and the domain is discovered automatically.

The Nextflow pipeline in [`src/xcalibur_echolas/workflow`](src/xcalibur_echolas/workflow) is adapted from [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS) and distributed under the MIT License; see [NOTICE.md](src/xcalibur_echolas/workflow/NOTICE.md). The rest of this package is AGPL-3.0-or-later, like the core.

Limitation: variant calling assumes a diploid organism (`HaplotypeCaller --ploidy 2`).

## Inputs

Both inputs are folders. `xcalibur plan` and `xcalibur run` check them before anything runs ([`validation.py`](src/xcalibur_echolas/validation.py)) and stop with exit code 2 and a message that names the file and the fix.

| Input | Rule |
|---|---|
| `reference` | Exactly one genome, `.fna`, `.fa` or `.fasta`, uncompressed, not empty. Index files (`.fai`, `.dict`, ...) are fine. |
| `raw_data` | gzip-compressed `*.fastq.gz` files (lowercase extension), at the top level of the folder. |
| Paired-end | `<sample>_R1.fastq.gz` + `<sample>_R2.fastq.gz`, or `_1`/`_2`. Both mates must be there. |
| Single-end | `<sample>.fastq.gz`, without a mate suffix. A sample cannot mix both layouts. |
| Sample names | Letters, digits, `_` and `-`. The pipeline cuts the name at the first `.` and removes every `_1`, `_2`, `_R1` and `_R2`, so `A.L1_R1` and `A.L2_R1` are the same sample (error) and `SRR_123_1` becomes `SRR23` (warning). Merge lanes before the run. |

Ignored: hidden files, the `_disabled/` folder and other subfolders (the pipeline does not read them; other subfolders give a warning), and files that are not FASTQ (warning). The check reads names, sizes and the first two bytes of each FASTQ (gzip signature) only.

