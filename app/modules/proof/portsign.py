import hashlib
import hmac
import os
from functools import lru_cache

from app.modules.journal.manifest import canonical


@lru_cache(maxsize=None)
def _port_key():
    """港方签发密钥。

    生产经 TIDE_SECRET 注入；未配置时退回与留档库绑定的派生密钥，
    让本机演示和测试可用，但跨库无法互验——部署必须显式配置密钥。
    """
    secret = os.environ.get('TIDE_SECRET')
    if secret:
        return secret.encode()
    import warnings

    fallback = os.environ.get('TIDE_DB', 'var/tide.sqlite3')
    warnings.warn('未配置 TIDE_SECRET，港签退回留档库派生密钥，仅限本机演示', stacklevel=2)
    return hashlib.sha256(b'tide-port-fallback:' + fallback.encode()).digest()


def port_sign(proof_body):
    """对不含签名字段的凭据正文做 HMAC，港方身份不可被船方或调包者伪造。"""
    return hmac.new(_port_key(), canonical(proof_body).encode(), hashlib.sha256).hexdigest()


def port_verifies(proof_body, signature):
    return isinstance(signature, str) and hmac.compare_digest(port_sign(proof_body), signature)
