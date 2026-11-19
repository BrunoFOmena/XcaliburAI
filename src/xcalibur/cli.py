"""Command-line entry point (skeleton)."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from xcalibur.config import (
    load_llm_config,
    locate_config,
    save_llm_config,
    user_config_path,
    validate_env_name,
    workspace_config_path,
)
from xcalibur.execution.base import RunSpec
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.llm import DEFAULT_KEY_ENVS, SUGGESTED_MODELS, SUPPORTED_PROVIDERS, create_provider
from xcalibur.llm.base import Message, ProviderConfig
from xcalibur.llm.errors import LLMAuthError, LLMConfigError, LLMError
from xcalibur.manifest import ManifestError
from xcalibur.registry import DomainRegistry
from xcalibur.version import __version__


def _parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"expected KEY=VALUE, got {item!r}")
        out[key] = value
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xcalibur", description="XcaliburAI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the version")
    sub.add_parser("domains", help="list installed domain packages")

    plan = sub.add_parser("plan", help="validate parameters and show the workflow command")
    plan.add_argument("domain")
    plan.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    plan.add_argument("-i", "--input", action="append", default=[], metavar="NAME=PATH")

    init = sub.add_parser(
        "init", help="configure the LLM provider (the key stays in the environment)"
    )
    init.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    init.add_argument("--model")
    init.add_argument("--api-key-env", metavar="NAME", help="environment variable holding the key")
    init.add_argument("--base-url")
    init.add_argument("--effort", help="reasoning effort (e.g. low, medium, high)")
    _add_location_args(init)
    init.add_argument("--non-interactive", action="store_true", help="never prompt; use flags")
    init.add_argument("--force", action="store_true", help="overwrite an existing configuration")
    init.add_argument("--check", action="store_true", help="test the connection after saving")

    check = sub.add_parser("check-llm", help="test the configured LLM provider")
    _add_location_args(check)
    return parser


def _add_location_args(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--config", type=Path, help="configuration file")
    where.add_argument("--workspace", type=Path, help="save in <workspace>/xcalibur.yaml")


def _ask(label: str, default: str | None) -> str | None:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _cmd_init(args: argparse.Namespace) -> int:
    interactive = not args.non_interactive
    provider = args.provider or "anthropic"
    model = args.model or SUGGESTED_MODELS[provider]
    key_env = args.api_key_env or DEFAULT_KEY_ENVS[provider]
    if interactive:
        provider = _ask("Provider (" + ", ".join(SUPPORTED_PROVIDERS) + ")", provider) or provider
        if provider not in SUPPORTED_PROVIDERS:
            print(f"error: unsupported provider {provider!r}", file=sys.stderr)
            return 2
        model = _ask("Model", args.model or SUGGESTED_MODELS[provider]) or model
        key_env = _ask(
            "Name of the environment variable with the API key",
            args.api_key_env or DEFAULT_KEY_ENVS[provider],
        ) or key_env
    try:
        validate_env_name(key_env)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.base_url and args.config is None and args.workspace is not None:
        print(
            "error: --base-url is not allowed in a workspace configuration (it would send "
            "your API key to that server); use the user configuration or --config",
            file=sys.stderr,
        )
        return 2

    if args.config is not None:
        path = args.config
    elif args.workspace is not None:
        path = workspace_config_path(args.workspace)
    else:
        path = user_config_path()
    if path.exists() and not args.force:
        if not interactive or (_ask(f"{path} exists. Overwrite? (y/N)", "n") or "n").lower() != "y":
            print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
            return 2

    config = ProviderConfig(
        provider=provider,
        model=model,
        api_key_env=key_env,
        base_url=args.base_url,
        effort=args.effort,
    )
    try:
        save_llm_config(config, path)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"saved {path}")
    if os.environ.get(key_env):
        print(f"{key_env} is set (the key itself is not saved)")
    else:
        print(f"note: export your API key in {key_env}; it is never written to the configuration")
    if args.check:
        return _check(config)
    return 0


def _check(config: ProviderConfig) -> int:
    try:
        provider = create_provider(config)
        answer = provider.complete(
            [
                Message(role="system", content="This is a connection test. Answer with: OK"),
                Message(role="user", content="ping"),
            ]
        )
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LLMAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    print(f"ok: {config.provider} model {config.model} answered ({len(answer)} characters)")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        config = load_llm_config(locate_config(args.config, args.workspace))
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _check(config)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "domains":
        registry = DomainRegistry.discover()
        if not len(registry):
            print("no domain packages installed")
            return 0
        for plugin in registry:
            manifest = plugin.manifest
            print(f"{manifest.name}\t{manifest.version}\t{manifest.display_name}")
        return 0

    if args.command == "plan":
        registry = DomainRegistry.discover()
        try:
            plugin = registry.get(args.domain)
            params = plugin.manifest.resolve_params(_parse_kv(args.param))
        except (KeyError, ManifestError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        inputs = {k: Path(v) for k, v in _parse_kv(args.input).items()}
        spec = RunSpec(plugin=plugin, params=params, inputs=inputs, dry_run=True)
        print(shlex.join(NextflowEngine().build_command(spec)))
        return 0

    if args.command == "init":
        return _cmd_init(args)

    if args.command == "check-llm":
        return _cmd_check(args)

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
