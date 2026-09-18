"""`foundry-task-orchestration-actor` — the gate, and the derivation table.

Four subcommands. `lint` is what CI runs against a sidecar; `show` prints every rendering
`config.py` derives from the two fields that are actually written down — the peers this instance
will call and where, the image repositories it will deploy from, the namespace, Service URLs and
Secrets an attempt will create — so an operator can check all of it before a run spends a single
peer session, rather than by reading a running actor's logs after the fact.

`render-cards` and `serve` are what make a use's whole repository one sidecar (ADR-FTOA-0001): the
first writes the actor's four cards beside it from the definition in this wheel, the second boots
the thing. Both run inside the image this package publishes; both work in a plain venv too.

House rule: every published package in this ecosystem ships a CLI named exactly the package.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import conformance
from .config import CapabilityConfig, ConfigError, component_url_env, lint, version
from .instance import render_cards
from .serve import DEFAULT_PORT, ServeError, serve
from .settings import ENV, Settings, SettingsError

_REGISTRY_PLACEHOLDER = "<registry>"
_SAMPLE_TASK = "TASK-NNN"


def _cmd_lint(args: argparse.Namespace) -> int:
    # Two gates, one command. The sidecar says which capability this use serves; the cards say
    # which actor it claims to be. A use can be wrong about either independently.
    report = lint(Path(args.folder))
    conformance_report = conformance.check(Path(args.folder))
    report.oks.extend(conformance_report.oks)
    report.warns.extend(conformance_report.warns)
    report.errors.extend(conformance_report.errors)
    for warning in report.warns:
        print(f"  ! {warning}")
    for error in report.errors:
        print(f"  FAIL {error}")
    if not report.ok:
        print(f"✗ {len(report.errors)} error(s)")
        return 1
    for line in report.oks:
        print(f"  ok   {line}")
    checked_cards = any((Path(args.folder) / name).exists() for name in conformance.CARD_FILES)
    print("✓ sidecar and cards conform" if checked_cards else "✓ sidecar conforms")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        config = CapabilityConfig.load(Path(args.folder))
        settings = Settings.from_env()
    except (ConfigError, SettingsError) as e:
        print(f"  FAIL {e}")
        return 2
    registry = args.registry or settings.image_registry or _REGISTRY_PLACEHOLDER
    run_id = config.run_id(_SAMPLE_TASK)

    rows = [
        ("capability", config.capability),
        ("source_repo", config.source_repo),
        ("actor name", config.actor_name),
        ("registry path", config.capability_path),
        ("workload prefix", config.workload_prefix),
        ("clone prefix", config.clone_prefix(_SAMPLE_TASK)),
        ("components (round 0)", ", ".join(config.components)),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")

    print("\n  peers")
    for peer in (config.implementation, config.testing):
        url = settings.peer_url(config, peer.role)
        override = ENV[f"{peer.role}_url"]
        url_from = (f"${override}" if url != peer.url
                    else "sidecar" if peer.declared_url else "derived")
        print(f"    {peer.role}")
        print(f"      repo  {peer.repo}  ({'sidecar' if peer.declared_repo else 'derived'})")
        print(f"      url   {url}  ({url_from})")

    print(f"\n  one attempt at {_SAMPLE_TASK}")
    print(f"    namespace / product  {run_id}")
    print(f"    implementation clone {config.implementation.repo}@impl/{_SAMPLE_TASK}")
    print(f"    platform stand-in    "
          f"{config.platform_folder if config.platform is not None else '(none declared)'}")
    for component in config.components:
        print(f"    {component}")
        print(f"      image name    {config.image_name(component)}")
        print(f"      image         {config.image_repository(registry, component)}:<version>")
        print(f"      test image    {config.image_repository(registry, component)}/tests:<version>")
        print(f"      deployment    {config.prefixed(run_id, config.workload_name(component))}")
        print(f"      service url   {config.service_url(run_id, component)}")
        print(f"      test job      {config.prefixed(run_id, config.test_job_name(component))}")
        for secret in config.render_secrets(run_id, component):
            print(f"      secret        {secret.name}  ({', '.join(sorted(secret.string_data))})")
    # Names only: a value is a template for an address, and for a stand-in it carries the
    # stand-in's own throwaway credentials, which a derivation table has no reason to print.
    test_env = [name for name, _ in config.render_test_env(run_id)]
    print(f"    test job env         "
          f"{', '.join([*(component_url_env(c) for c in config.components), *test_env])}")
    return 0


def _cmd_render_cards(args: argparse.Namespace) -> int:
    # The step that deletes the hand copy. Run at `docker build` time, right after the sidecar is
    # COPY'd in: from then on the cards in that folder came from a version, not from an editor.
    try:
        config = CapabilityConfig.load(Path(args.folder))
    except ConfigError as e:
        print(f"  FAIL {e}")
        return 2
    try:
        written = render_cards(config, Path(args.folder))
    except FileNotFoundError as e:
        print(f"  FAIL {e}")
        return 2
    for path in written:
        print(f"  ok   rendered {path}")
    print(f"✓ {config.actor_name} — four cards from the definition in "
          f"foundry-task-orchestration-actor=={version()}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    # No try/except around the boot itself. A misconfigured actor that starts anyway and refuses
    # every caller at the door is strictly worse than a pod that crash-loops with the reason on
    # stdout, which is what an uncaught error produces here.
    try:
        serve(Path(args.folder), port=args.port)
    except (ConfigError, SettingsError, ServeError) as e:
        print(f"  FAIL {e}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foundry-task-orchestration-actor",
        description="Inspect, validate and run one capability's task-orchestration actor.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lint_parser = sub.add_parser(
        "lint",
        help="validate a sidecar against foundry-task-orchestration-actor/agentic-context/v1")
    lint_parser.add_argument(
        "folder", nargs="?", default=".",
        help="the actor's folder, or the sidecar file itself (default: .)")
    lint_parser.set_defaults(func=_cmd_lint)

    show_parser = sub.add_parser(
        "show", help="print every identifier derived from the sidecar and the environment")
    show_parser.add_argument("folder", nargs="?", default=".")
    show_parser.add_argument(
        "--registry",
        help=f"render image refs against this registry (default: $IMAGE_REGISTRY, else "
             f"{_REGISTRY_PLACEHOLDER})")
    show_parser.set_defaults(func=_cmd_show)

    render_parser = sub.add_parser(
        "render-cards",
        help="write the actor's four cards into a use's folder, from the definition in this wheel")
    render_parser.add_argument(
        "folder", nargs="?", default=".",
        help="the folder holding this use's actor-agentic-context.yaml (default: .)")
    render_parser.set_defaults(func=_cmd_render_cards)

    serve_parser = sub.add_parser(
        "serve", help="boot this actor and answer its door (needs the `serve` extra)")
    serve_parser.add_argument(
        "folder", nargs="?", default=".",
        help="the folder holding this use's sidecar and cards (default: .)")
    serve_parser.add_argument(
        "--port", type=int, default=None,
        help=f"override $PORT (default: $PORT, else {DEFAULT_PORT})")
    serve_parser.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
