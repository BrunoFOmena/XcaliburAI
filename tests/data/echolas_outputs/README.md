# ECHOLAS output fixtures

Small outputs of one ECHOLAS run on the **synthetic** dataset of `tests/data/make_synthetic.py`
(6 samples in two clades; no real sequencing data). They let the result readers and the
interpreter agent (issue #9) be tested without running the pipeline.

| File | Pipeline path |
|---|---|
| `strain_PASS.vcf.gz` | `results/filtering/final/` |
| `strain_snps_Fpca.vcf.gz` | `results/filtering/pca/` |
| `strain_snps_Fphylo.vcf.gz` | `results/filtering/phylo/` |
| `strain.eigenval`, `strain.eigenvec` | `results/pca/pca_output/` |
| `strain_tree.treefile` | `results/phylogeny/tree/phylogeny/` |

To refresh them, run `pytest -m e2e` with `XCALIBUR_E2E_WORKDIR` set and copy the files
from `<workdir>/workspace/runs/smoke/`.
