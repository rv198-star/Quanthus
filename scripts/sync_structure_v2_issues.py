#!/usr/bin/env python3
"""Project only the seven approved Facts v2 delivery issues; no global planning sync.

Repository delivery status/evidence is authoritative. Default is read-only preview. --apply uses
existing authenticated GitHub plumbing, touches only exact stable IDs, and reads back every write.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STRUCT-004"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in ("B1", "B1-R", "B2", "B3", "B4", "B5")))


def load_plan() -> dict:
    raw = json.loads((ROOT / "config/structure-v2-delivery.v1.json").read_text())
    if raw["parent_id"] != PARENT or tuple(row["id"] for row in raw["items"]) != IDS:
        raise ValueError("delivery plan must contain exactly the admitted ordered IDs")
    if not (ROOT / raw["design"]).is_file():
        raise ValueError("governing delivery design missing")
    by_id = {row["id"]: row for row in raw["items"]}
    for row in raw["items"]:
        if row["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("unknown delivery status")
        if row["status"] == "closed" and not row["evidence"]:
            raise ValueError("closed issue requires repository evidence")
        if any(not (ROOT / path).is_file() for path in row["evidence"]):
            raise ValueError("closure evidence path missing")
        if row["status"] == "closed" and any(
            key in by_id and by_id[key]["status"] != "closed"
            for key in row["depends_on"]
        ):
            raise ValueError("child cannot close ahead of its predecessor")
    if by_id[PARENT]["status"] == "closed" and (
        raw["runtime_accepted"] is not True
        or any(by_id[key]["status"] != "closed" for key in IDS[1:])
    ):
        raise ValueError("parent cannot close ahead of accepted children/runtime")
    return raw


def issue_body(row: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} ({key})" if key in mapping else f"`{key}`"

    content = [
        f"<!-- quanthus-planning:v2 id={row['id']} kind={row['kind']} -->",
        f"## Scope\n{row['scope']}",
        f"## Design\n`{raw['design']}`\n\n"
        f"https://github.com/{REPO}/blob/main/{raw['design']}",
        f"## Dependencies\n{', '.join(link(key) for key in row['depends_on'])}",
        "## Acceptance\n" + "\n".join(
            f"- [{'x' if row['status'] == 'closed' else ' '}] {gate}"
            for gate in row["acceptance"]
        ),
        "## Current disposition\n" + row["status"] + "; status is not an economic/forward claim.",
        "## Evidence\n" + ("\n".join(f"- `{path}`" for path in row["evidence"])
                              or "Not accepted; implementation does not replace runtime evidence."),
        "## Boundaries\nNo DWD mutation, sealed partitions, formula migration, new Worker "
        "family, 13-state/ML or forward orders. Dictionary #215 is independently scheduled. "
        "Reuse existing contracts and pipeline; actual use belongs to audit, not Fact attributes.",
    ]
    if row["id"] not in {PARENT + "-B1", PARENT + "-B1-R"}:
        runtime = raw.get("runtime_qualification")
        if runtime is not None:
            content.append(
                "## Runtime qualification record\n```json\n"
                + json.dumps(runtime, ensure_ascii=False, indent=2) + "\n```"
            )
    if row["id"] == PARENT:
        content.append("## Children\n" + "\n".join(
            f"- [{'x' if child['status'] == 'closed' else ' '}] {link(child['id'])}: "
            f"{child['title']}"
            for child in raw["items"][1:]
        ))
    else:
        content.append(f"## Parent\n{link(PARENT)}")
    return "\n\n".join(content) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"apply": False, "items": raw["items"]}, ensure_ascii=False, indent=2))
        return 0
    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS))
    ordered = [*raw["items"][1:], raw["items"][0]]  # Close children before their parent.
    for row in ordered:
        key = row["id"]
        labels = [f"type:{row['kind']}", f"status:{row['status']}", "phase:P3", "area:labels"]
        observed, _ = github.upsert_issue(
            mapping.get(key), f"[{key}] {row['title']}",
            issue_body(row, raw, mapping), labels, None,
        )
        mapping[key] = observed
    # Reconcile references after all issue numbers exist; preserve every unrelated issue.
    result = []
    for row in ordered:
        key = row["id"]
        current = mapping[key]
        body = issue_body(row, raw, mapping)
        desired = "closed" if row["status"] == "closed" else "open"
        if current.get("body") != body or current["state"] != desired:
            github.api("PATCH", f"/repos/{REPO}/issues/{current['number']}",
                       {"body": body, "state": desired})
        checked = github.api("GET", f"/repos/{REPO}/issues/{current['number']}")
        if checked["body"] != body or checked["state"] != desired:
            raise RuntimeError(f"GitHub issue readback differs for {key}")
        result.append({"id": key, "number": checked["number"], "state": checked["state"],
                       "url": checked["html_url"]})
    print(json.dumps({"repository": REPO, "issues": result, "unrelated_issues_changed": False},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
