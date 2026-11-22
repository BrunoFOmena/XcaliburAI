"""The synthetic dataset of the end-to-end test is deterministic and matches its truth."""

import gzip
import hashlib
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
sys.path.insert(0, str(DATA_DIR))
import make_synthetic as synth  # noqa: E402


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_same_seed_gives_byte_identical_files(tmp_path):
    synth.generate(tmp_path / "a")
    synth.generate(tmp_path / "b")
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_truth_matches_the_committed_copy(tmp_path):
    synth.generate(tmp_path)
    assert (tmp_path / "truth.tsv").read_text() == (DATA_DIR / "synthetic_truth.tsv").read_text()


def test_layout_and_variants(tmp_path):
    variants = synth.generate(tmp_path)
    fasta = (tmp_path / "reference" / "synthetic.fa").read_text()
    assert [line[1:] for line in fasta.splitlines() if line.startswith(">")] == list(synth.CONTIGS)
    for sample in synth.SAMPLES:
        for mate in (1, 2):
            path = tmp_path / "raw_data" / f"{sample}_R{mate}.fastq.gz"
            with gzip.open(path, "rt") as fh:
                header, seq, plus, qual = (next(fh).strip() for _ in range(4))
            assert header.startswith(f"@{sample}.1") and plus == "+"
            assert len(seq) == len(qual) == synth.READ_LENGTH
            assert set(qual) <= set(synth.QUALITIES)
    assert synth.read_truth(tmp_path / "truth.tsv") == variants
    # Every sample differs from every other, so PCA and the tree have signal.
    profiles = {s: {v for v in variants if s in v.samples} for s in synth.SAMPLES}
    assert len({frozenset(p) for p in profiles.values()}) == len(synth.SAMPLES)
    assert {v.kind for v in variants} == {"snp", "indel"}


def test_sample_alleles_are_applied(tmp_path):
    rng = synth.random.Random(1)
    reference = synth.make_reference(rng)
    variants = synth.make_variants(rng, reference)
    for sample in synth.SAMPLES:
        genome = synth.apply_variants(reference, variants, sample)
        shift = {c: 0 for c in reference}
        for v in variants:
            if sample not in v.samples:
                continue
            start = v.pos - 1 + shift[v.contig]
            assert genome[v.contig][start : start + len(v.alt)] == v.alt
            shift[v.contig] += len(v.alt) - len(v.ref)
