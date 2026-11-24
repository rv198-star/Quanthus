"""October #749 supplies exchange codes on history rows and warehouse IDs on signed evidence."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from quanthus.product_daily.recommendation.paper_replay import (
    build_replay_envelope,
    load_replay_request,
)
from tests.unit.test_p11_paper_replay_r5 import (
    SESSION_DAYS,
    T1,
    _r5_action,
    _r5_event,
    _r7_req,
    _r7_sse_security,
    _returns_window,
)

TABLE = "payload/dwd_trading_venue/all/part-000000.parquet"
SHA = "sha256:ba7e6c9bdcde4918c3167e1efbd9db49b02244fbd64f4d2c5da343b219ad5fed"
SH = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
SZ = "venue_8c0bd9088a0dc88f28e5e7fa8efdce87"


@pytest.mark.parametrize(
    "variant",
    [
        "valid",
        "wrong_venue",
        "wrong_security",
        "wrong_register",
        "wrong_catalog",
        "wrong_action",
        "wrong_sha",
        "unreferenced",
        "ambiguous_member",
    ],
)
def test_signed_source_venue_keeps_original_ids_and_requires_bound_reference(variant):
    sec = _r7_sse_security(tuple(d for d in SESSION_DAYS if d <= T1))
    ident = sec["identity"]
    ident["venue_id"] = "SSE"
    ident["depends_on_member_ids"].append(TABLE)
    ex = _returns_window()[25]
    known = (date.fromisoformat(ex) - timedelta(days=2)).isoformat()
    event = dict(_r5_event(ex, cash="0.42"), venue_id=SZ if variant == "wrong_catalog" else SH)
    action = dict(
        _r5_action("sh.600000", ex, cash="0.42", known_at=known),
        venue_id=SZ if variant == "wrong_action" else SH,
    )
    req = _r7_req(sec, [event], [action])
    coverage = req["action_coverage"]
    coverage["securities"][0]["venue_id"] = SZ if variant == "wrong_venue" else SH
    coverage["registers"][0]["venue_id"] = SZ if variant == "wrong_register" else SH
    if variant == "wrong_security":
        coverage["securities"][0]["security_id"] = "other"
    if variant == "unreferenced":
        ident["depends_on_member_ids"].remove(TABLE)
    member = dict(
        member_id=TABLE,
        member_path=TABLE,
        package_id="standard-base-2.5.1-8cd966be87fc",
        version="2.5.1",
        sha256=SHA,
    )
    req["source_members"].append(member)
    if variant == "wrong_sha":
        member["sha256"] = "sha256:" + "f" * 64
    if variant == "ambiguous_member":
        req["source_members"].append(dict(member, sha256="sha256:" + "f" * 64))
    original = deepcopy(coverage)
    envelope, _, window, _ = build_replay_envelope(load_replay_request(req), T1)
    assert window["delivered"]["600000"]["entry_eligible"] is (variant == "valid")
    assert envelope["securities"][0]["identity"]["venue_id"] == "SSE"
    assert coverage == original
