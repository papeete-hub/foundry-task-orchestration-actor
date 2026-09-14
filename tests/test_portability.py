"""The extraction gate.

This package was pulled out of one capability's own repo, and it is published publicly. The whole
claim it makes — that a second capability instantiates it by writing a sidecar and nothing else —
is false the moment a single identifier from the instance it came from survives in `src/`.

A grep is a blunt instrument, and that is the point: it cannot be argued with during a review, and
it fails on the line that reintroduces the literal rather than three months later when someone
tries the second capability.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Identifiers belonging to the instance this package was extracted from — its id, its workload
# prefix, the database its stand-in was named for, the context its kubeconfig was hardcoded to —
# plus the shapes a lab reference usually takes. Extend this when a new instance is founded;
# nothing here should ever be authored against a client in the first place, but defence in depth
# is cheap.
FORBIDDEN = re.compile(
    r"bnk|rlvr|reliever|beneficiary|sup-002|sup\.002|banking-governance|banking-tech|"
    r"banking-knowledge|papeete-foundry|docker-desktop",
    re.IGNORECASE,
)

# The knowledge tools a capability's sidecar happens to name. This package must not know them
# either: `ground_in` supplies its own argv, so a `kpack`/`kontract` mention in `src/` would mean
# the machinery had grown an opinion about which tools exist.
NO_TOOL_NAMES = re.compile(r"\bkpack\b|\bkontract\b|\bkmint\b|\bkcapture\b", re.IGNORECASE)


# Text sources only — a .pyc under __pycache__ is build junk, not something to grep, and
# reading one as text raises rather than reporting a leak.
_TEXT = {".py", ".yaml", ".yml", ".md", ".toml", ".json"}


def _sources():
    return sorted(p for p in SRC.rglob("*") if p.is_file() and p.suffix in _TEXT)


def test_no_instance_identifier_survives_in_src():
    leaks = []
    for path in _sources():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if FORBIDDEN.search(line):
                leaks.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not leaks, "instance-specific identifiers found in src/:\n  " + "\n  ".join(leaks)


def test_no_knowledge_tool_is_named_in_src():
    leaks = []
    for path in _sources():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if NO_TOOL_NAMES.search(line):
                leaks.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not leaks, (
        "a knowledge tool is named in src/ — `ground_in` entries carry their own `fetch:` argv, "
        "so this package must know none of them:\n  " + "\n  ".join(leaks)
    )


def test_the_gate_would_actually_catch_something():
    """A gate that cannot fail is not a gate. Both patterns are checked against a known match, so
    a future edit that guts the regex is caught by this file rather than by its silence."""
    assert FORBIDDEN.search("papeete-foundry/SOME.CAP.ID-implementation")
    assert NO_TOOL_NAMES.search("subprocess.run(['kpack', 'pack'])")
    assert not FORBIDDEN.search("papeete-actor-synchronous-messaging")
    assert not NO_TOOL_NAMES.search("a package that repacks nothing")
