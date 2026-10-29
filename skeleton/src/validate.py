"""Stage 7 - validate (no LLM): check the output and exit non-zero if anything is wrong.

Checks:
  1 schema        every event has the required fields in the right format
  2 attribution   every event comes from a real submission, and its evidence quotes appear in it
  3 region        every published event has a confirmed location in one of the profile's municipalities,
                  backed by the submission text (region list) or a saved register lookup
  4 dates         published events are not in the past and fall inside the calendar window
  5 duplicates    no two published events are the same event
  6 privacy       no email addresses, phone numbers or listed names in the published output
  7 outputs       the output files (event data, calendar) exist, contain every published event, no garbled text
  8 answer key    every submission ended up as the answer key says (precision and completeness)

Input:  data/<name>/events.json, output/<name>/*, datasets/<name>/inbox + expected.json
Output: data/<name>/validation.json, a printed report, exit code 0 (pass) or 1 (fail)

Run on its own:  python src/validate.py --dataset mock
"""

import argparse
import datetime as dt
import json
import re
import sys

from check import EMAIL, redact
from common import Dataset, from_iso, read_json, squash, write_json
from locate import region_terms, term_in
from lookup import grounded, unambiguous_hit
from dedupe import place_names, same_event, same_slot

STATUSES = {"published", "needs_review", "rejected", "duplicate"}
REQUIRED = ["title", "start", "venue", "municipality", "category", "organizer", "source_ref", "received"]
FIELDS = ["schema_version", "id", "title", "start", "end", "recurrence", "venue", "place", "municipality", "location",
          "tags", "decisions",
          "category", "organizer", "contact_url", "description", "status", "issues", "duplicate_of",
          "merged_from", "source_ref", "received", "evidence"]
PUBLIC_TEXT = ["title", "venue", "place", "organizer", "description", "contact_url"]
MOJIBAKE = re.compile("Ã.|Â|â€|�")
# Broader than the check stage's pattern on purpose (also dots and hyphens), so the validator
# catches formats the scrubber missed. Dates and year ranges are excluded afterwards.
PHONE_WIDE = re.compile(r"(?<![\w.+-])(?:\+47[ .-]?)?\d(?:[ .-]?\d){7}(?![ .-]?\d)")
NOT_PHONE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{4})$")
ICS_TEXT = ("SUMMARY:", "LOCATION:", "DESCRIPTION:", "URL:", "X-WR-CALNAME:")
# Credits the registers' licences require in every output (written out here, not imported from render.py)
CREDITS = ["© Kartverket", "CC BY 4.0", "creativecommons.org/licenses/by/4.0",
           "Norsk lisens for offentlige data (NLOD)", "Brønnøysundregistrene", "data.norge.no/nlod"]


def ics_text(raw):
    """The human-readable property values of an .ics file (unfolded), without ids and timestamps."""
    unfolded = re.sub(r"\r?\n[ \t]", "", raw)
    return "\n".join(line.split(":", 1)[1] for line in unfolded.splitlines() if line.startswith(ICS_TEXT))


def phones(text):
    return [m.strip() for m in PHONE_WIDE.findall(text) if not NOT_PHONE.match(m.strip())]
OUTCOME_RANK = ["published", "needs_review", "duplicate", "rejected"]


def norm_quote(text):
    text = squash(text).replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"')
    return re.sub(r"\s*([-:/.,!])\s*", r"\1", text)


class Report:
    def __init__(self):
        self.results = []

    def add(self, number, name, problems, summary):
        self.results.append({"check": number, "name": name, "passed": not problems,
                             "summary": summary, "problems": problems})

    @property
    def passed(self):
        return all(r["passed"] for r in self.results)


def outcome_of(source_ref, events, unprocessed):
    mine = [e for e in events if e["source_ref"] == source_ref]
    if mine:
        return min((e["status"] for e in mine), key=OUTCOME_RANK.index), mine
    for u in unprocessed:
        if u["source_ref"] == source_ref:
            return u["status"], []
    return "missing", []


def run(dataset, log=print):
    store = read_json(dataset.data_dir / "events.json")
    events, unprocessed = store["events"], store["unprocessed"]
    published = [e for e in events if e["status"] == "published"]
    texts = {ref: text for ref, _, text in dataset.submissions()}
    profile = dataset.profile
    report = Report()

    # 1 schema
    problems = []
    ids = set()
    for e in events:
        tag = f"{e.get('source_ref')} '{e.get('title')}'"
        missing = [f for f in FIELDS if f not in e]
        if missing:
            problems.append(f"{tag}: missing fields {missing}")
        if e.get("status") not in STATUSES:
            problems.append(f"{tag}: unknown status {e.get('status')!r}")
        if e.get("id") in ids:
            problems.append(f"{tag}: duplicate id {e.get('id')}")
        ids.add(e.get("id"))
        if e.get("status") == "published":
            empty = [f for f in REQUIRED if not e.get(f)]
            if empty:
                problems.append(f"{tag}: published without {empty}")
            try:
                if dt.datetime.fromisoformat(e["start"]).tzinfo is None:
                    problems.append(f"{tag}: start has no time-zone offset")
            except (TypeError, ValueError):
                problems.append(f"{tag}: start is not an ISO date-time: {e.get('start')!r}")
            if e.get("category") not in profile["categories"]:
                problems.append(f"{tag}: unknown category {e.get('category')!r}")
            unknown_tags = [t for t in e.get("tags") or [] if t not in profile["tags"]]
            if unknown_tags:
                problems.append(f"{tag}: unknown tags {unknown_tags}")
            if e.get("contact_url") and not str(e["contact_url"]).startswith(("http://", "https://")):
                problems.append(f"{tag}: contact_url is not a web address")
    report.add(1, "schema", problems, f"{len(events)} events checked")

    # 2 attribution
    problems = []
    for e in events:
        if e["source_ref"] not in texts:
            problems.append(f"{e['source_ref']}: no such submission")
    for e in published:
        original = texts.get(e["source_ref"], "")
        # the LLM saw the text with emails/phones redacted, so quotes may come from either version
        source = norm_quote(original) + "\n" + norm_quote(redact(original))
        evidence = e.get("evidence") or {}
        for key in ("title", "date", "venue"):
            quote = evidence.get(key)
            if not quote:
                problems.append(f"{e['source_ref']} '{e['title']}': no evidence for {key}")
            elif len(re.sub(r"\W", "", norm_quote(quote))) < 3:
                problems.append(f"{e['source_ref']} '{e['title']}': {key} quote too short to prove anything: {quote!r}")
            elif norm_quote(quote) not in source:
                problems.append(f"{e['source_ref']} '{e['title']}': {key} quote not found in submission: {quote!r}")
        for t in e.get("tags") or []:
            spec = profile["tags"].get(t, {})
            if spec.get("needs_quote"):
                # a promise tag needs a whole-word quote from the original text that contains a cue word
                quote = ((e.get("decisions") or {}).get("tags", {}).get(t) or {}).get("quote")
                quote = norm_quote(quote).strip(" -:/.,!?;\"'") if isinstance(quote, str) else ""
                cues = [norm_quote(c) for c in spec.get("cues", [])]
                if not quote or not term_in(quote, norm_quote(original)) \
                        or (cues and not any(term_in(c, quote) for c in cues)):
                    problems.append(f"{e['source_ref']} '{e['title']}': tag {t!r} has no supporting quote in the submission")
    report.add(2, "attribution", problems, f"{len(published)} published events traced to their submissions")

    # 3 region: a confirmed location inside the region, and evidence that really supports it
    problems = []
    terms = region_terms(profile)
    sources = {}
    for e in published:
        tag = f"{e['source_ref']} '{e['title']}'"
        loc = e.get("location") or {}
        sources[loc.get("source")] = sources.get(loc.get("source"), 0) + 1
        if e["municipality"] not in profile["municipalities"]:
            problems.append(f"{tag}: {e['municipality']!r} is not in {profile['name']}")
        if loc.get("status") != "confirmed" or loc.get("municipality") != e["municipality"]:
            problems.append(f"{tag}: location is not confirmed ({loc.get('status')})")
        elif loc.get("source") == "region_list":
            names = [n.strip() for n in (loc.get("match") or "").split(",") if n.strip()]
            if not names or any(terms.get(n) != e["municipality"] or not term_in(n, texts.get(e["source_ref"], ""))
                                for n in names):
                problems.append(f"{tag}: {loc.get('match')!r} in the submission does not place it in {e['municipality']}")
        else:
            path = dataset.data_dir / "lookups" / f"{loc.get('lookup')}.json"
            lookup = read_json(path) if loc.get("lookup") and path.exists() else None
            if not lookup or not unambiguous_hit(lookup, e["municipality"]):
                problems.append(f"{tag}: no saved {loc.get('source')} lookup shows {e['municipality']} (and only there)")
            elif not grounded(lookup["query"], texts.get(e["source_ref"], "")):
                problems.append(f"{tag}: the search {lookup['query']!r} uses words that are not in the submission")
    report.add(3, "region", problems, "published locations confirmed by: "
               + (", ".join(f"{k} {v}" for k, v in sorted(sources.items(), key=str)) or "-"))

    # 4 dates
    problems = []
    for e in published:
        start = from_iso(e["start"]).date()
        if start < dt.date.fromisoformat(e["received"]):
            problems.append(f"{e['source_ref']} '{e['title']}': starts {start} before it was received")
        if not dataset.reference_date <= start <= dataset.window_end:
            problems.append(f"{e['source_ref']} '{e['title']}': {start} is outside the window "
                            f"{dataset.reference_date}..{dataset.window_end}")
    report.add(4, "dates", problems, f"window {dataset.reference_date} .. {dataset.window_end}")

    # 5 duplicates
    problems = []
    drop = place_names(dataset)
    for i, a in enumerate(published):
        for b in published[i + 1:]:
            if same_slot(a, b, dataset, drop) and same_event(a, b, drop):
                problems.append(f"'{a['title']}' ({a['source_ref']}) and '{b['title']}' ({b['source_ref']})")
    report.add(5, "duplicates", problems, "no two published events are the same event")

    # 6 privacy
    expected = dataset.expected()
    forbidden = [s for entry in expected.values() for s in entry.get("must_not_appear", [])]
    files = {name: dataset.output_dir / name for name in ("events.json", "calendar.md", "calendar.ics")}
    raw = {name: path.read_text(encoding="utf-8") for name, path in files.items() if path.exists()}
    try:
        data_events = json.loads(raw["events.json"])["events"] if "events.json" in raw else []
    except (ValueError, KeyError, TypeError):
        data_events = None
    outputs = {name: ics_text(text) if name.endswith(".ics") else text for name, text in raw.items()
               if name != "events.json"}
    outputs["events.json"] = "\n".join(str(d.get(f) or "") for d in data_events or [] for f in PUBLIC_TEXT)
    outputs["published events"] = "\n".join(str(e.get(f) or "") for e in published for f in PUBLIC_TEXT)
    problems = []
    for name, text in outputs.items():
        for match in EMAIL.findall(text) + phones(text):
            problems.append(f"{name}: contains {match!r}")
        for s in forbidden:
            if s.lower() in text.lower():
                problems.append(f"{name}: contains {s!r} (listed in must_not_appear)")
    report.add(6, "privacy", problems, f"scanned {', '.join(outputs)}")

    # 7 outputs
    problems = [f"{name} is missing (did the render stage run?)" for name in files if name not in raw]
    if data_events is None:
        problems.append("events.json is not valid event data")
    data_ids = {d.get("id") for d in data_events or []}
    if "events.json" in raw and data_events is not None and data_ids != {e["id"] for e in published}:
        problems.append("events.json does not contain exactly the published events")
    for e in published:
        # the calendar line itself ("- **18:00** Title – ..."), not just the title somewhere in the file
        if "calendar.md" in raw and f"** {e['title']} – " not in raw["calendar.md"]:
            problems.append(f"calendar.md lacks '{e['title']}' ({e['source_ref']})")
        if "calendar.ics" in raw and f"UID:{e['id']}@" not in raw["calendar.ics"]:
            problems.append(f"calendar.ics lacks '{e['title']}' ({e['source_ref']})")
    for name, text in outputs.items():
        for match in set(MOJIBAKE.findall(text)):
            problems.append(f"{name}: garbled text {match!r}")
    # the registers' licences (Kartverket CC BY 4.0, Brønnøysund NLOD 2.0) require a credit and a licence link
    for name, text in raw.items():
        unfolded = re.sub(r"\r?\n[ \t]", "", text)  # .ics lines are folded
        for credit in CREDITS:
            if credit not in unfolded:
                problems.append(f"{name}: source credit missing ({credit!r})")
    report.add(7, "outputs", problems, "events.json, calendar.md and calendar.ics contain every published event "
               "and the source credits; UTF-8 intact")

    # 8 answer key
    problems, warnings = [], []
    expected_published = actual_published = correct_published = 0
    for ref in texts:
        name = ref.split("/", 1)[1]
        if name not in expected:
            warnings.append(f"{name}: not in the answer key")
            continue
        want = expected[name]
        got, mine = outcome_of(ref, events, unprocessed)
        if got == "published":
            actual_published += 1
        if want["outcome"] == "published":
            expected_published += 1
        if got != want["outcome"]:
            issues = sorted({i for e in mine for i in e["issues"]})
            problems.append(f"{name}: expected {want['outcome']}, got {got}" + (f" {issues}" if issues else ""))
            continue
        accepted = want["issue"] if isinstance(want.get("issue"), list) else [want["issue"]] if want.get("issue") else []
        if accepted and not any(i in e["issues"] for e in mine for i in accepted) \
                and not any(u["source_ref"] == ref and u["issue"] in accepted for u in unprocessed):
            problems.append(f"{name}: expected issue {want['issue']!r}, got {sorted({i for e in mine for i in e['issues']})}")
        if want.get("duplicate_of"):
            kept_ids = {e["duplicate_of"] for e in mine}
            kept_refs = {e["source_ref"].split("/", 1)[1] for e in events if e["id"] in kept_ids}
            if want["duplicate_of"] not in kept_refs:
                problems.append(f"{name}: expected duplicate of {want['duplicate_of']}, got {sorted(kept_refs)}")
        ok = True
        specs = want.get("events", [])
        published_here = [e for e in mine if e["status"] == "published"]
        if want["outcome"] == "published" and len(published_here) > len(specs):
            problems.append(f"{name}: {len(published_here)} events published, the answer key expects {len(specs)}")
            ok = False
        for spec in specs:
            match = next((e for e in mine if e["status"] == "published"
                          and spec.get("title_contains", "").lower() in (e["title"] or "").lower()), None)
            if not match:
                problems.append(f"{name}: no published event with '{spec.get('title_contains')}' in the title")
                ok = False
                continue
            start = from_iso(match["start"]).strftime("%Y-%m-%dT%H:%M")
            for key, actual in (("start", start), ("municipality", match["municipality"]),
                                ("recurrence", match["recurrence"]), ("contact_url", match["contact_url"])):
                if key in spec and spec[key] != actual:
                    problems.append(f"{name}: {key} expected {spec[key]!r}, got {actual!r}")
                    ok = False
        if want["outcome"] == "published" and ok:
            correct_published += 1
    for name in expected:
        if f"inbox/{name}" not in texts:
            problems.append(f"{name}: in the answer key but not in the inbox")
    # Category and tags: reported for information only (the stand-in decision backend is not gated).
    agree_cat = agree_tags = total_cat = total_tags = 0
    for ref in texts:
        want = expected.get(ref.split("/", 1)[1], {})
        mine = [e for e in events if e["source_ref"] == ref and e["status"] in ("published", "needs_review")]
        if not mine or "category" not in want:
            continue
        total_cat += 1
        agree_cat += mine[0]["category"] == want["category"]
        for t, value in (want.get("tags") or {}).items():
            total_tags += 1
            agree_tags += (t in mine[0].get("tags", [])) == value
    decisions_note = (f"; category as in key {agree_cat}/{total_cat}, tags {agree_tags}/{total_tags} (info only)"
                      if total_cat else "")
    precision = correct_published / actual_published if actual_published else 1.0
    completeness = correct_published / expected_published if expected_published else 1.0
    report.add(8, "answer key", problems,
               f"{len(texts)} submissions; published: precision {precision:.0%}, completeness {completeness:.0%}"
               + decisions_note)
    report.results[-1]["warnings"] = warnings

    # print + save
    log(f"\nValidation report - dataset '{dataset.name}' ({dataset.profile['name']})")
    for r in report.results:
        log(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['check']} {r['name']:<12} {r['summary']}")
        for p in r["problems"]:
            log(f"          - {p}")
        for w in r.get("warnings", []):
            log(f"          (warning) {w}")
    log("RESULT: " + ("PASS - all checks passed" if report.passed else
                      "FAIL - " + ", ".join(r["name"] for r in report.results if not r["passed"])))
    write_json(dataset.data_dir / "validation.json", {"passed": report.passed, "checks": report.results,
                                                      "precision": precision, "completeness": completeness})
    return report.passed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    sys.exit(0 if run(Dataset(parser.parse_args().dataset)) else 1)


if __name__ == "__main__":
    main()
