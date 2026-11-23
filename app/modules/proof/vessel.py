import csv
import io

from app.errors import TideError
from app.modules.intake.contract import HEADERS
from app.modules.journal.manifest import canonical, digest


def vessel_csv(reports, vessel):
    """重建只含该船一行的船期表（保留规范字段排列与原始取值）。"""
    rows = reports['readings']['tables']['vessels']
    try:
        record = next(row for row in rows if row['raw']['vessel'].strip() == vessel)
    except StopIteration as exc:
        raise TideError('NO_VESSEL', '找不到该船期', 404) from exc
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HEADERS['vessels'], lineterminator='\n')
    writer.writeheader()
    writer.writerow(record['raw'])
    return buffer.getvalue(), record['line']


def vessel_scope(receipt, sources, reports, vessel):
    """按船裁剪证据：共享断面原件随证，本船船期单独成行，别船资料不随证。"""
    if vessel not in reports['coverage']['ships']:
        raise TideError('NO_VESSEL', '找不到该船期', 404)
    schedule, order_line = vessel_csv(reports, vessel)
    # 潮尺与站点是跨船共享的断面读数，可随证供离线复算；
    # 整份船期表只留指纹，别船吃水与申请时刻不离开港方留档。
    scoped_sources = {'observations': sources['observations'],
                      'stations': sources['stations'],
                      'vessels': schedule}
    scoped_reports = {'crossings': {vessel: reports['crossings'][vessel]},
                      'windows': {vessel: reports['windows'][vessel]},
                      'coverage': {'ships': {vessel: reports['coverage']['ships'][vessel]},
                                   'balanced': reports['coverage']['ships'][vessel]['balanced']}}
    archived_order = dict(reports['crossings'][vessel]['order'])
    return {
        'schema': 'tide-vessel-proof-1',
        'vessel': vessel,
        'vessel_line': order_line,
        'run_id': receipt['run_id'],
        'rule_version': receipt['rule_version'],
        'sources': scoped_sources,
        'anchors': {
            'run_id': receipt['run_id'],
            'rule_version': receipt['rule_version'],
            'result_sha256': receipt['result_sha256'],
            'vessel_report_sha256': digest(canonical(scoped_reports)),
            'vessel_source_sha256': digest(schedule),
            'vessel_order_sha256': digest(canonical(archived_order)),
            'input_digests': dict(receipt['inputs']),
        },
        'reports': scoped_reports,
    }
