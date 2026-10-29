"""Stage 5 - decide: typed decisions about each event (category and tags).

The questions are built from the deployment profile in the format used by typed decision models
such as Laya and Jev:
  - "choice": pick one label (here: the category)
  - "noul":   a yes/no statement (here: one per tag, e.g. "The text says taking part is free")

This is a *decision port*. In the walking skeleton the backend is a stand-in: the configured LLM
answers the same typed questions. The plan (weeks 45-46) is to add a calibrated decision
model (Laya locally, or Jev) behind the same port, returning probabilities instead.

Code checks every answer: an unknown category becomes "other", and a tag that makes a promise
("free", "new participants welcome") is only kept if its quote appears in the submission as whole
words and contains one of the tag's cue words from the profile ("gratis", "fri inngang", "velkommen", ...).

Input:  data/<name>/events.json, the profile's categories and tags, context/decide_instructions.md
Output: data/<name>/events.json (category, tags and decisions added to published / needs_review events)

Run on its own:              python src/decide.py --dataset mock
Show the typed questions:    python src/decide.py --show-questions
"""

import argparse
import json
import re
import sys

from check import redact
from common import CONTEXT_DIR, Dataset, load_env, read_json, squash, write_json
from llm import LLMError, client_from_env, parse_json_reply
from locate import term_in

BACKEND = "llm-stand-in"


def build_questions(profile):
    """The typed questions, in the choice/noul format of decision models like Laya and Jev."""
    questions = {"category": {
        "type": "choice",
        "instructions": "Which kind of activity is this?",
        "criteria": {label: spec["description"] for label, spec in profile["categories"].items()},
    }}
    for tag, spec in profile["tags"].items():
        questions[tag] = {"type": "noul", "instructions": spec["statement"]}
    return questions


def norm(text):
    return re.sub(r"\s*([-:/.,!])\s*", r"\1", squash(text))


def quote_supports(quote, text, cues):
    """True if the quote appears in the submission as whole words, is not the "[fjernet]" placeholder
    from redaction, and contains one of the tag's cue words ("gratis", "fri inngang", ...)."""
    quote = norm(quote).strip(" -:/.,!?;\"'") if isinstance(quote, str) else ""
    if not quote or "[fjernet]" in quote:
        return False
    if cues and not any(term_in(norm(cue), quote) for cue in cues):
        return False
    return term_in(quote, norm(text))


def verified(answers, profile, text):
    """Apply the code rules to the raw answers. Returns (category, tags, decisions)."""
    answers = answers if isinstance(answers, dict) else {}
    raw_category = answers.get("category").get("choice") if isinstance(answers.get("category"), dict) else None
    category = raw_category if isinstance(raw_category, str) and raw_category in profile["categories"] else "other"
    tags, decisions = [], {"backend": BACKEND, "category": category, "tags": {}}
    for tag, spec in profile["tags"].items():
        answer = answers.get(tag) if isinstance(answers.get(tag), dict) else {}
        value = answer.get("value") is True
        quote = answer.get("quote") if isinstance(answer.get("quote"), str) else None
        if value and spec.get("needs_quote") and not quote_supports(quote, text, spec.get("cues", [])):
            value = False  # a promise to residents needs support in the text
        decisions["tags"][tag] = {"value": value, "quote": quote if value else None}
        if value:
            tags.append(tag)
    return category, tags, decisions


def run(dataset, client, log=print):
    store = read_json(dataset.data_dir / "events.json")
    texts = {ref: text for ref, _, text in dataset.submissions()}
    questions = build_questions(dataset.profile)
    system = (CONTEXT_DIR / "decide_instructions.md").read_text(encoding="utf-8") \
        .replace("{{QUESTIONS}}", json.dumps(questions, ensure_ascii=False, indent=2))
    decided = 0
    for event in store["events"]:
        if event["status"] not in ("published", "needs_review"):
            continue
        text = texts.get(event["source_ref"], "")
        user = (f"<submission>\n{redact(text)}\n</submission>\n\n"
                f"The activity to answer about: {event['title']!r}")
        try:
            answers = parse_json_reply(client.complete(system, user)).get("answers")
        except (LLMError, ValueError, AttributeError) as e:
            log(f"[decide]   {event['source_ref']}: no decision ({e}); category 'other', no tags")
            answers = {}
        event["category"], event["tags"], event["decisions"] = verified(answers, dataset.profile, text)
        decided += 1
    for event in store["events"]:
        event.setdefault("tags", [])
        event.setdefault("decisions", None)
    log(f"[decide] {decided} events: category + tags (backend: {BACKEND}, {client.describe()})")
    write_json(dataset.data_dir / "events.json", store)
    return store


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    parser.add_argument("--show-questions", action="store_true", help="print the typed questions and exit")
    args = parser.parse_args()
    dataset = Dataset(args.dataset)
    if args.show_questions:
        print(json.dumps(build_questions(dataset.profile), ensure_ascii=False, indent=2))
        return
    try:
        client = client_from_env(load_env(), cache_dir=dataset.data_dir / "llm_responses")
    except LLMError as e:
        sys.exit(f"[decide] {e}")
    run(dataset, client)


if __name__ == "__main__":
    main()
