from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from llm_client import TokenUsage
from llm_client.provider import ProviderRequest, ProviderResponse

from ci_scribe.main import create_app

TRIAGE_OK = (
    '{"summary": "2 de 3 casos pasan, bajo el umbral 1.0.", "probable_cause": '
    '["desajuste determinista de idioma en el caso 2"], "severity": "moderate", '
    '"verdict": "fail", "actions": ["Corregir el caso 2"], "requires_rebaseline": false}'
)


class StubProvider:
    name = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = TokenUsage(input_tokens=10, output_tokens=5)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text=self.text, model=request.model, usage=self.usage)

    def stream(self, request: ProviderRequest):
        yield self.text


def _fixture_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / "src").mkdir(parents=True)
    (repo / "prompts").mkdir()
    (repo / "src" / "app.py").write_text("import openai\n")
    (repo / "prompts" / "p.md").write_text(
        "---\nid: p\nversion: 0.1.0\nschema: s\n---\nCuerpo.\n"
    )
    (repo / "core-consumer.yml").write_text(
        yaml.safe_dump(
            {
                "week": 7,
                "name": name,
                "core_version": "0.7.0",
                "reused_modules": ["llm-client", "ci-pack"],
                "new_surface": ["x"],
                "estimated_new_surface": 20,
            }
        )
    )
    return repo


def test_health_reusa_web_api_base() -> None:
    app = create_app(provider=StubProvider("texto"))
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "ci-scribe"
    assert body["provider"] == "stub"
    assert body["week"] == 7


def test_fleet_audit_aplica_lints_de_ci_pack() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        fixture = _fixture_repo(Path(d), "malo")
        app = create_app(provider=StubProvider("texto"))
        resp = TestClient(app).post("/fleet/audit", json={"repos": [str(fixture)]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["repos"][0]["ok"] is False
        assert any("openai" in v for v in body["repos"][0]["violations"])
        assert body["evidence"]["repo_count"] == 1


def test_fleet_triage_devuelve_diagnostico_validado() -> None:
    app = create_app(provider=StubProvider(TRIAGE_OK))
    report = json.dumps({"total": 3, "passed": 2, "threshold_ok": False})
    resp = TestClient(app).post("/fleet/triage", json={"repo": "commit-cli", "report": report})
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation_ok"] is True
    assert body["parsed"]["verdict"] == "fail"
    assert body["parsed"]["requires_rebaseline"] is False


def test_llm_endpoint_de_la_base_disponible() -> None:
    app = create_app(
        provider=StubProvider('{"summary": "x", "probable_cause": [], "severity": "none", "verdict": "pass", "actions": []}')
    )
    resp = TestClient(app).post(
        "/llm",
        json={
            "prompt_id": "eval-triage-generator",
            "variables": {"repo": "sec-check", "report": "{}"},
            "response_schema": "eval-triage-v1",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["validation_ok"] is True