"""The CLI — the gate CI runs, and the derivation table an operator reads."""
from __future__ import annotations

import pytest

from foundry_task_orchestration_actor import cli


def test_lint_passes_on_a_conformant_sidecar(config, tmp_path, capsys):
    assert cli.main(["lint", str(tmp_path)]) == 0
    assert "conforms" in capsys.readouterr().out


def test_lint_fails_on_a_missing_sidecar(tmp_path, capsys):
    assert cli.main(["lint", str(tmp_path / "nowhere")]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_lint_fails_on_a_broken_sidecar(sidecar_dict, write_sidecar, capsys):
    del sidecar_dict["components"]
    assert cli.main(["lint", str(write_sidecar(sidecar_dict))]) == 1
    assert "components" in capsys.readouterr().out


def test_lint_checks_rendered_cards_too(config, tmp_path, capsys):
    assert cli.main(["render-cards", str(tmp_path)]) == 0
    assert cli.main(["lint", str(tmp_path)]) == 0
    assert "sidecar and cards conform" in capsys.readouterr().out


def test_show_prints_every_derived_rendering(config, tmp_path, capsys, monkeypatch):
    for name in ("IMPLEMENTATION_URL", "TESTING_URL", "IMAGE_REGISTRY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["show", str(tmp_path), "--registry", "reg.example.com"]) == 0
    out = capsys.readouterr().out
    assert config.capability_path in out
    assert config.implementation.repo in out
    assert f"{config.testing.url}  (derived)" in out
    assert "reg.example.com/acme.parts/sup.007.wid/backend/tests:<version>" in out
    assert "test-sup-007-wid-task-nnn-sup-007-wid-stub" in out
    assert "http://test-sup-007-wid-task-nnn-sup-007-wid-backend" in out


def test_show_says_where_a_peer_url_came_from(config, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TESTING_URL", "http://localhost:8081")
    assert cli.main(["show", str(tmp_path)]) == 0
    assert "http://localhost:8081  ($TESTING_URL)" in capsys.readouterr().out


def test_render_cards_refuses_a_broken_sidecar(sidecar_dict, write_sidecar, capsys):
    sidecar_dict["capability"] = "NO.SEGMENT"
    assert cli.main(["render-cards", str(write_sidecar(sidecar_dict))]) == 2


def test_the_cli_needs_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])
