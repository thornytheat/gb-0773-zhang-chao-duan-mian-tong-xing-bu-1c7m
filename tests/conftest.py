import pytest

from app.flow import calculate
from app.main import create_app
from app.modules.proof import portsign
from app.source import load_sources


@pytest.fixture(autouse=True)
def fixed_port_secret(monkeypatch):
    """测试统一使用固定港签密钥，避免退回派生密钥告警。"""
    monkeypatch.setenv('TIDE_SECRET', 'test-port-secret')
    portsign._port_key.cache_clear()
    yield
    portsign._port_key.cache_clear()


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path / 'tide.sqlite3').test_client()


@pytest.fixture
def sources():
    return load_sources({'sample': 'dirty'})


@pytest.fixture
def case(sources):
    return calculate(sources)
