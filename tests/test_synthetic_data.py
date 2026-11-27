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
    written = sorted(p.name for p in (tmp_path / "raw_data").iterdir())
    assert written == sorted(n for s in synth.SAMPLES for n in synth.fastq_names(s))
    for sample in synth.SAMPLES:
        for name in synth.fastq_names(sample):
            with gzip.open(tmp_path / "raw_data" / name, "rt") as fh:
                header, seq, plus, qual = (next(fh).strip() for _ in range(4))
            assert header.startswith(f"@{sample}.1") and plus == "+"
            assert len(seq) == len(qual) == synth.READ_LENGTH
            assert set(qual) <= synth.PROFILES[sample].characters
    # SA2's R2 is written with its own, lower, quality profile (#48).
    firsts = {}
    for mate in (1, 2):
        with gzip.open(tmp_path / "raw_data" / f"SA2_R{mate}.fastq.gz", "rt") as fh:
            firsts[mate] = [next(fh).strip() for _ in range(4)][3]
    r2 = synth.PROFILES["SA2"].mate(2)
    assert r2.tail > 0 and r2.tail_quality not in firsts[1]
    assert firsts[2].endswith(r2.tail_quality * r2.tail)
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


def auto_path(layout: str, q: float) -> str:
    """The AUTO thresholds of modules/trimming_qc.nf."""
    if layout == "paired":
        return "pe_conservative" if q >= 30 else "pe_intermediate" if q >= 25 else "pe_aggressive"
    return "se_basic" if q >= 28 else "se_aggressive"


def test_profiles_send_each_sample_down_its_trimming_path():
    """The AUTO thresholds, applied to the quality AUTO decides on (lower mate of a pair)."""
    paths = []
    for sample, profile in synth.PROFILES.items():
        q = profile.auto_quality(synth.READ_LENGTH)
        expected = auto_path(profile.layout, q)
        assert profile.trimming == expected, (sample, q)
        paths.append(profile.trimming)
        # FastQC guesses Phred+64 when no character is below Q31.
        assert min(ord(c) - 33 for c in profile.characters) < 31
    assert sorted(set(paths)) == ["pe_aggressive", "pe_conservative", "pe_intermediate",
                                  "se_aggressive", "se_basic"]


def test_one_pair_has_mates_that_only_the_lower_mate_rule_classifies_right():
    """#48: a pair whose R1 and R2 fall on different paths, so taking either mate alone,
    or their mean, would pick another path than the rule of the pipeline."""
    profile = synth.PROFILES["SA2"]
    r1, r2 = (m.mean_quality(synth.READ_LENGTH) for m in profile.mates)
    assert r1 > 36 and 25 <= r2 < 30  # the issue's example: R1 ~Q36, R2 ~Q27
    assert auto_path("paired", r1) == "pe_conservative"
    assert auto_path("paired", (r1 + r2) / 2) == "pe_conservative"
    assert auto_path("paired", min(r1, r2)) == profile.trimming == "pe_intermediate"


def test_low_quality_tails_keep_reads_through_fastp():
    """fastp drops a read with more than 40% of bases under its quality cut, so the
    bad tail must stay under that, at the cut of the path the sample takes."""
    cuts = {"pe_intermediate": 25, "pe_aggressive": 30, "se_aggressive": 30}
    for sample, profile in synth.PROFILES.items():
        if profile.trimming in cuts:
            for mate in profile.mates:
                assert mate.low_fraction(cuts[profile.trimming], synth.READ_LENGTH) < 0.4, sample
