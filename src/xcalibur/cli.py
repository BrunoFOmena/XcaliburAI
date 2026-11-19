"""Command-line entry point (skeleton)."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from xcalibur.execution.base import RunSpec
from xcalibur.execution.nextflow import NextflowEngine
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
    return parser


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

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
