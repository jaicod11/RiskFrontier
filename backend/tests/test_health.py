"""Health endpoint contract.

The DB-up path is asserted against a real Postgres (see `docker compose up`);
the DB-down path is asserted by overriding the session dependency.
"""

from sqlalchemy.exc import OperationalError

from app.core.db import get_db
from app.main import app


def test_health_reports_ok_when_db_reachable(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}


def test_health_reports_503_when_db_unreachable(client):
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "disconnected"}


def test_root_returns_service_metadata(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
