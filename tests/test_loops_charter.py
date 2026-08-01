"""Tests for the charter engine (goal -> governed loop contract)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from coco_loops import charter as charter_mod
from coco_loops import lifetime
from coco_loops import runner as runner_mod
from coco_loops.charter import Charter, CharterError
from coco_loops.cli import infer_level, main
from coco_loops.paths import reset_config
from coco_loops.runner import PreflightResult


def _valid_dict() -> dict:
    return {
        "loop_id": "keep-build-green",
        "objective": "Make failing frontend tests pass and keep the build green.",
        "principles": [
            "Only touch code covered by a failing test; smallest diff.",
            "If a test is genuinely stale, propose updating it rather than weakening it silently.",
        ],
        "guardrails": ["No dependency changes.", "No CI config edits."],
        "context": ["frontend test suite"],
        "inputs": ["red tests from the detector"],
        "lifetime": "4h",
        "level": "L1",
    }


@pytest.fixture
def charter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loops_dir = tmp_path / "loops"
    runtime_dir = tmp_path / "coco"
    runtime_dir.mkdir()
    monkeypatch.setenv("COCO_LOOPS_DIR", str(loops_dir))
    monkeypatch.setenv("COCO_LOOPS_HOME", str(runtime_dir))
    monkeypatch.setenv("COCO_LOOP_RUN_LOG", str(runtime_dir / "loop-run-log.jsonl"))
    reset_config()
    yield {"loops_dir": loops_dir, "runtime_dir": runtime_dir}
    reset_config()


class TestFromDict:
    def test_coerces_and_defaults(self):
        charter = Charter.from_dict({"loop_id": "x", "objective": "o", "principles": "one rule"})
        assert charter.principles == ["one rule"]  # scalar coerced to list
        assert charter.lifetime == "4h"  # default
        assert charter.level == "L1"  # default
        assert charter.guardrails == []

    def test_ignores_unknown_keys(self):
        charter = Charter.from_dict({"loop_id": "x", "objective": "o", "bogus": 1})
        assert charter.loop_id == "x"

    def test_non_dict_raises(self):
        with pytest.raises(CharterError):
            Charter.from_dict(["not", "a", "dict"])


class TestValidate:
    def test_valid_charter_passes(self):
        assert validate_ok(_valid_dict())

    def test_bad_slug_rejected(self):
        d = _valid_dict()
        d["loop_id"] = "Not A Slug!"
        assert any("slug" in p for p in problems(d))

    def test_missing_objective_rejected(self):
        d = _valid_dict()
        d["objective"] = "  "
        assert any("objective" in p for p in problems(d))

    def test_no_principles_rejected(self):
        d = _valid_dict()
        d["principles"] = []
        assert any("principle" in p for p in problems(d))

    def test_level_above_l1_rejected(self):
        d = _valid_dict()
        d["level"] = "L2"
        found = problems(d)
        assert any("I7/I8" in p for p in found)

    def test_bad_lifetime_rejected(self):
        d = _valid_dict()
        d["lifetime"] = "later"
        assert any("duration" in p for p in problems(d))

    @pytest.mark.parametrize(
        ("field", "text", "invariant"),
        [
            ("principles", "Disable the verifier when it is slow.", "I1"),
            ("principles", "Auto-merge once the tests pass.", "I4/I8"),
            ("guardrails", "Edit the constitution to allow this loop.", "I2/I6"),
            ("principles", "Call api.openai.com directly for speed.", "I3"),
            ("principles", "git push the fix when it is green.", "I5"),
        ],
    )
    def test_floor_violation_rejected(self, field: str, text: str, invariant: str):
        d = _valid_dict()
        d[field] = [text]
        found = problems(d)
        assert any(invariant in p for p in found), found


class TestRender:
    def test_render_has_goal_principles_and_floor(self):
        text = charter_mod.render(Charter.from_dict(_valid_dict()))
        assert "keep the build green" in text
        assert "propose-only" in text
        assert "Only touch code covered by a failing test" in text
        assert "immutable constitution" in text


class TestContractMarkdown:
    def test_contract_declares_floor_and_level(self):
        md = charter_mod.to_contract_markdown(Charter.from_dict(_valid_dict()))
        assert "Inherits: loop-constitution.md (Layer 0, immutable)" in md
        assert "keep-build-green" in md
        assert infer_level(md) == "L1"


class TestInstall:
    def test_install_writes_contract_and_arms_lifetime(self, charter_env):
        # pre-existing kill-switch should be cleared by install
        (charter_env["runtime_dir"] / "keep-build-green.disabled").write_text("", encoding="utf-8")

        result = charter_mod.install(Charter.from_dict(_valid_dict()))

        contract = Path(result["contract_path"])
        assert contract.exists()
        assert contract.name == "keep-build-green.contract.md"
        assert result["armed_cap_seconds"] == 4 * 3600
        assert lifetime.cap_seconds("keep-build-green", state_dir=charter_env["runtime_dir"]) == 4 * 3600
        assert lifetime.used_seconds("keep-build-green", state_dir=charter_env["runtime_dir"]) == 0
        assert not (charter_env["runtime_dir"] / "keep-build-green.disabled").exists()

    def test_install_no_arm_writes_contract_only(self, charter_env):
        result = charter_mod.install(Charter.from_dict(_valid_dict()), arm=False)
        assert Path(result["contract_path"]).exists()
        assert result["armed_cap_seconds"] is None
        assert lifetime.cap_seconds("keep-build-green", state_dir=charter_env["runtime_dir"]) is None

    def test_invalid_charter_never_reaches_disk(self, charter_env):
        d = _valid_dict()
        d["principles"] = ["Auto-merge once tests pass."]  # floor violation
        with pytest.raises(CharterError):
            charter_mod.install(Charter.from_dict(d))
        assert not (charter_env["loops_dir"] / "keep-build-green.contract.md").exists()
        assert lifetime.cap_seconds("keep-build-green", state_dir=charter_env["runtime_dir"]) is None


class TestLoadJson:
    def test_bad_json_raises_charter_error(self):
        with pytest.raises(CharterError):
            charter_mod.load_charter_json("{not valid json")


class TestCharterCli:
    def test_schema_prints_expected_keys(self, charter_env, capsys):
        rc = main(["charter", "--schema"])
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert "loop_id" in parsed and "principles" in parsed and "lifetime" in parsed

    def test_from_stdin_renders_valid_charter(self, charter_env, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_dict())))
        rc = main(["charter", "--from-json", "-"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Loop charter: keep-build-green" in out

    def test_invalid_charter_exits_nonzero(self, charter_env, capsys, monkeypatch):
        d = _valid_dict()
        d["level"] = "L3"
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(d)))
        rc = main(["charter", "--from-json", "-"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "invalid" in err

    def test_install_via_cli_writes_and_arms(self, charter_env, capsys, monkeypatch):
        charter_file = charter_env["loops_dir"].parent / "charter.json"
        charter_file.write_text(json.dumps(_valid_dict()), encoding="utf-8")

        # --install now also warns (stderr only, does not block) on a broken default
        # maker pre-flight; stub it so this test never shells out to a real
        # cursor-agent process.
        monkeypatch.setattr(
            runner_mod,
            "cursor_agent_preflight",
            lambda: PreflightResult(ok=True, detail="stub: skipped in test"),
        )

        rc = main(["charter", "--from-json", str(charter_file), "--install"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "installed:" in out
        assert (charter_env["loops_dir"] / "keep-build-green.contract.md").exists()
        assert lifetime.cap_seconds("keep-build-green", state_dir=charter_env["runtime_dir"]) == 4 * 3600

        # and the freshly-installed loop is now discoverable
        list_rc = main(["list"])
        list_out = capsys.readouterr().out
        assert list_rc == 0
        assert "keep-build-green" in list_out


def problems(d: dict) -> list[str]:
    return charter_mod.validate(Charter.from_dict(d))


def validate_ok(d: dict) -> bool:
    return charter_mod.validate(Charter.from_dict(d)) == []
