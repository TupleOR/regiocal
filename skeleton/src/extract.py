"""Stage 1 - extract (LLM): read each submission and turn it into event fields.

Input:  datasets/<name>/inbox/*.txt, context/extract_instructions.md, the deployment profile
Output: data/<name>/extracted.json

Run on its own:  python src/extract.py --dataset mock
"""

import argparse
import datetime as dt
import sys
import time

from common import CONTEXT_DIR, Dataset, WEEKDAYS_NB, load_env, write_json
from check import redact
from llm import LLMError, client_from_env, parse_json_reply


def build_system_prompt(profile, received):
    template = (CONTEXT_DIR / "extract_instructions.md").read_text(encoding="utf-8")
    places = ", ".join(f"{place} ({municipality})" for place, municipality in profile["places"].items())
    table = "\n".join(
        f"  - {d.isoformat()} {WEEKDAYS_NB[d.weekday()]}"
        for d in (received + dt.timedelta(days=i) for i in range(-7, 50)))
    replacements = {
        "{{REGION_NAME}}": profile["name"],
        "{{MUNICIPALITIES}}": ", ".join(profile["municipalities"]),
        "{{PLACES}}": places,
        "{{CATEGORIES}}": ", ".join(profile["categories"]),
        "{{RECEIVED}}": f"{received.isoformat()} ({WEEKDAYS_NB[received.weekday()]})",
        "{{DATE_TABLE}}": table,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def build_user_prompt(text):
    # Email addresses and phone numbers are removed before anything is sent to the LLM.
    return f"Here is the submission:\n\n<submission>\n{redact(text)}\n</submission>"


def run(dataset, client, log=print):
    results = []
    submissions = dataset.submissions()
    log(f"[extract] {len(submissions)} submissions, LLM: {client.describe()}")
    for source_ref, received, text in submissions:
        entry = {"source_ref": source_ref, "received": received.isoformat() if received else None,
                 "events": [], "notes": None, "error": None}
        if received is None:
            entry["error"] = "missing 'received:' header line"
        else:
            started = time.time()
            try:
                reply = client.complete(build_system_prompt(dataset.profile, received), build_user_prompt(text))
                parsed = parse_json_reply(reply)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("events") or [], list):
                    raise LLMError("The model's JSON does not have an 'events' list.")
                entry["events"] = [e for e in parsed.get("events") or [] if isinstance(e, dict)]
                entry["notes"] = parsed.get("notes")
            except (LLMError, ValueError) as e:  # one bad submission must not stop the run
                entry["error"] = str(e)
            log(f"[extract]   {source_ref}: {len(entry['events'])} event(s)"
                f"{' ERROR: ' + entry['error'] if entry['error'] else ''} ({time.time() - started:.1f}s)")
        results.append(entry)
    out = {"llm": client.describe(), "submissions": results}
    write_json(dataset.data_dir / "extracted.json", out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    args = parser.parse_args()
    dataset = Dataset(args.dataset)
    try:
        client = client_from_env(load_env(), cache_dir=dataset.data_dir / "llm_responses")
    except LLMError as e:
        sys.exit(f"[extract] {e}")
    out = run(dataset, client)
    if any(s["error"] for s in out["submissions"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
