# ECHOLAS workflow

Nextflow DSL2 pipeline for short-read population genomics, imported from
[MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS) (MIT). See [NOTICE.md](NOTICE.md)
for the license and the list of adaptations.

| Path | Contents |
|---|---|
| `main.nf` | Orchestrator: reference indexing, then the 5 modules |
| `modules/` | `trimming_qc`, `alignment_variant`, `genotyping`, `variant_filtering`, `pca_phylogeny` |
| `scripts/` | Helpers called by the modules (PCA, FASTA generation) and standalone downstream tools (diversity, Fst, clade divergence, report) that are not part of `main.nf` |
| `nextflow.config` | Parameter defaults, executor and resources |

Parameters: `--mode` (only `AUTO`), `--raw_data <dir>` (`*.fastq.gz` or `*.fq.gz`; pairs
named `<sample>_R1`/`_R2`, `_1`/`_2` or `_R1_001`/`_R2_001`, any case) and
`--reference <dir>` (exactly one `.fna`/`.fa`/`.fasta`). Outputs are written to `results/`
in the launch directory.

Limitation: variant calling assumes a diploid organism (`HaplotypeCaller --ploidy 2`).
