import os
import socket
from datetime import date
from types import SimpleNamespace

import pytest

from company_assistant.db.seed import seed_database

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
AS_OF = date(2026, 11, 16)


def pytest_collection_modifyitems(items):
    for item in items:
        if "IntegrationTests" in item.nodeid:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def block_external_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        if os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("COHERE_API_KEY"):
            pytest.skip("Live tests require RUN_LIVE_TESTS=1 and a locally configured Cohere key.")
        return

    original_connect = socket.socket.connect

    def blocked(connection, address):
        if (
            request.node.get_closest_marker("integration")
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        ):
            return original_connect(connection, address)
        raise AssertionError("Network access is forbidden in offline tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def context():
    return SimpleNamespace(state={}, invocation_id="test-invocation")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    company = tmp_path / "company.db"
    audit = tmp_path / "audit.db"
    seed_database(company, AS_OF)
    monkeypatch.setenv("COMPANY_DB_PATH", str(company))
    monkeypatch.setenv("AUDIT_DB_PATH", str(audit))
    monkeypatch.setenv("DEMO_USER_ID", "E1001")
    monkeypatch.setattr("company_assistant.tools.common.today", lambda: AS_OF)
    return company, audit
