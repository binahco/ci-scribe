from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

from ci_pack import EvidenceReport, collect, render as render_evidence, run_lints
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider, Span
from llm_client.providers.opencode_cli import OpenCodeCLI
from schema_validate import SchemaRegistry
from test_kit import DatasetError, EvalCase, EvalDataset, run
from web_api_base import create_app as make_web_app
from web_api_base import llm_complete

from .models import EvalTriage

DEFAULT_MODEL = "opencode/big-pickle"
SCHEMA_ID = "eval-triage-v1"
ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_PROMPT = PROMPT_DIR / "eval-triage-generator.md"
DEFAULT_DATASET = ROOT / "evals" / "eval-triage-generator.jsonl"


def load_prompt(path: Path) -> tuple[str, str, str, Path]:
    text = path.read_text()
    if not text.startswith("---"):
        raise SystemExit(f"{path}: falta frontmatter")
    _, frontmatter, body = text.split("---", 2)
    data = yaml.safe_load(frontmatter)
    eval_path = ROOT / data["eval"] if not Path(data["eval"]).is_absolute() else Path(data["eval"])
    return data["id"], data["version"], body.strip(), eval_path


def render_prompt(prompt_id: str, prompt_version: str, variables: dict) -> list[dict]:
    _, _, body, _ = load_prompt(DEFAULT_PROMPT)
    system_part = body.split("## Sistema\n", 1)[1].split("## Usuario\n", 1)[0].strip()
    user_part = body.split("## Usuario\n", 1)[1].strip().format(
        repo=variables["repo"],
        report=variables["report"],
    )
    return [
        {"role": "system", "content": system_part},
        {"role": "user", "content": user_part},
    ]


def build_emitter(span_file: Path | None):
    def emit(span: Span, _result) -> None:
        if span_file is not None:
            with span_file.open("a") as handle:
                handle.write(span.as_jsonl() + "\n")
        else:
            sys.stderr.write(span.as_jsonl() + "\n")

    return emit


def build_client(provider, *, span_file: Path | None = None) -> LlmClient:
    registry = SchemaRegistry()
    registry.register(SCHEMA_ID, EvalTriage)
    return LlmClient(
        provider,
        consumer_repo="ci-scribe",
        model_aliases={"fast": DEFAULT_MODEL},
        validator=registry.make_validator(SCHEMA_ID),
        renderer=render_prompt,
        emitter=build_emitter(span_file),
    )


def judge_for(client: LlmClient) -> Callable[[EvalCase], CompletionResult]:
    def judge(case: EvalCase) -> CompletionResult:
        return client.complete(
            CompletionRequest(
                prompt_id=case.prompt_id,
                prompt_version=case.prompt_version,
                variables=case.input,
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["ci-scribe", "week-7"],
            )
        )

    return judge


class FleetAuditRequest(BaseModel):
    repos: list[str]
    spans_dir: str | None = None


class FleetTriageRequest(BaseModel):
    repo: str
    report: str


class AuditResponse(BaseModel):
    repos: list[dict] = []
    evidence: EvidenceReport | None = None

    model_config = {"arbitrary_types_allowed": True}


def create_app(*, provider, title: str = "ci-scribe", version: str = "0.1.0") -> FastAPI:
    """App FastAPI: web-api-base + fleet-ops (lints ci-pack y triaje LLM)."""
    client = build_client(provider)
    app = make_web_app(
        title,
        version,
        client=client,
        health_extra={"week": 7},
    )

    @app.post("/fleet/audit", tags=["fleet"])
    async def fleet_audit(req: FleetAuditRequest):
        rows: list[dict] = []
        for repo_str in req.repos:
            repo = Path(repo_str)
            report = run_lints([repo])
            rows.append(
                {
                    "repo": repo.name,
                    "ok": report.ok,
                    "violations": report.violations,
                }
            )
        spans_dir = Path(req.spans_dir) if req.spans_dir else None
        evidence = None
        if req.repos:
            evidence = collect([Path(r) for r in req.repos], spans_dir=spans_dir).to_plain()
        return {"repos": rows, "evidence": evidence}

    @app.post("/fleet/triage", tags=["fleet"])
    async def fleet_triage(req: FleetTriageRequest):
        from web_api_base import CompleteRequest

        return llm_complete(
            client,
            CompleteRequest(
                prompt_id="eval-triage-generator",
                prompt_version="0.1.0",
                variables={"repo": req.repo, "report": req.report},
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["ci-scribe", "triage"],
            ),
        )

    return app


def _run_eval(args) -> int:
    prompt_id, prompt_version, _, eval_path = load_prompt(DEFAULT_PROMPT)
    dataset_path = Path(args.dataset) if args.dataset else eval_path

    try:
        dataset = EvalDataset.from_jsonl(dataset_path)
    except DatasetError as exc:
        sys.stderr.write(f"ci-scribe: dataset inválido: {exc}\n")
        return 2

    if dataset.prompt_id != prompt_id or dataset.prompt_version != prompt_version:
        sys.stderr.write(
            f"ci-scribe: dataset referencia {dataset.prompt_id}@{dataset.prompt_version}, "
            f"el prompt es {prompt_id}@{prompt_version}\n"
        )
        return 2

    if args.replay:
        provider = ReplayProvider(args.replay, record=False)
    elif args.record:
        provider = ReplayProvider(args.record, record=True, inner=OpenCodeCLI(args.model))
    else:
        provider = OpenCodeCLI(args.model)

    client = build_client(provider, span_file=Path(args.span_file) if args.span_file else None)
    report = run(dataset, judge_for(client), mode=args.mode, threshold=args.threshold)
    print(report.model_dump_json(indent=2))

    if not report.threshold_ok:
        sys.stderr.write(f"ci-scribe: REGRESIÓN — {report.passed}/{report.total} casos pasan (umbral {args.threshold})\n")
        return 1
    return 0


def _run_audit(args) -> int:
    repos = [Path(r) for r in args.repos]
    if not repos:
        sys.stderr.write("ci-scribe: usa --repos ruta1 ruta2 ...\n")
        return 2
    report = run_lints(repos)
    print(report.summary())
    return 0 if report.ok else 1


def _run_evidence(args) -> int:
    repos = [Path(r) for r in args.repos]
    spans_dir = Path(args.spans) if args.spans else None
    evidence = collect(repos, spans_dir=spans_dir)
    out = ROOT / "docs" / "metrics" / "evidence.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_evidence(evidence))
    print(f"ci-scribe: evidencia de {evidence.repo_count} consumidores → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-scribe", description="Fleet ops de la flota llm-dev-core (ci-pack + triaje LLM).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("eval", help="evalúa el prompt eval-triage-generator (replay/record)")
    p_eval.add_argument("--dataset", default=None)
    p_eval.add_argument("--model", default=DEFAULT_MODEL)
    p_eval.add_argument("--replay", metavar="DIR", default=None)
    p_eval.add_argument("--record", metavar="DIR", default=None)
    p_eval.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    p_eval.add_argument("--threshold", type=float, default=1.0)
    p_eval.add_argument("--span-file", metavar="PATH", default=None)
    p_eval.set_defaults(func=_run_eval)

    p_audit = sub.add_parser("audit", help="lints de §3 (ci-pack) sobre repos consumidores")
    p_audit.add_argument("--repos", nargs="+", required=True)
    p_audit.set_defaults(func=_run_audit)

    p_ev = sub.add_parser("evidence", help="regenera la página de evidencia (§10.1)")
    p_ev.add_argument("--repos", nargs="+", required=True)
    p_ev.add_argument("--spans", default=None)
    p_ev.set_defaults(func=_run_evidence)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())