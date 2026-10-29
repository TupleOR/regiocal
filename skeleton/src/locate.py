"""Stage 2 - locate (LLM agent with tools): decide where each event takes place.

For every extracted event:
  1. Private address ("hjemme hos …", "heime hjå …") in the event's location -> status "private"
     (personal data; goes to review)
  2. Fast path, no LLM: the location quote names exactly one municipality or known place of the
     region, and that word is in the submission    -> "confirmed", source "region_list"
  3. Agent path: the LLM searches the address, place-name and business registers (lookup.py),
     a few searches at most, and answers with a municipality plus the lookup that shows it.
     Searches must use words from the submission only (others are refused). Code then checks that
     every matching hit of the cited lookup is in that one municipality -> "confirmed" (source = the register)
     otherwise                                    -> "suggestion" (goes to review) or "unknown"

Input:  data/<name>/extracted.json, context/locate_instructions.md
Output: data/<name>/located.json  (extracted.json with a "location" object on every event)

Run on its own:  python src/locate.py --dataset mock
"""

import argparse
import json
import re
import sys

from check import redact
from common import CONTEXT_DIR, Dataset, load_env, read_json, words, write_json
from llm import LLMError, client_from_env, parse_json_reply
from lookup import Lookups, find_hit, grounded, unambiguous_hit

MAX_LOOKUPS = 5
# bokmål and nynorsk: "hjemme hos", "heime hjå", "heima hjå", "privatbolig", "hjem til meg"
PRIVATE = re.compile(r"\b(hjemme|heime|heima)\s+(hos|hjå)\b|\bprivat(adresse|bolig|hjem|heim)?\b"
                     r"|\b(hjem|heim)\s+til\s+(meg|oss)\b", re.IGNORECASE)


def term_in(term, text):
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text or "") is not None


def region_terms(profile):
    terms = {m: m for m in profile["municipalities"]}
    terms.update(profile["places"])
    return terms  # word -> municipality


def location_phrase(fields):
    """The event's own location text: its location quote, venue and place."""
    evidence = fields.get("evidence") if isinstance(fields.get("evidence"), dict) else {}
    return " ".join(x for x in (evidence.get("location"), evidence.get("venue"),
                                fields.get("venue"), fields.get("place")) if isinstance(x, str))


def fast_path(fields, text, profile):
    """Location settled from the region's own lists, if the location quote names exactly one municipality."""
    phrase = location_phrase(fields)
    found = {t: m for t, m in region_terms(profile).items() if term_in(t, phrase) and term_in(t, text)}
    if len(set(found.values())) == 1:
        municipality = next(iter(found.values()))
        return {"status": "confirmed", "municipality": municipality, "source": "region_list",
                "match": ", ".join(sorted(found)), "lookup": None, "reason": "named in the submission"}
    return None


def verify(answer, calls, text):
    """Code check of the agent's answer: every matching hit of the cited lookup must be in that one
    municipality, and every search word must come from the submission. Returns (status, hit, lookup)."""
    municipality = (answer or {}).get("municipality")
    if not isinstance(municipality, str) or not municipality.strip():
        return "unknown", None, None
    try:
        number = int(answer.get("lookup"))
        if number < 1:
            raise ValueError
        lookup = calls[number - 1]
    except (TypeError, ValueError, IndexError):
        return "suggestion", None, None
    hit = unambiguous_hit(lookup, municipality)
    if hit and grounded(lookup["query"], text):
        return "confirmed", hit, lookup
    return "suggestion", find_hit(lookup, municipality), lookup


def agent(fields, text, received, dataset, client, lookups, log):
    profile = dataset.profile
    system = (CONTEXT_DIR / "locate_instructions.md").read_text(encoding="utf-8") \
        .replace("{{REGION_NAME}}", profile["name"]).replace("{{MUNICIPALITIES}}", ", ".join(profile["municipalities"]))
    extracted = {k: fields.get(k) for k in ("title", "venue", "place", "municipality")}
    extracted["location quote"] = (fields.get("evidence") if isinstance(fields.get("evidence"), dict) else {}).get("location")
    messages = [{"role": "user", "content":
                 f"<submission received=\"{received}\">\n{redact(text)}\n</submission>\n\n"
                 f"The earlier step extracted: {json.dumps(extracted, ensure_ascii=False)}\n\n"
                 f"Where does this event take place?"}]
    calls = []
    for _ in range(MAX_LOOKUPS + 2):
        reply = client.chat(system, messages)
        messages.append({"role": "assistant", "content": reply})
        try:
            data = parse_json_reply(reply)
        except (LLMError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        query = str(data.get("query") or "")
        if isinstance(data.get("answer"), dict) or "answer" in data:
            answer = data.get("answer") if isinstance(data.get("answer"), dict) else {}
            status, hit, lookup = verify(answer, calls, text)
            guess = answer.get("municipality") if isinstance(answer.get("municipality"), str) else None
            municipality = hit["municipality"] if hit else guess
            return {"status": status, "municipality": municipality,
                    "source": lookup["tool"].replace("search_", "") + "_register" if status == "confirmed" else "agent_guess",
                    "match": hit["name"] if hit else str(answer.get("match") or "")[:100] or None,
                    "lookup": lookup["key"] if lookup else None,
                    "reason": str(answer.get("reason") or "")[:300],
                    "searches": [f"{c['tool']}: {c['query']}" for c in calls]}
        if data.get("tool") and not grounded(query, text):
            missing = ", ".join(sorted(words(query) - words(text))) or "no search words"
            content = f"Lookup refused: search only for words that appear in the submission (not in it: {missing})."
        elif data.get("tool") and len(calls) < MAX_LOOKUPS:
            result = lookups.run(data["tool"], query)
            calls.append(result)
            shown = {"results": result["results"], "error": result["error"]}
            content = f"Lookup #{len(calls)} ({result['tool']}: {result['query']!r}):\n{json.dumps(shown, ensure_ascii=False)}"
        elif data.get("tool"):
            content = "You have used all your lookups. Give your final answer now."
        else:
            content = "Reply with one JSON object: a tool call or your final answer."
        messages.append({"role": "user", "content": content})
    return {"status": "unknown", "municipality": None, "source": "none", "match": None, "lookup": None,
            "reason": "the agent gave no answer", "searches": [f"{c['tool']}: {c['query']}" for c in calls]}


def run(dataset, client, log=print):
    extracted = read_json(dataset.data_dir / "extracted.json")
    texts = {ref: text for ref, _, text in dataset.submissions()}
    lookups = Lookups(dataset.profile, dataset.data_dir / "lookups", offline=client.format == "replay")
    counts = {}
    for sub in extracted["submissions"]:
        text = texts.get(sub["source_ref"], "")
        for fields in sub["events"]:
            if PRIVATE.search(location_phrase(fields)):
                location = {"status": "private", "municipality": None, "source": "none", "match": None,
                            "lookup": None, "reason": "the event seems to be at a private home"}
            else:
                location = fast_path(fields, text, dataset.profile)
                if location is None:
                    try:
                        location = agent(fields, text, sub["received"], dataset, client, lookups, log)
                    except LLMError as e:
                        location = {"status": "unknown", "municipality": None, "source": "none", "match": None,
                                    "lookup": None, "reason": f"locate failed: {e}"}
                    log(f"[locate]   {sub['source_ref']}: {location['status']} {location['municipality'] or ''}"
                        f" via {location['source']} ({'; '.join(location.get('searches', [])) or 'no lookups'})")
            fields["location"] = location
            label = location["source"] if location["status"] == "confirmed" else location["status"]
            counts[label] = counts.get(label, 0) + 1
    log("[locate] " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    write_json(dataset.data_dir / "located.json", extracted)
    return extracted


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    dataset = Dataset(parser.parse_args().dataset)
    try:
        client = client_from_env(load_env(), cache_dir=dataset.data_dir / "llm_responses")
    except LLMError as e:
        sys.exit(f"[locate] {e}")
    run(dataset, client)


if __name__ == "__main__":
    main()
