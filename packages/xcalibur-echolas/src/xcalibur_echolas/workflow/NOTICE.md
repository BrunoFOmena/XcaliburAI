# Third-party notice: ECHOLAS

The files in this directory (`main.nf`, `nextflow.config`, `modules/`, `scripts/`) are
adapted from **ECHOLAS**, https://github.com/MCamposDA/ECHOLAS, imported at commit
`cbd7884505ed34eae3d1dfc0db6a32d98ac059f3`, and are distributed under the MIT License below.

Changes made for XcaliburAI (no change to the scientific logic):

- `main.nf`: the input directories come from the `raw_data` and `reference` params
  instead of fixed `raw_data/` and `reference/` folders.
- `main.nf` and `modules/trimming_qc.nf` (#63): the sample and the mate come from a tag
  at the end of the file name only (`_R1`/`_R2`, `_1`/`_2`, `_R1_001`, any case), and the
  rest of the name, dots included, is the sample. `.fq.gz` and any-case extensions are
  read. `main.nf` passes the mate with each file, so `trimming_qc.nf` no longer re-reads
  it from the name. The original rule cut the name at the first `.`, removed every `_1`,
  `_2`, `_R1` and `_R2`, and looked for the mate anywhere in the name.
- `modules/trimming_qc.nf` (#81): the fastp paths (`TRIM_PE_INTERMEDIATE`, `TRIM_PE_AGGRESSIVE`, `TRIM_SE_BASIC`, `TRIM_SE_AGGRESSIVE`) trim with a Q15 sliding window (`--cut_tail`, `--cut_right`, and `--cut_front` on the aggressive paths) and then filter with fastp's defaults (`-q 15 -u 40`), instead of filtering only at `-q 25`/`-q 30`, which dropped whole low-quality reads. Length minimums and the other filters are unchanged.
- `nextflow.config`: a `params` block with the defaults (`mode`, `raw_data`, `reference`).
- `modules/pca_phylogeny.nf`: helper scripts are resolved from `${projectDir}/scripts/`,
  so the pipeline runs from any launch directory.
- Line endings normalized to LF (four files were CRLF); content otherwise unchanged.
- Not imported: the web dashboard, the FastAPI backend, Docker files, and
  `scripts/vcf_generate_fasta.sh` (superseded by `vcf_generate_fasta2.sh`, not used by the pipeline).

---

MIT License

Copyright (c) 2026 MCamposDA

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
