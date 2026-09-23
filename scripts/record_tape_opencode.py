from pathlib import Path

from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider
from llm_client.providers.opencode_cli import OpenCodeCLI
from schema_validate import SchemaRegistry
from test_kit import EvalCase, EvalDataset, run

from ci_scribe.main import build_client, judge_for, load_prompt

MODEL = "opencode/big-pickle"
ROOT = Path(__file__).resolve().parents[1]
CASSETTES = ROOT / "cassettes"
SCHEMA_ID = "eval-triage-v1"


def main() -> None:
    dataset = EvalDataset.from_jsonl(ROOT / "evals" / "eval-triage-generator.jsonl")
    prompt_id, prompt_version, _, _ = load_prompt(ROOT / "prompts" / "eval-triage-generator.md")
    assert dataset.prompt_id == prompt_id and dataset.prompt_version == prompt_version

    recorder = ReplayProvider(CASSETTES, record=True, inner=OpenCodeCLI(MODEL))
    client = build_client(recorder)
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"grabadas {len(dataset.cases)} respuestas; pass={report.passed}/{report.total}")
    for case_report in report.cases:
        print(case_report.model_dump())


if __name__ == "__main__":
    main()