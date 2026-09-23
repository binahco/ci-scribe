from __future__ import annotations

from pathlib import Path

import pytest
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider
from test_kit import EvalCase, EvalDataset, run

from ci_scribe.main import build_client, judge_for, load_prompt

ROOT = Path(__file__).resolve().parents[1]
CASSETTES = ROOT / "cassettes"
SCHEMA_ID = "eval-triage-v1"


@pytest.mark.skipif(
    not CASSETTES.is_dir() or not list(CASSETTES.glob("complete-*.jsonl")),
    reason="tape real no grabada (scripts/record_tape_opencode.py)",
)
def test_triage_replay_dataset_en_verde() -> None:
    dataset = EvalDataset.from_jsonl(ROOT / "evals/eval-triage-generator.jsonl")
    prompt_id, prompt_version, _, _ = load_prompt(ROOT / "prompts/eval-triage-generator.md")
    assert dataset.prompt_id == prompt_id and dataset.prompt_version == prompt_version

    provider = ReplayProvider(CASSETTES, record=False)
    client = build_client(provider)
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    assert report.threshold_ok, report.model_dump()
    assert report.total == 3