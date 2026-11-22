"""Deterministic synthetic dataset for the ECHOLAS end-to-end test.

Writes a small reference genome (3 contigs, 7.5 kb) and paired-end reads for six
samples in two clades, with known homozygous SNPs and indels inserted. Nothing here comes
from real sequencing data. The same seed always produces byte-identical files.

Usage:
    python tests/data/make_synthetic.py OUT_DIR

Layout of OUT_DIR:
    reference/synthetic.fa       reference genome
    raw_data/<S>_R1.fastq.gz     forward reads
    raw_data/<S>_R2.fastq.gz     reverse reads
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
COVERAGE = 40
ERROR_RATE = 0.002
# Phred+33 qualities drawn per base, mean about Q36: every sample takes the conservative
# trimming path. Values below Q31 ('?' and ':') must appear, otherwise FastQC guesses the
# old Phred+64 encoding (a file of 'I' alone reads as Q9).
QUALITIES = "IIIIIFFF?:"
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


def simulate_pairs(rng: random.Random, genome: dict[str, str]) -> list[tuple[str, str]]:
    pairs = []
    for seq in genome.values():
        n_pairs = len(seq) * COVERAGE // (2 * READ_LENGTH)
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


def _write_gz(path: Path, text: str) -> None:
    # mtime=0 and no file name in the header: the output depends on the content only.
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
        gz.write(text.encode("ascii"))


def _fastq(rng: random.Random, sample: str, reads: list[str], mate: int) -> str:
    records = []
    for i, read in enumerate(reads, 1):
        qual = "".join(rng.choices(QUALITIES, k=len(read)))
        records.append(f"@{sample}.{i} {mate}\n{read}\n+\n{qual}\n")
    return "".join(records)


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
        pairs = simulate_pairs(rng, apply_variants(reference, variants, sample))
        for mate in (1, 2):
            reads = [pair[mate - 1] for pair in pairs]
            fastq = _fastq(rng, sample, reads, mate)
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
