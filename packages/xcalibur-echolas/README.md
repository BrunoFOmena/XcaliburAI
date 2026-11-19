# xcalibur-echolas

XcaliburAI domain package for Leishmania population genomics (ECHOLAS). Install it next to `xcalibur-ai` and the domain is discovered automatically.

The Nextflow pipeline in [`src/xcalibur_echolas/workflow`](src/xcalibur_echolas/workflow) is adapted from [MCamposDA/ECHOLAS](https://github.com/MCamposDA/ECHOLAS) and distributed under the MIT License; see [NOTICE.md](src/xcalibur_echolas/workflow/NOTICE.md). The rest of this package is AGPL-3.0-or-later, like the core.

Limitation: variant calling assumes a diploid organism (`HaplotypeCaller --ploidy 2`).
