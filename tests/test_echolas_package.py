from xcalibur_echolas import plugin


def test_echolas_manifest_loads_and_resolves_defaults():
    p = plugin()
    assert p.name == "echolas"
    assert p.manifest.resolve_params({}) == {"mode": "AUTO"}
    assert {a.role for a in p.agents} == {"execution", "interpretation"}
