#!/usr/bin/env bash
# Check that every tool pinned in the ECHOLAS environment files runs and reports the
# pinned version. Prints "tool<TAB>pinned<TAB>reported" and exits 1 if anything is off.
#
#   check-tools.sh [ENV_DIR]     (default: the envs folder of the package)
#
# PLINK and R come from the echolas-pca environment, as in the pipeline's RUN_PCA step
# (ECHOLAS_PCA_BIN, default /opt/conda/envs/echolas-pca/bin); the rest from PATH.
set -uo pipefail

env_dir=${1:-"$(dirname "$0")/../src/xcalibur_echolas/workflow/envs"}
pca_bin=${ECHOLAS_PCA_BIN:-/opt/conda/envs/echolas-pca/bin}

# The command that prints each tool's version (stdout and stderr are both read).
version_of() {
    case "$1" in
        fastqc)      fastqc --version ;;
        trimmomatic) trimmomatic -version ;;
        fastp)       fastp --version ;;
        bwa)         bwa 2>&1 | grep -i '^version' ;;
        samtools)    samtools --version | head -n 1 ;;
        bcftools)    bcftools --version | head -n 1 ;;
        gatk4)       gatk --version 2>/dev/null | grep 'Genome Analysis Toolkit' ;;
        picard)      picard MarkDuplicates --version 2>&1 | grep -o 'Version:.*' ;;
        iqtree)      iqtree2 --version | head -n 1 ;;
        plink)       plink --version ;;
        r-base)      Rscript -e 'cat(as.character(getRversion()))' ;;
        r-ggplot2)   Rscript -e 'cat(as.character(packageVersion("ggplot2")))' ;;
        r-readr)     Rscript -e 'cat(as.character(packageVersion("readr")))' ;;
        r-dplyr)     Rscript -e 'cat(as.character(packageVersion("dplyr")))' ;;
        unzip)       unzip -v | head -n 1 ;;
        coreutils)   ls --version | head -n 1 ;;
        *)           return 2 ;;
    esac
}

# How a pinned version is written by the tool, when it differs from the conda version.
reported_as() {
    case "$1=$2" in
        plink=1.9.0b.7.7) echo "v1.9.0-b.7.7" ;;
        unzip=6.0)        echo "UnZip 6.00" ;;
        *)                echo "$2" ;;
    esac
}

status=0
pins=$(grep -hE '^\s*-\s*[A-Za-z0-9_.-]+=[0-9]' "$env_dir"/*.yml | sed -E 's/^\s*-\s*//')
[ -n "$pins" ] || { echo "no pinned packages found in $env_dir" >&2; exit 1; }
while IFS='=' read -r name version; do
    case "$name" in
        plink|r-*) output=$(PATH="$pca_bin:$PATH" version_of "$name" 2>&1) ;;
        *)         output=$(version_of "$name" 2>&1) ;;
    esac
    code=$?
    if [ "$code" = 2 ]; then
        printf '%s\t%s\t%s\n' "$name" "$version" "NO CHECK DEFINED"; status=1; continue
    fi
    line=$(printf '%s' "$output" | tr '\n' ' ' | sed -E 's/\s+/ /g; s/ $//')
    expected=$(reported_as "$name" "$version")
    if [[ "$line" == *"$expected"* ]]; then
        printf '%s\t%s\t%s\n' "$name" "$version" "$line"
    else
        printf '%s\t%s\t%s\n' "$name" "$version" "MISMATCH: ${line:-no output}"; status=1
    fi
done <<< "$pins"
exit $status
