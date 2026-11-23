import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import TideError
from app.flow import calculate
from app.modules.journal.manifest import canonical, digest
from app.modules.journal.service import record
from app.modules.journal.store import Journal
from app.modules.proof.portsign import port_sign
from app.modules.proof.service import issue_vessel, verify_vessel
from app.modules.proof.signature import seal
from app.source import load_sources

VESSELS = ('海棠17', '甬航05', '江桐62')


@pytest.fixture
def run(client):
    return client.post('/api/journal', json={'sample': 'dirty'}).json['run_id']


@pytest.fixture
def proof(client, run):
    return client.get(f'/api/export/{run}/vessel/海棠17').json


def test_vessel_proof_scoped_to_one_ship(client, run, proof):
    assert proof['schema'] == 'tide-vessel-proof-1'
    assert proof['vessel'] == '海棠17'
    assert set(proof['reports']['crossings']) == {'海棠17'}
    assert set(proof['reports']['windows']) == {'海棠17'}
    assert set(proof['reports']['coverage']['ships']) == {'海棠17'}
    blob = json.dumps(proof, ensure_ascii=False)
    assert '甬航05' not in blob and '江桐62' not in blob


def test_vessel_csv_has_only_own_schedule(proof):
    rows = [line for line in proof['sources']['vessels'].splitlines() if line]
    assert len(rows) == 2 and rows[0].startswith('vessel,')
    assert rows[1].startswith('海棠17,')
    # 共享断面读数随证，整份船期表只留指纹
    assert proof['sources']['observations'].startswith('﻿station')
    assert proof['anchors']['input_digests']['vessels'] != digest(proof['sources']['vessels'])


def test_conclusion_matches_archived_ship(client, run, proof):
    receipt, sources, reports = client.application.extensions['tide_journal'].get(run)
    archived = reports['coverage']['ships']['海棠17']
    assert proof['reports']['coverage']['ships']['海棠17'] == archived
    assert proof['anchors']['result_sha256'] == receipt['result_sha256']
    assert proof['run_id'] == run


def test_verify_seal_anchors_and_recompute(proof):
    assert verify_vessel(copy.deepcopy(proof))['verified'] is True
    result = verify_vessel(copy.deepcopy(proof), recompute=True)
    assert result['recomputed'] is True and result['vessel'] == '海棠17'


def test_all_vessels_and_samples(client):
    for sample in ('dirty', 'clean'):
        key = client.post('/api/journal', json={'sample': sample}).json['run_id']
        for vessel in VESSELS:
            doc = client.get(f'/api/export/{key}/vessel/{vessel}').json
            assert client.post('/api/export/verify', json=doc).status_code == 200
            assert client.post('/api/export/verify?recompute=1', json=doc).status_code == 200


def test_unknown_vessel_404(client, run):
    response = client.get(f'/api/export/{run}/vessel/幽灵船')
    assert (response.status_code, response.json['code']) == (404, 'NO_VESSEL')


def test_unknown_run_404(client):
    response = client.get('/api/export/missing/vessel/海棠17')
    assert (response.status_code, response.json['code']) == (404, 'NO_RUN')


def test_download_is_attachment_with_vessel_name(client, run):
    response = client.get(f'/api/export/{run}/vessel/海棠17')
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment;') and '海棠17' in disposition


def test_port_archive_keeps_full_batch(client, run):
    response = client.get(f'/api/export/{run}')
    assert response.status_code == 200
    assert set(response.json['reports']['coverage']['ships']) == set(VESSELS)
    assert 'manifest' in response.json and 'originals' in response.json


def test_retry_issue_is_identical_and_keeps_schedule(client, run):
    first = client.get(f'/api/export/{run}/vessel/海棠17').json
    second = client.get(f'/api/export/{run}/vessel/海棠17').json
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    client.post(f'/api/journal/{run}/rerun')
    third = client.get(f'/api/export/{run}/vessel/海棠17').json
    assert third['run_id'] == run and third['seal_sha256'] == first['seal_sha256']
    assert third['port_signature'] == first['port_signature']


def test_old_journal_still_signable(tmp_path):
    path = tmp_path / 'saved.db'
    first = create_app(path).test_client()
    key = first.post('/api/journal', json={'sample': 'clean'}).json['run_id']
    reopened = create_app(path).test_client()
    response = reopened.get(f'/api/export/{key}/vessel/甬航05')
    assert response.status_code == 200 and response.json['vessel'] == '甬航05'


def test_concurrent_issue_never_crosses_vessels(tmp_path, sources):
    journal = Journal(tmp_path / 'overlap.db')
    receipt = record(journal, sources)['manifest']
    reports = calculate(sources)
    with ThreadPoolExecutor(max_workers=4) as pool:
        docs = list(pool.map(
            lambda vessel: issue_vessel(receipt, sources, reports, vessel),
            ['海棠17', '甬航05', '江桐62', '海棠17']))
    for doc in docs:
        others = {name for name in VESSELS if name != doc['vessel']}
        blob = json.dumps(doc, ensure_ascii=False)
        assert not (others & set(json.loads(blob).get('reports', {})
                                 .get('coverage', {}).get('ships', {}).keys()))
        assert all(name not in blob for name in others)
    assert docs[0]['port_signature'] == docs[-1]['port_signature']


def _rebuild_with_tampered_anchor(proof, mutate_sources):
    doc = copy.deepcopy(proof)
    mutate_sources(doc)
    recalculated = calculate(doc['sources'])
    vessel = doc['vessel']
    doc['reports'] = {
        'crossings': {vessel: recalculated['crossings'][vessel]},
        'windows': {vessel: recalculated['windows'][vessel]},
        'coverage': {'ships': {vessel: recalculated['coverage']['ships'][vessel]},
                     'balanced': recalculated['coverage']['ships'][vessel]['balanced']}}
    doc['anchors']['vessel_source_sha256'] = digest(doc['sources']['vessels'])
    doc['anchors']['vessel_report_sha256'] = digest(canonical(doc['reports']))
    doc['anchors']['vessel_order_sha256'] = digest(
        canonical(recalculated['crossings'][vessel]['order']))
    doc.pop('seal_sha256')
    doc.pop('port_signature')
    doc.pop('verified')
    doc['seal_sha256'] = seal(doc)  # 攻击者可重算内容封缄，但没有港方密钥
    return doc


def test_swap_gauge_fails_even_with_forged_anchors(proof):
    forged = _rebuild_with_tampered_anchor(
        proof, lambda doc: doc['sources'].__setitem__(
            'observations', doc['sources']['observations'] + '\n'))
    with pytest.raises(TideError) as error:
        verify_vessel(forged)
    assert error.value.code == 'INVALID_PROOF'


def test_swap_schedule_fails_even_with_forged_anchors(proof):
    forged = _rebuild_with_tampered_anchor(
        proof, lambda doc: doc['sources'].__setitem__(
            'vessels', doc['sources']['vessels'].replace('2.3', '2.9')))
    for recompute in (False, True):
        with pytest.raises(TideError) as error:
            verify_vessel(copy.deepcopy(forged), recompute=recompute)
        assert error.value.code == 'INVALID_PROOF'


@pytest.mark.parametrize('mutate', [
    lambda doc: doc['reports']['coverage']['ships']['海棠17']['seconds'].__setitem__('open', '9999'),
    lambda doc: doc['sources'].__setitem__('stations', doc['sources']['stations'].replace('600', '900')),
    lambda doc: doc.__setitem__('seal_sha256', '0' * 64),
    lambda doc: doc.__setitem__('port_signature', '1' * 64),
    lambda doc: doc.pop('port_signature'),
    lambda doc: doc['anchors'].__setitem__('run_id', '2' * 64),
    lambda doc: doc['reports']['coverage']['ships'].__setitem__('甬航05', {'x': 1}),
    lambda doc: doc['anchors']['input_digests'].__setitem__('observations', '3' * 64),
])
def test_any_tamper_is_rejected(proof, mutate):
    doc = copy.deepcopy(proof)
    mutate(doc)
    with pytest.raises(TideError) as error:
        verify_vessel(doc)
    assert error.value.code == 'INVALID_PROOF'


def test_verify_endpoint(client, proof):
    ok = client.post('/api/export/verify', json=proof)
    assert ok.status_code == 200 and ok.json['verified'] is True
    recomputed = client.post('/api/export/verify?recompute=1', json=proof)
    assert recomputed.status_code == 200 and recomputed.json['recomputed'] is True
    bad = client.post('/api/export/verify', json=['nope'])
    assert (bad.status_code, bad.json['code']) == (422, 'BAD_PROOF')
    stolen = copy.deepcopy(proof)
    stolen['port_signature'] = 'deadbeef'
    assert client.post('/api/export/verify', json=stolen).status_code == 409


def test_port_signature_cannot_be_forged_without_secret(proof, monkeypatch):
    # 攻击者重铸全部船级锚点后，用自己的密钥签出的港签在本港密钥下验不过
    forged = _rebuild_with_tampered_anchor(
        proof, lambda doc: doc['sources'].__setitem__(
            'vessels', doc['sources']['vessels'].replace('0.2', '0.9')))
    from app.modules.proof import portsign
    monkeypatch.setenv('TIDE_SECRET', 'attacker-own-secret')
    portsign._port_key.cache_clear()
    forged['port_signature'] = port_sign(forged)
    monkeypatch.setenv('TIDE_SECRET', 'test-port-secret')
    portsign._port_key.cache_clear()
    with pytest.raises(TideError):
        verify_vessel(forged)


def test_issuance_page_links_vessel_and_archive(client, run):
    from flask import url_for
    page = client.get(f'/desk/proof?run={run}&vessel=甬航05').get_data(as_text=True)
    with client.application.test_request_context():
        vessel_link = url_for('api.export_vessel', key=run, vessel='甬航05')
        archive_link = url_for('api.download', key=run)
    assert vessel_link in page and archive_link in page
    assert '潮窗验签' in page and '甬航05' in page


def create_app(path):
    from app.main import create_app as factory
    return factory(path)
