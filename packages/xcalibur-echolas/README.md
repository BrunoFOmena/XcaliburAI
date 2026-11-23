# xcalibur-echolas

XcaliburAI domain package for Leishmania population genomics (ECHOLAS). Install it next to `xcalibur-ai` and the domain is discovered automatically.

The Nextflow pipeline in [`src/xcalibur_echolas/workflow`](src/xcalibur_echolas/workflow) is adapted from [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS) and distributed under the MIT License; see [NOTICE.md](src/xcalibur_echolas/workflow/NOTICE.md). The rest of this package is AGPL-3.0-or-later, like the core.

Limitation: variant calling assumes a diploid organism (`HaplotypeCaller --ploidy 2`).

## Inputs

Both inputs are folders. `xcalibur plan` and `xcalibur run` check them before anything runs ([`validation.py`](src/xcalibur_echolas/validation.py)) and stop with exit code 2 and a message that names the file and the fix.

| Input | Rule |
|---|---|
| `reference` | Exactly one genome, `.fna`, `.fa` or `.fasta`, uncompressed, not empty. Index files (`.fai`, `.dict`, ...) are fine. |
| `raw_data` | gzip-compressed FASTQ, `.fastq.gz` or `.fq.gz` (any case), at the top level of the folder. Uncompressed files are an error. |
| Paired-end | `<sample>_R1` + `<sample>_R2`, `_1`/`_2`, or Illumina's `_R1_001`/`_R2_001`, in any case (`_r1` works). The tag is read only at the end of the name. Both mates must be there. |
| Single-end | `<sample>.fastq.gz`, without a mate tag. A sample cannot mix both layouts, nor have two files for the same mate (`A_R1` and `A_r1`, or `A.fastq.gz` and `A.fq.gz`). |
| Sample names | Everything before the mate tag and the extension: `A.v2_R1` is sample `A.v2`, `SRR_123_1` is `SRR_123`, `S8_10` is the single-end sample `S8_10`. Letters, digits, `.`, `_` and `-`, starting with a letter or digit. |
| Mate tag | Read only at the very end of the name, as `_R1`/`_R2`, `_1`/`_2` or `_R1_` plus exactly three digits. So `A_R1-x` and `A_R1_1234` have none (single-end samples with those names), and `S_R2_1` is sample `S_R2`, mate R1. Split chunks of one mate (`A_R1_001` + `A_R1_002`) are an error: concatenate them first. |
| Lanes | `X_L001_R1_001` and `X_L002_R1_001` are run as two samples (`X_L001`, `X_L002`) with a warning; merge the lanes first if they are one sample. |

The pipeline (`main.nf`) and the check (`validation.py`) use the same two patterns, and a test runs both on the same names.

Ignored: hidden files, the `_disabled/` folder and other subfolders (the pipeline does not read them; other subfolders give a warning), and files that are not FASTQ (warning). The check reads names, sizes and the first two bytes of each FASTQ (gzip signature) only.

