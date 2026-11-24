"""Command-line entry point.

Exit codes: 0 success, 1 execution failure (or an unexpected error), 2 wrong usage or
invalid parameters, 130 interrupted. Errors are printed as one line (input problems found by
a domain's check, one line each), never as a traceback (set ``XCALIBUR_DEBUG=1`` to see it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from xcalibur.agents.core_tools import (
    RunArgs,
    RunIdArgs,
    plan_run,
    preview_run,
    read_results,
    run_pipeline,
)
from xcalibur.agents.tools import ToolContext, ToolError
from xcalibur.chat import ChatApp
from xcalibur.config import (
    ConfigLocation,
    check_workspace_settings,
    load_llm_config,
    locate_config,
    save_llm_config,
    user_config_path,
    validate_env_name,
    workspace_config_path,
    workspace_location,
)
from xcalibur.execution.nextflow import NextflowEngine
from xcalibur.llm import DEFAULT_KEY_ENVS, SUGGESTED_MODELS, SUPPORTED_PROVIDERS, create_provider
from xcalibur.llm.base import Message, ProviderConfig
from xcalibur.llm.errors import LLMAuthError, LLMConfigError, LLMError
from xcalibur.registry import DomainRegistry
from xcalibur.terminal import FollowingEngine, Style, color_enabled, stderr_printer
from xcalibur.version import __version__
from xcalibur.workspace import Workspace

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130

# Tool errors caused by the request (exit 2); anything else is an execution failure (exit 1).
USAGE_ERRORS = {"invalid_params", "invalid_inputs", "unknown_domain", "run_exists", "no_workspace"}


class UsageError(Exception):
    """Wrong arguments: reported in one line with exit code 2."""


def _parse_kv(items: list[str], what: str = "KEY=VALUE") -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"expected {what}, got {item!r}")
        out[key] = value
    return out


def _discover() -> DomainRegistry:
    return DomainRegistry.discover()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xcalibur",
        description="XcaliburAI: AI agents over workflow engines for genomics.",
        epilog="Exit codes: 0 success, 1 execution failure, 2 wrong usage or invalid "
        "parameters, 130 interrupted. Run 'xcalibur <command> -h' for a command's options.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="print the version")
    sub.add_parser("domains", help="list installed domain packages")

    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--json", action="store_true", help="print results as JSON on stdout")
    output.add_argument("--no-color", action="store_true", help="plain output (also NO_COLOR=1)")

    request = argparse.ArgumentParser(add_help=False)
    request.add_argument("domain", help="domain name, as 'xcalibur domains' lists it")
    request.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    request.add_argument("-i", "--input", action="append", default=[], metavar="NAME=PATH")
    request.add_argument(
        "--run-id",
        help="run identifier (letters, digits, '_', '-'; starts with a letter or digit)",
    )
    request.add_argument(
        "--workspace", type=Path, default=None, help="workspace directory (default: current)"
    )
    request.add_argument(
        "--nextflow", default="nextflow", metavar="PATH", help="Nextflow executable"
    )
    request.add_argument(
        "--profile",
        metavar="NAME",
        help="workflow profile(s), comma-separated (overrides the domain's default)",
    )

    sub.add_parser(
        "plan",
        parents=[request, output],
        help="validate parameters and show the workflow command (nothing runs)",
    )

    run = sub.add_parser(
        "run",
        parents=[request, output],
        help="validate and run a domain's workflow, without the LLM",
        description="Validate the parameters and inputs, run the workflow and record its "
        "provenance in <workspace>/runs/<run_id>/. Progress is streamed to stderr.",
    )
    run.add_argument("--resume", action="store_true", help="resume an earlier run (--run-id)")
    run.add_argument("--dry-run", action="store_true", help="only plan, like 'xcalibur plan'")

    chat = sub.add_parser(
        "chat",
        parents=[output],
        help="talk to the assistant; it plans and runs workflows after you confirm",
        description="Interactive session with the assistant. Session commands: /help, "
        "/status, /runs, /exit.",
    )
    chat.add_argument("--config", type=Path, help="LLM configuration file")
    chat.add_argument(
        "--workspace", type=Path, default=None, help="workspace directory (default: current)"
    )
    chat.add_argument(
        "--yes", action="store_true", help="run without asking for confirmation (automation)"
    )
    chat.add_argument("--max-steps", type=int, help="model calls per answer (default 8)")
    chat.add_argument("--nextflow", default="nextflow", metavar="PATH", help="Nextflow executable")

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
    # A workspace file follows the workspace rules (no base_url, only the standard key
    # variable): refuse such flags before asking anything, and never offer another variable.
    to_workspace = args.config is None and args.workspace is not None
    provider = args.provider or "anthropic"
    model = args.model or SUGGESTED_MODELS[provider]
    key_env = args.api_key_env or DEFAULT_KEY_ENVS[provider]
    if to_workspace:
        try:
            # First, so a pasted key is rejected without being echoed in the message below.
            validate_env_name(key_env)
            check_workspace_settings(
                ProviderConfig(provider, model, api_key_env=key_env, base_url=args.base_url),
                "--workspace",
            )
        except LLMConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if interactive:
        provider = _ask("Provider (" + ", ".join(SUPPORTED_PROVIDERS) + ")", provider) or provider
        if provider not in SUPPORTED_PROVIDERS:
            print(f"error: unsupported provider {provider!r}", file=sys.stderr)
            return 2
        model = _ask("Model", args.model or SUGGESTED_MODELS[provider]) or model
        if to_workspace:
            key_env = DEFAULT_KEY_ENVS[provider]
            print(f"API key variable: {key_env} (the only one a workspace configuration may use)")
        else:
            key_env = _ask(
                "Name of the environment variable with the API key",
                args.api_key_env or DEFAULT_KEY_ENVS[provider],
            ) or key_env
    try:
        validate_env_name(key_env)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
    if to_workspace:
        try:
            check_workspace_settings(config, "--workspace")
        except LLMConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
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


def _warn_override(location: ConfigLocation) -> None:
    warning = location.override_warning()
    if warning:
        print(f"warning: {warning}", file=sys.stderr)


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
    location = locate_config(args.config, args.workspace)
    _warn_override(location)
    try:
        config = load_llm_config(location)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"config: {location.describe()}")
    effort = f", effort {config.effort}" if config.effort else ""
    print(
        f"using {config.provider} model {config.model}{effort}, max_tokens {config.max_tokens}, "
        f"max_retries {config.max_retries}, timeout {config.timeout:g}s"
    )
    return _check(config)


# -- plan / run ------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace, dry_run: bool) -> int:
    err_style = Style(color_enabled(sys.stderr, args.no_color))
    out_style = Style(color_enabled(sys.stdout, args.no_color) and not args.json)
    fields: dict[str, Any] = {
        "domain": args.domain,
        "params": _parse_kv(args.param),
        "inputs": _parse_kv(args.input, "NAME=PATH"),
        "resume": getattr(args, "resume", False),
    }
    if args.profile is not None:
        fields["profile"] = args.profile
    if args.run_id is not None:
        fields["run_id"] = args.run_id
    elif fields["resume"]:
        raise UsageError("--resume needs the --run-id of the run to resume")
    try:
        request = RunArgs(**fields)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _report_error(args, "invalid_arguments", problems, EXIT_USAGE)

    workspace = Workspace((args.workspace or Path.cwd()).absolute())
    engine = FollowingEngine(NextflowEngine(args.nextflow), stderr_printer(err_style))
    ctx = ToolContext(domains=_discover(), workspace=workspace, engine=engine)

    if dry_run:
        try:
            plan = plan_run(request, ctx)
        except ToolError as exc:
            return _tool_failure(args, exc)
        if args.json:
            _print_json({"status": "planned", **plan})
        else:
            _print_input_check(plan, err_style)
            print(plan["command_text"])
        return EXIT_OK

    try:
        plan = preview_run(request, ctx)
    except ToolError as exc:
        return _tool_failure(args, exc)
    print(
        err_style("brand", f"Running {plan['domain']} · run {plan['run_id']}")
        + err_style("dim", f"\n  {plan['command_text']}"),
        file=sys.stderr,
        flush=True,
    )
    if not args.json:
        _print_input_check(plan, err_style)
    workspace.ensure()
    try:
        result = run_pipeline(request, replace(ctx, approved_plan=plan))
    except ToolError as exc:
        return _tool_failure(args, exc)
    try:
        outputs = read_results(RunIdArgs(run_id=result["run_id"]), ctx)
    except ToolError:
        outputs = None

    code = EXIT_OK if result["status"] == "succeeded" else EXIT_FAILED
    if args.json:
        payload = {"domain": plan["domain"], **result}
        if outputs is not None:
            payload["outputs"] = outputs["outputs"]
            payload["missing_outputs"] = outputs["missing_outputs"]
        _print_json(payload)
        return code
    _print_run(result, outputs, out_style)
    return code


def _print_input_check(plan: dict[str, Any], style: Style) -> None:
    """The domain's input summary and warnings, on stderr (stdout stays the command)."""
    check = plan.get("input_check")
    if not check:
        return
    if check.get("overview"):
        print(style("dim", f"  inputs: {check['overview']}"), file=sys.stderr)
    for warning in check.get("warnings", []):
        print(style("warn", f"  warning: {warning['message']}"), file=sys.stderr)


def _print_run(result: dict[str, Any], outputs: dict[str, Any] | None, style: Style) -> None:
    ok = result["status"] == "succeeded"
    mark = style("ok", "✓") if ok else style("error", "✗")
    code = "" if result["returncode"] is None else f" (exit {result['returncode']})"
    print(f"{mark} run {result['run_id']} {result['status']}{code}")
    print(f"  run dir  {result['run_dir']}")
    print(f"  log      {result['log_path']}")
    print(f"  record   {result['record_path']}")
    if outputs is None:
        return
    for output in outputs["outputs"]:
        for item in output["files"]:
            print(f"  {style('accent', output['name'])}  {item['path']}")
    if ok and outputs["missing_outputs"]:
        missing = ", ".join(outputs["missing_outputs"])
        print(style("warn", f"  missing declared outputs: {missing}"))


def _tool_failure(args: argparse.Namespace, exc: ToolError) -> int:
    code = EXIT_USAGE if exc.kind in USAGE_ERRORS else EXIT_FAILED
    issues = exc.details.get("errors")
    if issues and not getattr(args, "json", False):
        # Input problems: one line each, so a folder with many bad files stays readable.
        count = len(issues)
        lines = [f"{count} problem{'s' if count > 1 else ''} in the inputs:"]
        lines += [f"  - {issue['message']}" for issue in issues]
        return _report_error(args, exc.kind, "\n".join(lines), code)
    return _report_error(args, exc.kind, str(exc), code, exc.details)


def _report_error(
    args: argparse.Namespace,
    kind: str,
    message: str,
    code: int,
    details: dict[str, Any] | None = None,
) -> int:
    if getattr(args, "json", False):
        error: dict[str, Any] = {"type": kind, "message": message}
        # Structured details (e.g. the list of input errors) after the fixed keys.
        error.update({k: v for k, v in (details or {}).items() if k not in error})
        _print_json({"status": "error", "error": error})
    style = Style(color_enabled(sys.stderr, getattr(args, "no_color", False)))
    print(style("error", f"error: {message}"), file=sys.stderr)
    return code


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


# -- chat ---------------------------------------------------------------------------------


def _chat_config(args: argparse.Namespace, workspace: Path) -> ConfigLocation:
    """--config, else <workspace>/xcalibur.yaml if present, else the user configuration."""
    if args.config is not None:
        return ConfigLocation(args.config, trusted=True)
    local = workspace_config_path(workspace)
    if local.exists():
        return workspace_location(local)
    return ConfigLocation(user_config_path(), trusted=True)


def _cmd_chat(args: argparse.Namespace) -> int:
    if args.max_steps is not None and args.max_steps < 1:
        raise UsageError("--max-steps must be at least 1")
    root = (args.workspace or Path.cwd()).absolute()
    location = _chat_config(args, root)
    _warn_override(location)
    try:
        config = load_llm_config(location)
        provider = create_provider(config)
        # Fail now, not at the first message, when the SDK or the key is missing
        # (building the client checks both without any network call).
        getattr(provider, "client", None)
    except LLMConfigError as exc:
        return _report_error(args, "llm_config", str(exc), EXIT_USAGE)
    err_style = Style(color_enabled(sys.stderr, args.no_color))
    app = ChatApp(
        provider=provider,
        domains=_discover(),
        workspace=Workspace(root),
        engine=FollowingEngine(NextflowEngine(args.nextflow), stderr_printer(err_style)),
        style=Style(color_enabled(sys.stdout, args.no_color) and not args.json),
        auto_confirm=args.yes,
        json_mode=args.json,
        max_steps=args.max_steps,
        config_source=location.describe(),
    )
    _enable_line_editing()
    return app.run()


def _enable_line_editing() -> None:
    """Arrow keys and line history at the prompt, where the platform has readline."""
    try:
        import readline  # noqa: F401
    except ImportError:
        pass


# -- entry point --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - the end user never sees a traceback
        if os.environ.get("XCALIBUR_DEBUG"):
            raise
        print(
            f"error: {type(exc).__name__}: {exc} (set XCALIBUR_DEBUG=1 for the traceback)",
            file=sys.stderr,
        )
        return EXIT_FAILED


def _main(argv: list[str] | None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(__version__)
        return EXIT_OK

    if args.command == "domains":
        registry = _discover()
        if not len(registry):
            print("no domain packages installed")
            return EXIT_OK
        for plugin in registry:
            manifest = plugin.manifest
            print(f"{manifest.name}\t{manifest.version}\t{manifest.display_name}")
        return EXIT_OK

    if args.command == "plan":
        return _cmd_run(args, dry_run=True)

    if args.command == "run":
        return _cmd_run(args, dry_run=args.dry_run)

    if args.command == "chat":
        return _cmd_chat(args)

    if args.command == "init":
        return _cmd_init(args)

    if args.command == "check-llm":
        return _cmd_check(args)

    build_parser().print_help()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
