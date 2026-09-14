"""`CapabilityConfig` — one capability id and one repo, every other rendering derived.

WHY THIS FILE EXISTS. The hand-written orchestration actor this package was extracted from spelled
its capability out as module constants: the dotted id, the implementation repo, the testing repo,
the registry path with `CAP` dropped, the image prefix, the tempdir prefix — and, inside f-strings
scattered through the deploy steps, the k8s workload prefix every component's Deployment, Service
and Secret was named with. One fact, seven literals, and two of them (the registry path and the
image prefix) carried comments explaining that they had to agree with a peer actor's own copy.

They are now derivations of two fields. Nothing in this package spells a capability.

    capability   ACME.PARTS.CAP.SUP.007.WID                          ← the only id anyone writes
    source_repo  <owner>/ACME.PARTS.CAP.SUP.007.WID-task-orchestration  ← and the only repo

    actor_name            <repo half of source_repo>
    actor_slug            same, lowercased, dots to hyphens
    capability_slug       the id, lowercased, dots to hyphens
    capability_path       the id lowercased, split AT its `cap` segment: head / tail
    workload_name(c)      {tail, dots to hyphens}-{c}   a component's k8s object name
    image_name(c)         {capability lowercased}-{c}   the image its base manifest names
    image_repository(r,c) {r}/{capability_path}/{c}     where that image is pulled from
    run_id(t)             test-{t lowercased}           one attempt's namespace AND product
    peer repo (role)      {owner}/{capability}-{role}
    peer url  (role)      http://foundry-{capability_slug}-{role}

THE IMAGE REF IS A THREE-WAY CONTRACT. The implementation actor publishes
`<registry>/<capability_path>/<component>:<version>`, the testing actor publishes
`<registry>/<capability_path>/<component>/tests:<version>`, and this actor parses both back apart
(`parse_images`). All three compute `capability_path` the same way from the same id; it is
derivation output or nothing, and no tag scheme is invented here.

WHAT IS DERIVED AND WHAT IS DECLARED. Anything recoverable from the id or the repo is derived.
Anything genuinely additional is declared in the sidecar, once: which components round 0 names to
the tester (`components`), where a peer lives when it does not live where the convention says
(`peers`), and what a component needs beside it in an ephemeral namespace (`ephemeral`). How many
attempts to make, and how long to wait for a door, are NOT declared here — they are operational
tuning, and they are `settings.Settings` (the reasoning FIA's ADR-FIA-0002 wrote down for its own
sidecar: it declares only what this actor reads and acts on as a fact about the capability).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import yaml

CONTRACT = "foundry-task-orchestration-actor/agentic-context/v1"
SIDECAR = "actor-agentic-context.yaml"

_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "agentic-context.schema.yaml"
_CARDS_PATH = Path(__file__).resolve().parent / "cards"

# The segment a capability id carries to say "capability". It is dropped from the registry path
# because the path position already says it — every other token of the id survives, across
# segments rather than concatenated.
_CAPABILITY_SEGMENT = "cap"

# The two peers this actor drives. Their names are also the suffix each peer's repository carries
# by convention, which is what lets both be derived rather than declared.
ROLES = ("implementation", "testing")

# The product this family of actors is deployed as. papeete-deploy's wrapper kustomization
# prefixes every object it renders with `<product>-`, so a peer's in-cluster Service is
# `<product>-<its k8s-safe name>` — derived, not allocated, which is what lets the same product
# stand up in several namespaces at once. This is the product the three foundry-*-actor packages
# are built for; a use deployed under another one names its peers' `url:` in the sidecar, or
# overrides it in the environment.
PEER_SERVICE_PREFIX = "foundry"

# Where a component's own kustomize folder lives inside the implementation repo, and the overlay an
# ephemeral namespace applies. A convention of the component layout the implementation actor
# writes into, not of any one capability.
COMPONENT_DEPLOY_FOLDER = "{component}/deployment/dev"
EPHEMERAL_RECIPE = "ephemeral"

# What an unsubstituted placeholder looks like: a bare lowercase word in braces, and nothing else.
# Narrow on purpose — see `render_template`.
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

# A Kubernetes object name (RFC 1123 subdomain). Checked against a sample rendering at load time,
# so a secret template that cannot produce a valid name fails at startup rather than at the first
# attempt, after two peer sessions have already been paid for.
_K8S_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


def version() -> str:
    """This package's own version, as installed.

    Read from the installed distribution rather than written down a second time here: a literal in
    the source and a version in `pyproject.toml` are two facts that can disagree, and the one a
    rendered card would carry is the one nobody checks. In a source checkout with nothing
    installed there is no distribution to ask, and `unknown` is the honest answer.
    """
    try:
        return metadata.version("foundry-task-orchestration-actor")
    except metadata.PackageNotFoundError:
        return "unknown"


def cards_path() -> Path:
    """The folder holding this actor's own four cards — its definition, shipped in the wheel.

    The cards say what a foundry task-orchestration actor IS: its data dictionary, its message
    catalog, and the one door it answers. They name no capability, because which capability an
    instance serves is not part of what the actor is — that is the sidecar, supplied per use.
    `render-cards` renders a use's cards from here, and it is what
    `papeete-actor-synchronous-messaging lint-card` is pointed at to check that the `-actor` suffix
    in this package's name is a claim it actually honours (ADR-ECO-0022).
    """
    return _CARDS_PATH


def load_schema() -> dict:
    """The contract, as committed source inside this package.

    The path is the same in a source checkout and in an installed wheel, so there is no fallback
    and no second location to reason about. A wheel that lost it is a gate with nothing to
    enforce, which is worth failing loudly over rather than degrading past.
    """
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"{_SCHEMA_PATH.name} not found in {_SCHEMA_PATH.parent}.\n"
            "  The contract is committed source in this package, so this should be unreachable.\n"
            "  In a source checkout: the file was deleted — restore it from git.\n"
            "  In an installed wheel: the build shipped without its contract. Report it against "
            "the release."
        )
    return yaml.safe_load(_SCHEMA_PATH.read_text())


class ConfigError(ValueError):
    """The sidecar is unusable — missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class Peer:
    """One actor this one calls: where its repository is, and where its doors answer."""

    role: str
    repo: str
    url: str
    declared_repo: bool = False
    declared_url: bool = False

    @property
    def actor_name(self) -> str:
        """The repo half — the name that peer's own sidecar derives for itself."""
        return self.repo.partition("/")[2]


@dataclass(frozen=True)
class SecretTemplate:
    """One Secret created beside each touched component in an ephemeral namespace."""

    name: str
    data: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Secret:
    """A `SecretTemplate`, rendered for one run and one component."""

    name: str
    string_data: dict


@dataclass(frozen=True)
class CapabilityConfig:
    """Everything this actor needs to know about the capability it serves."""

    capability: str
    source_repo: str
    components: tuple[str, ...]
    implementation: Peer
    testing: Peer
    platform: str | None = None
    secrets: tuple[SecretTemplate, ...] = ()
    test_env: tuple[tuple[str, str], ...] = ()
    root: Path | None = None

    # ── loading ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, folder: str | Path = ".") -> CapabilityConfig:
        """Read the sidecar from `folder` (or from the file itself, if a file is given).

        Raises `ConfigError` for anything that would otherwise surface much later — a missing
        file, a contract this package does not implement, an absent required key, a declared
        platform folder that is not there. Every one of those is cheaper here than in the middle
        of an attempt, after a peer has already spent a session on the task.
        """
        path = Path(folder)
        if path.is_dir():
            path = path / SIDECAR
        try:
            raw = yaml.safe_load(path.read_text())
        except OSError as e:
            raise ConfigError(f"{path}: cannot be read: {e}") from e
        except yaml.YAMLError as e:
            raise ConfigError(f"{path}: does not parse: {e}") from e
        return cls.from_dict(raw, source=str(path), root=path.parent)

    @classmethod
    def from_dict(cls, raw: object, *, source: str = "<dict>",
                  root: Path | None = None) -> CapabilityConfig:
        if not isinstance(raw, dict):
            raise ConfigError(f"{source}: not a mapping")
        if raw.get("context") != CONTRACT:
            raise ConfigError(
                f"{source}: declares context '{raw.get('context')}', not {CONTRACT}"
            )

        for key in load_schema()["required"]:
            if key not in raw:
                raise ConfigError(f"{source}: missing required key '{key}'")

        capability = str(raw["capability"])
        source_repo = str(raw["source_repo"])
        components = _components(raw["components"], source)

        peers = raw.get("peers") or {}
        if not isinstance(peers, dict):
            raise ConfigError(f"{source}: `peers` must be a mapping of role to {{repo, url}}")
        unknown = sorted(set(peers) - set(ROLES))
        if unknown:
            # A typo'd role would otherwise be silently ignored and the derived default used in
            # its place — the one failure a derivation-with-override design must not have.
            raise ConfigError(f"{source}: peers names unknown role(s) {unknown}; "
                              f"known: {', '.join(ROLES)}")

        platform, secrets, test_env = _ephemeral(raw.get("ephemeral"), source)

        # The peers' defaults are derived from the id and the owner, so build a bare config first
        # and let it derive them — one code path for "declared" and "not declared".
        bare = cls(capability=capability, source_repo=source_repo, components=components,
                   implementation=Peer("implementation", "", ""),
                   testing=Peer("testing", "", ""),
                   platform=platform, secrets=secrets, test_env=test_env, root=root)
        # Force the derivations that can fail, here rather than at the first request that needs
        # one. A capability id with no `cap` segment is a typo, and it should not survive startup.
        _ = bare.capability_path, bare.actor_name

        config = cls(
            capability=capability, source_repo=source_repo, components=components,
            implementation=_peer(bare, "implementation", peers.get("implementation"), source),
            testing=_peer(bare, "testing", peers.get("testing"), source),
            platform=platform, secrets=secrets, test_env=test_env, root=root,
        )
        config._check_secrets(source)
        config._check_test_env(source)
        if root is not None and platform is not None:
            overlay = config.platform_folder / "k8s" / "overlays" / EPHEMERAL_RECIPE
            if not overlay.is_dir():
                raise ConfigError(
                    f"{source}: ephemeral.platform '{platform}' has no "
                    f"k8s/overlays/{EPHEMERAL_RECIPE}/ under {config.platform_folder} — "
                    f"papeete-deploy applies exactly that overlay, and a use's image must COPY the "
                    f"folder in beside the sidecar"
                )
        return config

    # ── the derived renderings ──────────────────────────────────────────────────────────────

    @property
    def owner(self) -> str:
        return self.source_repo.partition("/")[0]

    @property
    def actor_name(self) -> str:
        """The repo half of `source_repo` — this actor's own name."""
        owner, _, repo = self.source_repo.partition("/")
        if not owner or not repo:
            raise ConfigError(
                f"source_repo '{self.source_repo}' is not '<owner>/<repo>'"
            )
        return repo

    @property
    def actor_slug(self) -> str:
        """The actor name as a path/address-safe token: lowercased, dots to hyphens."""
        return self.actor_name.lower().replace(".", "-")

    @property
    def capability_slug(self) -> str:
        """The capability id as a DNS-1035-safe token: lowercased, dots to hyphens."""
        return self.capability.lower().replace(".", "-")

    def clone_prefix(self, task_id: str) -> str:
        """`tempfile.mkdtemp` prefix for one attempt's read-only clone of the implementation."""
        return f"{self.actor_slug}-{task_id}-"

    @property
    def capability_path(self) -> str:
        """The registry path form: the id lowercased and split AT its `cap` segment.

        `<ENT>.<DOMAIN>.CAP.<TYPE>.<NNN>.<CODE>` becomes `<ent>.<domain>/<type>.<nnn>.<code>`.
        Byte-identical to the implementation actor's own derivation — see this module's docstring.
        """
        segments = self.capability.lower().split(".")
        if _CAPABILITY_SEGMENT not in segments:
            raise ConfigError(
                f"capability '{self.capability}' has no '{_CAPABILITY_SEGMENT.upper()}' segment — "
                "the registry path is derived by splitting the id there, so an id without one "
                "cannot be placed"
            )
        cut = segments.index(_CAPABILITY_SEGMENT)
        head, tail = segments[:cut], segments[cut + 1:]
        if not head or not tail:
            raise ConfigError(
                f"capability '{self.capability}': nothing on "
                f"{'the left of' if not head else 'the right of'} its "
                f"'{_CAPABILITY_SEGMENT.upper()}' segment"
            )
        return f"{'.'.join(head)}/{'.'.join(tail)}"

    @property
    def workload_prefix(self) -> str:
        """The tail of the id, k8s-safe: what every component's Deployment and Service start with.

        A Service name is a DNS-1035 label and rejects dots outright, so the tail of
        `capability_path` has its dots turned into hyphens. The component manifests the
        implementation actor maintains follow this; this actor predicts the name rather than asking
        the cluster for it.
        """
        return self.capability_path.partition("/")[2].replace(".", "-")

    def workload_name(self, component: str) -> str:
        """A component's Deployment/Service name, before papeete-deploy's namePrefix."""
        return f"{self.workload_prefix}-{component}"

    def image_name(self, component: str) -> str:
        """The image name a component's base manifest carries — what kustomize retags."""
        return f"{self.capability.lower()}-{component}"

    def image_repository(self, registry: str, component: str) -> str:
        """Where a component's image is pulled from. Its test image is `…/tests` beneath it."""
        return f"{registry.rstrip('/')}/{self.capability_path}/{component}"

    def parse_images(self, images: list[str], *, test: bool = False) -> dict[str, str]:
        """`{component: version}` for every ref in a peer's `images` completion that names one of
        THIS capability's images — never re-derived, never looked up.

        Refs are registry-qualified (`<registry>/<capability_path>/<component>[/tests]:<tag>`), so
        both halves are read as PATH SEGMENTS: what follows the capability path is `<component>`
        or `<component>/tests`, and which one says whether it is a component image or its test
        image. Splitting the tag off with `rpartition(":")` alone would be wrong — a registry may
        carry a port, and `host:5000/repo` has a colon that is not a tag separator. A ref that
        does not name this capability is skipped.
        """
        marker = f"/{self.capability_path}/"
        result: dict[str, str] = {}
        for ref in images:
            repo, sep, tag = ref.rpartition(":")
            if not sep or "/" in tag:          # no tag at all — the colon was a host's port
                continue
            _, found, tail = repo.partition(marker)
            if not found:
                continue
            component, _, kind = tail.partition("/")
            if kind not in ("", "tests"):
                continue
            if (kind == "tests") != test:
                continue
            result[component] = tag
        return result

    # ── one attempt's ephemeral namespace ───────────────────────────────────────────────────

    @staticmethod
    def run_id(task_id: str) -> str:
        """Both the namespace name and the papeete-deploy product name for one attempt.

        One string, so every resource the run creates shares the exact `namePrefix` papeete-deploy's
        wrapper kustomization computes from it, and every resulting Service name is predictable
        without asking the cluster. Lowercased because a namespace is a DNS label; that is also
        all `papeete_version.normalize_name` does to a name without spaces.
        """
        return f"test-{task_id.strip().lower().replace(' ', '-')}"

    @staticmethod
    def prefixed(run_id: str, name: str) -> str:
        """An object's name once papeete-deploy's wrapper has prefixed it with the product."""
        return f"{run_id}-{name}"

    def service_url(self, run_id: str, component: str) -> str:
        """Where a black-box test reaches a component inside the run's namespace."""
        return f"http://{self.prefixed(run_id, self.workload_name(component))}"

    @property
    def platform_folder(self) -> Path | None:
        """The declared platform stand-in, resolved against the sidecar's own folder."""
        if self.platform is None:
            return None
        return (self.root or Path(".")) / self.platform

    def render_secrets(self, run_id: str, component: str) -> list[Secret]:
        """Every declared `ephemeral.secrets` entry, rendered for one run and one component."""
        values = self._template_values(run_id, component)
        return [
            Secret(name=render_template(t.name, values),
                   string_data={k: render_template(v, values) for k, v in t.data})
            for t in self.secrets
        ]

    def render_test_env(self, run_id: str) -> list[tuple[str, str]]:
        """Every declared `ephemeral.test_env` variable, rendered for one run.

        Set on the test Job beside each `<COMPONENT>_URL`. Its templates get `{run_id}` and
        `{capability}` only: the Job runs every touched component's tests in one container, so a
        per-component placeholder would have no single value to take."""
        values = {"capability": self.capability, "run_id": run_id}
        return [(name, render_template(value, values)) for name, value in self.test_env]

    def _check_test_env(self, source: str) -> None:
        try:
            self.render_test_env(self.run_id("TASK-NNN"))
        except ConfigError as e:
            raise ConfigError(f"{source}: ephemeral.test_env: {e}") from e
        reserved = {component_url_env(c) for c in self.components}
        clash = sorted(reserved & {name for name, _ in self.test_env})
        if clash:
            raise ConfigError(
                f"{source}: ephemeral.test_env redefines {clash}, which this actor already sets to "
                f"each component's in-namespace address — a second value for the same variable "
                f"would silently decide which one a test reads")

    def _template_values(self, run_id: str, component: str) -> dict[str, str]:
        return {
            "capability": self.capability,
            "component": component,
            "run_id": run_id,
            "workload": self.workload_name(component),
        }

    def _check_secrets(self, source: str) -> None:
        sample_run = self.run_id("TASK-NNN")
        for component in self.components:
            try:
                rendered = self.render_secrets(sample_run, component)
            except ConfigError as e:
                raise ConfigError(f"{source}: ephemeral.secrets: {e}") from e
            for secret in rendered:
                if not _K8S_NAME.match(secret.name):
                    raise ConfigError(
                        f"{source}: ephemeral.secrets name renders to '{secret.name}' for "
                        f"component '{component}', which is not a valid Kubernetes object name"
                    )


def render_template(template: str, values: dict[str, str]) -> str:
    """Substitute known placeholders into a string, literally.

    LITERAL REPLACEMENT, NOT `str.format`. A secret's value is a URL or a DSN — somebody else's
    syntax, where braces may be ordinary characters. Only the names in `values` are substituted;
    every other brace passes through untouched. A leftover `{bare_word}` IS still refused, because
    that is what a typo'd placeholder looks like, and passing it through would hand a component a
    DSN with `{runid}` in it to fail on inside a namespace that is about to be deleted.
    """
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    leftover = _PLACEHOLDER.search(rendered)
    if leftover:
        raise ConfigError(
            f"{template!r} names a placeholder this config cannot supply ({leftover.group(0)}); "
            "available: " + ", ".join("{" + k + "}" for k in sorted(values))
        )
    return rendered


def _components(raw: object, source: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(c, str) and c for c in raw):
        raise ConfigError(f"{source}: `components` must be a list of component names")
    if not raw:
        raise ConfigError(f"{source}: `components` is empty — round 0 would name nothing to the "
                          f"tester")
    if len(set(raw)) != len(raw):
        raise ConfigError(f"{source}: `components` names a component twice")
    for name in raw:
        if not re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", name):
            # It becomes part of a Service name and of an environment variable's name.
            raise ConfigError(f"{source}: component '{name}' must be a lowercase DNS label")
    return tuple(raw)


def _peer(config: CapabilityConfig, role: str, raw: object, source: str) -> Peer:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: peers.{role} must be a mapping with optional repo, url")
    unknown = sorted(set(raw) - {"repo", "url"})
    if unknown:
        raise ConfigError(f"{source}: peers.{role} has unknown key(s) {unknown}")
    repo = raw.get("repo") or f"{config.owner}/{config.capability}-{role}"
    owner, _, name = str(repo).partition("/")
    if not owner or not name:
        raise ConfigError(f"{source}: peers.{role}.repo '{repo}' is not '<owner>/<repo>'")
    url = raw.get("url") or f"http://{PEER_SERVICE_PREFIX}-{config.capability_slug}-{role}"
    if not str(url).startswith(("http://", "https://")):
        raise ConfigError(f"{source}: peers.{role}.url '{url}' must be an http(s) URL")
    return Peer(role=role, repo=str(repo), url=str(url).rstrip("/"),
                declared_repo="repo" in raw, declared_url="url" in raw)


def component_url_env(component: str) -> str:
    """`<COMPONENT>_URL` — the one dev↔test addressing convention in use today. An environment
    variable name cannot carry a hyphen, so one becomes an underscore."""
    return f"{component.upper().replace('-', '_')}_URL"


_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _ephemeral(raw: object, source: str) -> tuple[str | None, tuple[SecretTemplate, ...],
                                                  tuple[tuple[str, str], ...]]:
    if raw is None:
        return None, (), ()
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: `ephemeral` must be a mapping with optional platform, "
                          f"secrets, test_env")
    unknown = sorted(set(raw) - {"platform", "secrets", "test_env"})
    if unknown:
        raise ConfigError(f"{source}: ephemeral has unknown key(s) {unknown}")

    platform = raw.get("platform")
    if platform is not None:
        platform = str(platform)
        if platform.startswith("/") or ".." in Path(platform).parts:
            # Resolved against the sidecar's own folder, which is what a use's image COPYs.
            raise ConfigError(f"{source}: ephemeral.platform '{platform}' must be a relative path "
                              f"beside the sidecar")

    secrets = []
    for index, entry in enumerate(raw.get("secrets") or ()):
        where = f"{source}: ephemeral.secrets[{index}]"
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ConfigError(f"{where}: must be a mapping with a `name`")
        data = entry.get("data")
        if not isinstance(data, dict) or not data or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ConfigError(f"{where}: `data` must be a non-empty mapping of string to string")
        secrets.append(SecretTemplate(name=str(entry["name"]), data=tuple(data.items())))

    test_env = raw.get("test_env") or {}
    if not isinstance(test_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in test_env.items()):
        raise ConfigError(f"{source}: ephemeral.test_env must be a mapping of variable name to "
                          f"string template")
    for name in test_env:
        if not _ENV_NAME.match(name):
            raise ConfigError(f"{source}: ephemeral.test_env name '{name}' is not an environment "
                              f"variable name (UPPER_SNAKE_CASE)")
    return platform, tuple(secrets), tuple(test_env.items())


# ── the gate ────────────────────────────────────────────────────────────────────────────────

@dataclass
class Report:
    """What `lint` found. Errors fail; warnings are read and not acted on."""

    oks: list[str]
    warns: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def lint(folder: str | Path = ".") -> Report:
    """Validate one sidecar against `foundry-task-orchestration-actor/agentic-context/v1`.

    A sidecar declaring some other `context:` is read, warned, and not checked further — the same
    discipline papeete-actor applies to a card: UNMIGRATED is not non-conformant, and migrating is
    the owning pair's own act.
    """
    path = Path(folder)
    if path.is_dir():
        path = path / SIDECAR
    report = Report(oks=[], warns=[], errors=[])

    if not path.exists():
        report.errors.append(f"{path}: no such file")
        return report
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        report.errors.append(f"{path}: does not parse or cannot be read: {e}")
        return report
    if not isinstance(raw, dict):
        report.errors.append(f"{path}: not a mapping")
        return report
    if raw.get("context") != CONTRACT:
        report.warns.append(
            f"{path}: declares '{raw.get('context')}' — UNMIGRATED, not checked against {CONTRACT}"
        )
        return report

    try:
        config = CapabilityConfig.from_dict(raw, source=str(path), root=path.parent)
    except ConfigError as e:
        report.errors.append(str(e))
        return report

    report.oks.append(f"{path} conforms to {CONTRACT}")
    report.oks.append(f"capability      {config.capability}")
    report.oks.append(f"actor           {config.actor_name}")
    report.oks.append(f"registry path   {config.capability_path}")
    report.oks.append(f"components      {', '.join(config.components)}")
    for peer in (config.implementation, config.testing):
        report.oks.append(f"{peer.role:<15} {peer.repo} @ {peer.url}")
    if config.platform is None and config.secrets:
        # Not an error: a secret may point at something outside the namespace. But secrets that
        # name a stand-in nobody deploys is the shape of a half-deleted `platform:` line.
        report.warns.append(
            f"{path}: ephemeral.secrets are declared with no ephemeral.platform — whatever they "
            f"point at must already be reachable from a fresh namespace"
        )
    return report
