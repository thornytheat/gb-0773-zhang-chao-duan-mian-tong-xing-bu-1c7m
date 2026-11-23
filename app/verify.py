"""船方离线验真工具。

用法：
    python -m app.verify 凭据.json
    python -m app.verify 凭据.json --recompute

第一步只做封缄与留档锚点校验，无需重算；加 --recompute 后用随证共享
潮尺、站点原件及本船船期行重算本船结论，调包任一部分都会失败。
"""
import json
import sys

from app.errors import TideError
from app.modules.proof.service import verify_vessel


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    recompute = '--recompute' in argv
    paths = [arg for arg in argv if arg != '--recompute']
    if len(paths) != 1:
        print('用法: python -m app.verify 凭据.json [--recompute]', file=sys.stderr)
        return 2
    with open(paths[0], encoding='utf-8') as handle:
        proof = json.load(handle)
    try:
        result = verify_vessel(proof, recompute=recompute)
    except TideError as error:
        print(f'验真失败：{error}（{error.code}）', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('验真通过：凭据对应留档', result['run_id'][:12], '中该船的结论'
          + ('，复算一致。' if recompute else '。'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
