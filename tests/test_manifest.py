import pytest

from xcalibur.manifest import DomainManifest, ManifestError


def make(**overrides):
    base = {
        "name": "demo",
        "display_name": "Demo",
        "version": "0.0.0",
        "workflow": {"entrypoint": "main.nf"},
        "params": [
            {"name": "threads", "type": "integer", "default": 2, "minimum": 1, "maximum": 8},
            {"name": "mode", "type": "enum", "choices": ["a", "b"], "default": "a"},
            {"name": "label", "type": "string", "required": True},
        ],
    }
    base.update(overrides)
    return DomainManifest.model_validate(base)


def test_defaults_and_coercion():
    params = make().resolve_params({"label": "x", "threads": "4"})
    assert params == {"threads": 4, "mode": "a", "label": "x"}


def test_missing_required():
    with pytest.raises(ManifestError, match="missing required"):
        make().resolve_params({})


def test_unknown_param_rejected():
    with pytest.raises(ManifestError, match="unknown parameters"):
        make().resolve_params({"label": "x", "bogus": 1})


def test_range_and_enum_checked():
    with pytest.raises(ManifestError, match="maximum"):
        make().resolve_params({"label": "x", "threads": 99})
    with pytest.raises(ManifestError, match="not in"):
        make().resolve_params({"label": "x", "mode": "z"})


def test_duplicate_names_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        make(params=[{"name": "a"}, {"name": "a"}])


def test_unsupported_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        make(schema_version=99)
