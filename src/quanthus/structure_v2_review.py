"""Pure deterministic summary and outcome-blind sampling for the bounded v2 slice."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from quanthus.structure_v2 import StructureV2Result
from quanthus.structure_v2_inputs import signed_distance


def report_counts(structure: StructureV2Result, request: dict) -> dict[str, Any]:
    first, last = (date.fromisoformat(request[key]) for key in ("report_start", "report_end"))
    episodes = [item for item in structure.episodes if first <= item.market_date <= last]
    latest = {}
    for item in structure.episodes:
        if item.market_date <= last:
            latest[item.episode_id] = item
    scope_end_open = [item.episode_id for item in latest.values()
                      if item.phase in {"active", "below"}]
    categories = {
        f"{code}:{scale}:{kind}": sum(item.security_code == code and item.scale == scale
                                     and item.observation == kind for item in episodes)
        for code in request["security_codes"] for scale in request["lookbacks"]
        for kind in ("started", "lost", "reclaimed", "censored")
    }
    return {
        "measurements": sum(first <= item.market_date <= last for item in structure.measurements),
        "interactions": sum(first <= item.market_date <= last for item in structure.interactions),
        "episode_observations": len(episodes),
        "episode_observation_counts": dict(sorted(Counter(
            item.observation for item in episodes
        ).items())),
        "scale_unavailable": sum(not item["available"] for item in structure.availability
                                 if first <= item["market_date"] <= last),
        "review_category_counts": categories,
        "scope_end_open_processes": sorted(scope_end_open),
        "scope_end_disposition": "right-censored-report-only-no-business-row-mutation",
    }


def select_review_sample(structure: StructureV2Result, request: dict) -> list[dict[str, Any]]:
    first, last = (date.fromisoformat(request[key]) for key in ("report_start", "report_end"))
    episodes = [item for item in structure.episodes if first <= item.market_date <= last]
    selected, seen = [], set()
    for code in request["security_codes"]:
        for scale in request["lookbacks"]:
            for kind in ("started", "lost", "reclaimed"):
                matches = [item for item in episodes if item.security_code == code
                           and item.scale == scale and item.observation == kind]
                if matches and matches[0].key not in seen:
                    selected.append(matches[0])
                    seen.add(matches[0].key)
    buckets = {code: [item for item in episodes if item.security_code == code
                      and item.key not in seen and item.phase != "censored"]
               for code in request["security_codes"]}
    for index in range(request["review_sample_limit"]):
        for code in request["security_codes"]:
            if index < len(buckets[code]) and len(selected) < request["review_sample_limit"]:
                item = buckets[code][index]
                selected.append(item)
                seen.add(item.key)
    return [{"security_code": item.security_code, "market_date": item.market_date,
             "scale": item.scale, "episode_id": item.episode_id, "phase": item.phase,
             "observation": item.observation, "started_at": item.started_at,
             "anchor_key": item.anchor_key, "anchor_revision": item.anchor_revision,
             "frozen_level": item.frozen_level, "close": item.close,
             "signed_distance": None if item.close is None
             else signed_distance(item.close, item.frozen_level),
             "exchange_session_age": item.exchange_session_age, "hold_count": item.hold_count,
             "previous_loss_ref": item.previous_loss_ref, "revision_id": item.revision_id}
            for item in selected[:request["review_sample_limit"]]]
