from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from business_graph_api import main as api_main
from business_graph_core.files.registry import InMemoryFileRegistry
from business_graph_core.files.storage import LocalFileStorage
from business_graph_core.services.analyzer import AnalyzerService
from business_graph_core.settings import settings

API_HEADERS = {"X-API-Key": settings.api_key}
SAMPLE_XLSX = Path("examples/sample-data/dependency_rules.xlsx")
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    registry = InMemoryFileRegistry()
    storage = LocalFileStorage(tmp_path / "files")
    service = AnalyzerService(file_registry=registry)

    monkeypatch.setattr(api_main, "_file_registry", registry)
    monkeypatch.setattr(api_main, "_file_storage", storage)
    monkeypatch.setattr(api_main, "_service", service)

    return TestClient(api_main.app)


def upload_sample(
    client: TestClient,
    *,
    workspace_id: str = "default",
    filename: str = "dependency_rules.xlsx",
):
    with SAMPLE_XLSX.open("rb") as sample:
        return client.post(
            "/api/v1/files",
            params={"workspace_id": workspace_id},
            headers=API_HEADERS,
            files={"file": (filename, sample, XLSX_CONTENT_TYPE)},
        )


def test_file_upload_registers_and_lists_file(api_client):
    upload_response = upload_sample(api_client, workspace_id="workspace-a")

    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert upload_payload["workspace_id"] == "workspace-a"
    assert upload_payload["original_filename"] == "dependency_rules.xlsx"
    assert upload_payload["size_bytes"] > 0
    assert len(upload_payload["sha256"]) == 64
    assert upload_payload["status"] == "stored"
    assert "storage_path" not in upload_payload

    list_response = api_client.get(
        "/api/v1/files",
        params={"workspace_id": "workspace-a"},
        headers=API_HEADERS,
    )

    assert list_response.status_code == 200
    files = list_response.json()
    assert len(files) == 1
    assert files[0]["file_id"] == upload_payload["file_id"]
    assert files[0]["original_filename"] == "dependency_rules.xlsx"
    assert "storage_path" not in files[0]


def test_analyze_registered_file_ids_extracts_rules(api_client):
    upload_response = upload_sample(api_client, workspace_id="analysis-workspace")
    file_id = upload_response.json()["file_id"]

    analysis_response = api_client.post(
        "/api/v1/analyses/files",
        headers=API_HEADERS,
        json={
            "workspace_id": "analysis-workspace",
            "file_ids": [file_id],
        },
    )

    assert analysis_response.status_code == 200
    payload = analysis_response.json()
    assert payload["confirmed_relations_count"] > 0
    assert payload["summary"]["node_count"] > 0
    assert payload["warnings"] == []


def test_registered_files_are_workspace_scoped(api_client):
    upload_response = upload_sample(api_client, workspace_id="workspace-a")
    file_id = upload_response.json()["file_id"]

    list_response = api_client.get(
        "/api/v1/files",
        params={"workspace_id": "workspace-b"},
        headers=API_HEADERS,
    )
    assert list_response.status_code == 200
    assert list_response.json() == []

    analysis_response = api_client.post(
        "/api/v1/analyses/files",
        headers=API_HEADERS,
        json={
            "workspace_id": "workspace-b",
            "file_ids": [file_id],
        },
    )

    assert analysis_response.status_code == 404


def test_upload_filename_is_sanitized_and_stays_under_storage_root(api_client, tmp_path):
    upload_response = upload_sample(
        api_client,
        workspace_id="safe-workspace",
        filename="../../evil.xlsx",
    )

    assert upload_response.status_code == 200
    payload = upload_response.json()
    assert payload["original_filename"] == "evil.xlsx"

    record = api_main._service.file_registry.get_file(
        "safe-workspace",
        payload["file_id"],
    )
    storage_path = Path(record.storage_path)

    assert storage_path.name == "evil.xlsx"
    assert storage_path.exists()
    assert storage_path.resolve().is_relative_to((tmp_path / "files").resolve())
