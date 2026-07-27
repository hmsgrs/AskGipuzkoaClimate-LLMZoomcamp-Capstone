from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_default_compose_runtime_is_clone_auditable():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert {"postgres", "runtime-init", "streamlit", "grafana"} <= set(services)
    assert "ports" not in services["postgres"]
    assert services["runtime-init"]["depends_on"]["postgres"]["condition"] == (
        "service_healthy"
    )
    assert services["streamlit"]["depends_on"]["runtime-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["grafana"]["depends_on"]["runtime-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["streamlit"]["ports"] == ["127.0.0.1:8501:8501"]
    assert services["grafana"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["kestra"]["profiles"] == ["ingestion"]
    assert services["kestra-init"]["profiles"] == ["ingestion"]
    assert services["streamlit"]["environment"]["DATA_MODE"] == "snapshot"
    assert services["streamlit"]["environment"]["RETRIEVAL_BACKEND"] == "pgvector"
    assert "OPENAI_API_KEY" in services["streamlit"]["environment"]


def test_application_image_contains_only_committed_reviewer_snapshot():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY data/snapshots ./data/snapshots" in dockerfile
    assert "COPY evaluation/session_fixtures ./evaluation/session_fixtures" in dockerfile
    assert "!data/snapshots/gipuzkoa-demo-2026-07-22/" in dockerignore
    assert "docs/api_keys/" in dockerignore


def test_application_image_validates_streamlit_import_path_during_build():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert 'PYTHONPATH="/app"' in dockerfile
    assert "AppTest.from_file('/app/app/streamlit_app.py')" in dockerfile
    assert "import app, urllib.request" in compose
    assert "SESSION_FIXTURE_MANIFEST" in compose
