"""Readers for the ECHOLAS outputs: VCF, PCA (PLINK) and tree (IQ-TREE) (issue #9).

Every number the interpreter agent shows comes from these functions, never from the
model. They use the standard library only, stream large files, and never write.

* VCF: plain or gzip/bgzip text; counts samples, variant types and genotypes.
* PCA: PLINK ``.eigenval`` (one eigenvalue per line) and ``.eigenvec``
  (``FID IID PC1 PC2 ...``).
* Tree: Newick as IQ-TREE writes it, with ``SH-aLRT/UFBoot`` support on internal nodes.
"""

from __future__ import annotations

import gzip
import io
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# IQ-TREE's guidance: a split is reliable when SH-aLRT >= 80 % and UFBoot >= 95 %.
SH_ALRT_MIN = 80.0
UFBOOT_MIN = 95.0
MAX_LISTED = 50
"""Longest list returned for per-contig, per-sample and per-split details."""


class ResultsError(ValueError):
    """An output file is missing, unreadable or not in the expected format."""


# -- VCF ----------------------------------------------------------------------------------


def summarize_vcf(path: Path) -> dict[str, Any]:
    """Samples, variants by type and filter, per-contig counts and per-sample genotypes."""
    try:
        scan = _scan_vcf(path)
    except (OSError, EOFError) as exc:  # includes a truncated or corrupt gzip stream
        raise ResultsError(f"Cannot read {path}: {exc}") from None
    samples, records = scan["samples"], scan["records"]
    if not samples and records == 0:
        raise ResultsError(f"{path}: no #CHROM header and no records; is it a VCF?")

    sample_rows = [
        {
            "sample": name,
            "hom_ref": c["hom_ref"],
            "het": c["het"],
            "hom_alt": c["hom_alt"],
            "missing": c["missing"],
            "missing_percent": _percent(c["missing"], records),
        }
        for name, c in zip(samples, scan["per_sample"], strict=True)
    ]
    contig_rows = [{"contig": k, "variants": v} for k, v in scan["contigs"].most_common()]
    return {
        "file": str(path),
        "sample_count": len(samples),
        "variant_count": records,
        "by_type": dict(sorted(scan["types"].items())),
        "by_filter": dict(sorted(scan["filters"].items())),
        "multiallelic": scan["multiallelic"],
        "contig_count": len(contig_rows),
        "contigs": contig_rows[:MAX_LISTED],
        "contigs_not_listed": max(0, len(contig_rows) - MAX_LISTED),
        "samples": sample_rows[:MAX_LISTED],
        "samples_not_listed": max(0, len(sample_rows) - MAX_LISTED),
    }


def _scan_vcf(path: Path) -> dict[str, Any]:
    samples: list[str] = []
    per_sample: list[Counter[str]] = []
    types: Counter[str] = Counter()
    filters: Counter[str] = Counter()
    contigs: Counter[str] = Counter()
    multiallelic = records = 0
    with _open_text(path) as handle:
        for line_no, line in enumerate(handle, 1):
            if line.startswith("##"):
                continue
            fields = line.rstrip("\n").split("\t")
            if line.startswith("#CHROM"):
                samples = fields[9:]
                per_sample = [Counter() for _ in samples]
                continue
            if not line.strip():
                continue
            if len(fields) < 8:
                raise ResultsError(f"{path}: line {line_no} is not a VCF record")
            records += 1
            contigs[fields[0]] += 1
            filters[fields[6]] += 1
            alts = [a for a in fields[4].split(",") if a not in (".", "*")]
            if len(alts) > 1:
                multiallelic += 1
            types[_variant_type(fields[3], alts)] += 1
            if len(fields) > 9 and per_sample:
                keys = fields[8].split(":")
                gt_at = keys.index("GT") if "GT" in keys else None
                for counter, value in zip(per_sample, fields[9:], strict=False):
                    parts = value.split(":")
                    gt = parts[gt_at] if gt_at is not None and gt_at < len(parts) else "."
                    counter[_genotype_class(gt)] += 1
    return {"samples": samples, "per_sample": per_sample, "types": types,
            "filters": filters, "contigs": contigs, "multiallelic": multiallelic,
            "records": records}


def _variant_type(ref: str, alts: list[str]) -> str:
    if not alts:
        return "no_alt"
    kinds = set()
    for alt in alts:
        if alt.startswith("<") or "[" in alt or "]" in alt:
            kinds.add("symbolic")
        elif len(ref) == len(alt) == 1:
            kinds.add("snp")
        elif len(ref) == len(alt):
            kinds.add("mnp")
        else:
            kinds.add("indel")
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _genotype_class(gt: str) -> str:
    alleles = gt.replace("|", "/").split("/")
    if not alleles or any(a in (".", "") for a in alleles):
        return "missing"
    if all(a == "0" for a in alleles):
        return "hom_ref"
    if len(set(alleles)) == 1:
        return "hom_alt"
    return "het"


# -- PCA ----------------------------------------------------------------------------------


def read_pca(eigenval: Path, eigenvec: Path) -> dict[str, Any]:
    """Eigenvalues, their share among the computed PCs and PC1/PC2 per sample."""
    values = []
    for line_no, line in enumerate(_read_lines(eigenval), 1):
        if line.strip():
            values.append(_float(line.strip(), eigenval, line_no))
    if not values:
        raise ResultsError(f"{eigenval}: no eigenvalues")
    # Tiny negative values are numerical noise of the decomposition, not variance.
    clipped = [max(v, 0.0) for v in values]
    total = sum(clipped)

    coordinates = []
    for line_no, line in enumerate(_read_lines(eigenvec), 1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 3:
            raise ResultsError(f"{eigenvec}: line {line_no} needs FID, IID and PCs")
        pcs = [_float(p, eigenvec, line_no) for p in parts[2:]]
        coordinates.append({
            "sample": parts[1],
            "PC1": round(pcs[0], 4),
            "PC2": round(pcs[1], 4) if len(pcs) > 1 else None,
        })
    return {
        "files": [str(eigenval), str(eigenvec)],
        "pcs_computed": len(values),
        "sample_count": len(coordinates),
        "eigenvalues": [round(v, 4) for v in values],
        "percent_of_computed": [_percent(v, total) for v in clipped],
        "scope_note": (
            "Percentages are each PC's share of the PCs PLINK computed (--pca 10), not of "
            "the total genetic variance, unless every non-zero PC was computed "
            "(at most samples - 1)."
        ),
        "all_pcs_computed": len(values) >= max(len(coordinates) - 1, 1),
        "coordinates": coordinates[:MAX_LISTED * 4],
        "samples_not_listed": max(0, len(coordinates) - MAX_LISTED * 4),
    }


# -- tree ---------------------------------------------------------------------------------


@dataclass
class Node:
    name: str = ""
    length: float | None = None
    label: str = ""
    children: list[Node] = field(default_factory=list)

    def leaves(self) -> list[str]:
        if not self.children:
            return [self.name]
        return [leaf for child in self.children for leaf in child.leaves()]


def parse_newick(text: str) -> Node:
    """Parse one Newick tree (quoted names, comments in [] and branch lengths)."""
    parser = _Newick(text.strip())
    root = parser.node()
    parser.skip()
    if parser.peek() != ";":
        raise ResultsError(f"Newick: expected ';' at position {parser.pos}")
    parser.pos += 1
    parser.skip()
    if parser.pos != len(parser.text):
        raise ResultsError("Newick: text after the end of the tree (one tree per file)")
    return root


def summarize_tree(path: Path) -> dict[str, Any]:
    """Leaves, splits (bipartitions) with SH-aLRT/UFBoot support and branch lengths."""
    text = "".join(_read_lines(path))
    try:
        root = parse_newick(text)
    except (ResultsError, IndexError, ValueError) as exc:
        raise ResultsError(f"{path}: not a Newick tree ({exc})") from None
    leaves = sorted(root.leaves())
    if len(set(leaves)) != len(leaves):
        raise ResultsError(f"{path}: repeated leaf names")
    splits = []
    for node in _all_nodes(root):
        if not node.children or node is root:
            continue
        side = set(node.leaves())
        other = set(leaves) - side
        if len(side) < 2 or len(other) < 2:
            continue  # a leaf edge, or the root's own edge: not an informative split
        shown = min((sorted(side), sorted(other)), key=lambda s: (len(s), s))
        sh_alrt, ufboot = _support(node.label)
        splits.append({
            "taxa": shown,
            "sh_alrt": sh_alrt,
            "ufboot": ufboot,
            "supported": (sh_alrt is not None and ufboot is not None
                          and sh_alrt >= SH_ALRT_MIN and ufboot >= UFBOOT_MIN),
            "branch_length": node.length,
        })
    total_length = sum(n.length for n in _all_nodes(root) if n.length is not None)
    splits.sort(key=lambda s: (-(s["ufboot"] or 0), len(s["taxa"]), s["taxa"]))
    supported = sum(1 for s in splits if s["supported"])
    return {
        "file": str(path),
        "leaf_count": len(leaves),
        "leaves": leaves[:MAX_LISTED * 4],
        "leaves_not_listed": max(0, len(leaves) - MAX_LISTED * 4),
        "split_count": len(splits),
        "supported_splits": supported,
        "unsupported_splits": len(splits) - supported,
        # Numbers, so that an answer may quote them (text does not ground numbers, #68).
        "support_thresholds": {"sh_alrt": SH_ALRT_MIN, "ufboot": UFBOOT_MIN},
        "support_rule": (
            f"A split is supported when SH-aLRT >= {SH_ALRT_MIN:g} and "
            f"UFBoot >= {UFBOOT_MIN:g} (IQ-TREE guidance). Splits are bipartitions of an "
            "unrooted tree; each is listed by its smaller side."
        ),
        "total_tree_length": round(total_length, 4),
        "splits": splits[:MAX_LISTED],
        "splits_not_listed": max(0, len(splits) - MAX_LISTED),
    }


def _all_nodes(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def _support(label: str) -> tuple[float | None, float | None]:
    """IQ-TREE labels internal nodes ``SH-aLRT/UFBoot``; a single value is UFBoot."""
    parts = [p for p in label.split("/") if p]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None, None
    if len(values) == 2:
        return values[0], values[1]
    if len(values) == 1:
        return None, values[0]
    return None, None


class _Newick:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip(self) -> None:
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch.isspace():
                self.pos += 1
            elif ch == "[":
                end = self.text.find("]", self.pos)
                if end < 0:
                    raise ResultsError("Newick: unclosed comment")
                self.pos = end + 1
            else:
                return

    def node(self) -> Node:
        self.skip()
        node = Node()
        if self.peek() == "(":
            self.pos += 1
            node.children.append(self.node())
            self.skip()
            while self.peek() == ",":
                self.pos += 1
                node.children.append(self.node())
                self.skip()
            if self.peek() != ")":
                raise ResultsError(f"Newick: expected ')' at position {self.pos}")
            self.pos += 1
            node.label = self.word()
        else:
            node.name = self.word()
            if not node.name:
                raise ResultsError(f"Newick: empty leaf name at position {self.pos}")
        self.skip()
        if self.peek() == ":":
            self.pos += 1
            node.length = float(self.word())
        return node

    def word(self) -> str:
        self.skip()
        if self.peek() == "'":
            out = []
            self.pos += 1
            while self.pos < len(self.text):
                ch = self.text[self.pos]
                self.pos += 1
                if ch == "'":
                    if self.peek() == "'":  # '' is an escaped quote
                        out.append("'")
                        self.pos += 1
                        continue
                    return "".join(out)
                out.append(ch)
            raise ResultsError("Newick: unclosed quote")
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in "(),:;[" \
                and not self.text[self.pos].isspace():
            self.pos += 1
        return self.text[start:self.pos]


# -- helpers ------------------------------------------------------------------------------


def _open_text(path: Path) -> io.TextIOBase:
    try:
        with path.open("rb") as raw:
            magic = raw.read(2)
        if magic == b"\x1f\x8b":
            return io.TextIOWrapper(gzip.open(path), encoding="utf-8", errors="replace")
        return path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ResultsError(f"Cannot read {path}: {exc.strerror or exc}") from None


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ResultsError(f"Cannot read {path}: {exc.strerror or exc}") from None


def _float(text: str, path: Path, line_no: int) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ResultsError(f"{path}: line {line_no}: {text!r} is not a number") from None
    if not math.isfinite(value):
        raise ResultsError(f"{path}: line {line_no}: {text!r} is not finite")
    return value


def _percent(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0
