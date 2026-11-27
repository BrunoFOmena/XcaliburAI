"""Deterministic synthetic dataset for the ECHOLAS end-to-end test.

Writes a small reference genome (3 contigs, 7.5 kb) and reads for six samples in two
clades, with known homozygous SNPs and indels inserted. Nothing here comes from real
sequencing data. The same seed always produces byte-identical files.

Each sample has its own read layout and quality profile (PROFILES), chosen so that the
AUTO trimming mode takes every path once (issue #77): paired-end conservative,
intermediate and aggressive, single-end basic and aggressive. One pair has a worse R2
than R1, as real runs often do, so the rule that combines the mates is exercised (#48).
One conservative pair also has short inserts that read into the adapter, so the e2e test
sees Trimmomatic keep both mates of those pairs (#92, #106).

Usage:
    python tests/data/make_synthetic.py OUT_DIR

Layout of OUT_DIR:
    reference/synthetic.fa       reference genome
    raw_data/<S>_R1.fastq.gz     forward reads (paired-end samples)
    raw_data/<S>_R2.fastq.gz     reverse reads (paired-end samples)
    raw_data/<S>.fastq.gz        reads of the single-end samples
    truth.tsv                    inserted variants (1-based positions, VCF-style alleles)

Only the standard library is used, so the CI can run it before installing anything.
"""

from __future__ import annotations

import argparse
import gzip
import random
from dataclasses import dataclass
from pathlib import Path

SEED = 20261122
CONTIGS = {"contig1": 3000, "contig2": 2500, "contig3": 2000}
CLADES = {"A": ["SA1", "SA2", "SA3"], "B": ["SB1", "SB2", "SB3"]}
SAMPLES = [s for members in CLADES.values() for s in members]

READ_LENGTH = 100
INSERT_MEAN = 300
INSERT_SD = 30
ERROR_RATE = 0.002
# Short inserts (#106): shorter than the read, so each read runs into the adapter. They
# stay above every path's minimum length (MINLEN/--length_required, at most 50).
SHORT_INSERT = (60, 90)
# What a read shows after a short insert: the TruSeq adapter, as in Trimmomatic's
# TruSeq3-PE.fa, then the rest of the Illumina adapter (a fixed index for i7).
ADAPTER_R1 = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCACACAGTGATCTCGTATGCCGTCTTCTGCTTG"
ADAPTER_R2 = "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGTAGATCTCGGTGGTCGCCGTATCATT"

# Phred+33 qualities, drawn per base from these pools. Each pool needs characters below
# Q31, otherwise FastQC guesses the old Phred+64 encoding (a file of 'I' alone reads as Q9).
# AUTO picks the path from the mean quality FastQC reports (modules/trimming_qc.nf),
# the lower of the two mates for a pair (#48): paired >= 30 conservative, 25-30
# intermediate, < 25 aggressive; single >= 28 basic, < 28 aggressive.
HIGH = "IIIIIFFF?:"  # mean ~Q36.6


@dataclass(frozen=True)
class Profile:
    layout: str  # "paired" or "single"
    qualities: str  # pool each base's quality is drawn from
    coverage: int
    trimming: str  # results/trimmed/<path> the pipeline must choose
    tail: int = 0  # the last `tail` bases of every read get `tail_quality` instead
    tail_quality: str = ""
    r2: Profile | None = None  # quality of the R2 reads, when it differs from R1's
    short_inserts: float = 0.0  # extra pairs with short inserts, as a share (paired only)

    def mate(self, number: int) -> Profile:
        """The profile whose qualities mate ``number`` (1 or 2) is written with."""
        return self.r2 if number == 2 and self.r2 is not None else self

    @property
    def mates(self) -> list[Profile]:
        return [self.mate(1), self.mate(2)] if self.layout == "paired" else [self]

    def auto_quality(self, length: int = 100) -> float:
        """The quality AUTO decides on: the lower mean of the two mates of a pair, the
        mean of the only file of a single-end sample (rule of modules/trimming_qc.nf, #48)."""
        return min(m.mean_quality(length) for m in self.mates)

    def read_qualities(self, rng: random.Random, length: int) -> str:
        head = max(0, length - self.tail)
        return "".join(rng.choices(self.qualities, k=head)) + self.tail_quality * (length - head)

    @property
    def characters(self) -> set[str]:
        chars = set(self.qualities) | set(self.tail_quality)
        return chars | self.r2.characters if self.r2 is not None else chars

    def mean_quality(self, length: int = 100) -> float:
        """The value AUTO reads: the mean of FastQC's per-base quality rows.

        FastQC 0.12.1 reports positions 1-9 one by one and then in pairs (10-11, ...), so
        for 100 bp reads there are 55 rows and the start of the read weighs more than
        the end. The pipeline averages those rows (FASTQC_PARSE in trimming_qc.nf).
        """
        head = sum(ord(c) - 33 for c in self.qualities) / len(self.qualities)
        tail = ord(self.tail_quality) - 33 if self.tail else 0.0

        def quality(position: int) -> float:  # 1-based
            return tail if position > length - self.tail else head

        rows = [[p] for p in range(1, min(9, length) + 1)]
        rows += [list(range(p, min(p + 2, length + 1))) for p in range(10, length + 1, 2)]
        return sum(sum(quality(p) for p in row) / len(row) for row in rows) / len(rows)

    def low_fraction(self, cut: int, length: int = 100) -> float:
        """Share of a read's bases under ``cut`` (fastp drops reads above 40%)."""
        head = sum(ord(c) - 33 < cut for c in self.qualities) / len(self.qualities)
        tail = 1.0 if self.tail and ord(self.tail_quality) - 33 < cut else 0.0
        return (head * (length - self.tail) + tail * self.tail) / length


# Lower-quality reads end in a low-quality tail, as real reads do: bad bases scattered
# over the whole read would also break GATK's local assembly (it skips bases under Q10).
# The tails give a low mean with few enough bad bases that fastp keeps the reads.
MID = {"qualities": "G", "tail": 35, "tail_quality": "+"}  # Q38 then Q10: mean Q28.8
LOW = {"qualities": "G", "tail": 38, "tail_quality": "!"}  # Q38 then Q0: mean Q24.5


# Low-quality samples get more coverage: fastp drops some reads, and HaplotypeCaller
# ignores bases under Q10.
PROFILES = {
    "SA1": Profile("paired", HIGH, 40, "pe_conservative"),
    # R1 ~Q36.6, R2 ~Q28.8: the lower mate decides (intermediate); their mean, Q32.7,
    # would be conservative, and before #48 the path depended on which FastQC ended first.
    "SA2": Profile("paired", HIGH, 50, "pe_intermediate",
                   r2=Profile("paired", coverage=50, trimming="pe_intermediate", **MID)),
    "SA3": Profile("paired", coverage=60, trimming="pe_aggressive", **LOW),
    # Plus 20% more pairs with 60-90 bp inserts, which read into the adapter: without
    # Trimmomatic's keepBothReads, the conservative path lost them (#92).
    "SB1": Profile("paired", HIGH, 40, "pe_conservative", short_inserts=0.2),
    "SB2": Profile("single", HIGH, 40, "se_basic"),
    "SB3": Profile("single", coverage=60, trimming="se_aggressive", **LOW),
}
MARGIN = 400  # no variant this close to a contig end (coverage and strand balance drop)
MIN_SPACING = 60  # nor this close to another variant

SNPS_PER_CLADE = 8
SNPS_SHARED = 3  # in every sample
SNPS_PRIVATE = 1  # per sample
INDELS = [("A", 2), ("B", 3)]  # (clade, deletion length)

_COMPLEMENT = str.maketrans("ACGT", "TGCA")


@dataclass(frozen=True)
class Variant:
    contig: str
    pos: int  # 1-based, VCF convention
    ref: str
    alt: str
    samples: tuple[str, ...]

    @property
    def kind(self) -> str:
        return "snp" if len(self.ref) == len(self.alt) == 1 else "indel"


def revcomp(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def make_reference(rng: random.Random) -> dict[str, str]:
    return {name: "".join(rng.choices("ACGT", k=size)) for name, size in CONTIGS.items()}


def _positions(rng: random.Random, n: int) -> list[tuple[str, int]]:
    """``n`` distinct, well spaced 0-based positions spread over the contigs."""
    taken: list[tuple[str, int]] = []
    names = list(CONTIGS)
    while len(taken) < n:
        contig = names[len(taken) % len(names)]
        pos = rng.randrange(MARGIN, CONTIGS[contig] - MARGIN)
        if all(c != contig or abs(p - pos) >= MIN_SPACING for c, p in taken):
            taken.append((contig, pos))
    return taken


def make_variants(rng: random.Random, reference: dict[str, str]) -> list[Variant]:
    groups: list[tuple[str, ...]] = [tuple(CLADES["A"])] * SNPS_PER_CLADE
    groups += [tuple(CLADES["B"])] * SNPS_PER_CLADE
    groups += [tuple(SAMPLES)] * SNPS_SHARED
    groups += [(s,) for s in SAMPLES for _ in range(SNPS_PRIVATE)]
    spots = _positions(rng, len(groups) + len(INDELS))

    variants = []
    for (contig, pos), samples in zip(spots, groups, strict=False):
        ref = reference[contig][pos]
        alt = rng.choice([b for b in "ACGT" if b != ref])
        variants.append(Variant(contig, pos + 1, ref, alt, samples))
    for (contig, pos), (clade, length) in zip(spots[len(groups) :], INDELS, strict=True):
        seq = reference[contig]
        # Move to a spot where the deletion cannot slide left or right, so its VCF
        # representation is unique and matches what a caller reports after left-alignment.
        while seq[pos] == seq[pos + length] or seq[pos + 1] == seq[pos + length + 1]:
            pos += 1
        # VCF deletion: REF is the anchor base plus the deleted bases, ALT the anchor.
        ref = seq[pos : pos + 1 + length]
        variants.append(Variant(contig, pos + 1, ref, ref[0], tuple(CLADES[clade])))
    return sorted(variants, key=lambda v: (list(CONTIGS).index(v.contig), v.pos))


def apply_variants(
    reference: dict[str, str], variants: list[Variant], sample: str
) -> dict[str, str]:
    """Haplotype of ``sample``: homozygous, so a single sequence per contig."""
    genome = {}
    for contig, seq in reference.items():
        here = [v for v in variants if v.contig == contig and sample in v.samples]
        out, cursor = [], 0
        for v in sorted(here, key=lambda v: v.pos):
            start = v.pos - 1
            out.append(seq[cursor:start])
            out.append(v.alt)
            cursor = start + len(v.ref)
        out.append(seq[cursor:])
        genome[contig] = "".join(out)
    return genome


def _mutate(rng: random.Random, read: str) -> str:
    bases = list(read)
    for i, base in enumerate(bases):
        if rng.random() < ERROR_RATE:
            bases[i] = rng.choice([b for b in "ACGT" if b != base])
    return "".join(bases)


def simulate_pairs(
    rng: random.Random, genome: dict[str, str], coverage: int
) -> list[tuple[str, str]]:
    pairs = []
    for seq in genome.values():
        n_pairs = len(seq) * coverage // (2 * READ_LENGTH)
        for _ in range(n_pairs):
            size = max(2 * READ_LENGTH, round(rng.gauss(INSERT_MEAN, INSERT_SD)))
            size = min(size, len(seq))
            start = rng.randrange(0, len(seq) - size + 1)
            fragment = seq[start : start + size]
            if rng.random() < 0.5:  # reads from both strands
                fragment = revcomp(fragment)
            r1 = _mutate(rng, fragment[:READ_LENGTH])
            r2 = _mutate(rng, revcomp(fragment)[:READ_LENGTH])
            pairs.append((r1, r2))
    return pairs


def short_insert_pairs(
    rng: random.Random, genome: dict[str, str], n_pairs: int
) -> list[tuple[str, str]]:
    """Pairs whose insert is shorter than the read: each read ends in its adapter."""
    contigs = list(genome.values())
    pairs = []
    for _ in range(n_pairs):
        seq = rng.choice(contigs)
        size = rng.randint(*SHORT_INSERT)
        start = rng.randrange(0, len(seq) - size + 1)
        fragment = seq[start : start + size]
        if rng.random() < 0.5:
            fragment = revcomp(fragment)
        r1 = _mutate(rng, (fragment + ADAPTER_R1)[:READ_LENGTH])
        r2 = _mutate(rng, (revcomp(fragment) + ADAPTER_R2)[:READ_LENGTH])
        pairs.append((r1, r2))
    return pairs


def _write_gz(path: Path, text: str) -> None:
    # mtime=0 and no file name in the header: the output depends on the content only.
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
        gz.write(text.encode("ascii"))


def _fastq(
    rng: random.Random, sample: str, reads: list[str], mate: int, profile: Profile,
    first: int = 1,
) -> str:
    records = []
    for i, read in enumerate(reads, first):
        qual = profile.read_qualities(rng, len(read))
        records.append(f"@{sample}.{i} {mate}\n{read}\n+\n{qual}\n")
    return "".join(records)


def fastq_names(sample: str) -> list[str]:
    """File names written for ``sample``, by its layout."""
    if PROFILES[sample].layout == "single":
        return [f"{sample}.fastq.gz"]
    return [f"{sample}_R1.fastq.gz", f"{sample}_R2.fastq.gz"]


def write_truth(path: Path, variants: list[Variant]) -> None:
    lines = ["#contig\tpos\tref\talt\ttype\tsamples"]
    for v in variants:
        lines.append(f"{v.contig}\t{v.pos}\t{v.ref}\t{v.alt}\t{v.kind}\t{','.join(v.samples)}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def read_truth(path: Path) -> list[Variant]:
    variants = []
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        contig, pos, ref, alt, _kind, samples = line.split("\t")
        variants.append(Variant(contig, int(pos), ref, alt, tuple(samples.split(","))))
    return variants


def generate(out: Path, seed: int = SEED) -> list[Variant]:
    rng = random.Random(seed)
    reference = make_reference(rng)
    variants = make_variants(rng, reference)

    (out / "reference").mkdir(parents=True, exist_ok=True)
    (out / "raw_data").mkdir(parents=True, exist_ok=True)
    fasta = "".join(
        f">{name}\n" + "\n".join(seq[i : i + 60] for i in range(0, len(seq), 60)) + "\n"
        for name, seq in reference.items()
    )
    (out / "reference" / "synthetic.fa").write_text(fasta, encoding="ascii")

    for sample in SAMPLES:
        profile = PROFILES[sample]
        genome = apply_variants(reference, variants, sample)
        pairs = simulate_pairs(rng, genome, profile.coverage)
        # Short-insert pairs use a generator of their own, for their bases and qualities,
        # so the other samples' reads stay the same.
        extra = random.Random(f"{seed}:{sample}:short-inserts")
        short = short_insert_pairs(extra, genome, round(len(pairs) * profile.short_inserts))
        if profile.layout == "single":
            # Both reads of each fragment, as independent single-end reads.
            reads = [read for pair in pairs for read in pair]
            fastq = _fastq(rng, sample, reads, 1, profile)
            _write_gz(out / "raw_data" / f"{sample}.fastq.gz", fastq)
            continue
        for mate in (1, 2):
            reads = [pair[mate - 1] for pair in pairs]
            fastq = _fastq(rng, sample, reads, mate, profile.mate(mate))
            fastq += _fastq(extra, sample, [pair[mate - 1] for pair in short], mate,
                            profile.mate(mate), first=len(reads) + 1)
            _write_gz(out / "raw_data" / f"{sample}_R{mate}.fastq.gz", fastq)

    write_truth(out / "truth.tsv", variants)
    return variants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="output directory")
    parser.add_argument("--seed", type=int, default=SEED, help=f"random seed (default {SEED})")
    args = parser.parse_args(argv)
    variants = generate(args.out, args.seed)
    print(f"wrote {len(SAMPLES)} samples and {len(variants)} variants to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
