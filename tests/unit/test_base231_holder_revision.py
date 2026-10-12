from copy import deepcopy
from datetime import datetime, timezone

from quanthus.pipelines.securities_warehouse.base231_holder_revision import revision_views
from tests.unit.test_base231_native_delta import compare, row


def run(tmp_path, old, new):
    return compare(tmp_path, old, new, configure=revision_views, view='representation_revisions')


def changed():
    old = dict(row(), share_nature=None, knowledge_to=None)
    new = dict(old, share_nature='流通A股',
               knowledge_from=datetime(2026, 10, 12, tzinfo=timezone.utc))
    return old, new


def test_exact_same_source_and_scalars_can_fix_representation_as_new_knowledge(tmp_path):
    old, new = changed()
    before = deepcopy(old)
    selected, _ = run(tmp_path, [old], [new])
    assert selected == [new] and old == before


def test_same_values_in_a_different_original_artifact_do_not_grant_priority(tmp_path):
    old, new = changed()
    new['source_artifact_sha256'] = 'sha256:' + 'b' * 64
    assert run(tmp_path, [old], [new])[0] == []


def test_same_artifact_but_different_body_identity_is_not_representation_repair(tmp_path):
    old, new = changed()
    new['source_record_id'] += '-different-body'
    assert run(tmp_path, [old], [new])[0] == []


def test_changed_scalar_cannot_be_repaired(tmp_path):
    old, new = changed()
    new['holding_quantity'] += 1
    assert run(tmp_path, [old], [new])[0] == []


def test_multiple_component_matches_cannot_be_guessed(tmp_path):
    old, new = changed()
    other = dict(new, share_nature='限售A股')
    assert run(tmp_path, [old], [new, other])[0] == []


def test_prior_closed_or_nonforward_knowledge_is_not_reopened(tmp_path):
    old, new = changed()
    old['knowledge_to'] = new['knowledge_from']
    assert run(tmp_path, [old], [new])[0] == []


def test_missing_old_holder_cannot_be_disposed_as_exit(tmp_path):
    old, new = changed()
    missing = dict(old, holder_rank=2, shareholder_source_name='另一合成股东')
    selected, _ = run(tmp_path, [old, missing], [new])
    assert selected == [new]  # Missing holder remains unchanged; no inferred exit.


def test_closed_history_preserves_every_old_field_except_interval_end(tmp_path):
    old, new = changed()
    selected, _ = compare(tmp_path, [old], [new], configure=revision_views,
                          view='representation_closed_old')
    assert selected == [dict(old, knowledge_to=new['knowledge_from'])]
