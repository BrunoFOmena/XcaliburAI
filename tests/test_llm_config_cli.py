"""`xcalibur init` / `check-llm` and the secret-free configuration file."""

import pytest
import yaml

from xcalibur import cli
from xcalibur.config import ConfigLocation, load_llm_config, save_llm_config
from xcalibur.llm.base import ProviderConfig
from xcalibur.llm.errors import LLMAuthError, LLMConfigError, LLMError

SECRET = "sk-ant-test-SECRET-456"


def init(tmp_path, *extra):
    return cli.main(["init", "--non-interactive", "--workspace", str(tmp_path), *extra])


def test_init_non_interactive_saves_only_the_variable_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    assert init(tmp_path, "--model", "claude-opus-5-5") == 0
    text = (tmp_path / "xcalibur.yaml").read_text()
    assert SECRET not in text and SECRET not in capsys.readouterr().out
    llm = yaml.safe_load(text)["llm"]
    assert llm["provider"] == "anthropic" and llm["model"] == "claude-opus-5-5"
    assert llm["api_key_env"] == "ANTHROPIC_API_KEY"


def test_init_rejects_a_pasted_key(tmp_path, capsys):
    assert init(tmp_path, "--api-key-env", SECRET) == 2
    assert "NAME of an environment variable" in capsys.readouterr().err
    assert not (tmp_path / "xcalibur.yaml").exists()


def test_init_does_not_overwrite_without_force(tmp_path):
    assert init(tmp_path) == 0
    assert init(tmp_path, "--model", "other") == 2
    assert init(tmp_path, "--model", "other", "--force") == 0
    assert load_llm_config(tmp_path / "xcalibur.yaml").model == "other"


def test_init_interactive_uses_answers_and_defaults(tmp_path, monkeypatch):
    answers = iter(["", "claude-sonnet-5-5", "MY_KEY"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    path = tmp_path / "xcalibur.yaml"
    assert cli.main(["init", "--config", str(path)]) == 0
    config = load_llm_config(ConfigLocation(path, trusted=True))
    assert (config.provider, config.model, config.api_key_env) == (
        "anthropic",
        "claude-sonnet-5-5",
        "MY_KEY",
    )


def test_init_defaults_to_the_user_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli.main(["init", "--non-interactive"]) == 0
    assert (tmp_path / "cfg" / "xcalibur" / "xcalibur.yaml").exists()


def test_check_llm_without_key_names_the_variable_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XCAL_MISSING", raising=False)
    path = str(tmp_path / "c.yaml")
    args = ["--non-interactive", "--config", path, "--api-key-env", "XCAL_MISSING"]
    assert cli.main(["init", *args]) == 0
    assert cli.main(["check-llm", "--config", path]) == 2
    err = capsys.readouterr().err
    assert "XCAL_MISSING is not set" in err and "Traceback" not in err


def test_check_llm_reports_success_and_auth_failure(tmp_path, monkeypatch, capsys):
    assert init(tmp_path) == 0

    class Fake:
        def __init__(self, exc=None):
            self.exc = exc

        def complete(self, messages):
            if self.exc:
                raise self.exc
            return "OK"

    monkeypatch.setattr(cli, "create_provider", lambda config: Fake())
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 0
    assert "answered" in capsys.readouterr().out

    monkeypatch.setattr(cli, "create_provider", lambda c: Fake(LLMAuthError("rejected")))
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 3

    monkeypatch.setattr(cli, "create_provider", lambda c: Fake(LLMError("server error")))
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 4


def test_check_llm_without_configuration(tmp_path, capsys):
    assert cli.main(["check-llm", "--config", str(tmp_path / "none.yaml")]) == 2
    assert "xcalibur init" in capsys.readouterr().err


def test_config_round_trip_and_validation(tmp_path):
    path = tmp_path / "c.yaml"
    config = ProviderConfig(provider="anthropic", model="m", api_key_env="KEY_ENV", effort="low")
    assert load_llm_config(ConfigLocation(save_llm_config(config, path), trusted=True)) == config

    path.write_text("llm:\n  provider: anthropic\n  model: m\n  api_key: x\n")
    with pytest.raises(LLMConfigError, match="unknown llm settings api_key"):
        load_llm_config(path)

    with pytest.raises(LLMConfigError, match="NAME"):
        save_llm_config(ProviderConfig(provider="anthropic", model="m", api_key_env=SECRET), path)


WORKSPACE_WITH_BASE_URL = (
    "llm:\n  provider: anthropic\n  model: m\n  base_url: https://attacker.example\n"
)


def test_workspace_config_cannot_set_base_url(tmp_path, monkeypatch, capsys):
    (tmp_path / "xcalibur.yaml").write_text(WORKSPACE_WITH_BASE_URL)
    built = []
    monkeypatch.setattr(cli, "create_provider", lambda c: built.append(c))
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 2
    monkeypatch.chdir(tmp_path)  # ./xcalibur.yaml picked up implicitly
    assert cli.main(["check-llm"]) == 2
    assert "base_url" in capsys.readouterr().err and not built


def test_base_url_is_allowed_from_an_explicit_config(tmp_path):
    path = tmp_path / "xcalibur.yaml"
    path.write_text(WORKSPACE_WITH_BASE_URL)
    assert load_llm_config(ConfigLocation(path, trusted=True)).base_url
    with pytest.raises(LLMConfigError, match="base_url"):
        load_llm_config(path)  # a bare Path is untrusted


def test_workspace_config_cannot_choose_another_key_variable(tmp_path, monkeypatch, capsys):
    (tmp_path / "xcalibur.yaml").write_text(
        "llm:\n  provider: anthropic\n  model: m\n  api_key_env: GITHUB_TOKEN\n"
    )
    monkeypatch.setattr(cli, "create_provider", lambda c: pytest.fail("must not build"))
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert init(tmp_path, "--api-key-env", "GITHUB_TOKEN", "--force") == 2
    assert init(tmp_path, "--api-key-env", "ANTHROPIC_API_KEY", "--force") == 0


def test_init_refuses_base_url_in_a_workspace(tmp_path, capsys):
    assert init(tmp_path, "--base-url", "https://proxy.example") == 2
    assert "base_url" in capsys.readouterr().err
    assert not (tmp_path / "xcalibur.yaml").exists()


BASE = "llm:\n  provider: anthropic\n  model: m\n"


@pytest.mark.parametrize(
    "content, message",
    [
        ("llm: [", "not valid YAML"),
        ("- a\n- b\n", "no 'llm' section"),
        ("llm:\n  provider: anthropic\n", "missing llm settings model"),
        (BASE + "  max_tokens: abc\n", "'max_tokens' must be int"),
        (BASE + "  max_tokens: true\n", "'max_tokens' must be int"),
        (BASE + "  timeout: 0\n", "greater than zero"),
        (BASE + "  timeout: .nan\n", "finite"),
        (BASE + "  1: x\n", "setting names must be text"),
    ],
)
def test_invalid_config_is_a_clear_error_without_traceback(tmp_path, capsys, content, message):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err


def test_non_utf8_config_is_a_clear_error(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff\xfe\x00llm")
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert "UTF-8" in err and "Traceback" not in err


def test_init_reports_a_config_that_cannot_be_written(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert cli.main(["init", "--non-interactive", "--config", str(blocker / "c.yaml")]) == 2
    assert "Could not write" in capsys.readouterr().err
