nextflow.enable.dsl=2

////////////////////////////////////////////////////
//// IMPORTS
////////////////////////////////////////////////////

include { TRIMMING_QC } from './modules/trimming_qc.nf'
include { ALIGNMENT_VARIANT; INDEX_REF } from './modules/alignment_variant.nf'
include { GENOTYPING } from './modules/genotyping.nf'
include { VARIANT_FILTERING } from './modules/variant_filtering.nf'
include { PCA_PHYLOGENY } from './modules/pca_phylogeny.nf'

////////////////////////////////////////////////////
//// PARAMS
////////////////////////////////////////////////////

// Defaults live in nextflow.config. raw_data and reference are directories; XcaliburAI
// passes absolute paths (the run executes inside its own run directory).
params.mode = params.mode ?: "AUTO"

////////////////////////////////////////////////////
//// WORKFLOW PRINCIPAL
////////////////////////////////////////////////////

workflow {

    ////////////////////////////////////////////////////
    // REFERÊNCIA (resolvida e indexada uma única vez)
    ////////////////////////////////////////////////////

    ref_files = file("${params.reference}/*.{fna,fa,fasta}", checkIfExists: false)
    if (ref_files.size() == 0) {
        error "No reference genome found in ${params.reference} (expected a .fna, .fa or .fasta file)"
    }
    if (ref_files.size() > 1) {
        error "Multiple reference genomes found in ${params.reference} (${ref_files*.name}) — keep only one .fna/.fa/.fasta file"
    }
    ref = Channel.value(ref_files[0])
    indexed_ref = INDEX_REF(ref)

    ////////////////////////////////////////////////////
    // INPUT
    ////////////////////////////////////////////////////

    // Sample and mate come from the file name (issue #63): <sample>.fastq.gz is single-end,
    // <sample>_R1/_R2 (or _1/_2, or Illumina's _R1_001) is a pair. The mate tag is read only
    // at the end of the name and in any case; the rest of the name, dots included, is the
    // sample. xcalibur_echolas/validation.py uses these same two patterns (tests compare).
    def FASTQ_NAME  = ~/(?i)^(.+)\.f(?:ast)?q\.gz$/
    def MATE_SUFFIX = ~/(?i)^(.+?)_(?:R([12])_\d{3}|R?([12]))$/

    reads = Channel
        .fromPath("${params.raw_data}/*")
        .filter { f -> f.name ==~ FASTQ_NAME }
        .map { f ->

            def stem = (f.name =~ FASTQ_NAME)[0][1]
            def tag  = stem =~ MATE_SUFFIX

            tag.matches()
                ? tuple(tag.group(1), "R" + (tag.group(2) ?: tag.group(3)), f)
                : tuple(stem, "SE", f)
        }

    ////////////////////////////////////////////////////
    // MÓDULO 1 — TRIMMING
    ////////////////////////////////////////////////////

    trimming_out = TRIMMING_QC(reads)

    ////////////////////////////////////////////////////
    // MÓDULO 2 — ALIGNMENT + GVCF
    ////////////////////////////////////////////////////

    alignment_out = ALIGNMENT_VARIANT(trimming_out.trimmed_reads, indexed_ref)

    ////////////////////////////////////////////////////
    // MÓDULO 3 — GENOTYPING
    ////////////////////////////////////////////////////

    genotyped = GENOTYPING(alignment_out.gvcfs, indexed_ref)

    ////////////////////////////////////////////////////
    // MÓDULO 4 - VARIANT_FILTERING
    ///////////////////////////////////////////////////

    variant_filtering = VARIANT_FILTERING(genotyped, indexed_ref)

    ////////////////////////////////////////////////////
    // MÓDULO 5 - PCA_PHYLOGENY
    ///////////////////////////////////////////////////

    pca_phylo = PCA_PHYLOGENY(
        variant_filtering.vcf_pca,
        variant_filtering.vcf_phylo,
        indexed_ref
    )

}