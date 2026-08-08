# Copyright Sierra
"""What was actually running when the benchmark ran — read, not asserted.

Everything here is CAPTURED: from the process environment, from git, or from the
backend's own agent configuration over HTTP. Nothing is a constant a human typed
into a manifest, because a hand-asserted environment fact is a claim about the past
that nobody re-checks.

THE METADATA HEAD
-----------------
Whissle's cascade has one layer nothing else has: the whissle-large metadata head,
which emits per-interim emotion/intent/age/gender alongside the transcript. In
production it is OFF — ``WHISSLE_STT_TRANSPORT`` is unset and every agent in the org
routes STT to a third party (AssemblyAI / Sarvam / Deepgram). So every benchmark run
to date measured our stack with its distinguishing layer disabled.

That is a load-bearing caveat for any competitive reading of these numbers, and it is
exactly the sort of caveat that evaporates between the run and the slide. So it is
captured on every manifest from two independent sources:

1. ``WHISSLE_STT_TRANSPORT`` in the harness environment (:func:`stt_transport_env`);
2. the ``stt_provider`` the backend reports for the agent under test
   (:func:`agent_stt_provider`) — the authoritative one, since it is what the
   backend will actually do rather than what the harness hoped.

A text run gets the same block. It is *more* important there, not less: a text run
has no STT at all, so the metadata head is doubly absent, and a reader who sees the
block cannot later assume it was on.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schema import NOT_RECORDED

#: Set to a whissle transport (e.g. ``grpc``) to route STT through whissle-large and
#: light up the metadata head. Unset in production, and therefore unset for every run
#: this archive has ever seen.
STT_TRANSPORT_ENV = "WHISSLE_STT_TRANSPORT"

#: STT providers that are NOT ours. If the agent under test names one of these, the
#: metadata head was not in the path no matter what the harness environment said.
THIRD_PARTY_STT = ("deepgram", "assemblyai", "sarvam", "openai", "azure", "google")

METADATA_HEAD_OFF_REASON = (
    "the whissle-large metadata head was NOT in this run's path. Production STT "
    f"routes to a third-party provider and {STT_TRANSPORT_ENV} is unset, so the "
    "per-interim emotion/intent/age/gender sidecar — the layer that distinguishes "
    "our cascade from a stock STT+LLM+TTS stack — was disabled. Any competitive "
    "reading of this run is a reading of the cascade WITHOUT its differentiator."
)

METADATA_HEAD_TEXT_REASON = (
    "this run was driven over text, so there was no speech recognition at all and "
    "therefore no metadata head. Recorded explicitly so a later reader cannot assume "
    "the head was contributing."
)


def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: float = 10.0) -> Optional[str]:
    """Best-effort subprocess capture. Never raises — an environment probe must not
    be able to sink a run that already completed."""
    try:
        out = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except Exception:  # noqa: BLE001 — capture is best-effort by design
        return None
    return out.stdout.strip() or None


# ── git ────────────────────────────────────────────────────────────────────────


@dataclass
class GitState:
    """A repository's exact state. ``dirty`` matters as much as ``sha``: a run
    produced from a working tree with uncommitted edits is not reproducible from the
    SHA alone, and saying so is the difference between a reproduce command that works
    and one that looks like it should."""

    sha: str = NOT_RECORDED
    short_sha: str = NOT_RECORDED
    branch: str = NOT_RECORDED
    dirty: Optional[bool] = None
    describe: str = NOT_RECORDED
    source: str = NOT_RECORDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "branch": self.branch,
            "dirty": self.dirty,
            "dirty_note": (
                "uncommitted changes were present — this SHA alone does not reproduce "
                "the run" if self.dirty else None
            ),
            "describe": self.describe,
            "source": self.source,
        }


def git_state(repo: Optional[Path]) -> GitState:
    """Read a git working tree's state, degrading to ``not recorded`` outside one."""
    if repo is None or not Path(repo).exists():
        return GitState(source="no repository path available")
    repo = Path(repo)
    sha = _run(["git", "rev-parse", "HEAD"], repo)
    if not sha:
        return GitState(source=f"{repo} is not a git working tree")
    status = _run(["git", "status", "--porcelain"], repo)
    return GitState(
        sha=sha,
        short_sha=sha[:9],
        branch=_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo) or NOT_RECORDED,
        dirty=bool(status),
        describe=_run(["git", "describe", "--tags", "--always", "--dirty"], repo)
        or NOT_RECORDED,
        source=f"git -C {repo} rev-parse HEAD",
    )


def harness_repo(start: Optional[Path] = None) -> Optional[Path]:
    """The tau2-bench-w checkout this module is running from."""
    here = Path(start or __file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "tau2").is_dir():
            return candidate
    return None


#: Where the backend's commit comes from when the deployment does not serve one. The
#: backend exposes ``GET /health`` -> ``{"ok": true}`` and nothing else identifying,
#: so a SHA has to be injected by whoever deployed it.
BACKEND_SHA_ENV = "WHISSLE_BACKEND_SHA"

BACKEND_SHA_UNAVAILABLE = (
    "the backend serves no version or commit endpoint (GET /health returns only "
    f"{{'ok': true}}), so its exact revision is recoverable only if the deployer sets "
    f"{BACKEND_SHA_ENV}. Recorded as {NOT_RECORDED!r} rather than guessed from a "
    "deploy time or a local checkout, which would be a different artifact."
)


def backend_state(base_url: str, api_key: Optional[str] = None) -> dict[str, Any]:
    """What the backend says about itself. Reachability is checked because "the
    backend was up" is itself part of a run's provenance."""
    sha = os.getenv(BACKEND_SHA_ENV)
    block: dict[str, Any] = {
        "base_url": base_url or NOT_RECORDED,
        "sha": sha or NOT_RECORDED,
        "sha_source": f"${BACKEND_SHA_ENV}" if sha else BACKEND_SHA_UNAVAILABLE,
        "reachable": None,
        "health": None,
    }
    if not base_url:
        return block
    try:
        import requests

        r = requests.get(f"{base_url.rstrip('/')}/health", timeout=15)
        block["reachable"] = r.status_code < 400
        block["health"] = r.json() if r.status_code < 400 else f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001 — a probe never sinks an archive write
        block["reachable"] = False
        block["health"] = f"{type(exc).__name__}: {exc}"
    return block


# ── the metadata head ──────────────────────────────────────────────────────────


def stt_transport_env() -> Optional[str]:
    """``WHISSLE_STT_TRANSPORT`` as the harness saw it. ``None`` means unset, which
    means the metadata head was off."""
    value = os.getenv(STT_TRANSPORT_ENV)
    return value.strip() if value and value.strip() else None


def agent_stt_provider(
    base_url: str, api_key: Optional[str], agent_id: Optional[str]
) -> dict[str, Any]:
    """The STT provider the BACKEND has configured for the agent under test.

    This outranks the harness environment: it is what the backend will actually do.
    Fetched over HTTP, never assumed — and a fetch failure is recorded as a failure
    rather than silently becoming "no third party", which would read as good news.
    """
    block: dict[str, Any] = {
        "agent_id": agent_id or NOT_RECORDED,
        "stt_provider": NOT_RECORDED,
        "tts_provider": NOT_RECORDED,
        "source": NOT_RECORDED,
        "fetch_error": None,
    }
    if not (base_url and api_key and agent_id):
        block["fetch_error"] = (
            "base URL, API key and agent id are all required to read the agent's STT "
            "configuration; at least one was missing"
        )
        return block
    try:
        import requests

        r = requests.get(
            f"{base_url.rstrip('/')}/api/agents/{agent_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if r.status_code >= 400:
            block["fetch_error"] = f"HTTP {r.status_code}: {r.text[:200]}"
            return block
        payload = r.json()
    except Exception as exc:  # noqa: BLE001
        block["fetch_error"] = f"{type(exc).__name__}: {exc}"
        return block

    if isinstance(payload, dict):
        block["stt_provider"] = payload.get("stt_provider") or NOT_RECORDED
        block["tts_provider"] = payload.get("tts_provider") or NOT_RECORDED
        block["source"] = f"GET {base_url.rstrip('/')}/api/agents/{{agent_id}}"
    return block


def metadata_head(
    *,
    modality: str,
    base_url: str = "",
    api_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    probe_backend: bool = True,
) -> dict[str, Any]:
    """The metadata-head block that goes on EVERY manifest.

    ``in_path`` is the one-lookup answer. It is ``True`` only when a whissle
    transport is configured AND the agent is not routed to a third-party STT — two
    independent conditions, because either one alone can silently disable the head.
    """
    from .schema import is_voice

    transport = stt_transport_env()
    agent = (
        agent_stt_provider(base_url, api_key, agent_id)
        if probe_backend
        else {"agent_id": agent_id or NOT_RECORDED, "stt_provider": NOT_RECORDED,
              "tts_provider": NOT_RECORDED, "source": "backend probe disabled",
              "fetch_error": None}
    )
    provider = str(agent.get("stt_provider") or "").lower()
    third_party = provider in THIRD_PARTY_STT

    if not is_voice(modality):
        in_path, reason = False, METADATA_HEAD_TEXT_REASON
    elif transport and not third_party:
        in_path, reason = True, (
            f"{STT_TRANSPORT_ENV}={transport!r} and the agent is not routed to a "
            "third-party STT, so the whissle-large metadata head was in the path"
        )
    else:
        bits = []
        if not transport:
            bits.append(f"{STT_TRANSPORT_ENV} is unset")
        if third_party:
            bits.append(f"the agent under test routes STT to {provider!r}")
        in_path = False
        reason = f"{METADATA_HEAD_OFF_REASON} ({'; '.join(bits)})"

    return {
        "in_path": in_path,
        "reason": reason,
        "captured_from": "environment + backend agent configuration, not asserted",
        "stt_transport_env": {
            "name": STT_TRANSPORT_ENV,
            "value": transport or NOT_RECORDED,
            "set": transport is not None,
        },
        "agent_stt": agent,
        "third_party_stt": third_party,
    }


# ── the whole environment ──────────────────────────────────────────────────────


@dataclass
class Environment:
    """Everything about the machine and services that produced a run."""

    harness_git: GitState = field(default_factory=GitState)
    backend: dict[str, Any] = field(default_factory=dict)
    metadata_head: dict[str, Any] = field(default_factory=dict)
    python: str = ""
    platform: str = ""
    hostname: str = ""
    #: Selected env vars, with secrets fingerprinted rather than recorded.
    env_vars: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_git": self.harness_git.to_dict(),
            "backend": self.backend,
            "metadata_head": self.metadata_head,
            "python": self.python,
            "platform": self.platform,
            "hostname": self.hostname,
            "env_vars": self.env_vars,
        }


#: Env vars worth recording. Anything whose name looks secret is fingerprinted by
#: :func:`_fingerprint` — an archive that leaks a live API key is a liability, and
#: "which key was used" is answerable without the key itself.
CAPTURED_ENV = (
    "WHISSLE_BASE",
    "WHISSLE_AGENT_ID",
    "WHISSLE_MODEL",
    STT_TRANSPORT_ENV,
    "LLM_PROVIDER",
    "TAU2_ARCHIVE_DIR",
    BACKEND_SHA_ENV,
)

SECRET_ENV = ("WHISSLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


def _fingerprint(value: str) -> str:
    """A stable, non-reversible handle for a secret: enough to answer "was it the
    same key?", useless to anyone who copies it out of the archive."""
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def capture(
    *,
    modality: str,
    base_url: str = "",
    api_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    probe_backend: bool = True,
) -> Environment:
    """Snapshot the environment. Every probe degrades to ``not recorded``; nothing
    here can raise into a caller that has already finished a benchmark run."""
    base = base_url or os.getenv("WHISSLE_BASE") or ""
    key = api_key or os.getenv("WHISSLE_API_KEY")

    env_vars: dict[str, str] = {}
    for name in CAPTURED_ENV:
        env_vars[name] = os.getenv(name) or NOT_RECORDED
    for name in SECRET_ENV:
        raw = os.getenv(name)
        env_vars[name] = _fingerprint(raw) if raw else NOT_RECORDED

    return Environment(
        harness_git=git_state(harness_repo()),
        backend=backend_state(base, key) if probe_backend else {
            "base_url": base or NOT_RECORDED, "sha": os.getenv(BACKEND_SHA_ENV) or NOT_RECORDED,
            "sha_source": BACKEND_SHA_UNAVAILABLE, "reachable": None,
            "health": "backend probe disabled",
        },
        metadata_head=metadata_head(
            modality=modality, base_url=base, api_key=key, agent_id=agent_id,
            probe_backend=probe_backend,
        ),
        python=sys.version.split()[0],
        platform=platform.platform(),
        hostname=platform.node(),
        env_vars=env_vars,
    )
