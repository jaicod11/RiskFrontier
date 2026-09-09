"""The API surface itself: one error shape, real response models, hard caps."""

from __future__ import annotations

import pytest

from app.core.errors import ERROR_CODES
from app.core.limitations import (
    BENCHMARK_PRICE_INDEX_WARNING,
    SURVIVORSHIP_BIAS_WARNING,
)
from app.core.limits import (
    MAX_BOOTSTRAP_RESAMPLES,
    MAX_FRONTIER_POINTS,
    MAX_HORIZON_DAYS,
    MAX_LOOKBACK_DAYS,
    MAX_N_SIMS,
    MAX_TICKERS,
    MAX_WINDOW_YEARS,
)

PORTFOLIO = {
    "positions": [
        {"ticker": "RELIANCE", "weight": 0.5},
        {"ticker": "TCS", "weight": 0.5},
    ],
    "total_value_inr": 1_000_000,
}
BACKTEST_BODY = {
    "tickers": ["RELIANCE", "TCS"],
    "target_weights": {"RELIANCE": 0.5, "TCS": 0.5},
    "start_date": "2022-01-03",
    "end_date": "2025-12-31",
}
BOOTSTRAP_BODY = {
    "tickers": ["RELIANCE", "TCS", "HDFCBANK"],
    "strategy": {
        "kind": "constant_mix",
        "target_weights": {"RELIANCE": 0.34, "TCS": 0.33, "HDFCBANK": 0.33},
    },
    "window_years": 3.0,
    "include_windows": False,
}


def _assert_error_contract(response, status: int, error_code: str):
    """Every error, everywhere, is this exact shape."""
    assert response.status_code == status, response.text
    payload = response.json()

    assert set(payload) == {"error_code", "message", "details"}, payload
    assert payload["error_code"] == error_code
    assert payload["error_code"] in ERROR_CODES
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["details"], dict)
    return payload


# --- documentation ----------------------------------------------------------


def test_openapi_schema_loads(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    assert schema["info"]["title"]
    assert schema["paths"]
    # Every operation is documented.
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert operation.get("summary"), f"{method.upper()} {path} has no summary"
            assert operation.get("description"), f"{method.upper()} {path} has no description"


def test_docs_page_renders(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert b"swagger" in response.content.lower()


def test_every_path_is_unchanged(client):
    """Phase 7 was reorganisation, not a URL change."""
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {
        "/",
        "/health",
        "/api/securities",
        "/api/securities/{ticker}/prices",
        "/api/risk/var",
        "/api/portfolio/optimize",
        "/api/backtest/run",
        "/api/backtest/bootstrap",
    }


def test_every_operation_declares_a_response_model(client):
    """No bare dicts: the frontend can generate types from this."""
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            content = operation["responses"]["200"].get("content", {})
            body = content.get("application/json", {})
            assert body.get("schema"), f"{method.upper()} {path} has no 200 schema"
            reference = str(body["schema"])
            assert "$ref" in reference or "items" in reference, (
                f"{method.upper()} {path} returns an untyped body"
            )


def test_tags_are_documented(client):
    schema = client.get("/openapi.json").json()
    described = {tag["name"] for tag in schema.get("tags", [])}
    used = {
        tag
        for operations in schema["paths"].values()
        for operation in operations.values()
        for tag in operation.get("tags", [])
    }
    assert used <= described, f"undocumented tags: {used - described}"


# --- the error contract -----------------------------------------------------


def test_validation_error_uses_the_contract(client):
    response = client.post("/api/risk/var", json={"portfolio": {}})
    payload = _assert_error_contract(response, 422, "VALIDATION_ERROR")
    assert "fields" in payload["details"]


def test_invalid_weights_error_code(client):
    body = {
        "portfolio": {
            "positions": [
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "TCS", "weight": 0.3},
            ],
            "total_value_inr": 1_000_000,
        }
    }
    payload = _assert_error_contract(
        client.post("/api/risk/var", json=body), 422, "INVALID_WEIGHTS"
    )
    assert "0.800000" in payload["message"]
    assert payload["details"]["sum"] == pytest.approx(0.8)


def test_duplicate_tickers_error_code(client):
    body = {
        "portfolio": {
            "positions": [
                {"ticker": "RELIANCE", "weight": 0.5},
                {"ticker": "RELIANCE.NS", "weight": 0.5},
            ],
            "total_value_inr": 1_000_000,
        }
    }
    payload = _assert_error_contract(
        client.post("/api/risk/var", json=body), 422, "INVALID_WEIGHTS"
    )
    assert payload["details"]["duplicates"] == ["RELIANCE"]


def test_unknown_ticker_in_body_error_code(client):
    body = {
        "portfolio": {
            "positions": [{"ticker": "NOTATICKER", "weight": 1.0}],
            "total_value_inr": 1_000_000,
        }
    }
    payload = _assert_error_contract(
        client.post("/api/risk/var", json=body), 422, "UNKNOWN_TICKER"
    )
    assert "NOTATICKER" in payload["message"]


def test_unknown_ticker_in_path_is_not_found(client):
    """A path-addressed resource that does not exist is genuinely a 404."""
    _assert_error_contract(
        client.get("/api/securities/NOTATICKER/prices"), 404, "NOT_FOUND"
    )


def test_infeasible_constraints_error_code(client):
    body = {
        "tickers": ["RELIANCE", "TCS", "INFY", "ITC", "SBIN"],
        "max_weight_per_asset": 0.1,
    }
    payload = _assert_error_contract(
        client.post("/api/portfolio/optimize", json=body), 422,
        "INFEASIBLE_CONSTRAINTS",
    )
    assert "0.1" in payload["message"]


def test_insufficient_coverage_error_code(client):
    body = dict(BACKTEST_BODY)
    body["tickers"] = ["RELIANCE", "JIOFIN"]
    body["target_weights"] = {"RELIANCE": 0.5, "JIOFIN": 0.5}
    response = client.post("/api/backtest/run", json=body)
    if response.status_code == 200:
        pytest.skip("database not ingested")
    payload = _assert_error_contract(response, 422, "INSUFFICIENT_COVERAGE")
    assert "JIOFIN" in payload["message"]


def test_invalid_parameter_error_code(client):
    response = client.get(
        "/api/securities/RELIANCE/prices",
        params={"start": "2024-06-01", "end": "2024-01-01"},
    )
    _assert_error_contract(response, 422, "INVALID_PARAMETER")


def test_inverted_backtest_dates_error_code(client):
    body = dict(BACKTEST_BODY, start_date="2026-01-01", end_date="2022-01-01")
    _assert_error_contract(
        client.post("/api/backtest/run", json=body), 422, "INVALID_PARAMETER"
    )


def test_unexpected_exception_returns_generic_500(client, monkeypatch):
    """The client learns nothing; the server logs the traceback."""
    import app.routers.risk as risk_router

    def boom(*args, **kwargs):
        raise RuntimeError("internal detail that must not leak: secret-token-123")

    monkeypatch.setattr(risk_router, "build_returns_matrix", boom)

    # A TestClient that lets the app's own handler produce the response, rather
    # than re-raising the exception into the test.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.post("/api/risk/var", json={"portfolio": PORTFOLIO})
    payload = _assert_error_contract(response, 500, "INTERNAL_ERROR")
    assert "secret-token-123" not in response.text
    assert "Traceback" not in response.text


# --- request caps -----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("n_sims", MAX_N_SIMS + 1),
        ("horizon_days", MAX_HORIZON_DAYS + 1),
        ("lookback_days", MAX_LOOKBACK_DAYS + 1),
    ],
)
def test_var_caps_reject_at_the_limit(client, field, value):
    body = {"portfolio": PORTFOLIO, field: value}
    payload = _assert_error_contract(
        client.post("/api/risk/var", json=body), 422, "REQUEST_LIMIT_EXCEEDED"
    )
    assert field in payload["message"]
    assert payload["details"]["parameter"] == field
    assert payload["details"]["limit"] == pytest.approx(value - 1)


def test_var_accepts_the_limit_exactly(client):
    """The cap rejects above the limit, not at it."""
    body = {"portfolio": PORTFOLIO, "n_sims": MAX_N_SIMS, "horizon_days": 1}
    response = client.post("/api/risk/var", json=body)
    assert response.status_code in (200, 422)
    if response.status_code == 422:
        assert response.json()["error_code"] != "REQUEST_LIMIT_EXCEEDED"


def test_too_many_positions_rejected(client):
    positions = [
        {"ticker": f"T{i}", "weight": 1.0 / (MAX_TICKERS + 1)}
        for i in range(MAX_TICKERS + 1)
    ]
    body = {"portfolio": {"positions": positions, "total_value_inr": 1_000_000}}
    payload = _assert_error_contract(
        client.post("/api/risk/var", json=body), 422, "REQUEST_LIMIT_EXCEEDED"
    )
    assert payload["details"]["limit"] == MAX_TICKERS


def test_optimizer_caps(client):
    payload = _assert_error_contract(
        client.post(
            "/api/portfolio/optimize",
            json={"tickers": ["RELIANCE", "TCS"], "n_frontier_points": MAX_FRONTIER_POINTS + 1},
        ),
        422, "REQUEST_LIMIT_EXCEEDED",
    )
    assert payload["details"]["parameter"] == "n_frontier_points"


def test_backtest_date_range_cap(client):
    body = dict(BACKTEST_BODY, start_date="1900-01-01", end_date="2026-01-01")
    payload = _assert_error_contract(
        client.post("/api/backtest/run", json=body), 422, "REQUEST_LIMIT_EXCEEDED"
    )
    assert payload["details"]["parameter"] == "date range"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("n_resamples", MAX_BOOTSTRAP_RESAMPLES + 1),
        ("window_years", MAX_WINDOW_YEARS + 1),
    ],
)
def test_bootstrap_caps(client, field, value):
    body = dict(BOOTSTRAP_BODY)
    body[field] = value
    payload = _assert_error_contract(
        client.post("/api/backtest/bootstrap", json=body), 422,
        "REQUEST_LIMIT_EXCEEDED",
    )
    assert payload["details"]["parameter"] == field


# --- limitations survive serialisation --------------------------------------


def test_backtest_json_carries_the_benchmark_warnings(client):
    """Assert on the JSON the client receives, not the Python object."""
    response = client.post("/api/backtest/run", json=BACKTEST_BODY)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200

    raw = response.text
    assert SURVIVORSHIP_BIAS_WARNING in raw
    assert BENCHMARK_PRICE_INDEX_WARNING in raw

    limitations = response.json()["limitations"]
    assert SURVIVORSHIP_BIAS_WARNING in limitations
    assert BENCHMARK_PRICE_INDEX_WARNING in limitations
    assert all(isinstance(item, str) for item in limitations)


def test_bootstrap_json_carries_the_benchmark_warnings(client):
    response = client.post("/api/backtest/bootstrap", json=BOOTSTRAP_BODY)
    if response.status_code == 422:
        pytest.skip("database not ingested")
    assert response.status_code == 200

    raw = response.text
    assert SURVIVORSHIP_BIAS_WARNING in raw
    assert BENCHMARK_PRICE_INDEX_WARNING in raw
    assert SURVIVORSHIP_BIAS_WARNING in response.json()["limitations"]


def test_every_analytical_response_has_limitations_of_one_shape(client):
    """Same field, same type, everywhere."""
    calls = [
        ("/api/risk/var", {"portfolio": PORTFOLIO, "n_sims": 500}),
        ("/api/portfolio/optimize", {"tickers": ["RELIANCE", "TCS", "INFY"], "n_frontier_points": 3}),
        ("/api/backtest/run", BACKTEST_BODY),
        ("/api/backtest/bootstrap", BOOTSTRAP_BODY),
    ]
    for path, body in calls:
        response = client.post(path, json=body)
        if response.status_code == 422:
            pytest.skip("database not ingested")
        payload = response.json()
        assert "limitations" in payload, path
        assert isinstance(payload["limitations"], list), path
        assert payload["limitations"], f"{path} returned an empty limitations list"
        assert all(isinstance(item, str) for item in payload["limitations"]), path


# --- response consistency ---------------------------------------------------


def test_every_simulating_response_reports_its_data_window(client):
    calls = [
        ("/api/risk/var", {"portfolio": PORTFOLIO, "n_sims": 500}),
        ("/api/portfolio/optimize", {"tickers": ["RELIANCE", "TCS", "INFY"], "n_frontier_points": 3}),
        ("/api/backtest/run", BACKTEST_BODY),
        ("/api/backtest/bootstrap", BOOTSTRAP_BODY),
    ]
    for path, body in calls:
        response = client.post(path, json=body)
        if response.status_code == 422:
            pytest.skip("database not ingested")
        window = response.json().get("data_window")
        assert window is not None, f"{path} reports no data_window"
        assert {"start_date", "end_date", "trading_days"} <= set(window), path
        assert window["start_date"] < window["end_date"], path
        assert window["trading_days"] > 0, path


def test_monetary_fields_are_suffixed_consistently(client):
    """Every rupee amount says so in its name."""
    schema = client.get("/openapi.json").json()
    offenders = []
    money_words = ("capital", "pnl", "var_", "cvar_")
    for name, model in schema["components"]["schemas"].items():
        for field in model.get("properties", {}):
            if field.endswith(("_inr", "_pct", "_bps", "_ratio", "_return")):
                continue
            if any(word in field for word in money_words):
                offenders.append(f"{name}.{field}")
    assert not offenders, f"monetary fields without a unit suffix: {offenders}"


def test_field_names_are_snake_case(client):
    import re

    schema = client.get("/openapi.json").json()
    offenders = [
        f"{name}.{field}"
        for name, model in schema["components"]["schemas"].items()
        for field in model.get("properties", {})
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field)
    ]
    assert not offenders, f"non snake_case fields: {offenders}"


def test_drawdown_is_named_consistently(client):
    """No mix of max_dd and max_drawdown across endpoints."""
    schema = client.get("/openapi.json").json()
    names = {
        field
        for model in schema["components"]["schemas"].values()
        for field in model.get("properties", {})
        if "draw" in field or field.endswith("_dd")
    }
    assert names <= {
        "max_drawdown",
        "max_drawdown_peak_date",
        "max_drawdown_trough_date",
    }, names


def test_every_post_request_model_has_an_example(client):
    """/docs is only useful if the try-it-out payload is realistic."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    for name in ("VarRequest", "OptimizeRequest", "BacktestRequest", "BootstrapRequest"):
        assert name in components, f"{name} missing from the schema"
        examples = components[name].get("examples")
        assert examples, f"{name} has no request example"


def test_an_example_demonstrates_a_limitations_bearing_response(client):
    """At least one example spells out that the response carries caveats."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    narrated = []
    for name in ("BacktestRequest", "BootstrapRequest"):
        for example in components[name].get("examples", []):
            if isinstance(example, dict) and "description" in example:
                narrated.append(example["description"])

    assert narrated, "no example explains the limitations field"
    joined = " ".join(narrated).lower()
    assert "survivorship" in joined
    assert "limitations" in joined


def test_error_responses_are_documented_as_the_error_model(client):
    """A generated client should know 422 bodies are ErrorResponse."""
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method != "post":
                continue
            response_422 = operation["responses"].get("422")
            assert response_422, f"{method.upper()} {path} does not document 422"
            body = str(response_422.get("content", {}))
            assert "ErrorResponse" in body, f"{method.upper()} {path} 422 is untyped"


# --- production budgets -----------------------------------------------------


def test_walk_forward_block_bootstrap_is_budgeted_separately(client):
    """The one combination that cannot reuse the optimiser cache.

    It must be refused above budget with an actionable message rather than
    accepted and left to time out at the proxy.
    """
    from app.core.limits import MAX_WALK_FORWARD_BLOCK_RESAMPLES

    body = {
        "tickers": ["RELIANCE", "TCS", "HDFCBANK"],
        "strategy": {"kind": "walk_forward", "objective": "max_sharpe"},
        "method": "block_bootstrap",
        "n_resamples": MAX_WALK_FORWARD_BLOCK_RESAMPLES + 1,
        "window_years": 3.0,
    }
    payload = _assert_error_contract(
        client.post("/api/backtest/bootstrap", json=body), 422,
        "REQUEST_LIMIT_EXCEEDED",
    )
    assert payload["details"]["limit"] == MAX_WALK_FORWARD_BLOCK_RESAMPLES
    assert payload["details"]["strategy_kind"] == "walk_forward"
    # The message must name the cheaper alternative, not just say "no".
    assert "rolling_windows" in payload["message"]


def test_constant_mix_block_bootstrap_keeps_the_higher_budget(client):
    """Only the expensive combination is restricted."""
    from app.core.limits import MAX_WALK_FORWARD_BLOCK_RESAMPLES

    body = {
        "tickers": ["RELIANCE", "TCS", "HDFCBANK"],
        "strategy": {
            "kind": "constant_mix",
            "target_weights": {"RELIANCE": 0.34, "TCS": 0.33, "HDFCBANK": 0.33},
        },
        "method": "block_bootstrap",
        "n_resamples": MAX_WALK_FORWARD_BLOCK_RESAMPLES + 1,
        "window_years": 3.0,
        "include_windows": False,
    }
    response = client.post("/api/backtest/bootstrap", json=body)
    # Either it runs, or it fails for a reason other than this budget.
    if response.status_code == 422:
        assert response.json()["details"].get("limit") != MAX_WALK_FORWARD_BLOCK_RESAMPLES


def test_request_budgets_are_environment_configurable(monkeypatch):
    """Production lowers these; local development keeps the generous defaults."""
    from app.core.config import Settings

    generous = Settings()
    assert generous.max_n_sims == 200_000

    monkeypatch.setenv("MAX_N_SIMS", "25000")
    monkeypatch.setenv("MAX_WALK_FORWARD_BLOCK_RESAMPLES", "15")
    constrained = Settings()
    assert constrained.max_n_sims == 25_000
    assert constrained.max_walk_forward_block_resamples == 15


def test_localhost_origins_do_not_leak_into_production(monkeypatch):
    """The dev CORS default must not survive into a production deployment."""
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    production = Settings()
    assert production.cors_origin_list == []
    assert production.cors_origin_regex

    # Explicitly configured origins are still honoured.
    monkeypatch.setenv("CORS_ORIGINS", "https://riskfrontier.vercel.app")
    explicit = Settings()
    assert explicit.cors_origin_list == ["https://riskfrontier.vercel.app"]


def test_development_keeps_its_localhost_origins():
    from app.core.config import Settings

    dev = Settings()
    assert "http://localhost:5173" in dev.cors_origin_list


def test_migrations_can_use_a_separate_unpooled_url(monkeypatch):
    """Alembic must be able to bypass a transaction pooler.

    Neon's pooled endpoint does not preserve the session state Alembic needs,
    so migrations take DATABASE_URL_UNPOOLED when it is set while the
    application engine keeps using the pooled DATABASE_URL.
    """
    import importlib.util
    from pathlib import Path

    from app.core.config import Settings

    def migration_url_for(settings) -> str:
        """Evaluate env.py's helper without entering a migration context."""
        source = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
        namespace: dict = {}
        prelude = source.read_text().split("config = context.config")[0]
        exec(compile(prelude, "env.py", "exec"), namespace)  # noqa: S102
        namespace["settings"] = settings
        return namespace["migration_url"]()

    assert importlib.util.find_spec("alembic") is not None

    # Both set: the app uses the pooled URL, migrations use the direct one.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooled-host/db")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgres://u:p@direct-host/db")
    split = Settings()
    assert "pooled-host" in split.sqlalchemy_database_uri
    assert "direct-host" in migration_url_for(split)
    # The legacy postgres:// scheme is normalised on this URL too.
    assert migration_url_for(split).startswith("postgresql+psycopg2://")

    # Only DATABASE_URL set: migrations fall back to it, which is local dev.
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    single = Settings()
    assert single.database_url_unpooled is None
    assert migration_url_for(single) == single.sqlalchemy_database_uri


def test_app_engine_ignores_the_unpooled_url(monkeypatch):
    """Only Alembic changes; the request path still uses the pooled URL."""
    from app.core.config import Settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooled-host/db")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:p@direct-host/db")
    settings = Settings()

    assert "pooled-host" in settings.sqlalchemy_database_uri
    assert "direct-host" not in settings.sqlalchemy_database_uri
