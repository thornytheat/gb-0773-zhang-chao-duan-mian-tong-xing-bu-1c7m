import json

from app.config import RULES
from app.errors import TideError
from app.flow import calculate
from app.modules.journal.manifest import canonical, digest

from .portsign import port_sign, port_verifies
from .signature import seal, verifies
from .vessel import vessel_scope


def issue(receipt, sources, reports):
    if not verifies(reports, receipt['result_sha256']):
        raise TideError('INVALID_PROOF', '归档结果校验未通过', 409)
    if set(sources) != set(receipt['inputs']):
        raise TideError('INVALID_PROOF', '归档原件清单不符', 409)
    if any(digest(text) != receipt['inputs'][name] for name, text in sources.items()):
        raise TideError('INVALID_PROOF', '归档原件校验未通过', 409)
    evidence = {'manifest': receipt, 'originals': sources, 'reports': reports}
    return {**evidence, 'seal_sha256': seal(evidence), 'verified': True}


def issue_vessel(receipt, sources, reports, vessel):
    """签发船级凭据：结论对应留档里的该船，材料按船裁剪，并加盖港方 HMAC 港签。"""
    if not verifies(reports, receipt['result_sha256']):
        raise TideError('INVALID_PROOF', '归档结果校验未通过', 409)
    proof = vessel_scope(receipt, sources, reports, vessel)
    # 港签覆盖凭据正文（不含两个签名字段），船方剥离签名后可独立重算比对
    return {**proof, 'seal_sha256': seal(proof),
            'port_signature': port_sign(proof), 'verified': True}


def _check_anchors(proof):
    anchors = proof['anchors']
    if proof.get('schema') != 'tide-vessel-proof-1' or proof['rule_version'] != anchors['rule_version']:
        raise TideError('INVALID_PROOF', '凭据版本或规则锚点不符', 409)
    if proof['run_id'] != anchors['run_id']:
        raise TideError('INVALID_PROOF', '凭据锚点与留档标识不符', 409)
    # run_id 由规则版本与全簿原件摘要定址，绑定港方留档；
    # 攻击者重铸船级锚点也无法伪造这一全簿绑定，无需复算即可检出调包船期。
    if digest(canonical([RULES, anchors['input_digests']])) != anchors['run_id']:
        raise TideError('INVALID_PROOF', '留档号与全簿原件指纹绑定不符', 409)
    if digest(canonical(proof['reports'])) != anchors['vessel_report_sha256']:
        raise TideError('INVALID_PROOF', '船期结论与留档指纹不符', 409)
    if digest(proof['sources']['vessels']) != anchors['vessel_source_sha256']:
        raise TideError('INVALID_PROOF', '本船船期原件与凭据指纹不符', 409)
    for name in ('observations', 'stations'):
        if digest(proof['sources'][name]) != anchors['input_digests'][name]:
            raise TideError('INVALID_PROOF', f'{name} 原件与留档指纹不符', 409)
    vessel = proof['vessel']
    if set(proof['reports']['crossings']) != {vessel} or set(proof['reports']['windows']) != {vessel} \
            or set(proof['reports']['coverage']['ships']) != {vessel}:
        raise TideError('INVALID_PROOF', '凭据混入其他船期结论', 409)


def verify_vessel(proof, recompute=False):
    """船方离线验真：先验港方 HMAC 港签，再验封缄与留档锚点，最后按需复算。"""
    claimed = proof.get('seal_sha256')
    port_signature = proof.get('port_signature')
    body = {key: value for key, value in proof.items()
            if key not in ('seal_sha256', 'port_signature', 'verified')}
    # 港签覆盖含内容封缄在内的整个正文，任何调包（含重铸船级锚点）都无法伪造
    if not port_verifies(body, port_signature):
        raise TideError('INVALID_PROOF', '港方签名验不过，凭据非本港签发或已被调包', 409)
    if not claimed or seal(body) != claimed:
        raise TideError('INVALID_PROOF', '凭据封缄验不过，内容已被改动', 409)
    _check_anchors(body)
    vessel = body['vessel']
    anchors = body['anchors']
    if recompute:
        try:
            recalculated = calculate(body['sources'])
            # 单船 CSV 重建后行号从 2 开始，恢复成留档船期表中的原行号再比对
            for stage in ('crossings', 'windows'):
                recalculated[stage][vessel]['order']['line'] = body['vessel_line']
            recalculated['coverage']['ships'][vessel]['order_line'] = body['vessel_line']
            # 锚点绑定留档规范船期：伪造船级锚点调包船期时在此拦下
            order = recalculated['crossings'][vessel]['order']
            if digest(canonical(order)) != anchors['vessel_order_sha256']:
                raise TideError('INVALID_PROOF', '本船船期与留档订单锚点不符，船期已被调包', 409)
            for stage in ('crossings', 'windows', 'coverage'):
                if stage == 'coverage':
                    actual = recalculated[stage]['ships'][vessel]
                    expected = body['reports'][stage]['ships'][vessel]
                else:
                    actual = recalculated[stage][vessel]
                    expected = body['reports'][stage][vessel]
                # 凭据经 JSON 往返后整数会变成字符串，比较前统一归一化
                if json.loads(canonical(actual)) != json.loads(canonical(expected)):
                    raise TideError('INVALID_PROOF', f'{stage} 复算与凭据结论不符', 409)
        except KeyError as exc:
            raise TideError('INVALID_PROOF', '复算找不到对应船期，船期已被调包', 409) from exc
    return {'vessel': vessel, 'run_id': body['run_id'], 'rule_version': body['rule_version'],
            'seal_sha256': claimed, 'port_signature': port_signature,
            'recomputed': bool(recompute), 'verified': True}
