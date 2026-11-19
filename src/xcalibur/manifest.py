"""Domain manifest: the contract between a domain package and the core.

A manifest declares what a workflow needs (inputs), what it accepts (params),
and what it produces (outputs). The core uses it to validate a request *before*
anything runs, so agents cannot silently pass unsafe or malformed parameters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1

ParamType = Literal["string", "integer", "number", "boolean", "path", "enum"]


class ManifestError(ValueError):
    """Raised when a manifest is malformed or a parameter set is invalid."""


class ParamSpec(BaseModel):
    name: str
    type: ParamType = "string"
    description: str = ""
    default: Any = None
    required: bool = False
    choices: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _check_enum(self) -> ParamSpec:
        if self.type == "enum" and not self.choices:
            raise ValueError(f"param '{self.name}': enum requires 'choices'")
        return self


class InputSpec(BaseModel):
    name: str
    kind: str = Field(description="Data kind, e.g. fastq, fasta, vcf, directory.")
    description: str = ""
    required: bool = True
    multiple: bool = False
    pattern: str | None = Field(default=None, description="Glob the engine expects.")


class OutputSpec(BaseModel):
    name: str
    kind: str
    pattern: str = Field(description="Glob relative to the run directory.")
    description: str = ""


class WorkflowSpec(BaseModel):
    engine: Literal["nextflow"] = "nextflow"
    entrypoint: str = Field(description="Path to the workflow, relative to the package.")
    profile: str | None = None
    container: str | None = Field(default=None, description="Container image reference.")


class DomainManifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    display_name: str
    version: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    workflow: WorkflowSpec
    inputs: list[InputSpec] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> DomainManifest:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version} (expected {SCHEMA_VERSION})"
            )
        for label, items in (("param", self.params), ("input", self.inputs)):
            names = [i.name for i in items]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"duplicate {label} names: {sorted(dupes)}")
        return self

    def resolve_params(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply defaults and validate ``values``; return the final parameter set.

        Unknown parameters are rejected rather than ignored.
        """
        values = dict(values or {})
        known = {p.name: p for p in self.params}
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise ManifestError(f"unknown parameters for '{self.name}': {unknown}")

        resolved: dict[str, Any] = {}
        for name, spec in known.items():
            if name in values:
                resolved[name] = _coerce(spec, values[name])
            elif spec.default is not None:
                resolved[name] = spec.default
            elif spec.required:
                raise ManifestError(f"missing required parameter '{name}'")
        return resolved


def _coerce(spec: ParamSpec, raw: Any) -> Any:
    try:
        if spec.type == "integer":
            value: Any = int(raw)
        elif spec.type == "number":
            value = float(raw)
        elif spec.type == "boolean":
            value = _to_bool(raw)
        else:
            value = str(raw)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"parameter '{spec.name}': cannot read {raw!r} as {spec.type}") from exc

    if spec.type == "enum" and value not in (spec.choices or []):
        raise ManifestError(f"parameter '{spec.name}': {value!r} not in {spec.choices}")
    if spec.type in ("integer", "number"):
        if spec.minimum is not None and value < spec.minimum:
            raise ManifestError(f"parameter '{spec.name}': {value} < minimum {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ManifestError(f"parameter '{spec.name}': {value} > maximum {spec.maximum}")
    return value


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError(raw)


def load_manifest(path: str | Path) -> DomainManifest:
    """Read and validate a ``manifest.yaml``."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: manifest must be a mapping")
    try:
        return DomainManifest.model_validate(data)
    except ValueError as exc:
        raise ManifestError(f"{path}: {exc}") from exc
