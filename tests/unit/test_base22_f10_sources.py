from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_f10_sources import bind_f10_receipt


def fixture():
    rows = []
    for category, content in [('公司概况', '合成公司'), ('分红扩股', '合成分红')]:
        rows.append({'security_id': '000001', 'stock_code': '000001',
                     'category': category, 'content': content,
                     'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
                     'market_date': '2026-07-31', 'source': 'tdx.get_company_info_content',
                     'source_revision': 'quanthus-fundamentals-provider-v1',
                     'acquired_at': '2026-07-31T21:00:00.123456+08:00'})
    data = ''.join(r['content'] for r in rows).encode()
    receipt = {'dataset': 'raw_f10', 'security_id': '000001',
               'request_json': json.dumps({'operation': 'F10C+F10', 'symbol': '000001'}),
               'status': 'success_nonempty', 'reason_code': 'ok',
               'response_rows': 2, 'response_bytes': len(data),
               'response_sha256': hashlib.sha256(data).hexdigest(),
               'acquired_at': '2026-07-31T21:00:05.654321+08:00'}
    return rows, receipt


def bind(rows, receipt):
    return bind_f10_receipt(rows, receipt, market_date=date(2026, 7, 31),
                            observed_at='2026-10-05T10:00:00.111111+08:00')


def test_complete_original_order_response_and_clocks_without_provider_time_inference():
    rows, receipt = fixture()
    before = copy.deepcopy((rows, receipt))
    proof = bind(rows, receipt)
    assert (rows, receipt) == before
    assert proof['response_rows'] == 2
    assert proof['complete_selected_response'] is True
    assert proof['provider_acquisition'] == 'UNAVAILABLE'
    assert proof['knowledge_floor'].isoformat() == '2026-10-05T02:00:00.111111+00:00'
    assert proof['receipt_completion_observation'].microsecond == 654321
    assert proof['legacy_normalization_restored'] is False


@pytest.mark.parametrize('change', ['missing', 'order', 'content', 'aggregate_hash', 'bytes',
                                    'count', 'request', 'market', 'revision', 'source',
                                    'failed', 'before_completion', 'duplicate_category'])
def test_selected_raw_closure_rejects_precise_inconsistencies(change):
    rows, receipt = fixture()
    if change == 'missing':
        rows.pop()
    elif change == 'order':
        rows.reverse()
    elif change == 'content':
        rows[0]['content'] += '变'
    elif change == 'aggregate_hash':
        receipt['response_sha256'] = 'f' * 64
    elif change == 'bytes':
        receipt['response_bytes'] += 1
    elif change == 'count':
        receipt['response_rows'] += 1
    elif change == 'request':
        receipt['request_json'] = '{"operation":"F10C+F10","symbol":"000002"}'
    elif change == 'market':
        rows[0]['market_date'] = '2026-08-01'
    elif change == 'revision':
        rows[0]['source_revision'] = 'unknown'
    elif change == 'source':
        rows[0]['source'] = 'unknown'
    elif change == 'failed':
        receipt['status'] = 'failed'
    elif change == 'before_completion':
        receipt['acquired_at'] = '2026-07-31T20:00:00+08:00'
    else:
        rows[1]['category'] = rows[0]['category']
    with pytest.raises(BoundedError, match='F10_'):
        bind(rows, receipt)


def test_empty_response_is_qualified_empty_observation_not_historical_no_change():
    _, receipt = fixture()
    receipt.update(status='justified_empty', reason_code='provider_has_no_f10',
                   response_rows=0, response_bytes=0, response_sha256=None)
    proof = bind([], receipt)
    assert proof['complete_selected_response'] is True
    assert proof['historical_action_coverage_or_no_change'] is False


def test_new_observation_cannot_precede_original_collector_completion():
    rows, receipt = fixture()
    with pytest.raises(BoundedError, match='F10_OBSERVATION_PRECEDES_RECEIPT'):
        bind_f10_receipt(rows, receipt, market_date=date(2026, 7, 31),
                         observed_at='2026-07-31T20:00:00+08:00')
