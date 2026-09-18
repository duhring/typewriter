#!/usr/bin/env python3
"""Manage manual-first video projects, approvals, publication, and metrics.

The durable source of truth is owners-inbox/video-projects/<slug>/project.json.
Every mutation rewrites a human-readable, indexed project.md alongside it.
Large media stays in place and is referenced by path plus SHA-256 identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pka_index import GovernedRecordError, forget_markdown_artifact, index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = PKA_ROOT / "owners-inbox" / "video-projects"

STATE_SEQUENCE = [
    "developing",
    "brief-review",
    "brief-approved",
    "deck-review",
    "deck-approved",
    "recorded",
    "clean-ready",
    "manual-edit",
    "master-qc",
    "package-review",
    "package-approved",
    "private-upload",
    "youtube-qa",
    "release-approved",
    "published",
    "blog-review",
    "blog-approved",
    "complete",
]


def workflow_sequence(project: dict) -> list[str]:
    """Absent output selection means the original contract, without migration."""
    if "requested_outputs" not in project:
        return list(STATE_SEQUENCE)
    sequence = STATE_SEQUENCE[:STATE_SEQUENCE.index("private-upload")]
    if project["publication_mode"] == "automated-private":
        sequence += ["private-upload", "youtube-qa", "release-approved", "published"]
    if "article" in project["requested_outputs"]:
        sequence += ["blog-review"]
        if project["publication_mode"] == "automated-private":
            sequence += ["blog-approved"]
    return sequence + ["complete"]


def configure_outputs(*, slug: str, requested_outputs: list[str], publication_mode: str) -> dict:
    """Explicit opt-in for existing work; preserve every prior event and approval."""
    project = load_project(slug)
    if set(requested_outputs) not in ({"video"}, {"video", "article"}):
        raise ValueError("requested_outputs must be video or video plus article")
    if publication_mode not in {"owner", "automated-private"}:
        raise ValueError("publication_mode must be owner or automated-private")
    previous = {key: project.get(key) for key in ("requested_outputs", "publication_mode")}
    project["requested_outputs"] = sorted(set(requested_outputs))
    project["publication_mode"] = publication_mode
    if project["state"] == "complete" or project["state"] not in workflow_sequence(project):
        raise ValueError("Current state is not eligible for this output path; history cannot be rewritten")
    project.setdefault("history", []).append({
        "at": _now(), "from": project["state"], "to": project["state"],
        "event": "output-selection", "previous": previous,
        "requested_outputs": project["requested_outputs"], "publication_mode": publication_mode,
        "note": "Requested outputs explicitly selected; prior approvals and publications unchanged.",
    })
    return save_project(project)


def delivery_artifacts(project: dict) -> dict:
    """Identify the actual requested materials; never synthesize approvals."""
    required = ["final_master", "transcript", "youtube_package", "thumbnail"]
    if "article" in project["requested_outputs"]:
        required.append("blog_draft")
    result = {}
    for kind in required:
        artifact = (project.get("selections", {}).get("thumbnail") if kind == "thumbnail"
                    else current_artifact(project, kind))
        if not artifact:
            raise ValueError(f"Requested delivery is missing {kind}")
        if hash_path(resolve_path(artifact["path"])) != artifact["sha256"]:
            raise ValueError(f"Requested delivery has changed on disk: {kind}")
        result[kind] = dict(artifact)
    if not approval_is_current(project, "master") or not approval_is_current(project, "package"):
        raise ValueError("Delivery requires current master and package approvals")
    return result


def validate_completion(project: dict) -> dict | None:
    if "requested_outputs" not in project:
        _require(project, "substack" in project.get("publications", {}),
                 "Substack publication record is missing")
        return
    materials = delivery_artifacts(project)
    if project["publication_mode"] == "automated-private":
        channels = ["youtube"] + (["substack"] if "article" in project["requested_outputs"] else [])
        for channel in channels:
            _require(project, channel in project.get("publications", {}),
                     f"{channel} publication record is missing")
    return materials


WORKFLOW_STAGE_SEQUENCE = [
    "development",
    "deck",
    "production",
    "master",
    "packaging",
    "editorial",
]

# Code-owned production flow: the pipeline as designed.
#
# STATE_SEQUENCE and GATE_ARTIFACTS describe permitted progression and the
# evidence a gate requires; neither says what an artifact was built from.
# ARTIFACT_FLOW supplies that missing shape: for each kind, its stage and the
# kinds the standard pipeline expects to feed it.
#
# This is the designed flow, NOT recorded causality. A run may have been made
# some other way — imported, one-shot, or predating a stage — and this map has
# no way to know. It says which inputs to expect; a gap between that and what a
# run holds is a divergence worth looking at, never an explanation of one.
# Recording what actually produced what is a separate, later mechanism.
#
# Declaration order is stage order; the renderer relies on it.
ARTIFACT_FLOW = {
    "interview": {"stage": "development", "expected_inputs": []},
    "claims_register": {"stage": "development", "expected_inputs": ["interview"]},
    "challenge_report": {"stage": "development", "expected_inputs": ["claims_register"]},
    "outline": {"stage": "development", "expected_inputs": ["claims_register"]},
    "sourced_figures": {"stage": "development", "expected_inputs": ["outline"]},
    "balance_check": {"stage": "development", "expected_inputs": ["outline"]},
    "brief": {"stage": "development", "expected_inputs": ["outline", "balance_check"]},
    "cuecam_spec": {"stage": "deck", "expected_inputs": ["outline"]},
    "image_suggestions": {"stage": "deck", "expected_inputs": ["brief"]},
    "cuecam_bundle": {"stage": "deck", "expected_inputs": ["cuecam_spec", "image_suggestions"]},
    "raw_recording": {"stage": "production", "expected_inputs": ["cuecam_bundle"]},
    "cleaned_video": {"stage": "production", "expected_inputs": ["raw_recording"]},
    "final_master": {"stage": "master", "expected_inputs": ["cleaned_video"]},
    "qc_report": {"stage": "master", "expected_inputs": ["final_master"]},
    "transcript": {"stage": "master", "expected_inputs": ["final_master"]},
    "thumbnail_reference": {"stage": "packaging", "expected_inputs": ["final_master"]},
    "thumbnail": {"stage": "packaging", "expected_inputs": ["thumbnail_reference"]},
    "youtube_package": {"stage": "packaging", "expected_inputs": ["transcript", "brief"]},
    # Second editorial loop. John's own staging, from the intake interview:
    # reflective interview after the recording -> confirmed thesis -> first draft
    # for manual editing -> owner's substantive edit -> reconciled final.
    "editorial_interview": {"stage": "editorial", "expected_inputs": ["transcript"]},
    "blog_thesis": {"stage": "editorial", "expected_inputs": ["editorial_interview"]},
    "substack_brief": {"stage": "editorial", "expected_inputs": ["blog_thesis", "transcript"]},
    "blog_draft": {
        "stage": "editorial",
        "expected_inputs": ["blog_thesis", "substack_brief", "transcript"],
    },
    "blog_owner_edit": {"stage": "editorial", "expected_inputs": ["blog_draft"]},
    "blog_final": {"stage": "editorial", "expected_inputs": ["blog_owner_edit"]},
    "published_blog": {"stage": "editorial", "expected_inputs": ["blog_final"]},
}
ARTIFACT_KINDS = set(ARTIFACT_FLOW)
MULTI_ARTIFACT_KINDS = {"thumbnail", "thumbnail_reference"}
GATE_ARTIFACTS = {
    "brief": ["brief"],
    "deck": ["cuecam_bundle"],
    "master": ["final_master", "qc_report"],
    "package": ["final_master", "youtube_package", "thumbnail"],
    "release": ["final_master", "youtube_package", "thumbnail"],
    "blog": ["blog_draft"],
}
GATE_OPTIONAL_ARTIFACTS = {
    "brief": [
        "interview",
        "claims_register",
        "challenge_report",
        "outline",
        "sourced_figures",
        "balance_check",
    ],
}
DEVELOPMENT_ARTIFACTS = {
    "interview.md": "interview",
    "claims.json": "claims_register",
    "challenge.md": "challenge_report",
    "outline.md": "outline",
    "sourced-figures.md": "sourced_figures",
    "balance-check.md": "balance_check",
    "brief.md": "brief",
    "cuecam-spec.txt": "cuecam_spec",
    "image-prompts.md": "image_suggestions",
}
DEVELOPMENT_CATEGORIES = {
    "interview.md": "development-interview",
    "challenge.md": "development-challenge",
    "outline.md": "development-outline",
    "sourced-figures.md": "development-sourced-figures",
    "balance-check.md": "development-balance-check",
    "brief.md": "development-brief",
    "image-prompts.md": "development-assets",
}
GATE_STATES = {
    "brief": "brief-review",
    "deck": "deck-review",
    "master": "master-qc",
    "package": "package-review",
    "release": "youtube-qa",
    "blog": "blog-review",
}
# An observation is a claim about the pipeline, so it outlives the run that
# raised it: proposed -> accepted -> implemented -> verified, or rejected.
# A decision, by contrast, is a closed fact and has no status at all.
OBSERVATION_STATUSES = ["proposed", "accepted", "implemented", "verified", "rejected"]

# Enforced, not merely described. Without this an observation could be marked
# verified without ever having been implemented, which is exactly the claim the
# status is supposed to make unfakeable. `verified` is terminal: a regression is
# a new observation, not a reopened one.
OBSERVATION_TRANSITIONS = {
    "proposed": {"accepted", "rejected"},
    "accepted": {"implemented", "rejected"},
    "implemented": {"verified", "rejected"},
    "verified": set(),
    "rejected": {"proposed"},
}

# The upward axis: which procedures govern each stage, most specific first. An
# observation about a stage should end up changing the first file listed; the
# rest are the other procedures that stage draws on.
STAGE_GOVERNANCE = {
    "development": ["docs/larry/development.md", "docs/larry/video-production.md"],
    "deck": ["docs/larry/cuecam.md", "docs/larry/video-production.md"],
    "production": ["docs/larry/video-production.md"],
    "master": ["docs/larry/video-production.md"],
    "packaging": ["docs/larry/youtube-writing.md", "docs/larry/video-production.md"],
    # The editorial loop is defined in video-production.md sections 7 and 9;
    # youtube-writing.md governs how Reed and Maven actually draft.
    "editorial": ["docs/larry/video-production.md", "docs/larry/youtube-writing.md"],
}


def governing_docs(stage: str) -> list[str]:
    return list(STAGE_GOVERNANCE.get(stage, []))


def primary_governance(stage: str) -> str | None:
    docs = governing_docs(stage)
    return docs[0] if docs else None

YOUTUBE_QA_REQUIRED = {
    "playback",
    "hd-processing",
    "title",
    "description-links",
    "chapters",
    "thumbnail-mobile",
    "captions",
    "audience",
}
YOUTUBE_QA_OPTIONAL = {"monetization", "playlist", "end-screen", "cards"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:80] or "video-project"


def _project_dir(slug: str) -> Path:
    return PROJECTS_DIR / slugify(slug)


def _manifest_path(slug: str) -> Path:
    return _project_dir(slug) / "project.json"


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PKA_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PKA_ROOT / path
    return path.resolve()


def hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    raise FileNotFoundError(path)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def load_project(slug: str) -> dict:
    path = _manifest_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"Video project not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_records(project: dict, kind: str) -> list[dict]:
    value = project.get("artifacts", {}).get(kind)
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def current_artifact(project: dict, kind: str) -> dict | None:
    records = artifact_records(project, kind)
    return records[-1] if records else None


def stage_of(kind: str) -> str:
    return (ARTIFACT_FLOW.get(kind) or {}).get("stage", "unmapped")


def expected_inputs(kind: str) -> list[str]:
    """Kinds the designed pipeline expects to feed this one. Not recorded causality."""
    return list((ARTIFACT_FLOW.get(kind) or {}).get("expected_inputs", []))


def expected_outputs(kind: str) -> list[str]:
    """Kinds the designed pipeline expects this one to feed. Not recorded causality."""
    return [k for k, spec in ARTIFACT_FLOW.items() if kind in spec["expected_inputs"]]


def flow_order(kinds) -> list[str]:
    """Pipeline order. Kinds outside the flow map sort last, alphabetically."""
    present = set(kinds)
    ordered = [k for k in ARTIFACT_FLOW if k in present]
    return ordered + sorted(present.difference(ordered))


def _is_vault_note(display_path: str) -> bool:
    """True when Obsidian can open the artifact directly as a note."""
    return display_path.endswith(".md") and not display_path.startswith("/")


def _wikilink(display_path: str, label: str) -> str:
    return f"[[{display_path[:-3]}|{label}]]"


def _media_note_path(slug: str, kind: str, record: dict) -> Path:
    stem = kind
    if kind in MULTI_ARTIFACT_KINDS:
        stem = f'{kind}-{record.get("sha256", "")[:8]}'
    return _project_dir(slug) / "media" / f"{stem}.md"


def _media_note_rel(slug: str, kind: str, record: dict) -> str:
    return _display_path(_media_note_path(slug, kind, record))


def _artifact_link(project: dict, kind: str, record: dict) -> str:
    """Link to the artifact itself when it is a note, else to its wrapper note."""
    display = record.get("path", "")
    label = Path(display).name
    if _is_vault_note(display):
        return _wikilink(display, label)
    return _wikilink(_media_note_rel(project["slug"], kind, record), label)


def _retrospective_line(project: dict) -> list[str]:
    """Forward link to the retrospective, so the run page is not a dead end."""
    path = _project_dir(project["slug"]) / "retrospective.md"
    if not path.exists():
        return []
    link = _wikilink(_display_path(path), "Retrospective")
    return [f"**Retrospective:** {link}", ""]


def _kind_refs(artifacts: dict, kinds: list[str]) -> str:
    """Related kinds; struck through when the design expects one but none is attached."""
    if not kinds:
        return "—"
    return ", ".join(f"`{k}`" if artifacts.get(k) else f"~~{k}~~" for k in kinds)


def _render_overview(project: dict) -> str:
    lines = [
        "---",
        f'title: {json.dumps(project["title"] + " — Video Project")}',
        "category: video-project",
        f'status: {project["state"]}',
        f'created_at: {project["created_at"]}',
        f'updated_at: {project["updated_at"]}',
        f'slug: {project["slug"]}',
        "---",
        "",
        f'# {project["title"]} — Video Project',
        "",
        f'**State:** `{project["state"]}`  ',
        f'**Summary:** {project.get("summary") or "None."}  ',
        f'**Next:** {project.get("next_action") or "Not set."}',
        "",
        *([f"**Requested outputs:** {', '.join(project['requested_outputs'])}",
           f"**Publication mode:** {project['publication_mode']}",
           f"**Materials delivered:** {'yes' if project.get('deliveries') else 'not recorded'}",
           f"**Publication confirmed:** {', '.join(project.get('publications', {})) or 'not recorded'}", ""]
          if "requested_outputs" in project else []),
        *_retrospective_line(project),
        "## Production Flow",
        "",
        "Pipeline order, grouped by stage. Expected inputs and outputs come from",
        "`ARTIFACT_FLOW` in `tools/video_project.py` — the pipeline **as designed**,",
        "not a record of what this run actually did. A struck-through kind is one the",
        "design expects but this run has no artifact for: a divergence to look at, not",
        "an explanation of one.",
        "",
    ]
    artifacts = project.get("artifacts", {})
    if not artifacts:
        lines.append("No artifacts attached.")
    else:
        ordered = flow_order(artifacts)
        for stage in WORKFLOW_STAGE_SEQUENCE + ["unmapped"]:
            kinds = [kind for kind in ordered if stage_of(kind) == stage]
            if not kinds:
                continue
            docs = governing_docs(stage)
            heading = f"### {stage.capitalize()}"
            if docs:
                links = ", ".join(_wikilink(doc, Path(doc).stem) for doc in docs)
                heading += f" · governed by {links}"
            lines.extend([
                heading,
                "",
                "| Kind | Artifact | Expected inputs | Expected outputs | Identity | Source / rights |",
                "|---|---|---|---|---|---|",
            ])
            for kind in kinds:
                for record in artifact_records(project, kind):
                    lines.append(
                        f'| `{kind}` | {_artifact_link(project, kind, record)} '
                        f'| {_kind_refs(artifacts, expected_inputs(kind))} '
                        f'| {_kind_refs(artifacts, expected_outputs(kind))} '
                        f'| `{record.get("sha256", "")[:12]}` '
                        f'| {(record.get("source") or "—")} / {(record.get("rights") or "—")} |'
                    )
            lines.append("")

    lines.extend(["", "## Approvals", ""])
    approvals = project.get("approvals", {})
    if approvals:
        for gate, record in sorted(approvals.items()):
            status = "current" if approval_is_current(project, gate) else "stale"
            lines.append(
                f'- **{gate}:** approved by {record["approved_by"]} at '
                f'{record["approved_at"]} (`{status}`)'
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Blockers", ""])
    blockers = project.get("blockers", [])
    if blockers:
        for item in blockers:
            status = "resolved" if item.get("resolved_at") else "active"
            lines.append(
                f'- `{item["id"]}` [{status}] {item["message"]}'
                + (f' — {item["resolution"]}' if item.get("resolution") else "")
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Decisions", ""])
    decisions = project.get("decisions", [])
    if decisions:
        for item in decisions:
            kinds = ", ".join(f"`{kind}`" for kind in item.get("artifacts", []))
            line = f'- `{item["id"]}` **{item["stage"]}** — {item["summary"]}'
            if item.get("why"):
                line += f' _{item["why"]}_'
            trailer = ", ".join(filter(None, [item.get("by"), item.get("at")]))
            lines.append(f'{line} ({trailer})' + (f' · {kinds}' if kinds else ""))
    else:
        lines.append("- None recorded.")

    lines.extend(["", "## Observations", ""])
    observations = project.get("observations", [])
    if observations:
        for item in observations:
            kinds = ", ".join(f"`{kind}`" for kind in item.get("artifacts", []))
            line = f'- `{item["id"]}` **{item["stage"]}** [`{item["status"]}`] {item["summary"]}'
            if item.get("proposes"):
                line += f' → _{item["proposes"]}_'
            if item.get("changed"):
                line += f' · changed: `{item["changed"]}`'
            if item.get("verified_by_run"):
                line += f' · verified on `{item["verified_by_run"]}`'
            lines.append(line + (f' · {kinds}' if kinds else ""))
    else:
        lines.append("- None recorded.")

    youtube = project.get("youtube") or {}
    lines.extend(["", "## YouTube", ""])
    if youtube:
        lines.extend([
            f'- URL: {youtube.get("url") or "Not set."}',
            f'- Privacy: `{youtube.get("privacy") or "unknown"}`',
            f'- QA complete: `{str(youtube_qa_complete(project)).lower()}`',
        ])
    else:
        lines.append("- Not uploaded.")

    lines.extend(["", "## Publications", ""])
    publications = project.get("publications", {})
    if publications:
        for channel, record in sorted(publications.items()):
            publication_date = record.get("published_at") or "publication date not supplied"
            lines.append(f'- **{channel}:** {record["url"]} ({publication_date})')
    else:
        lines.append("- None.")

    lines.extend(["", "## Metrics", ""])
    metrics = project.get("metrics", [])
    if metrics:
        for item in metrics:
            values = ", ".join(
                f"{key}={value}" for key, value in item.items()
                if key not in {"window", "recorded_at", "note"} and value is not None
            )
            lines.append(f'- **{item["window"]}:** {values or "recorded"} ({item["recorded_at"]})')
    else:
        lines.append("- None.")

    lines.extend([
        "",
        "## State History",
        "",
    ])
    for item in project.get("history", []):
        lines.append(
            f'- {item["at"]}: `{item.get("from") or "created"}` → `{item["to"]}`'
            + (f' — {item["note"]}' if item.get("note") else "")
        )
    return "\n".join(lines).strip() + "\n"


MEDIA_PROBE_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".mkv", ".wav", ".mp3", ".m4a", ".png", ".jpg", ".jpeg",
}
_PROBE_CACHE: dict[str, dict] = {}


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _probe_media(path: Path, sha: str) -> dict:
    """Container format and duration via ffprobe. A missing ffprobe is not an error."""
    if sha in _PROBE_CACHE:
        return _PROBE_CACHE[sha]
    info: dict = {}
    if path.suffix.lower() in MEDIA_PROBE_EXTENSIONS:
        try:
            completed = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration,format_long_name", "-of", "json", str(path),
                ],
                capture_output=True, text=True, timeout=30, check=True,
            )
            fmt = json.loads(completed.stdout).get("format") or {}
            if fmt.get("format_long_name"):
                info["format"] = fmt["format_long_name"]
            if fmt.get("duration"):
                info["duration"] = _format_duration(float(fmt["duration"]))
        except (OSError, subprocess.SubprocessError, ValueError):
            info = {}
    _PROBE_CACHE[sha] = info
    return info


def _file_size(path: Path) -> int | None:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
    except OSError:
        return None
    return None


def _gates_citing(project: dict, kind: str) -> list[str]:
    citing = []
    for gate, kinds in sorted(GATE_ARTIFACTS.items()):
        if kind in kinds and gate in project.get("approvals", {}):
            state = "current" if approval_is_current(project, gate) else "stale"
            citing.append(f"{gate} ({state})")
    return citing


def _render_media_note(project: dict, kind: str, record: dict) -> str:
    """Stand-in note for an artifact Obsidian cannot open, carrying its identity
    and its place in the designed flow."""
    slug = project["slug"]
    title = project["title"]
    display = record.get("path", "")
    path = resolve_path(display)
    present = path.exists()
    sha = record.get("sha256", "")
    in_repo = not display.startswith("/")
    probe = _probe_media(path, sha) if present else {}
    size = _file_size(path) if present else None

    upstream = [
        _artifact_link(project, other, current_artifact(project, other))
        for other in expected_inputs(kind)
        if current_artifact(project, other)
    ]
    downstream = [
        _artifact_link(project, other, current_artifact(project, other))
        for other in expected_outputs(kind)
        if current_artifact(project, other)
    ]
    qc = project.get("qc") or {}
    gates = _gates_citing(project, kind)
    project_note = _display_path(_project_dir(slug) / "project.md")

    lines = [
        "---",
        "title: " + json.dumps(f"{title} — {kind}"),
        "category: video-media",
        f"video_project: {slug}",
        f"artifact_kind: {kind}",
        f"stage: {stage_of(kind)}",
        f"sha256: {sha}",
        "media_path: " + json.dumps(display),
        f"in_repo: {str(in_repo).lower()}",
        f"present: {str(present).lower()}",
        f'attached_at: {record.get("attached_at", "")}',
        "---",
        "",
        f"# {kind} — {title}",
        "",
        "Wrapper note. The artifact itself is not Markdown, so this note stands in for",
        "it inside the vault and carries its identity and its place in the flow.",
        "",
        "## Identity",
        "",
        f"- **Location:** `{display}`" + ("" if in_repo else "  (outside the repository)"),
        "- **Present on this machine:** " + ("yes" if present else "**NO — path does not resolve**"),
        f"- **SHA-256:** `{sha}`",
    ]
    if size is not None:
        lines.append(f"- **Size:** {_format_size(size)}")
    if probe.get("format"):
        lines.append(f'- **Format:** {probe["format"]}')
    if probe.get("duration"):
        lines.append(f'- **Duration:** {probe["duration"]}')
    lines.append(f'- **Attached:** {record.get("attached_at") or "Unknown."}')
    source = record.get("source") or "—"
    rights = record.get("rights") or "—"
    lines.append(f"- **Source / rights:** {source} / {rights}")
    if record.get("note"):
        lines.append(f'- **Note:** {record["note"]}')

    lines.extend([
        "",
        "## Flow",
        "",
        f"- **Stage:** {stage_of(kind)}",
        "- **Expected inputs (present on this run):** "
        + (", ".join(upstream) if upstream else "—"),
        "- **Expected outputs (present on this run):** "
        + (", ".join(downstream) if downstream else "—"),
        "- **Gates citing this artifact:** " + (", ".join(gates) if gates else "None."),
    ])
    if kind in {"final_master", "qc_report"} and qc:
        lines.append(
            f'- **QC:** {qc.get("status", "unknown")} ({qc.get("checked_at", "unrecorded")})'
        )
    lines.extend([
        "",
        "## Run",
        "",
        f"- Project: {_wikilink(project_note, title)}",
        f'- Project state: `{project["state"]}`',
    ])
    return "\n".join(lines).strip() + "\n"


def write_media_notes(project: dict) -> list[str]:
    """Emit one wrapper note per artifact Obsidian cannot open as a note.

    Stale notes from replaced artifacts are removed along with their index rows,
    unless a governed record cites them — those are kept and reported.
    """
    slug = project["slug"]
    directory = _project_dir(slug) / "media"
    written: list[str] = []
    skipped: list[str] = []
    keep: set[str] = set()
    for kind in flow_order(project.get("artifacts", {})):
        for record in artifact_records(project, kind):
            if _is_vault_note(record.get("path", "")):
                continue
            rel = _media_note_rel(slug, kind, record)
            note = PKA_ROOT / rel
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(_render_media_note(project, kind, record), encoding="utf-8")
            index_markdown_artifact(
                note,
                title=f"{project['title']} — {kind}",
                category="video-media",
                tags=["video-media", slug, kind],
                summary=f"Media wrapper note for the {kind} artifact of {project['title']}.",
            )
            keep.add(note.name)
            written.append(rel)
    if directory.exists():
        # Every note in this directory is generated here and has just been
        # rewritten; anything left over belongs to a replaced artifact. Drop the
        # index rows with the file, or the orphans resurface as kb_provenance
        # failures.
        for stale in sorted(directory.glob("*.md")):
            if stale.name in keep:
                continue
            try:
                forget_markdown_artifact(stale)
            except GovernedRecordError as exc:
                # A governed record still points at it. Leave the note in place
                # and say so rather than destroying the thing being cited.
                print(f"⚠️  Kept {_display_path(stale)}: {exc}", file=sys.stderr)
                skipped.append(_display_path(stale))
                continue
            stale.unlink()
    return written


def _index_overview(path: Path, project: dict) -> dict:
    return index_markdown_artifact(
        path,
        title=f'{project["title"]} — Video Project',
        category="video-project",
        tags=["video-project", project["slug"], project["state"]],
        summary=project.get("summary") or f'Manual-first video project: {project["title"]}',
    )


def render_views(project: dict) -> dict:
    """Regenerate the human-readable views from the manifest.

    Output only: it never writes project.json and never touches updated_at, so
    re-rendering cannot make a finished run look as though it changed.
    """
    directory = _project_dir(project["slug"])
    directory.mkdir(parents=True, exist_ok=True)
    overview = directory / "project.md"
    media_notes = write_media_notes(project)
    overview.write_text(_render_overview(project), encoding="utf-8")
    indexed = _index_overview(overview, project)
    return {
        "slug": project["slug"],
        "state": project["state"],
        "overview": _display_path(overview),
        "knowledge_base_id": indexed["knowledge_base_id"],
        "file_id": indexed["file_id"],
        "media_notes": media_notes,
    }


def save_project(project: dict) -> dict:
    project["updated_at"] = _now()
    directory = _project_dir(project["slug"])
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "project.json"
    _atomic_json(manifest, project)
    result = render_views(project)
    result["manifest"] = _display_path(manifest)
    return result


def create_project(*, title: str, slug: str | None, summary: str,
                   requested_outputs: list[str] | None = None, publication_mode: str = "owner") -> dict:
    project_slug = slugify(slug or title)
    path = _manifest_path(project_slug)
    if path.exists():
        raise FileExistsError(f"Video project already exists: {path}")
    now = _now()
    project = {
        "schema_version": 1,
        "slug": project_slug,
        "title": title.strip(),
        "summary": summary.strip(),
        "state": "developing",
        "next_action": "Run the intake interview and assemble the first brief.",
        "created_at": now,
        "updated_at": now,
        "artifacts": {},
        "artifact_history": [],
        "approvals": {},
        "blockers": [],
        "decisions": [],
        "observations": [],
        "qc": {},
        "selections": {},
        "youtube": {},
        "publications": {},
        "metrics": [],
        "history": [{"at": now, "from": None, "to": "developing", "note": "Project created."}],
    }
    if requested_outputs is not None:
        if set(requested_outputs) not in ({"video"}, {"video", "article"}):
            raise ValueError("requested_outputs must be video or video plus article")
        if publication_mode not in {"owner", "automated-private"}:
            raise ValueError("publication_mode must be owner or automated-private")
        project["requested_outputs"] = sorted(set(requested_outputs))
        project["publication_mode"] = publication_mode
    result = save_project(project)
    result["created"] = True
    return result


def attach_artifact(
    *, slug: str, kind: str, raw_path: str, note: str = "", source: str = "", rights: str = ""
) -> dict:
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"Unknown artifact kind: {kind}")
    project = load_project(slug)
    path = resolve_path(raw_path)
    if not path.exists():
        raise FileNotFoundError(path)
    record = {
        "path": _display_path(path),
        "sha256": hash_path(path),
        "attached_at": _now(),
        "note": note.strip(),
        "source": source.strip(),
        "rights": rights.strip(),
    }
    artifacts = project.setdefault("artifacts", {})
    if kind in MULTI_ARTIFACT_KINDS:
        values = artifact_records(project, kind)
        if not any(item.get("sha256") == record["sha256"] for item in values):
            values.append(record)
        artifacts[kind] = values
    else:
        previous = current_artifact(project, kind)
        if previous and previous.get("sha256") != record["sha256"]:
            project.setdefault("artifact_history", []).append({"kind": kind, **previous})
        artifacts[kind] = record
    save_result = save_project(project)
    return {**save_result, "attached": kind, "artifact": record}


def _approval_hashes(project: dict, gate: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for kind in GATE_ARTIFACTS[gate]:
        records = artifact_records(project, kind)
        if not records:
            raise ValueError(f"Cannot approve {gate}: missing {kind} artifact")
        if kind == "thumbnail":
            selected = project.get("selections", {}).get("thumbnail")
            if not selected:
                raise ValueError("Cannot approve package/release: no thumbnail selected")
            result[kind] = [selected["sha256"]]
        else:
            result[kind] = [record["sha256"] for record in records]
    for kind in GATE_OPTIONAL_ARTIFACTS.get(gate, []):
        records = artifact_records(project, kind)
        if records:
            result[kind] = [record["sha256"] for record in records]
    if gate in {"package", "release"}:
        title = project.get("selections", {}).get("title")
        if not title:
            raise ValueError(f"Cannot approve {gate}: no title selected")
        result["selected_title"] = [
            hashlib.sha256(title["text"].encode("utf-8")).hexdigest()
        ]
    if gate == "release":
        youtube = project.get("youtube", {})
        if not youtube.get("video_id"):
            raise ValueError("Cannot approve release: private YouTube upload is missing")
        release_identity = json.dumps(
            {"video_id": youtube["video_id"], "qa": youtube.get("qa", {})},
            sort_keys=True,
            separators=(",", ":"),
        )
        result["youtube_private_qa"] = [
            hashlib.sha256(release_identity.encode("utf-8")).hexdigest()
        ]
    return result


def approval_is_current(project: dict, gate: str) -> bool:
    approval = project.get("approvals", {}).get(gate)
    if not approval:
        return False
    try:
        return approval.get("artifact_hashes") == _approval_hashes(project, gate)
    except ValueError:
        return False


def approve_gate(*, slug: str, gate: str, approved_by: str, note: str = "") -> dict:
    if gate not in GATE_ARTIFACTS:
        raise ValueError(f"Unknown approval gate: {gate}")
    project = load_project(slug)
    gate_state = GATE_STATES[gate]
    if project["state"] != gate_state:
        seq = STATE_SEQUENCE
        if project["state"] in seq and gate_state in seq and seq.index(project["state"]) > seq.index(gate_state):
            pass
        else:
            raise ValueError(
                f"Cannot approve {gate} while state={project['state']}; expected {gate_state}"
            )
    if gate == "master" and project.get("qc", {}).get("status") != "pass":
        raise ValueError("Cannot approve master: QC has not passed")
    if gate == "release" and not youtube_qa_complete(project):
        raise ValueError("Cannot approve release: private YouTube QA is incomplete")
    if gate == "package":
        unverified_refs = [
            item["path"] for item in artifact_records(project, "thumbnail_reference")
            if not item.get("rights")
        ]
        if unverified_refs:
            raise ValueError(
                "Cannot approve package: thumbnail reference rights are unrecorded for "
                + ", ".join(unverified_refs)
            )
    hashes = _approval_hashes(project, gate)
    project.setdefault("approvals", {})[gate] = {
        "approved_by": approved_by.strip(),
        "approved_at": _now(),
        "artifact_hashes": hashes,
        "note": note.strip(),
    }
    result = save_project(project)
    return {**result, "approved": gate, "approval_current": True}


def _require(project: dict, condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_target(project: dict, target: str) -> dict | None:
    if target == "complete":
        return validate_completion(project)
    requirements = {
        "brief-review": (current_artifact(project, "brief") is not None, "brief artifact is missing"),
        "brief-approved": (approval_is_current(project, "brief"), "brief approval is missing or stale"),
        "deck-review": (approval_is_current(project, "brief"), "brief approval is missing or stale"),
        "deck-approved": (approval_is_current(project, "deck"), "deck approval is missing or stale"),
        "recorded": (current_artifact(project, "raw_recording") is not None, "raw recording is missing"),
        "clean-ready": (current_artifact(project, "cleaned_video") is not None, "cleaned video is missing"),
        "master-qc": (current_artifact(project, "final_master") is not None, "final master is missing"),
        "package-review": (approval_is_current(project, "master"), "master approval is missing or stale"),
        "package-approved": (approval_is_current(project, "package"), "package approval is missing or stale"),
        "private-upload": (bool(project.get("youtube", {}).get("video_id")), "private YouTube upload is missing"),
        "youtube-qa": (youtube_qa_complete(project), "private YouTube QA is incomplete"),
        "release-approved": (approval_is_current(project, "release"), "release approval is missing or stale"),
        "published": ("youtube" in project.get("publications", {}), "YouTube publication record is missing"),
        "blog-review": (current_artifact(project, "blog_draft") is not None, "blog draft is missing"),
        "blog-approved": (approval_is_current(project, "blog"), "blog approval is missing or stale"),
    }
    if target in requirements:
        _require(project, *requirements[target])


def advance_project(*, slug: str, target: str | None, note: str = "") -> dict:
    project = load_project(slug)
    active_blockers = [item for item in project.get("blockers", []) if not item.get("resolved_at")]
    if active_blockers:
        raise ValueError(
            f"Cannot advance with {len(active_blockers)} active blocker(s); resolve them first"
        )
    current = project["state"]
    sequence = workflow_sequence(project)
    if current not in sequence:
        raise ValueError(f"Unknown current state: {current}")
    current_index = sequence.index(current)
    if current_index == len(sequence) - 1:
        raise ValueError("Project is already complete")
    expected = sequence[current_index + 1]
    target_state = target or expected
    if target_state != expected:
        raise ValueError(f"Invalid transition {current} → {target_state}; expected {expected}")
    materials = _validate_target(project, target_state)
    if target_state == "complete" and "requested_outputs" in project:
        project.setdefault("deliveries", []).append({
            "at": _now(), "artifacts": materials,
            "requested_outputs": list(project["requested_outputs"]),
            "status": "materials-delivered",
        })
    project["state"] = target_state
    project["next_action"] = note.strip() or f"Complete the `{sequence[min(current_index + 2, len(sequence) - 1)]}` gate."
    if target_state == "complete" and "requested_outputs" in project:
        project["next_action"] = (
            "Materials delivered. Publication is recorded separately when confirmed by the owner."
            if project["publication_mode"] == "owner" else "Requested materials delivered and publication confirmed."
        )
    project.setdefault("history", []).append(
        {"at": _now(), "from": current, "to": target_state, "note": note.strip()}
    )
    result = save_project(project)
    return {**result, "from": current, "to": target_state}


def import_master_qc(*, slug: str, note: str = "") -> dict:
    """Enter the tracked workflow at master QC for an externally produced video.

    This is intentionally narrower than a general state override: it only works
    for a newly created project with no approvals, and only after the exact final
    master and a passing QC report have been attached through the normal tools.
    """
    project = load_project(slug)
    _require(
        project,
        project["state"] == "developing",
        f"Master import requires state=developing; current state={project['state']}",
    )
    _require(project, not project.get("approvals"), "Master import requires a project with no approvals")
    _require(
        project,
        not [item for item in project.get("blockers", []) if not item.get("resolved_at")],
        "Master import requires all blockers to be resolved",
    )
    _require(
        project,
        current_artifact(project, "final_master") is not None,
        "Final master is missing; run tools/video_qc.py with --project first",
    )
    _require(
        project,
        current_artifact(project, "qc_report") is not None,
        "QC report is missing; run tools/video_qc.py with --project first",
    )
    _require(project, project.get("qc", {}).get("status") == "pass", "Final-master QC has not passed")

    previous = project["state"]
    project["state"] = "master-qc"
    project["next_action"] = "Approve the exact final master after owner playback review."
    project.setdefault("history", []).append({
        "at": _now(),
        "from": previous,
        "to": "master-qc",
        "note": note.strip() or "Externally produced video imported at the final-master QC gate.",
    })
    result = save_project(project)
    return {**result, "from": previous, "to": "master-qc", "imported": True}


def add_blocker(*, slug: str, message: str, owner: str = "") -> dict:
    project = load_project(slug)
    digest = hashlib.sha256(message.strip().lower().encode("utf-8")).hexdigest()[:8]
    active = [item for item in project.get("blockers", []) if not item.get("resolved_at")]
    if any(item["id"] == digest for item in active):
        raise ValueError(f"Blocker already active: {digest}")
    blocker = {
        "id": digest,
        "message": message.strip(),
        "owner": owner.strip(),
        "created_at": _now(),
        "resolved_at": None,
        "resolution": "",
    }
    project.setdefault("blockers", []).append(blocker)
    result = save_project(project)
    return {**result, "blocker": blocker}


def sync_development(*, slug: str, development_dir: Path | None = None) -> dict:
    """Register the standard development folder in the shared project manifest.

    The folder is portable git state. Missing files are skipped so the command
    can be rerun after each road-work stage without inventing placeholders.
    """
    directory = development_dir or (PKA_ROOT / "owners-inbox" / "development" / slugify(slug))
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    attached: list[str] = []
    for filename, kind in DEVELOPMENT_ARTIFACTS.items():
        path = directory / filename
        if not path.exists():
            continue
        attach_artifact(
            slug=slug,
            kind=kind,
            raw_path=str(path),
            note="Synchronized from the shared development folder.",
        )
        category = DEVELOPMENT_CATEGORIES.get(filename)
        if category:
            index_markdown_artifact(
                path,
                title=f"{load_project(slug)['title']} — {kind.replace('_', ' ').title()}",
                category=category,
                tags=["video-development", slugify(slug), kind],
                summary=f"{kind.replace('_', ' ').title()} for video project {slugify(slug)}.",
            )
        attached.append(kind)
    if not attached:
        raise ValueError(f"No recognized development artifacts found in {directory}")
    project = load_project(slug)
    return {
        "slug": project["slug"],
        "state": project["state"],
        "development_dir": _display_path(directory),
        "attached": attached,
    }


def resolve_blocker(*, slug: str, blocker_id: str, resolution: str) -> dict:
    project = load_project(slug)
    matches = [
        item for item in project.get("blockers", [])
        if item["id"].startswith(blocker_id) and not item.get("resolved_at")
    ]
    if not matches:
        raise ValueError(f"Active blocker not found: {blocker_id}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous blocker ID prefix: {blocker_id}")
    matches[0]["resolved_at"] = _now()
    matches[0]["resolution"] = resolution.strip()
    result = save_project(project)
    return {**result, "resolved_blocker": matches[0]}


def _validate_annotation(stage: str, artifacts: list[str]) -> tuple[str, list[str]]:
    stage = stage.strip()
    if stage not in WORKFLOW_STAGE_SEQUENCE:
        raise ValueError(
            f"Unknown stage: {stage}. Expected one of {WORKFLOW_STAGE_SEQUENCE}."
        )
    kinds = [item.strip() for item in artifacts if item.strip()]
    unknown = [kind for kind in kinds if kind not in ARTIFACT_KINDS]
    if unknown:
        raise ValueError(f"Unknown artifact kind(s): {', '.join(sorted(unknown))}")
    return stage, kinds


def record_decision(
    *, slug: str, stage: str, summary: str, why: str = "", artifacts: list[str] | None = None,
    by: str = "",
) -> dict:
    """Record a judgment made during the run.

    A decision is closed the moment it is made — it is what happened, not a
    proposal. This is the sideways axis the ledger never had: the map can show
    that a run diverged, but only this can say why. Repeating a decision is
    allowed; the same call can genuinely be made twice in one run.
    """
    project = load_project(slug)
    stage, kinds = _validate_annotation(stage, artifacts or [])
    at = _now()
    existing = project.setdefault("decisions", [])
    # A decision is an event, so identity includes when it happened: the same
    # call can legitimately be made twice in one run.
    seed = f'{summary.strip().lower()}|{at}|{len(existing)}'
    decision = {
        "id": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8],
        "at": at,
        "stage": stage,
        "summary": summary.strip(),
        "why": why.strip(),
        "artifacts": kinds,
        "by": by.strip(),
    }
    existing.append(decision)
    result = save_project(project)
    return {**result, "decision": decision}


def record_observation(
    *, slug: str, stage: str, summary: str, proposes: str = "",
    artifacts: list[str] | None = None, by: str = "",
) -> dict:
    """Raise a claim about the pipeline itself, evidenced by this run."""
    project = load_project(slug)
    stage, kinds = _validate_annotation(stage, artifacts or [])
    now = _now()
    observation = {
        "id": hashlib.sha256(summary.strip().lower().encode("utf-8")).hexdigest()[:8],
        "at": now,
        "stage": stage,
        "summary": summary.strip(),
        "proposes": proposes.strip(),
        "artifacts": kinds,
        "by": by.strip(),
        "status": "proposed",
        "changed": "",
        "verified_by_run": "",
        "history": [{"at": now, "status": "proposed", "note": ""}],
    }
    if any(item["id"] == observation["id"] for item in project.get("observations", [])):
        raise ValueError(f"Observation already recorded: {observation['id']}")
    project.setdefault("observations", []).append(observation)
    result = save_project(project)
    return {**result, "observation": observation}


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def update_observation(
    *, slug: str, observation_id: str, status: str, note: str = "",
    changed: str = "", verified_by_run: str = "",
) -> dict:
    """Advance an observation and, when it closes, say what changed and where.

    Evidence is bound to the transition that requires it. It cannot be supplied
    early and banked for later, or an observation could arrive at `verified`
    carrying a run named before the change was ever implemented.
    """
    if status not in OBSERVATION_STATUSES:
        raise ValueError(f"Unknown status: {status}. Expected one of {OBSERVATION_STATUSES}.")
    project = load_project(slug)
    for observation in project.get("observations", []):
        if observation["id"] == observation_id:
            break
    else:
        raise ValueError(f"Observation not found: {observation_id}")

    current = observation.get("status", "proposed")
    allowed = OBSERVATION_TRANSITIONS.get(current, set())
    if status not in allowed:
        permitted = ", ".join(sorted(allowed)) or "nothing (terminal)"
        raise ValueError(
            f"Cannot move observation {observation_id} from `{current}` to `{status}`. "
            f"From `{current}` the only permitted status is: {permitted}."
        )

    if changed and status != "implemented":
        raise ValueError(
            "--changed is evidence for the `implemented` transition and cannot be "
            f"supplied while moving to `{status}`."
        )
    if verified_by_run and status != "verified":
        raise ValueError(
            "--verified-by-run is evidence for the `verified` transition and cannot "
            f"be supplied while moving to `{status}`."
        )

    if status == "implemented" and not changed:
        raise ValueError(
            "An implemented observation must name what changed (--changed <path>)."
        )
    if status == "verified" and not verified_by_run:
        raise ValueError(
            "A verified observation must name the run that tested it "
            "(--verified-by-run <slug>)."
        )

    if verified_by_run:
        cited = slugify(verified_by_run)
        manifest = _manifest_path(cited)
        if not manifest.exists():
            raise ValueError(f"No such run to verify against: {cited}")
        if cited == project["slug"]:
            raise ValueError(
                "An observation cannot be verified by the run that raised it; "
                "name the later run that tested the change."
            )
        # "Later" has to mean later in time, not merely a different slug.
        try:
            cited_created = json.loads(manifest.read_text(encoding="utf-8")).get("created_at", "")
        except (OSError, json.JSONDecodeError, AttributeError):
            cited_created = ""
        raised = _parse_timestamp(observation.get("at", ""))
        created = _parse_timestamp(cited_created)
        if raised and created and created <= raised:
            raise ValueError(
                f"Run `{cited}` was created {cited_created}, at or before this "
                f"observation was raised ({observation['at']}); it cannot have "
                "tested the change."
            )

    observation["status"] = status
    if changed:
        observation["changed"] = changed.strip()
    if verified_by_run:
        observation["verified_by_run"] = slugify(verified_by_run)
    observation.setdefault("history", []).append(
        {"at": _now(), "status": status, "note": note.strip()}
    )
    result = save_project(project)
    return {**result, "observation": observation}


def select_title(*, slug: str, title: str, option: int | None = None) -> dict:
    project = load_project(slug)
    project.setdefault("selections", {})["title"] = {
        "text": title.strip(),
        "option": option,
        "selected_at": _now(),
    }
    result = save_project(project)
    return {**result, "selected_title": project["selections"]["title"]}


def select_thumbnail(*, slug: str, raw_path: str) -> dict:
    project = load_project(slug)
    path = resolve_path(raw_path)
    digest = hash_path(path)
    candidates = artifact_records(project, "thumbnail")
    match = next((item for item in candidates if item.get("sha256") == digest), None)
    if not match:
        raise ValueError("Thumbnail must be attached as a thumbnail artifact before selection")
    project.setdefault("selections", {})["thumbnail"] = {
        "path": match["path"],
        "sha256": digest,
        "selected_at": _now(),
    }
    result = save_project(project)
    return {**result, "selected_thumbnail": project["selections"]["thumbnail"]}


def package_review(slug: str) -> dict:
    project = load_project(slug)
    package = current_artifact(project, "youtube_package")
    if not package:
        raise ValueError("YouTube package artifact is missing")
    from publish_to_youtube import parse_package

    parsed = parse_package(resolve_path(package["path"]))
    references = artifact_records(project, "thumbnail_reference")
    return {
        "slug": project["slug"],
        "state": project["state"],
        "final_master": current_artifact(project, "final_master"),
        "titles": parsed.get("titles", []),
        "description": parsed.get("description", ""),
        "tags": parsed.get("tags", []),
        "thumbnail_prompt": parsed.get("thumbnail_prompt", ""),
        "thumbnail_candidates": artifact_records(project, "thumbnail"),
        "thumbnail_references": references,
        "unverified_reference_rights": [item["path"] for item in references if not item.get("rights")],
        "selections": project.get("selections", {}),
        "package_approval_current": approval_is_current(project, "package"),
    }


def record_qc(*, slug: str, report_path: str, status: str, checks: dict) -> dict:
    project = load_project(slug)
    path = resolve_path(report_path)
    if not path.exists():
        raise FileNotFoundError(path)
    artifact = {
        "path": _display_path(path),
        "sha256": hash_path(path),
        "attached_at": _now(),
        "note": "Final-master QC report.",
        "source": "tools/video_qc.py",
        "rights": "",
    }
    project.setdefault("artifacts", {})["qc_report"] = artifact
    project["qc"] = {
        "status": status,
        "checked_at": _now(),
        "report": artifact["path"],
        "checks": checks,
    }
    result = save_project(project)
    return {**result, "qc": project["qc"]}


def record_youtube_private(
    *, slug: str, video_id: str, url: str, privacy: str, title: str = ""
) -> dict:
    project = load_project(slug)
    if project.get("publication_mode") == "owner":
        raise ValueError("Owner publication mode does not use the automated private-upload path")
    _require(project, project["state"] == "package-approved", "Project state must be package-approved")
    _require(project, approval_is_current(project, "package"), "Package approval is missing or stale")
    if privacy != "private":
        raise ValueError("Initial workflow upload must be private")
    previous = project["state"]
    project["youtube"] = {
        "video_id": video_id.strip(),
        "url": url.strip(),
        "privacy": privacy,
        "title": title.strip() or project.get("selections", {}).get("title", {}).get("text", ""),
        "uploaded_at": _now(),
        "qa": {},
    }
    project["state"] = "private-upload"
    project["next_action"] = "Review private YouTube playback and complete the QA checklist."
    project.setdefault("history", []).append(
        {"at": _now(), "from": previous, "to": "private-upload", "note": "Private upload recorded."}
    )
    result = save_project(project)
    return {**result, "youtube": project["youtube"]}


def youtube_qa_complete(project: dict) -> bool:
    qa = project.get("youtube", {}).get("qa", {})
    complete = {item for item, value in qa.items() if value.get("status") in {"pass", "na"}}
    return YOUTUBE_QA_REQUIRED.issubset(complete)


def record_youtube_qa(
    *, slug: str, passed: list[str], not_applicable: list[str], note: str = ""
) -> dict:
    project = load_project(slug)
    if project.get("publication_mode") == "owner":
        raise ValueError("Owner publication mode does not use the automated private-upload path")
    _require(project, bool(project.get("youtube", {}).get("video_id")), "No private YouTube upload recorded")
    allowed = YOUTUBE_QA_REQUIRED | YOUTUBE_QA_OPTIONAL
    unknown = (set(passed) | set(not_applicable)) - allowed
    if unknown:
        raise ValueError("Unknown YouTube QA item(s): " + ", ".join(sorted(unknown)))
    qa = project.setdefault("youtube", {}).setdefault("qa", {})
    stamp = _now()
    for item in passed:
        qa[item] = {"status": "pass", "checked_at": stamp}
    for item in not_applicable:
        qa[item] = {"status": "na", "checked_at": stamp}
    if note:
        project["youtube"]["qa_note"] = note.strip()
    if youtube_qa_complete(project):
        previous = project["state"]
        project["state"] = "youtube-qa"
        if previous != "youtube-qa":
            project.setdefault("history", []).append(
                {"at": stamp, "from": previous, "to": "youtube-qa", "note": "Private YouTube QA complete."}
            )
        project["next_action"] = "Approve release after final owner review."
    result = save_project(project)
    return {
        **result,
        "qa_complete": youtube_qa_complete(project),
        "remaining": sorted(YOUTUBE_QA_REQUIRED - set(qa)),
    }


def record_publication(
    *, slug: str, channel: str, url: str, published_at: str | None = None
) -> dict:
    if channel not in {"youtube", "substack"}:
        raise ValueError("channel must be youtube or substack")
    project = load_project(slug)
    if project.get("publication_mode") == "owner":
        expected_output = "video" if channel == "youtube" else "article"
        _require(project, expected_output in project["requested_outputs"], "Channel was not requested")
        _require(project, bool(url.strip()), "A confirmed publication URL is required")
        _require(project, project["state"] in {"package-approved", "blog-review", "complete"},
                 "Materials must be ready before recording owner publication")
        if channel in project.get("publications", {}):
            raise ValueError("Publication already recorded; historical events are not overwritten")
    elif channel == "youtube":
        _require(project, project["state"] == "release-approved", "Project state must be release-approved")
        _require(project, approval_is_current(project, "release"), "Release approval is missing or stale")
    elif channel == "substack":
        _require(project, project["state"] == "blog-approved", "Project state must be blog-approved")
        _require(project, approval_is_current(project, "blog"), "Blog approval is missing or stale")
    project.setdefault("publications", {})[channel] = {
        "url": url.strip(),
        "published_at": (published_at if project.get("publication_mode") == "owner" else published_at or _now()),
    }
    if project.get("publication_mode") == "owner":
        project["publications"][channel]["confirmed_at"] = _now()
    if channel == "youtube":
        project.setdefault("youtube", {})["privacy"] = "public"
    result = save_project(project)
    return {**result, "published": channel, "publication": project["publications"][channel]}


def create_substack_brief(
    *, slug: str, pov: str, reader: str, mode: str, adds: str, cta: str, headline: str
) -> dict:
    project = load_project(slug)
    directory = _project_dir(project["slug"])
    path = directory / "substack-brief.md"
    transcript = current_artifact(project, "transcript")
    youtube = project.get("publications", {}).get("youtube") or project.get("youtube", {})
    content = f"""---
title: {json.dumps(project['title'] + ' — Substack Brief')}
category: substack
created_at: {_now()}
video_project: {project['slug']}
---

# {project['title']} — Substack Brief

## Editorial Direction

- **POV:** {pov.strip()}
- **Intended reader:** {reader.strip()}
- **Mode:** {mode}
- **What the article adds beyond the video:** {adds.strip()}
- **Call to action:** {cta.strip() or 'None specified.'}
- **Headline direction:** {headline.strip() or 'Open.'}

## Source Package

- Final transcript: `{transcript['path'] if transcript else 'Not attached.'}`
- Video URL: {youtube.get('url') or 'Not published.'}
- Approved title: {project.get('selections', {}).get('title', {}).get('text') or 'Not selected.'}
- Video brief: `{(current_artifact(project, 'brief') or {}).get('path', 'Not attached.')}`

## Writing Instruction

Create a reader-first article in John's voice. Do not convert the transcript mechanically. Preserve the approved POV, add the value described above, and return a draft for owner review before publication.
"""
    path.write_text(content, encoding="utf-8")
    indexed = index_markdown_artifact(
        path,
        title=f'{project["title"]} — Substack Brief',
        category="substack",
        tags=["substack", "video-companion", project["slug"]],
        summary=f'Substack adaptation brief for {project["title"]}.',
    )
    attach_result = attach_artifact(
        slug=slug,
        kind="substack_brief",
        raw_path=str(path),
        note="Owner-directed Substack adaptation brief.",
    )
    return {**attach_result, "substack_brief": _display_path(path), "brief_kb_id": indexed["knowledge_base_id"]}


def record_metrics(*, slug: str, window: str, values: dict, note: str = "") -> dict:
    project = load_project(slug)
    item = {"window": window, "recorded_at": _now(), "note": note.strip(), **values}
    project.setdefault("metrics", []).append(item)
    result = save_project(project)
    return {**result, "metrics_recorded": item}


def _elapsed(history: list[dict]) -> str:
    if len(history) < 2:
        return "unknown"
    try:
        start = datetime.fromisoformat(history[0]["at"])
        end = datetime.fromisoformat(history[-1]["at"])
    except (ValueError, KeyError):
        return "unknown"
    minutes = int((end - start).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"



def _canvas_node(node_id: str, x: int, y: int, width: int, height: int, **extra) -> dict:
    node = {"id": node_id, "x": x, "y": y, "width": width, "height": height}
    node.update(extra)
    return node


def generate_production_canvas(project: dict) -> Path:
    """Write an Obsidian Canvas of every artifact this run touched.

    One group per pipeline stage, in ARTIFACT_FLOW order; one card per attached
    artifact record (superseded thumbnails included, so rework is visible).
    Vault files become file cards that open on click; media outside the vault
    becomes a text card carrying its path; publications become link cards.
    The canvas is a view of the manifest, regenerated on every retrospective,
    never a source of truth.
    """
    slug = project["slug"]
    directory = _project_dir(slug)
    artifacts = project.get("artifacts", {})
    card_w, gap, col_pad = 380, 24, 40
    col_w = card_w + 2 * col_pad
    nodes: list[dict] = []
    edges: list[dict] = []

    stages: dict[str, list[tuple[str, dict]]] = {}
    seen_paths: set[tuple[str, str]] = set()
    for kind in flow_order(artifacts):
        for record in artifact_records(project, kind):
            # One card per file per stage: owner edit, final, and published blog
            # often share a file, and three identical cards say nothing.
            key = (stage_of(kind), record.get("path", ""))
            if key in seen_paths:
                continue
            seen_paths.add(key)
            stages.setdefault(stage_of(kind), []).append((kind, record))
    stage_order = [s for s in WORKFLOW_STAGE_SEQUENCE if s in stages]
    stage_order += [s for s in stages if s not in stage_order]

    # Publications and the run's own record pages close the map on the right.
    tail: list[tuple[str, dict]] = []
    for channel, pub in sorted((project.get("publications") or {}).items()):
        if pub.get("url"):
            tail.append(("link", {"url": pub["url"], "label": channel}))
    for name in ("project.md", "retrospective.md", "draft-to-published-delta.md", "project.json"):
        candidate = directory / name
        if candidate.exists():
            tail.append(("file", {"path": _display_path(candidate)}))
    if tail:
        stages["record"] = tail
        stage_order.append("record")

    first_ids: dict[str, str] = {}
    last_ids: dict[str, str] = {}
    for col, stage in enumerate(stage_order):
        x = col * col_w
        y = 80
        ids: list[str] = []
        for row, (kind, record) in enumerate(stages[stage]):
            node_id = f"{stage}-{row}"
            display = record.get("path", "") if kind != "link" else ""
            is_image = display.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            height = 240 if is_image else 90
            if kind == "link":
                node = _canvas_node(node_id, x + col_pad, y, card_w, height,
                                    type="link", url=record["url"])
            elif display and not display.startswith("/"):
                # Obsidian file cards open files only; a bundle directory gets
                # its wrapper note, as the project page does.
                target = display
                if resolve_path(display).is_dir():
                    note = _media_note_path(slug, kind, record)
                    target = _display_path(note) if note.exists() else display
                node = _canvas_node(node_id, x + col_pad, y, card_w, height,
                                    type="file", file=target)
            else:
                label = kind.replace("_", " ")
                note = record.get("note") or ""
                text = f"**{label}** (outside the vault)\n`{display}`"
                if note:
                    text += f"\n\n{note}"
                    height = 150
                node = _canvas_node(node_id, x + col_pad, y, card_w, height,
                                    type="text", text=text)
            nodes.append(node)
            ids.append(node_id)
            y += height + gap
        nodes.append(_canvas_node(f"group-{stage}", x, 0, col_w, y + 20,
                                  type="group", label=stage, color=str(col % 6 + 1)))
        for a, b in zip(ids, ids[1:]):
            edges.append({"id": f"e-{a}-{b}", "fromNode": a, "toNode": b,
                          "fromSide": "bottom", "toSide": "top"})
        first_ids[stage] = ids[0]
        last_ids[stage] = ids[-1]
    for prev, nxt in zip(stage_order, stage_order[1:]):
        edges.append({"id": f"x-{prev}-{nxt}", "fromNode": last_ids[prev], "toNode": first_ids[nxt],
                      "fromSide": "right", "toSide": "left"})

    pubs = ", ".join(
        f'{ch} {pub["published_at"][:10] if pub.get("published_at") else "date not supplied"}'
        for ch, pub in sorted((project.get("publications") or {}).items())
    )
    nodes.append(_canvas_node("title", 0, -140, min(col_w * 3, col_w * max(len(stage_order), 1)), 100,
                              type="text",
                              text=f'# {project["title"]}\nEverything this run touched, in pipeline order. '
                                   f'Click a card to open it. State: `{project["state"]}`'
                                   + (f" · Published: {pubs}" if pubs else "")))

    path = directory / "production-map.canvas"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1) + "\n", encoding="utf-8")
    return path


def generate_retrospective(*, slug: str) -> dict:
    """Write what this run can honestly say about itself.

    It reports divergence, rework, owner intervention, and the decisions and
    observations that were recorded. Where nothing was recorded it says so
    rather than inferring: an empty decision log means the judgment was not
    captured, never that no judgment was exercised.
    """
    project = load_project(slug)
    artifacts = project.get("artifacts", {})
    history = project.get("history", [])
    decisions = project.get("decisions", [])
    observations = project.get("observations", [])
    directory = _project_dir(project["slug"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "retrospective.md"
    title = f'{project["title"]} — Retrospective'

    divergences = []
    for kind in flow_order(artifacts):
        absent = [item for item in expected_inputs(kind) if item not in artifacts]
        if absent:
            divergences.append((kind, absent))

    rework = project.get("artifact_history", [])
    stale_gates = [
        gate for gate in sorted(project.get("approvals", {}))
        if not approval_is_current(project, gate)
    ]
    open_observations = [
        item for item in observations
        if item.get("status") in {"proposed", "accepted", "implemented"}
    ]

    lines = [
        "---",
        "title: " + json.dumps(title),
        "category: video-retrospective",
        f'video_project: {project["slug"]}',
        f'state: {project["state"]}',
        f"generated_at: {_now()}",
        "---",
        "",
        f"# {title}",
        "",
        f'- Run: {_wikilink(_display_path(directory / "project.md"), project["title"])}',
        f'- Final state: `{project["state"]}`',
        f'- Elapsed from creation to last transition: {_elapsed(history)}',
        f"- Artifacts registered: {sum(len(artifact_records(project, k)) for k in artifacts)}"
        f" across {len({stage_of(k) for k in artifacts})} stage(s)",
        "",
        "## Divergence from the designed flow",
        "",
        "Attached artifacts whose expected inputs are absent. This says the run was",
        "assembled differently than the pipeline describes; it does not say why.",
        "",
    ]
    if divergences:
        for kind, absent in divergences:
            lines.append(f'- `{kind}` — missing {", ".join(f"`{a}`" for a in absent)}')
    else:
        lines.append("- None. Every attached artifact has its expected inputs.")

    lines.extend(["", "## Rework", ""])
    if rework:
        for item in rework:
            lines.append(
                f'- `{item.get("kind")}` replaced (previous `{item.get("sha256", "")[:12]}`'
                f' attached {item.get("attached_at", "unknown")})'
                + (f' — {item["note"]}' if item.get("note") else "")
            )
    else:
        lines.append("- No artifact was replaced after first attachment.")

    lines.extend(["", "## Owner intervention", ""])
    approvals = project.get("approvals", {})
    if approvals:
        for gate, record in sorted(approvals.items()):
            lines.append(f'- Approved `{gate}` — {record["approved_by"]}, {record["approved_at"]}')
    else:
        lines.append("- No gate approvals recorded.")
    if stale_gates:
        lines.append(f'- **Stale approvals:** {", ".join(f"`{g}`" for g in stale_gates)}')
    blockers = project.get("blockers", [])
    if blockers:
        for item in blockers:
            state = "resolved" if item.get("resolved_at") else "active"
            lines.append(f'- Blocker `{item["id"]}` [{state}] {item["message"]}')
    else:
        lines.append("- No blocker was raised.")

    lines.extend(["", "## Decisions recorded", ""])
    if decisions:
        for item in decisions:
            lines.append(
                f'- `{item["id"]}` **{item["stage"]}** — {item["summary"]}'
                + (f' _{item["why"]}_' if item.get("why") else "")
            )
    else:
        lines.extend([
            "None. Judgment was exercised on this run — it always is — but none of it",
            "was captured, so this retrospective cannot say where the run turned on a",
            "call rather than on the procedure. Record decisions during the next run",
            "with `video_project.py decision`.",
        ])

    lines.extend(["", "## Observations", ""])
    if observations:
        for item in observations:
            lines.append(
                f'- `{item["id"]}` [`{item["status"]}`] **{item["stage"]}** {item["summary"]}'
                + (f' → _{item["proposes"]}_' if item.get("proposes") else "")
            )
    else:
        lines.append("- None raised.")

    lines.extend(["", "## Candidate workflow changes", ""])
    if open_observations:
        for item in open_observations:
            governing = primary_governance(item["stage"])
            target = (
                f' — would change {_wikilink(governing, Path(governing).stem)}'
                if governing else ""
            )
            lines.append(f'- `{item["id"]}` {item.get("proposes") or item["summary"]}{target}')
    else:
        lines.append("- None open.")

    lines.extend(["", "## Governing procedures", ""])
    for stage in WORKFLOW_STAGE_SEQUENCE:
        docs = governing_docs(stage)
        if docs and any(stage_of(k) == stage for k in artifacts):
            primary = f'{_wikilink(docs[0], docs[0])} (primary)'
            others = ", ".join(_wikilink(doc, doc) for doc in docs[1:])
            lines.append(f'- **{stage}:** {primary}' + (f', {others}' if others else ""))

    canvas_path = generate_production_canvas(project)
    lines.extend([
        "",
        "## Production map",
        "",
        f"- Every artifact this run touched, as an Obsidian canvas: "
        f"[[{_display_path(canvas_path)}|production-map.canvas]]",
    ])

    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    indexed = index_markdown_artifact(
        path,
        title=title,
        category="video-retrospective",
        tags=["video-retrospective", project["slug"], project["state"]],
        summary=f'Retrospective for the {project["title"]} run.',
    )
    # The run page gains its forward link only once this file exists.
    render_views(project)
    return {
        "slug": project["slug"],
        "retrospective": _display_path(path),
        "canvas": _display_path(canvas_path),
        "divergences": len(divergences),
        "rework": len(rework),
        "decisions": len(decisions),
        "observations": len(observations),
        "knowledge_base_id": indexed["knowledge_base_id"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage manual-first video projects")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create")
    p.add_argument("--title", required=True)
    p.add_argument("--slug")
    p.add_argument("--summary", default="")
    p.add_argument("--outputs", nargs="+", choices=["video", "article"], default=["video"])
    p.add_argument("--publication-mode", choices=["owner", "automated-private"], default="owner")

    p = sub.add_parser("configure-outputs", help="Explicitly select an output path for an eligible existing project")
    p.add_argument("--slug", required=True)
    p.add_argument("--outputs", nargs="+", choices=["video", "article"], required=True)
    p.add_argument("--publication-mode", choices=["owner", "automated-private"], required=True)

    p = sub.add_parser("status")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("render", help="Regenerate project.md and the media wrapper notes")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("attach")
    p.add_argument("--slug", required=True)
    p.add_argument("--kind", required=True, choices=sorted(ARTIFACT_KINDS))
    p.add_argument("--path", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--source", default="")
    p.add_argument("--rights", default="")

    p = sub.add_parser("sync-development")
    p.add_argument("--slug", required=True)
    p.add_argument("--development-dir")

    p = sub.add_parser("approve")
    p.add_argument("--slug", required=True)
    p.add_argument("--gate", required=True, choices=sorted(GATE_ARTIFACTS))
    p.add_argument("--by", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("advance")
    p.add_argument("--slug", required=True)
    p.add_argument("--to", choices=STATE_SEQUENCE)
    p.add_argument("--note", default="")

    p = sub.add_parser("import-master")
    p.add_argument("--slug", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("block")
    p.add_argument("--slug", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--owner", default="")

    p = sub.add_parser("resolve-blocker")
    p.add_argument("--slug", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--resolution", required=True)

    p = sub.add_parser("decision", help="Record a judgment made during the run")
    p.add_argument("--slug", required=True)
    p.add_argument("--stage", required=True, choices=WORKFLOW_STAGE_SEQUENCE)
    p.add_argument("--summary", required=True)
    p.add_argument("--why", default="")
    p.add_argument("--artifact", action="append", default=[], choices=sorted(ARTIFACT_KINDS))
    p.add_argument("--by", default="")

    p = sub.add_parser("observation", help="Raise a claim about the pipeline itself")
    p.add_argument("--slug", required=True)
    p.add_argument("--stage", required=True, choices=WORKFLOW_STAGE_SEQUENCE)
    p.add_argument("--summary", required=True)
    p.add_argument("--proposes", default="")
    p.add_argument("--artifact", action="append", default=[], choices=sorted(ARTIFACT_KINDS))
    p.add_argument("--by", default="")

    p = sub.add_parser("observation-status", help="Advance an observation")
    p.add_argument("--slug", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--status", required=True, choices=OBSERVATION_STATUSES)
    p.add_argument("--note", default="")
    p.add_argument("--changed", default="", help="Procedure or tool the change landed in")
    p.add_argument("--verified-by-run", dest="verified_by_run", default="")

    p = sub.add_parser("retrospective", help="Generate the run retrospective")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("select-title")
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--option", type=int)

    p = sub.add_parser("select-thumbnail")
    p.add_argument("--slug", required=True)
    p.add_argument("--path", required=True)

    p = sub.add_parser("review-package")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("youtube-private")
    p.add_argument("--slug", required=True)
    p.add_argument("--video-id", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--privacy", default="private", choices=["private"])
    p.add_argument("--title", default="")

    p = sub.add_parser("youtube-qa")
    p.add_argument("--slug", required=True)
    p.add_argument("--pass-item", action="append", default=[])
    p.add_argument("--na-item", action="append", default=[])
    p.add_argument("--note", default="")

    p = sub.add_parser("publication")
    p.add_argument("--slug", required=True)
    p.add_argument("--channel", required=True, choices=["youtube", "substack"])
    p.add_argument("--url", required=True)
    p.add_argument("--published-at")

    p = sub.add_parser("substack-brief")
    p.add_argument("--slug", required=True)
    p.add_argument("--pov", required=True)
    p.add_argument("--reader", required=True)
    p.add_argument("--mode", required=True, choices=["companion", "standalone", "alternate"])
    p.add_argument("--adds", required=True)
    p.add_argument("--cta", default="")
    p.add_argument("--headline", default="")

    p = sub.add_parser("metrics")
    p.add_argument("--slug", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--views", type=int)
    p.add_argument("--ctr", type=float)
    p.add_argument("--average-view", dest="average_view")
    p.add_argument("--substack-opens", dest="substack_opens", type=int)
    p.add_argument("--note", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "create":
        result = create_project(title=args.title, slug=args.slug, summary=args.summary,
                                requested_outputs=args.outputs, publication_mode=args.publication_mode)
    elif args.command == "configure-outputs":
        result = configure_outputs(slug=args.slug, requested_outputs=args.outputs,
                                   publication_mode=args.publication_mode)
    elif args.command == "status":
        result = load_project(args.slug)
    elif args.command == "render":
        result = render_views(load_project(args.slug))
    elif args.command == "attach":
        result = attach_artifact(
            slug=args.slug, kind=args.kind, raw_path=args.path, note=args.note,
            source=args.source, rights=args.rights,
        )
    elif args.command == "sync-development":
        result = sync_development(
            slug=args.slug,
            development_dir=resolve_path(args.development_dir) if args.development_dir else None,
        )
    elif args.command == "approve":
        result = approve_gate(slug=args.slug, gate=args.gate, approved_by=args.by, note=args.note)
    elif args.command == "advance":
        result = advance_project(slug=args.slug, target=args.to, note=args.note)
    elif args.command == "import-master":
        result = import_master_qc(slug=args.slug, note=args.note)
    elif args.command == "block":
        result = add_blocker(slug=args.slug, message=args.message, owner=args.owner)
    elif args.command == "resolve-blocker":
        result = resolve_blocker(
            slug=args.slug, blocker_id=args.id, resolution=args.resolution
        )
    elif args.command == "decision":
        result = record_decision(
            slug=args.slug, stage=args.stage, summary=args.summary, why=args.why,
            artifacts=args.artifact, by=args.by,
        )
    elif args.command == "observation":
        result = record_observation(
            slug=args.slug, stage=args.stage, summary=args.summary, proposes=args.proposes,
            artifacts=args.artifact, by=args.by,
        )
    elif args.command == "observation-status":
        result = update_observation(
            slug=args.slug, observation_id=args.id, status=args.status, note=args.note,
            changed=args.changed, verified_by_run=args.verified_by_run,
        )
    elif args.command == "retrospective":
        result = generate_retrospective(slug=args.slug)
    elif args.command == "select-title":
        result = select_title(slug=args.slug, title=args.title, option=args.option)
    elif args.command == "select-thumbnail":
        result = select_thumbnail(slug=args.slug, raw_path=args.path)
    elif args.command == "review-package":
        result = package_review(args.slug)
    elif args.command == "youtube-private":
        result = record_youtube_private(
            slug=args.slug, video_id=args.video_id, url=args.url,
            privacy=args.privacy, title=args.title,
        )
    elif args.command == "youtube-qa":
        result = record_youtube_qa(
            slug=args.slug, passed=args.pass_item, not_applicable=args.na_item, note=args.note,
        )
    elif args.command == "publication":
        result = record_publication(
            slug=args.slug, channel=args.channel, url=args.url, published_at=args.published_at,
        )
    elif args.command == "substack-brief":
        result = create_substack_brief(
            slug=args.slug, pov=args.pov, reader=args.reader, mode=args.mode,
            adds=args.adds, cta=args.cta, headline=args.headline,
        )
    elif args.command == "metrics":
        result = record_metrics(
            slug=args.slug, window=args.window,
            values={
                "views": args.views,
                "ctr": args.ctr,
                "average_view": args.average_view,
                "substack_opens": args.substack_opens,
            },
            note=args.note,
        )
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
