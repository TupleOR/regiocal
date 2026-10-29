"""Run the whole pipeline in one command:

    extract (LLM) -> locate (LLM agent + register lookups) -> check -> de-dupe
    -> decide (typed decisions: category + tags) -> render -> validate

Usage:
    python run.py                     # dataset 'mock', LLM from .env (replay if none is set)
    python run.py --dataset group
    python run.py --provider replay   # force offline replay of saved LLM responses

Exit code 0 when validation passes, 1 when it fails or a stage cannot run.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import check  # noqa: E402
import decide  # noqa: E402
import dedupe  # noqa: E402
import extract  # noqa: E402
import locate  # noqa: E402
import render  # noqa: E402
import validate  # noqa: E402
from common import Dataset, load_env  # noqa: E402
from llm import LLMError, PROVIDERS, client_from_env  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Run the community calendar pipeline end to end.")
    parser.add_argument("--dataset", default="mock", help="folder under datasets/ (default: mock)")
    parser.add_argument("--provider", choices=list(PROVIDERS), help="override LLM_PROVIDER from .env")
    args = parser.parse_args()

    started = time.time()
    dataset = Dataset(args.dataset)
    env = load_env()
    if args.provider:
        env = {"LLM_PROVIDER": args.provider} if args.provider == "replay" else {**env, "LLM_PROVIDER": args.provider}
    try:
        client = client_from_env(env, cache_dir=dataset.data_dir / "llm_responses")
    except LLMError as e:
        print(f"Cannot start: {e}")
        return 1

    print(f"Dataset '{dataset.name}': {dataset.meta.get('description', '')}")
    print(f"Region: {dataset.profile['name']} · reference date {dataset.reference_date} · LLM: {client.describe()}\n")
    extracted = extract.run(dataset, client)
    failed = [s for s in extracted["submissions"] if s["error"]]
    if failed and len(failed) == len(extracted["submissions"]):
        print(f"\nExtraction failed for every submission (first error: {failed[0]['error']}). Stopping.")
        return 1
    locate.run(dataset, client)
    check.run(dataset)
    dedupe.run(dataset)
    decide.run(dataset, client)
    render.run(dataset)
    passed = validate.run(dataset)
    print(f"\nFinished in {time.time() - started:.0f}s. Calendar: output/{dataset.name}/calendar.md, "
          f"output/{dataset.name}/calendar.ics")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
