# xcalibur-echolas

XcaliburAI domain package for Leishmania population genomics (ECHOLAS). Install it next to `xcalibur-ai` and the domain is discovered automatically.

The Nextflow pipeline in [`src/xcalibur_echolas/workflow`](src/xcalibur_echolas/workflow) is adapted from [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS) and distributed under the MIT License; see [NOTICE.md](src/xcalibur_echolas/workflow/NOTICE.md). The rest of this package is AGPL-3.0-or-later, like the core.

Limitation: variant calling assumes a diploid organism (`HaplotypeCaller --ploidy 2`).

## Trimming (mode AUTO)

AUTO picks a path per sample from the mean quality FastQC reports (the mean of its per-base rows). FastQC runs on each file, so a paired-end sample has two values, R1's and R2's: the **lower** one decides (#48), because the trimming applies to both mates and the worse one, usually R2, is what needs it. A pair with R1 at Q36 and R2 at Q27 takes `pe_intermediate`, in every run. A single-end sample uses the value of its only file.

| Path | Chosen when | Tool and parameters |
|---|---|---|
| `pe_conservative` | paired, mean >= Q30 | Trimmomatic `ILLUMINACLIP:TruSeq3-PE.fa:2:30:10 LEADING:20 TRAILING:20 SLIDINGWINDOW:4:20 MINLEN:36` |
| `pe_intermediate` | paired, Q25-Q30 | fastp `--cut_tail --cut_right -W 4 -M 15 -q 15 -u 40 -l 50`, adapters, poly-G/X, low complexity |
| `pe_aggressive` | paired, < Q25 | the same plus `--cut_front` and `--complexity_threshold 30` |
| `se_basic` | single-end, >= Q28 | fastp `--cut_tail -W 4 -M 15 -q 15 -u 40 -l 36` |
| `se_aggressive` | single-end, < Q28 | fastp `--cut_front --cut_tail --cut_right -W 4 -M 15 -q 15 -u 40 -l 50`, poly-G/X, low complexity 30 |

The fastp paths **trim** low-quality bases with a 4-base sliding window at Q15 and only then filter what is left (#81). Before, they only filtered: with `--qualified_quality_phred 30` and fastp's 40% limit, a sample of low quality could lose nearly all its reads. Q15, the window and the 50 bp minimum follow the read processing of Franssen et al. 2020 (eLife 9:e51243) for the *L. donovani* complex (Trimmomatic `TRAILING:15 SLIDINGWINDOW:4:15 MINLEN:50`, which is fastp `--cut_tail --cut_right -M 15`). Studies of variant calling find quality trimming changes few calls (Bush 2020, Microbial Genomics 7:000434; Liu et al. 2012, BMC Genomics) and gentle thresholds lose less data (MacManes 2014, Front. Genet. 5:13), since BWA-MEM soft-clips poor read ends and HaplotypeCaller weighs each base by its quality. A benchmark of these choices with known truth is tracked in #83.

## Running environment

The pipeline runs in either of two ways, with the same tool versions:

| Profile | Tools come from | Needs |
|---|---|---|
| `standard` (default) | One conda environment per process, created by Nextflow from each process's `conda` directive | Nextflow and conda/mamba/micromamba |
| `docker` | The ECHOLAS image; conda is not used | Nextflow and Docker |

Combine profiles with a comma: `xcalibur run echolas --profile docker ...` or `--profile test,docker` for the synthetic smoke test.

### Pinned versions

| Tool | Version | Tool | Version |
|---|---|---|---|
| FastQC | 0.12.1 | GATK | 4.6.2.0 |
| Trimmomatic | 0.40 | Picard | 3.1.1 |
| fastp | 1.0.1 | IQ-TREE | 2.3.6 |
| BWA | 0.7.17 | PLINK | 1.9.0b.7.7 |
| samtools | 1.19.2 | R | 4.5.3 (ggplot2 4.0.4, readr 2.2.0, dplyr 1.2.1) |
| bcftools | 1.19 | unzip, coreutils | 6.0, 9.12 |

They are written in three places that tests keep equal (`tests/test_echolas_container.py`): the `conda` directive of each process (`workflow/modules/*.nf`), the environment files [`workflow/envs/echolas.yml`](src/xcalibur_echolas/workflow/envs/echolas.yml) and [`echolas-pca.yml`](src/xcalibur_echolas/workflow/envs/echolas-pca.yml), and their explicit locks (`*.linux-64.lock`, every package with its build and md5). PLINK and R live in a separate environment because samtools/bcftools 1.19 need zlib < 1.3 and R 4.5 needs zlib >= 1.3.

### The image

```bash
cd packages/xcalibur-echolas
docker build -f docker/Dockerfile --build-arg VERSION=0.0.0 -t ghcr.io/brunofomena/xcalibur-echolas:0.0.0 .
docker run --rm ghcr.io/brunofomena/xcalibur-echolas:0.0.0 bash /opt/echolas/check-tools.sh /opt/echolas/envs
```

- [`docker/Dockerfile`](docker/Dockerfile) installs both locks on `mambaorg/micromamba:2.0.5-debian12-slim`, and the build fails if any tool is missing or at another version ([`docker/check-tools.sh`](docker/check-tools.sh)). The list is kept in the image at `/opt/echolas/tool-versions.tsv`.
- The tag is the package version. It is set in `manifest.yaml` (`workflow.container`, recorded in each run's provenance as `container`) and in the `docker` profile of `nextflow.config`.
- Each run's record (`run-<id>.json`) lists, under `software.tools`, the version of every tool its tasks used: from the conda environments with `standard`, or from the image's `/opt/echolas/tool-versions.tsv` (named by the label `org.xcalibur.tool-versions`) with `docker`, together with the image id and digests.
- To change a version, edit the `.yml` file and the matching `conda` directive, then run `docker/lock.sh` (needs micromamba) to regenerate the locks.
- Publishing: the image is meant for `ghcr.io/brunofomena/xcalibur-echolas:<version>`, pushed when a version is released. The CI (`container` job) builds it on every PR, checks the tools and runs the smoke test with `-profile test,docker`, but does not publish it yet; until then, build it locally with the command above.

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
