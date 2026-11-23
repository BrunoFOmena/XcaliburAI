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

## Agents and tools

| Agent | Role | Instructions | Tools |
|---|---|---|---|
| `echolas_executor` | execution | [`prompts/executor.md`](src/xcalibur_echolas/prompts/executor.md) | `describe_domain`, `plan_run`, `run_pipeline` (asks the user), `get_run_status`, `read_results` |
| `echolas_interpreter` | interpretation | [`prompts/interpreter.md`](src/xcalibur_echolas/prompts/interpreter.md) | `get_run_status`, `read_results` and the four tools below; it cannot run anything |

The executor validates the inputs through `plan_run` (the input check of this package) and does not run when they are invalid; it explains each error with its fix.

Result tools (agents see them as `echolas__<name>`). They read a finished run of the workspace by `run_id`, never leave the run directory and are *grounded*: every number the answer gives about the results must come from them, and an answer with an untraced number is corrected once and otherwise flagged.

| Tool | Reads | Returns |
|---|---|---|
| `summarize_run` | all of the below | Everything at once, plus the caveats that apply |
| `summarize_vcf` | `final` (PASS), `pca` or `phylo` VCF | Samples, variants by type and filter, multiallelic sites, per-contig counts, per-sample hom-ref/het/hom-alt/missing |
| `read_pca` | `.eigenval`, `.eigenvec` (PLINK) | Eigenvalues, each PC's share of the computed PCs, PC1/PC2 per sample |
| `read_tree` | `.treefile` (IQ-TREE) | Leaves, splits (bipartitions) with SH-aLRT/UFBoot, supported or not (SH-aLRT >= 80 and UFBoot >= 95), branch lengths, total length |

Caveats are decided by the tool, not the model: fewer than 10 samples, samples with more than 20% missing genotypes, no heterozygous calls, PCA shares of a partial set of PCs, unsupported splits, branch lengths inflated by SNP-only data without ascertainment correction (#52), and diploid calling of an often aneuploid organism. The readers use only the Python standard library and stream large VCFs; long lists are cut at 50 items (200 for samples), with the count of what was left out.
