"""Separate-adversary verifier (A gate) for loop change proposals.

Default stance is REJECT. The verifier runs the injected test runner itself and
never trusts a caller's claim that tests passed. Also enforces loop-constitution
invariants (denylist paths, verifier bypass, raw-cloud egress, constitution edits).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from coco_loops.paths import resolve_constitution_path

# Paths/patterns that trigger auto-REJECT or ESCALATE_HUMAN when touched.
_DENYLIST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)\.env", re.I),
    re.compile(r"/secrets/", re.I),
    re.compile(r"/credentials/", re.I),
    re.compile(r"/migrations/", re.I),
    re.compile(r"alembic/versions/", re.I),
    re.compile(r"(^|/)auth/", re.I),
    re.compile(r"(^|/)payments/", re.I),
    re.compile(r"(^|/)billing/", re.I),
)

_CONSTITUTION_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"loop-constitution\.md$", re.I),
    re.compile(r"loops/loop-constitution\.md$", re.I),
)

_CONSTRAINTS_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"loop-constraints\.md$", re.I),
    re.compile(r"loop-constraints", re.I),
)

_VERIFIER_DISABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"VERIFIER_DISABLED\s*=\s*True", re.I),
    re.compile(r"disable_verifier", re.I),
    re.compile(r"bypass_verifier", re.I),
    re.compile(r"skip_verifier", re.I),
    re.compile(r"no_verifier", re.I),
)

_RAW_CLOUD_HOSTS: frozenset[str] = frozenset({
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
})

# M0 fix-loop: high-blast-radius paths always require human review (ESCALATE wins over APPROVE).
M0_FORCE_ESCALATE_PATTERNS: tuple[str, ...] = (
    "crates/connect-m0-backend/src/schema.rs",
    "crates/connect-m0-backend/src/store.rs",  # DDL-bearing
    "crates/connect-m0-backend/src/llm.rs",
    "crates/connect-m0-backend/src/embed.rs",
    "crates/connect-sovereignty/src/guard.rs",
    "crates/connect-sovereignty/src/profile.rs",
    "crates/connect-m0-backend/src/server.rs",  # loopback-bind
    "catalog/m0.toml",
)


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


@dataclass
class ChangeSpec:
    """Description of a proposed change for adversarial review."""

    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""
    description: str = ""
    proposes_constitution_edit: bool = False
    proposes_constraints_edit: bool = False
    proposes_verifier_disable: bool = False
    proposes_raw_cloud_egress: bool = False
    raw_cloud_hosts: list[str] = field(default_factory=list)
    destructive_spine_op: bool = False


@dataclass
class ExecutedTestResult:
    passed: bool
    output: str = ""
    tests_run: int = 0
    tests_failed: int = 0


class TestRunner(Protocol):
    def __call__(self, change: ChangeSpec) -> ExecutedTestResult: ...


@dataclass
class VerifierResult:
    verdict: Verdict
    evidence: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.verdict == Verdict.APPROVE


def load_constitution(path: Path | None = None) -> str:
    target = path or resolve_constitution_path()
    return target.read_text(encoding="utf-8")


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _matches_any(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    normalized = _normalize_path(path)
    return any(p.search(normalized) for p in patterns)


def _is_crate_level_cargo_toml(path: str) -> bool:
    norm = _normalize_path(path)
    parts = norm.split("/")
    return len(parts) == 3 and parts[0] == "crates" and parts[2] == "Cargo.toml"


def _is_rust_surface(path: str) -> bool:
    norm = _normalize_path(path)
    return norm.startswith("crates/") or norm.endswith(".rs") or _is_crate_level_cargo_toml(norm)


def _crates_from_paths(files_changed: list[str]) -> set[str]:
    crates: set[str] = set()
    for path in files_changed:
        parts = _normalize_path(path).split("/")
        if len(parts) >= 2 and parts[0] == "crates":
            crates.add(parts[1])
    return crates


def _matches_m0_force_escalate(path: str) -> bool:
    norm = _normalize_path(path)
    for pattern in M0_FORCE_ESCALATE_PATTERNS:
        if pattern in norm or norm.endswith(pattern):
            return True
    if norm.startswith("crates/connect-m0-backend/") and norm.endswith("Cargo.toml"):
        return True
    if norm.startswith("crates/connect-sovereignty/") and norm.endswith("Cargo.toml"):
        return True
    return False


def _any_m0_force_escalate(files_changed: list[str]) -> bool:
    return any(_matches_m0_force_escalate(p) for p in files_changed)


def check_constitution_violations(change: ChangeSpec) -> VerifierResult | None:
    """Return a verdict if the change touches an invariant; else None."""
    evidence: list[str] = []

    if change.proposes_constitution_edit:
        evidence.append("Proposal edits the loop constitution (immutable to loops).")
        return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    if change.proposes_constraints_edit:
        evidence.append("Proposal edits loop-constraints / denylist (immutable to loops).")
        return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    for path in change.files_changed:
        if _matches_any(path, _CONSTITUTION_PATH_PATTERNS):
            evidence.append(f"Touched constitution path: {path}")
            return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)
        if _matches_any(path, _CONSTRAINTS_PATH_PATTERNS):
            evidence.append(f"Touched constraints/denylist path: {path}")
            return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    if change.proposes_verifier_disable:
        evidence.append("Proposal disables or bypasses the verifier.")
        return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    for path in change.files_changed:
        if "verifier" in _normalize_path(path).lower() and any(
            p.search(change.diff_summary) for p in _VERIFIER_DISABLE_PATTERNS
        ):
            evidence.append(f"Verifier bypass pattern in diff for: {path}")
            return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    if change.proposes_raw_cloud_egress:
        hosts = change.raw_cloud_hosts or sorted(_RAW_CLOUD_HOSTS)
        evidence.append(f"Raw public-cloud egress proposed: {', '.join(hosts)}")
        return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    for host in change.raw_cloud_hosts:
        if host in _RAW_CLOUD_HOSTS:
            evidence.append(f"Raw public-cloud host in change: {host}")
            return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    if change.destructive_spine_op:
        evidence.append("Destructive/spine operation requires human attendance.")
        return VerifierResult(verdict=Verdict.ESCALATE_HUMAN, evidence=evidence)

    for path in change.files_changed:
        if _matches_any(path, _DENYLIST_PATH_PATTERNS):
            evidence.append(f"Touched denylisted path: {path}")
            return VerifierResult(verdict=Verdict.ESCALATE_HUMAN, evidence=evidence)

    return None


def verify(
    change: ChangeSpec,
    test_runner: TestRunner,
    *,
    constitution_path: Path | None = None,
) -> VerifierResult:
    """Run constitution checks then execute tests. Default REJECT until proven."""
    _ = load_constitution(constitution_path)  # constitution must exist and be readable

    violation = check_constitution_violations(change)
    if violation is not None:
        return violation

    evidence: list[str] = [
        "Default stance: REJECT until verifier runs tests and finds strong evidence.",
    ]

    test_result = test_runner(change)
    evidence.append(
        f"Verifier executed tests: passed={test_result.passed}, "
        f"run={test_result.tests_run}, failed={test_result.tests_failed}."
    )
    if test_result.output:
        evidence.append(f"Test output (truncated): {test_result.output[:500]}")

    if not test_result.passed:
        evidence.append("Tests did not pass — REJECT.")
        return VerifierResult(verdict=Verdict.REJECT, evidence=evidence)

    if _any_m0_force_escalate(change.files_changed):
        evidence.append(
            "Touched M0 high-blast-radius path — ESCALATE_HUMAN (human review required)."
        )
        return VerifierResult(verdict=Verdict.ESCALATE_HUMAN, evidence=evidence)

    evidence.append("Tests passed and no constitution violations — APPROVE.")
    return VerifierResult(verdict=Verdict.APPROVE, evidence=evidence)


def make_test_runner_from_callable(
    fn: Callable[[ChangeSpec], ExecutedTestResult],
) -> TestRunner:
    return fn


@dataclass(frozen=True)
class ProposalVerdict:
    """Loop adapter result: verdict + human-readable reasoning."""

    verdict: Verdict
    reasoning: str


def _detect_raw_cloud_in_diff(diff_text: str) -> list[str]:
    return [host for host in _RAW_CLOUD_HOSTS if host in diff_text]


def _run_independent_tests(repo: Path, files_changed: list[str]) -> ExecutedTestResult:
    """Verifier-owned checks — never trust maker diagnostics."""
    repo = Path(repo)
    outputs: list[str] = []
    tests_run = 0
    tests_failed = 0
    passed = True

    fe_files = [f for f in files_changed if _normalize_path(f).startswith("frontend/")]
    be_files = [f for f in files_changed if _normalize_path(f).startswith("backend/")]
    rust_files = [f for f in files_changed if _is_rust_surface(f)]

    if fe_files:
        tests_run += 1
        proc = subprocess.run(
            ["npx", "tsc", "-b"],
            cwd=repo / "frontend",
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            passed = False
            tests_failed += 1
        outputs.append(
            f"tsc exit={proc.returncode}\n{(proc.stdout + proc.stderr)[:1500]}"
        )

    if be_files:
        tests_run += 1
        proc = subprocess.run(
            ["uv", "run", "python", "-m", "pytest", "-q", "--tb=no", "-x"],
            cwd=repo / "backend",
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            passed = False
            tests_failed += 1
        outputs.append(
            f"pytest exit={proc.returncode}\n{(proc.stdout + proc.stderr)[:1500]}"
        )

    if rust_files:
        tests_run += 1
        cargo_cmds: list[list[str]] = [
            ["cargo", "build", "--workspace"],
        ]
        test_packages = {"connect-m0-backend"} | _crates_from_paths(rust_files)
        for pkg in sorted(test_packages):
            cargo_cmds.append(["cargo", "test", "-p", pkg])
        cargo_cmds.append(
            ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"]
        )
        for cmd in cargo_cmds:
            proc = subprocess.run(
                cmd,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                passed = False
                tests_failed += 1
            label = " ".join(cmd[:3]) if cmd[0] == "cargo" else " ".join(cmd)
            outputs.append(
                f"{label} exit={proc.returncode}\n{(proc.stdout + proc.stderr)[:1500]}"
            )

    if tests_run == 0 and files_changed:
        passed = False
        outputs.append(
            "no recognized test surface for changed files → cannot verify → REJECT"
        )

    return ExecutedTestResult(
        passed=passed,
        output="\n".join(outputs),
        tests_run=tests_run,
        tests_failed=tests_failed,
    )


def verify_proposal(
    repo: Path,
    task: dict,
    files_changed: list[str],
    diff_text: str,
) -> ProposalVerdict:
    """Loop adapter entry point: constitution gate + independent test execution."""
    description = str(task.get("name") or task.get("instruction") or "")
    hosts_found = _detect_raw_cloud_in_diff(diff_text)
    change = ChangeSpec(
        files_changed=list(files_changed),
        diff_summary=diff_text[:8000],
        description=description,
        proposes_raw_cloud_egress=bool(hosts_found),
        raw_cloud_hosts=hosts_found,
    )

    def runner(_change: ChangeSpec) -> ExecutedTestResult:
        return _run_independent_tests(repo, files_changed)

    result = verify(change, runner)
    reasoning = " | ".join(result.evidence) if result.evidence else result.verdict.value
    return ProposalVerdict(verdict=result.verdict, reasoning=reasoning)
