# Third-party notice: ECHOLAS

The files in this directory (`main.nf`, `nextflow.config`, `modules/`, `scripts/`) are
adapted from **ECHOLAS**, https://github.com/MCamposDA/ECHOLAS, imported at commit
`cbd7884505ed34eae3d1dfc0db6a32d98ac059f3`, and are distributed under the MIT License below.

Changes made for XcaliburAI (no change to the scientific logic):

- `main.nf`: the input directories come from the `raw_data` and `reference` params
  instead of fixed `raw_data/` and `reference/` folders.
- `nextflow.config`: a `params` block with the defaults (`mode`, `raw_data`, `reference`).
- `modules/pca_phylogeny.nf`: helper scripts are resolved from `${projectDir}/scripts/`,
  so the pipeline runs from any launch directory.
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
