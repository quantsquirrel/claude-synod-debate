"""Tests for tools/provider_backend.py — bridge↔direct backend resolution."""

import importlib.util
import json
from pathlib import Path

import pytest

_pb_path = Path(__file__).parent.parent / "tools" / "provider_backend.py"
_spec = importlib.util.spec_from_file_location("provider_backend", _pb_path)
provider_backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provider_backend)

BRIDGE = provider_backend.BRIDGE
DIRECT = provider_backend.DIRECT
get_backend = provider_backend.get_backend
resolve_entry = provider_backend.resolve_entry
resolve_roster = provider_backend.resolve_roster
BackendResolutionError = provider_backend.BackendResolutionError


# ---------------------------------------------------------------------------
# get_backend — precedence + fallback
# ---------------------------------------------------------------------------


class TestGetBackend:
    def test_default_is_bridge(self, monkeypatch):
        monkeypatch.delenv(provider_backend.BACKEND_ENV, raising=False)
        assert get_backend() == BRIDGE

    def test_env_direct(self, monkeypatch):
        monkeypatch.setenv(provider_backend.BACKEND_ENV, "direct")
        assert get_backend() == DIRECT

    def test_env_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv(provider_backend.BACKEND_ENV, "  DIRECT  ")
        assert get_backend() == DIRECT

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv(provider_backend.BACKEND_ENV, "direct")
        assert get_backend("bridge") == BRIDGE

    def test_unknown_value_falls_back_to_bridge(self, monkeypatch):
        monkeypatch.setenv(provider_backend.BACKEND_ENV, "gibberish")
        assert get_backend() == BRIDGE

    def test_unknown_explicit_falls_back_to_bridge(self, monkeypatch):
        monkeypatch.delenv(provider_backend.BACKEND_ENV, raising=False)
        assert get_backend("nonsense") == BRIDGE


# ---------------------------------------------------------------------------
# resolve_entry — bridge identity + direct rewrites
# ---------------------------------------------------------------------------


class TestResolveEntryBridge:
    def test_bridge_is_identity_copy(self):
        entry = {"provider": "gemini", "cli": "agy-cli", "model": "3.5-flash", "timeout_sec": 60}
        out = resolve_entry(entry, BRIDGE)
        assert out == entry
        assert out is not entry  # copy, not alias

    def test_bridge_does_not_mutate_input(self):
        entry = {"provider": "openai", "cli": "cliproxy-cli", "model": "gpt55fast"}
        resolve_entry(entry, BRIDGE)
        assert entry["cli"] == "cliproxy-cli"
        assert entry["model"] == "gpt55fast"


class TestResolveEntryDirect:
    def test_gemini_35_flash_to_flash_latest(self):
        entry = {"provider": "gemini", "cli": "agy-cli", "model": "3.5-flash", "thinking": "high"}
        out = resolve_entry(entry, DIRECT)
        assert out["cli"] == "gemini-3"
        assert out["model"] == "flash-latest"
        assert out["thinking"] == "high"  # untouched
        assert out["backend"] == DIRECT
        assert out["bridge_model"] == "3.5-flash"

    def test_openai_gpt55fast_to_gpt55(self):
        entry = {"provider": "openai", "cli": "cliproxy-cli", "model": "gpt55fast"}
        out = resolve_entry(entry, DIRECT)
        assert out["cli"] == "openai-cli"
        assert out["model"] == "gpt55"

    def test_openai_gpt54mini_passthrough(self):
        entry = {"provider": "openai", "cli": "cliproxy-cli", "model": "gpt54mini"}
        out = resolve_entry(entry, DIRECT)
        assert out["cli"] == "openai-cli"
        assert out["model"] == "gpt54mini"

    def test_openai_gpt55_passthrough(self):
        entry = {"provider": "openai", "cli": "cliproxy-cli", "model": "gpt55"}
        out = resolve_entry(entry, DIRECT)
        assert out["model"] == "gpt55"

    def test_unknown_model_raises(self):
        entry = {"provider": "openai", "cli": "cliproxy-cli", "model": "totally-unknown"}
        with pytest.raises(BackendResolutionError):
            resolve_entry(entry, DIRECT)

    def test_unknown_provider_raises(self):
        entry = {"provider": "anthropic", "cli": "x", "model": "y"}
        with pytest.raises(BackendResolutionError):
            resolve_entry(entry, DIRECT)

    def test_does_not_mutate_input(self):
        entry = {"provider": "gemini", "cli": "agy-cli", "model": "3.5-flash"}
        resolve_entry(entry, DIRECT)
        assert entry["cli"] == "agy-cli"
        assert entry["model"] == "3.5-flash"


# ---------------------------------------------------------------------------
# resolve_roster against the REAL config/model_matrix.json
# ---------------------------------------------------------------------------


class TestResolveRealMatrix:
    @pytest.fixture
    def matrix(self):
        path = Path(__file__).parent.parent / "config" / "model_matrix.json"
        return json.loads(path.read_text())

    def test_every_tier_resolves_direct(self, matrix):
        """Direct backend must map every bridge roster entry across all tiers.
        Guards against a new model string sneaking into the matrix without a
        DIRECT_MODEL translation."""
        for tier, roster in matrix["tiers"].items():
            resolved = resolve_roster(roster, DIRECT)
            for entry in resolved:
                assert entry["cli"] in ("gemini-3", "openai-cli"), tier
                assert entry["backend"] == DIRECT

    def test_bridge_roster_unchanged(self, matrix):
        for roster in matrix["tiers"].values():
            assert resolve_roster(roster, BRIDGE) == roster


class TestResolveEntryGuards:
    def test_unsupported_backend_raises(self):
        entry = {"provider": "gemini", "cli": "agy-cli", "model": "3.5-flash"}
        with pytest.raises(BackendResolutionError):
            resolve_entry(entry, "quantum")


# ---------------------------------------------------------------------------
# main() CLI — stdin roster + flags + exit codes
# ---------------------------------------------------------------------------


class TestMainCLI:
    def _run(self, argv, capsys, monkeypatch, stdin=None):
        import sys

        monkeypatch.setattr(sys, "argv", ["provider_backend.py"] + argv)
        if stdin is not None:
            import io

            monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        provider_backend.main()
        return capsys.readouterr().out

    def test_direct_via_roster_json_file(self, tmp_path, capsys, monkeypatch):
        roster = [{"provider": "gemini", "cli": "agy-cli", "model": "3.5-flash"}]
        p = tmp_path / "roster.json"
        p.write_text(json.dumps(roster))
        out = self._run(["--backend", "direct", "--roster-json", str(p)], capsys, monkeypatch)
        data = json.loads(out)
        assert data["backend"] == "direct"
        assert data["models"][0]["cli"] == "gemini-3"

    def test_bridge_via_stdin(self, capsys, monkeypatch):
        roster = [{"provider": "openai", "cli": "cliproxy-cli", "model": "gpt55fast"}]
        out = self._run(["--backend", "bridge"], capsys, monkeypatch, stdin=json.dumps(roster))
        data = json.loads(out)
        assert data["backend"] == "bridge"
        assert data["models"][0]["model"] == "gpt55fast"

    def test_non_list_roster_exits_2(self, capsys, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._run(["--backend", "bridge"], capsys, monkeypatch, stdin=json.dumps({"x": 1}))
        assert exc.value.code == 2

    def test_unreadable_roster_exits_2(self, tmp_path, capsys, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._run(
                ["--roster-json", str(tmp_path / "missing.json")], capsys, monkeypatch
            )
        assert exc.value.code == 2

    def test_unmappable_direct_roster_exits_2(self, capsys, monkeypatch):
        roster = [{"provider": "openai", "cli": "cliproxy-cli", "model": "no-such-model"}]
        with pytest.raises(SystemExit) as exc:
            self._run(["--backend", "direct"], capsys, monkeypatch, stdin=json.dumps(roster))
        assert exc.value.code == 2
