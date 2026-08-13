#!/usr/bin/env python3
"""Synchronize Quanthus design work with GitHub planning objects.

The repository documents are the source of truth. This command manages only labels,
milestones, and issues whose titles start with a Quanthus stable work-item id.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_REPOSITORY = "rv198-star/Quanthus"
MANAGED_LABEL_PREFIXES = ("type:", "status:", "priority:", "phase:", "area:")


@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class ChapterSpec:
    number: str
    path: str
    phase: str
    area: str
    milestone: str
    status: str


@dataclass(frozen=True)
class WorkItem:
    stable_id: str
    title: str
    dependencies: str
    evidence: str
    phase: str
    area: str
    status: str
    priority: str
    milestone: str | None
    design_path: str
    issue_type: str


LABELS = (
    LabelSpec("type:epic", "5319E7", "Design chapter or delivery epic"),
    LabelSpec("type:task", "1D76DB", "Executable work item"),
    LabelSpec("status:ready", "0E8A16", "Ready under the current release gate"),
    LabelSpec("status:closed", "6E7781", "Accepted or completed with linked evidence"),
    LabelSpec("status:blocked", "B60205", "Blocked by an explicit predecessor gate"),
    LabelSpec("status:triage", "FBCA04", "Defined but not released for implementation"),
    LabelSpec("priority:critical", "D73A4A", "Release-blocking critical work"),
    LabelSpec("priority:high", "E99695", "High-priority work in the released phase"),
    LabelSpec("priority:normal", "C5DEF5", "Normal planned work"),
    *(LabelSpec(f"phase:P{phase}", "0052CC", f"Delivery phase P{phase}") for phase in range(1, 8)),
    LabelSpec("phase:PK", "0B7285", "Orthogonal Platform Kernel engineering milestone"),
    LabelSpec("phase:meta", "6F42C1", "Cross-phase project governance"),
    LabelSpec("area:data", "BFDADC", "Point-in-time data foundation"),
    LabelSpec("area:backtest", "D4C5F9", "Backtest engine and accounting"),
    LabelSpec("area:labels", "FEF2C0", "Labels and feature contracts"),
    LabelSpec("area:experts", "F9D0C4", "Experts and aggregation"),
    LabelSpec("area:positions", "C2E0C6", "Position and execution lifecycle"),
    LabelSpec("area:events", "FDE2A7", "Event library and datasets"),
    LabelSpec("area:ml", "D8B4FE", "Research scoring and models"),
    LabelSpec("area:governance", "E4E669", "Validation and change governance"),
    LabelSpec("area:roadmap", "D4C5F9", "Delivery roadmap and gates"),
    LabelSpec("area:observability", "BFD4F2", "Monitoring and attribution"),
    LabelSpec("area:platform", "9ED9CC", "Platform identity, API, jobs, Web, and operations"),
    LabelSpec("area:docs", "0075CA", "Product and design documentation"),
)

PLATFORM_KERNEL_MILESTONE = "Platform Kernel Extraction"
WORKER_RUNTIME_MILESTONE = "PostgreSQL Worker Runtime"

MILESTONES = (
    "P1 Data Foundation",
    "P2 Backtest Engine",
    "P3 Rule Vertical Slice",
    "P4 Event Library",
    "P5 Research Scoring",
    "P6 Simulation Governance",
    "P7 Live Validation",
    PLATFORM_KERNEL_MILESTONE,
    WORKER_RUNTIME_MILESTONE,
)

CHAPTERS = (
    ChapterSpec(
        "01", "docs/design/lld/01-data-foundation.md", "P1", "data", MILESTONES[0], "ready"
    ),
    ChapterSpec(
        "02", "docs/design/lld/02-backtest-engine.md", "P2", "backtest", MILESTONES[1], "ready"
    ),
    ChapterSpec(
        "03", "docs/design/lld/03-label-feature-system.md", "P3", "labels", MILESTONES[2], "closed"
    ),
    ChapterSpec(
        "04", "docs/design/lld/04-experts-aggregator.md", "P3", "experts", MILESTONES[2], "closed"
    ),
    ChapterSpec(
        "05",
        "docs/design/lld/05-position-execution.md",
        "P3",
        "positions",
        MILESTONES[2],
        "closed",
    ),
    ChapterSpec(
        "06", "docs/design/lld/06-event-library.md", "P4", "events", MILESTONES[3], "ready"
    ),
    ChapterSpec(
        "07", "docs/design/lld/07-research-scoring.md", "P5", "ml", MILESTONES[4], "blocked"
    ),
    ChapterSpec(
        "08",
        "docs/design/lld/08-validation-governance.md",
        "P6",
        "governance",
        MILESTONES[5],
        "blocked",
    ),
    ChapterSpec("09", "docs/design/lld/09-roadmap.md", "meta", "roadmap", MILESTONES[0], "ready"),
    ChapterSpec(
        "10",
        "docs/design/lld/10-monitoring-attribution.md",
        "P2",
        "observability",
        MILESTONES[1],
        "ready",
    ),
    ChapterSpec(
        "11",
        "docs/design/lld/11-platform-kernel.md",
        "PK",
        "platform",
        PLATFORM_KERNEL_MILESTONE,
        "closed",
    ),
)

PHASE_GATES = {
    "meta": "Active project governance; changes must preserve document and issue traceability.",
    "P1": (
        "Conditionally accepted under p1-c1-conditional-20260725-v1; 4,173 C1 factor "
        "visibility exceptions remain fail-closed."
    ),
    "P2": (
        "Conditionally accepted for close-to-next-open research; target two-window activation "
        "remains blocked by config/p2-gates.v1.json."
    ),
    "P3": (
        "Accepted as a rule-only historical capability slice under config/p3-gates.v1.json; "
        "the frozen fixture is not a strategy lineage."
    ),
    "P4": "Allowed for event-library implementation under config/p3-gates.v1.json.",
    "P5": "Blocked until the P4 event library and frozen rule baselines are accepted.",
    "P6": "Simulation governance starts after the P3 baseline is frozen.",
    "P7": "Blocked until frozen simulation supports an explicit live/no-live decision.",
    "PK": (
        "Platform Kernel is orthogonal to P1-P7. PK1-PK4, the ADR 0010 PostgreSQL Worker "
        "Runtime, and PK6 cutover/recovery are accepted; first-cutover PK5 is not applicable "
        "under config/platform-kernel-gates.v1.json."
    ),
}

CRITICAL_IDS = {"C1", "C2", "S1"}
HIGH_IDS = {
    "P1-DATA-001",
    "P1-SNAPSHOT-001",
    "P1-ACCEPT-001",
    "P2-ENG-001",
    "P2-MATCH-001",
    "P2-LEDGER-001",
    "P2-ACCEPT-001",
}


def parse_table(lines: list[str], start: int) -> list[dict[str, str]]:
    header = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


def id_tables(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.startswith("| ID |"):
            rows.extend(parse_table(lines, index))
    return rows


def section(path: Path, name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        if re.match(rf"^##+ (?:\d+\. )?{re.escape(name)}$", line, re.IGNORECASE):
            start = index + 1
            level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            next_level = len(line) - len(line.lstrip("#"))
            if next_level <= level:
                break
        selected.append(line)
    return "\n".join(selected).strip()


def document_title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return re.sub(r"^# LLD \d+:\s*", "", first).strip()


def first_paragraph(content: str) -> str:
    paragraphs = [part.strip().replace("\n", " ") for part in content.split("\n\n")]
    return next((part for part in paragraphs if part and not part.startswith("|")), "")


def chapter_rows(
    root: Path,
) -> tuple[dict[str, tuple[ChapterSpec, dict[str, str]]], dict[str, list[dict[str, str]]]]:
    owners: dict[str, tuple[ChapterSpec, dict[str, str]]] = {}
    by_chapter: dict[str, list[dict[str, str]]] = {}
    for chapter in CHAPTERS:
        rows = id_tables(root / chapter.path)
        by_chapter[chapter.number] = rows
        for row in rows:
            stable_id = row["ID"]
            if stable_id in owners:
                previous = owners[stable_id][0]
                raise ValueError(
                    f"{stable_id} is owned by both LLD-{previous.number} and LLD-{chapter.number}"
                )
            owners[stable_id] = (chapter, row)
    return owners, by_chapter


def index_rows(root: Path) -> list[dict[str, str]]:
    path = root / "docs/work-items/index.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted_sections = {
        "Meta": "meta",
        "P1 data foundation": "P1",
        "P2 backtest and observability": "P2",
        "P3 rule-only vertical slice": "P3",
        "P4-P7": "explicit",
        "Platform Kernel": "PK",
    }
    phase: str | None = None
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.startswith("## "):
            phase = wanted_sections.get(line[3:])
        if phase is not None and line.startswith("| ID |"):
            for row in parse_table(lines, index):
                if phase != "explicit":
                    row["Phase"] = phase
                rows.append(row)
            phase = None
    return rows


def work_items(root: Path) -> list[WorkItem]:
    owners, _ = chapter_rows(root)
    items: list[WorkItem] = []
    for row in index_rows(root):
        stable_id = row["ID"]
        if stable_id not in owners:
            raise ValueError(f"{stable_id} has no governing LLD work-item row")
        chapter, design_row = owners[stable_id]
        phase = row["Phase"]
        dependencies = design_row.get("Dependency") or row.get("Depends on") or "None"
        evidence = design_row.get("Acceptance evidence", "Linked implementation evidence")
        state = row.get("State", "")
        if phase == "meta":
            status = "ready" if state == "active" else "triage"
        elif phase == "P3" and row.get("Disposition", "").startswith("closed"):
            status = "closed"
        elif phase == "P3" and row.get("Disposition", "").startswith("deferred"):
            status = "blocked"
        elif phase == "PK":
            disposition = row.get("Disposition", "")
            if disposition in {"open", "ready", "open epic"}:
                status = "ready"
            elif disposition.startswith("accepted"):
                status = "closed"
            else:
                status = "blocked"
        else:
            status = "ready" if phase in {"P1", "P2", "P3", "P4"} else "blocked"
        if stable_id in CRITICAL_IDS:
            priority = "critical"
        elif stable_id in HIGH_IDS:
            priority = "high"
        else:
            priority = "normal"
        if phase == "meta":
            milestone = None
        elif phase == "PK":
            milestone = (
                WORKER_RUNTIME_MILESTONE
                if stable_id.startswith("PK-WORKER-")
                else PLATFORM_KERNEL_MILESTONE
            )
        else:
            milestone = MILESTONES[int(phase[1:]) - 1]
        items.append(
            WorkItem(
                stable_id=stable_id,
                title=row["Title"],
                dependencies=dependencies,
                evidence=evidence,
                phase=phase,
                area=chapter.area,
                status=status,
                priority=priority,
                milestone=milestone,
                design_path=chapter.path,
                issue_type="epic" if stable_id == "PK-WORKER-000" else "task",
            )
        )
    return items


def repo_url(repository: str, path: str) -> str:
    return f"https://github.com/{repository}/blob/main/{path}"


def task_body(item: WorkItem, repository: str) -> str:
    blocked_note = ""
    worker_decision = ""
    if item.stable_id.startswith("PK-WORKER-"):
        worker_decision = (
            "- [Worker Runtime decision]("
            f"{repo_url(repository, 'docs/architecture/adr-0010-postgresql-worker-runtime.md')}"
            ")\n"
        )
    return f"""<!-- quanthus-planning:v1 id={item.stable_id} kind=task -->
## Objective

{item.title}.

## Governing design

- [Detailed design]({repo_url(repository, item.design_path)})
{worker_decision}- [Product requirements]({repo_url(repository, "docs/product/prd.md")})
- [High-level design]({repo_url(repository, "docs/design/hld.md")})

## Phase and gate

{PHASE_GATES[item.phase]}{blocked_note}

## Dependencies

{item.dependencies}

## Acceptance evidence

{item.evidence}

## Traceability

- Stable work-item id: `{item.stable_id}`
- [Work-item index]({repo_url(repository, "docs/work-items/index.md")})
- Policy source: [`config/p0-policy.v1.json`]({repo_url(repository, "config/p0-policy.v1.json")})

## Completion checklist

- [ ] Implementation and configuration are versioned.
- [ ] Tests cover the governing contract and failure behavior.
- [ ] Acceptance evidence is linked from this issue.
- [ ] Documentation and traceability are updated.
- [ ] `make check` passes.
"""


def epic_body(
    root: Path,
    chapter: ChapterSpec,
    rows: list[dict[str, str]],
    task_urls: dict[str, str],
    repository: str,
) -> str:
    path = root / chapter.path
    goal = section(path, "Goal") or section(path, "Role") or section(path, "Build order")
    acceptance = section(path, "Acceptance")
    if not acceptance:
        acceptance = (
            "- [ ] The chapter's milestones and release mechanics are represented by linked issues."
        )
    checklist = []
    for row in rows:
        stable_id = row["ID"]
        title = row.get("Work", "")
        if stable_id in task_urls:
            checklist.append(f"- [ ] [{stable_id}]({task_urls[stable_id]}) - {title}")
        else:
            checklist.append(f"- [ ] `{stable_id}` - {title}")
    checklist_text = "\n".join(checklist) or "- [ ] No work items have been declared."
    return f"""<!-- quanthus-planning:v1 id=LLD-{chapter.number} kind=epic -->
## Objective

{first_paragraph(goal)}

## Governing documents

- [LLD {chapter.number}: {document_title(path)}]({repo_url(repository, chapter.path)})
- [Product requirements]({repo_url(repository, "docs/product/prd.md")})
- [High-level design]({repo_url(repository, "docs/design/hld.md")})

## Phase and gate

{PHASE_GATES[chapter.phase]}

## Work items

{checklist_text}

## Acceptance

{acceptance}
"""


class GitHub:
    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must use OWNER/NAME form")
        self.repository = repository

    def api(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        command = ["gh", "api", "--method", method, endpoint]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload)
        result = subprocess.run(
            command,
            check=False,
            input=input_text,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown GitHub CLI error"
            raise RuntimeError(f"GitHub API {method} {endpoint} failed: {detail}")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def pages(self, endpoint: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, 100):
            batch = self.api("GET", f"{endpoint}{separator}per_page=100&page={page}")
            results.extend(batch)
            if len(batch) < 100:
                return results
        raise RuntimeError(f"pagination limit exceeded for {endpoint}")

    def labels(self) -> list[dict[str, Any]]:
        return self.pages(f"/repos/{self.repository}/labels")

    def milestones(self) -> list[dict[str, Any]]:
        return self.pages(f"/repos/{self.repository}/milestones?state=all")

    def issues(self) -> list[dict[str, Any]]:
        issues = self.pages(f"/repos/{self.repository}/issues?state=all")
        if issues:
            listed_numbers = {int(issue["number"]) for issue in issues}
            missing_numbers = sorted(set(range(1, max(listed_numbers) + 1)) - listed_numbers)
            if len(missing_numbers) > 100:
                raise RuntimeError(
                    "too many issue-number gaps to safely reconcile GitHub list consistency"
                )
            for number in missing_numbers:
                try:
                    issues.append(self.api("GET", f"/repos/{self.repository}/issues/{number}"))
                except RuntimeError as error:
                    if "HTTP 404" not in str(error):
                        raise
        return [issue for issue in issues if "pull_request" not in issue]

    def upsert_label(self, spec: LabelSpec, current: dict[str, dict[str, Any]]) -> str:
        existing = current.get(spec.name.casefold())
        payload = {"new_name": spec.name, "color": spec.color, "description": spec.description}
        if existing is None:
            created = self.api(
                "POST",
                f"/repos/{self.repository}/labels",
                {"name": spec.name, "color": spec.color, "description": spec.description},
            )
            current[spec.name.casefold()] = created
            return "created"
        if (
            existing.get("color", "").casefold() == spec.color.casefold()
            and existing.get("description", "") == spec.description
        ):
            return "reused"
        updated = self.api(
            "PATCH",
            f"/repos/{self.repository}/labels/{quote(existing['name'], safe='')}",
            payload,
        )
        current[spec.name.casefold()] = updated
        return "updated"

    def upsert_milestone(self, title: str, current: dict[str, dict[str, Any]]) -> tuple[int, str]:
        existing = current.get(title)
        if existing is not None:
            return int(existing["number"]), "reused"
        created = self.api(
            "POST",
            f"/repos/{self.repository}/milestones",
            {"title": title, "description": f"Quanthus {title} release gate."},
        )
        current[title] = created
        return int(created["number"]), "created"

    def upsert_issue(
        self,
        existing: dict[str, Any] | None,
        title: str,
        body: str,
        labels: list[str],
        milestone: int | None,
    ) -> tuple[dict[str, Any], str]:
        existing_labels = [] if existing is None else [item["name"] for item in existing["labels"]]
        unmanaged = [
            name for name in existing_labels if not name.startswith(MANAGED_LABEL_PREFIXES)
        ]
        desired_labels = sorted(set(labels + unmanaged))
        existing_body = None if existing is None else existing.get("body")
        desired_body = body
        if (
            isinstance(existing_body, str)
            and "<!-- quanthus-planning:v2 " in existing_body
            and "<!-- quanthus-planning:v1 " in body
        ):
            desired_body = existing_body
        payload: dict[str, Any] = {
            "title": title,
            "body": desired_body,
            "labels": desired_labels,
            "milestone": milestone,
        }
        if existing is None:
            return self.api("POST", f"/repos/{self.repository}/issues", payload), "created"
        current_milestone = existing.get("milestone")
        current_milestone_number = (
            None if current_milestone is None else current_milestone["number"]
        )
        unchanged = (
            existing["title"] == title
            and existing_body == desired_body
            and sorted(existing_labels) == desired_labels
            and current_milestone_number == milestone
        )
        if unchanged:
            return existing, "reused"
        endpoint = f"/repos/{self.repository}/issues/{existing['number']}"
        return self.api("PATCH", endpoint, payload), "updated"


def issue_by_stable_id(
    issues: Iterable[dict[str, Any]], targets: set[str]
) -> dict[str, dict[str, Any]]:
    found: dict[str, list[dict[str, Any]]] = {stable_id: [] for stable_id in targets}
    for issue in issues:
        match = re.match(r"^\[([^]]+)]", issue["title"])
        if match and match.group(1) in found:
            found[match.group(1)].append(issue)
    duplicates = {key: value for key, value in found.items() if len(value) > 1}
    if duplicates:
        summary = ", ".join(
            f"{key}: {[item['number'] for item in value]}" for key, value in duplicates.items()
        )
        raise RuntimeError(f"duplicate stable ids detected: {summary}")
    return {key: values[0] for key, values in found.items() if values}


def synchronize(root: Path, repository: str) -> dict[str, Any]:
    items = work_items(root)
    _, rows_by_chapter = chapter_rows(root)
    target_ids = {item.stable_id for item in items} | {f"LLD-{item.number}" for item in CHAPTERS}
    github = GitHub(repository)

    existing_issues = github.issues()
    issues_by_id = issue_by_stable_id(existing_issues, target_ids)

    label_state = {item["name"].casefold(): item for item in github.labels()}
    label_results = {"created": 0, "updated": 0, "reused": 0}
    for spec in LABELS:
        label_results[github.upsert_label(spec, label_state)] += 1

    milestone_state = {item["title"]: item for item in github.milestones()}
    milestone_numbers: dict[str, int] = {}
    milestone_results = {"created": 0, "reused": 0}
    for title in MILESTONES:
        number, result = github.upsert_milestone(title, milestone_state)
        milestone_numbers[title] = number
        milestone_results[result] += 1

    task_results = {"created": 0, "updated": 0, "reused": 0}
    task_urls: dict[str, str] = {}
    for item in items:
        labels = [
            f"type:{item.issue_type}",
            f"status:{item.status}",
            f"priority:{item.priority}",
            f"phase:{item.phase}",
            f"area:{item.area}",
        ]
        milestone = None if item.milestone is None else milestone_numbers[item.milestone]
        issue, result = github.upsert_issue(
            issues_by_id.get(item.stable_id),
            f"[{item.stable_id}] {item.title}",
            task_body(item, repository),
            labels,
            milestone,
        )
        task_results[result] += 1
        task_urls[item.stable_id] = issue["html_url"]
        issues_by_id[item.stable_id] = issue

    epic_results = {"created": 0, "updated": 0, "reused": 0}
    epic_urls: dict[str, str] = {}
    for chapter in CHAPTERS:
        stable_id = f"LLD-{chapter.number}"
        labels = [
            "type:epic",
            f"status:{chapter.status}",
            f"phase:{chapter.phase}",
            f"area:{chapter.area}",
        ]
        issue, result = github.upsert_issue(
            issues_by_id.get(stable_id),
            f"[{stable_id}] {document_title(root / chapter.path)}",
            epic_body(root, chapter, rows_by_chapter[chapter.number], task_urls, repository),
            labels,
            milestone_numbers[chapter.milestone],
        )
        epic_results[result] += 1
        epic_urls[stable_id] = issue["html_url"]
        issues_by_id[stable_id] = issue

    for stable_id in sorted(target_ids):
        verification_issue = issues_by_id.get(stable_id)
        if verification_issue is None:
            raise RuntimeError(f"GitHub verification failed; missing stable id: {stable_id}")
        remote_issue = github.api(
            "GET", f"/repos/{repository}/issues/{verification_issue['number']}"
        )
        if not remote_issue["title"].startswith(f"[{stable_id}]"):
            raise RuntimeError(
                f"GitHub verification failed for {stable_id}: issue #{verification_issue['number']}"
            )

    issue_by_stable_id(github.issues(), target_ids)

    return {
        "repository": f"https://github.com/{repository}",
        "labels": {"total": len(LABELS), **label_results},
        "milestones": {"total": len(MILESTONES), **milestone_results},
        "epics": {"total": len(CHAPTERS), **epic_results, "urls": epic_urls},
        "tasks": {"total": len(items), **task_results},
        "critical": {stable_id: task_urls[stable_id] for stable_id in sorted(CRITICAL_IDS)},
        "duplicate_stable_ids": 0,
    }


def dry_run(root: Path, repository: str) -> dict[str, Any]:
    items = work_items(root)
    owners, rows_by_chapter = chapter_rows(root)
    return {
        "repository": repository,
        "labels": len(LABELS),
        "milestones": len(MILESTONES),
        "epics": len(CHAPTERS),
        "tasks": len(items),
        "critical_ids": sorted(CRITICAL_IDS),
        "work_items_by_chapter": {
            f"LLD-{chapter.number}": len(rows_by_chapter[chapter.number]) for chapter in CHAPTERS
        },
        "owned_stable_ids": len(owners),
    }


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", default=DEFAULT_REPOSITORY, help="GitHub repository as OWNER/NAME"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the plan through the authenticated gh CLI; otherwise perform an offline dry run"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = (
            synchronize(project_root(), args.repo)
            if args.apply
            else dry_run(project_root(), args.repo)
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
