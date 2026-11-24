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
    err = capsys.readouterr().err
    assert "NAME of an environment variable" in err and SECRET not in err
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
        ("llm: [", "invalid YAML"),
        ("- a\n- b\n", "no 'llm' section"),
        ("llm:\n  provider: anthropic\n", "missing llm settings model"),
        (BASE + "  max_tokens: abc\n", "'max_tokens' must be int"),
        (BASE + "  max_tokens: true\n", "'max_tokens' must be int"),
        (BASE + "  timeout: 0\n", "greater than zero"),
        (BASE + "  timeout: .nan\n", "finite"),
        (BASE + "  timeout: " + "9" * 400 + "\n", "'timeout' must be a finite number"),
        (BASE + "  timeout: 10000000000\n", "at most 86400 seconds"),
        (BASE + "  max_tokens: " + "9" * 5000 + "\n", "invalid YAML (Exceeds the limit"),
        (BASE + "  timeout: 2024-13-45\n", "invalid YAML (month must be in 1..12"),
        (BASE + "  refusal_fallback: " + "9" * 200 + "\n", "got 999"),
        (BASE + "  1: x\n", "setting names must be text"),
    ],
)
def test_invalid_config_is_a_clear_error_without_traceback(tmp_path, capsys, content, message):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err and "set_int_max_str_digits" not in err


@pytest.mark.parametrize(
    "content, where, problem",
    [
        ("llm: [", "1:7", "expected the node content, but found '<stream end>'"),
        ("llm:\n  a: 1\n b: 2\n", "3:2", "expected <block end>, but found '<block mapping start>'"),
    ],
)
def test_invalid_yaml_is_one_line_with_line_and_column(tmp_path, capsys, content, where, problem):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert err == f"error: {path}:{where}: invalid YAML ({problem})\n"


@pytest.mark.parametrize(
    "content, where",
    [
        ("llm:\n  model: a\x07\n", "2:11"),
        ("llm:\r\n  model: a\x07\r\n", "2:11"),
        ("\ufeffllm: \x07\n", "1:6"),
        ("llm:\u2028  model: a\x07\n", "2:11"),
        ("llm:\x85\u2029  model: a\x07\n", "3:11"),
    ],
)
def test_disallowed_character_reports_line_and_column(tmp_path, capsys, content, where):
    path = tmp_path / "bad.yaml"
    path.write_bytes(content.encode("utf-8"))
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    err = capsys.readouterr().err
    assert err == (
        f"error: {path}:{where}: invalid YAML "
        "(unacceptable character #x0007: special characters are not allowed)\n"
    )


def test_yaml_error_without_position_is_one_line(tmp_path, capsys, monkeypatch):
    def fail(_text):
        raise yaml.YAMLError("first line\n  second line")

    monkeypatch.setattr(yaml, "safe_load", fail)
    path = tmp_path / "bad.yaml"
    path.write_text("llm: {}\n")
    assert cli.main(["check-llm", "--config", str(path)]) == 2
    assert capsys.readouterr().err == f"error: {path}: invalid YAML (first line second line)\n"


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


# -- #40: where the configuration came from, workspace limits, validation edges ------------


def _fake_ok(monkeypatch):
    class Fake:
        def complete(self, messages):
            return "OK"

    monkeypatch.setattr(cli, "create_provider", lambda config: Fake())


def test_check_llm_names_the_file_and_whether_it_is_a_workspace_file(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _fake_ok(monkeypatch)
    assert cli.main(["init", "--non-interactive", "--effort", "low"]) == 0
    user = tmp_path / "cfg" / "xcalibur" / "xcalibur.yaml"
    capsys.readouterr()
    assert cli.main(["check-llm"]) == 0
    out = capsys.readouterr().out
    assert f"config: {user} (user configuration)" in out
    assert "model claude-opus-5-5, effort low, max_tokens 16000, max_retries 2" in out

    assert cli.main(["check-llm", "--config", str(user)]) == 0
    assert f"config: {user} (user configuration)" in capsys.readouterr().out
    other = tmp_path / "other.yaml"
    other.write_text(BASE)
    assert cli.main(["check-llm", "--config", str(other)]) == 0
    assert f"config: {other} (--config)" in capsys.readouterr().out

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "xcalibur.yaml").write_text(BASE)
    assert cli.main(["check-llm", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert f"config: {ws / 'xcalibur.yaml'} (workspace file, not trusted" in out


def test_a_local_file_replacing_the_user_configuration_is_announced(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _fake_ok(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "xcalibur.yaml").write_text(BASE)
    monkeypatch.chdir(ws)
    assert cli.main(["check-llm"]) == 0
    assert "replaces your user configuration" not in capsys.readouterr().err  # none exists

    assert cli.main(["init", "--non-interactive", "--base-url", "https://proxy.example"]) == 0
    capsys.readouterr()
    assert cli.main(["check-llm"]) == 0
    err = capsys.readouterr().err
    user = tmp_path / "cfg" / "xcalibur" / "xcalibur.yaml"
    local = ws / "xcalibur.yaml"
    assert err.startswith(f"warning: {local} replaces your user configuration {user}")
    assert "--config" in err


@pytest.mark.parametrize(
    "setting, limit",
    [("max_retries: 6", "max_retries above the 5"), ("timeout: 600.5", "timeout above the 600"),
     ("max_tokens: 64001", "max_tokens above the 64000")],
)
def test_workspace_cost_and_waiting_settings_are_capped(tmp_path, capsys, setting, limit):
    path = tmp_path / "xcalibur.yaml"
    path.write_text(BASE + f"  {setting}\n")
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert limit in err and "--config" in err and "Traceback" not in err
    assert load_llm_config(ConfigLocation(path, trusted=True))  # the user may go above


def test_workspace_limits_are_inclusive(tmp_path):
    path = tmp_path / "xcalibur.yaml"
    path.write_text(BASE + "  max_retries: 5\n  timeout: 600\n  max_tokens: 64000\n")
    config = load_llm_config(path)
    assert (config.max_retries, config.timeout, config.max_tokens) == (5, 600, 64000)


def test_workspace_with_an_unknown_provider_never_says_none(tmp_path, capsys):
    (tmp_path / "xcalibur.yaml").write_text(
        "llm:\n  provider: openai\n  model: m\n  api_key_env: OPENAI_API_KEY\n"
    )
    assert cli.main(["check-llm", "--workspace", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "unsupported provider 'openai'; available: anthropic" in err and "None" not in err


def test_init_interactive_in_a_workspace_never_asks_for_another_key_variable(
    tmp_path, monkeypatch, capsys
):
    prompts = []
    answers = iter(["", "claude-sonnet-5-5"])

    def fake_input(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["init", "--workspace", str(tmp_path)]) == 0
    assert len(prompts) == 2 and not any("environment variable" in p for p in prompts)
    assert "API key variable: ANTHROPIC_API_KEY" in capsys.readouterr().out
    assert load_llm_config(tmp_path / "xcalibur.yaml").api_key_env == "ANTHROPIC_API_KEY"


def test_init_in_a_workspace_refuses_bad_flags_before_asking(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("prompted"))
    args = ["init", "--workspace", str(tmp_path)]
    assert cli.main([*args, "--api-key-env", "GITHUB_TOKEN"]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert cli.main([*args, "--base-url", "https://proxy.example"]) == 2
    assert "base_url" in capsys.readouterr().err
    assert not (tmp_path / "xcalibur.yaml").exists()


def test_init_never_echoes_a_pasted_key_as_a_prompt_default(tmp_path, monkeypatch, capsys):
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["init", "--config", str(tmp_path / "c.yaml"), "--api-key-env", SECRET]) == 2
    captured = capsys.readouterr()
    assert "NAME of an environment variable" in captured.err
    assert not prompts and SECRET not in captured.out + captured.err
    assert not (tmp_path / "c.yaml").exists()


def test_no_replacement_warning_for_a_missing_workspace_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli.main(["init", "--non-interactive"]) == 0
    capsys.readouterr()
    empty = tmp_path / "ws"
    empty.mkdir()
    assert cli.main(["check-llm", "--workspace", str(empty)]) == 2
    err = capsys.readouterr().err
    assert "No configuration at" in err and "replaces your user configuration" not in err
