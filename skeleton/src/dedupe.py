"""Stage 4 - de-dupe and clashes (no LLM).

Same start + same municipality + same venue + overlapping titles  -> duplicate (one copy kept)
Same start + same municipality + same venue, different titles      -> clash (both go to review)
Weekly activities are compared on every occurrence inside the calendar window.

Input:  data/<name>/checked.json
Output: data/<name>/events.json   (the canonical event store that every output reads)

Run on its own:  python src/dedupe.py --dataset mock
"""

import argparse

from common import Dataset, occurrences, overlap, read_json, words, write_json

SAME = 0.5  # word overlap needed to call two venues or two titles "the same"


def place_names(dataset):
    profile = dataset.profile
    return set(profile["municipalities"]) | set(profile["places"])


def venue_words(event, drop):
    return words(event.get("venue"), drop=drop)


def same_venue(a, b, drop):
    va, vb = venue_words(a, drop), venue_words(b, drop)
    if not va or not vb:  # venue was only a place name, e.g. "Otta"
        return (a.get("venue") or "").lower() == (b.get("venue") or "").lower()
    return overlap(va, vb) >= SAME


def same_event(a, b, drop):
    return overlap(words(a.get("title"), drop), words(b.get("title"), drop)) >= SAME


def same_slot(a, b, dataset, drop):
    """Same municipality, same venue, and at least one shared start time (weekly events repeat)."""
    if a["municipality"] != b["municipality"] or not same_venue(a, b, drop):
        return False
    times_a = set(occurrences(a, dataset.window_end))
    return any(t in times_a for t in occurrences(b, dataset.window_end))


def run(dataset, log=print):
    checked = read_json(dataset.data_dir / "checked.json")
    events = checked["events"]
    drop = place_names(dataset)
    # Published copies first, then earliest received: that is the copy kept when two are the same event.
    candidates = sorted((e for e in events if e["status"] in ("published", "needs_review") and e["start"]),
                        key=lambda e: (e["status"] != "published", e["received"] or "", e["source_ref"]))
    duplicates = clashes = 0
    for i, a in enumerate(candidates):
        if a["status"] == "duplicate":
            continue
        for b in candidates[i + 1:]:
            if b["status"] == "duplicate" or not same_slot(a, b, dataset, drop):
                continue
            if same_event(a, b, drop):
                b["status"], b["duplicate_of"] = "duplicate", a["id"]
                b["issues"] = b["issues"] + ["duplicate"]
                a["merged_from"].append(b["source_ref"])
                duplicates += 1
            else:
                for e in (a, b):
                    if "clash" not in e["issues"]:
                        e["issues"].append("clash")
                        e["status"] = "needs_review"
                clashes += 1
    log(f"[dedupe] {duplicates} duplicate(s) merged, {clashes} clash(es) sent to review")
    out = {"schema_version": "1", "events": events, "unprocessed": checked["unprocessed"]}
    write_json(dataset.data_dir / "events.json", out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    run(Dataset(parser.parse_args().dataset))


if __name__ == "__main__":
    main()
