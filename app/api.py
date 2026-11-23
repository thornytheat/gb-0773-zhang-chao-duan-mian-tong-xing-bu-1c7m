from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request

from app.errors import TideError
from app.flow import STEPS, calculate
from app.modules.journal.service import record
from app.modules.proof.service import issue, issue_vessel, verify_vessel
from app.source import load_sources

api = Blueprint('api', __name__, url_prefix='/api')


def journal():
    return current_app.extensions['tide_journal']


@api.route('/stage/<stage>', methods=['GET', 'POST'])
def stage_result(stage):
    if stage not in STEPS:
        raise TideError('NO_STAGE', '找不到潮窗模块', 404)
    if 'run' in request.args and request.method == 'GET':
        _, _, reports = journal().get(request.args['run'])
    else:
        payload = request.get_json(silent=True) if request.method == 'POST' else {'sample': request.args.get('sample', 'dirty')}
        reports = calculate(load_sources(payload), stage)
    return jsonify(reports[stage])


@api.get('/journal')
def list_journal():
    return jsonify(runs=journal().list())


@api.post('/journal')
def publish_journal():
    result = record(journal(), load_sources(request.get_json(silent=True)))
    return jsonify(result), 200 if result['reused'] else 201


@api.get('/journal/<key>')
def inspect_journal(key):
    receipt, _, reports = journal().get(key)
    return jsonify(run_id=key, manifest=receipt, coverage=reports['coverage'])


@api.post('/journal/<key>/rerun')
def repeat_journal(key):
    manifest, sources, _ = journal().get(key)
    return jsonify(record(journal(), sources, manifest))


def _attachment(filename):
    return {'Content-Disposition':
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"}


@api.get('/export/<key>/vessel/<vessel>')
def export_vessel(key, vessel):
    """向船方签发：只含该船可验真凭据，别船资料不随证。"""
    receipt, sources, reports = journal().get(key)
    proof = issue_vessel(receipt, sources, reports, vessel)
    response = jsonify(proof)
    response.headers.update(_attachment(f'tide-{key[:12]}-{vessel}.json'))
    return response


@api.post('/export/verify')
def verify_export():
    proof = request.get_json(silent=True)
    if not isinstance(proof, dict):
        raise TideError('BAD_PROOF', '请提交船级凭据 JSON')
    recompute = request.args.get('recompute') in ('1', 'true', 'yes')
    return jsonify(verify_vessel(proof, recompute=recompute))


@api.get('/export/<key>')
def download(key):
    """港方留底：整批原件与全船结论，仅港方工作台使用，不发给船方。"""
    response = jsonify(issue(*journal().get(key)))
    response.headers.update(_attachment(f'tide-archive-{key[:12]}.json'))
    return response
