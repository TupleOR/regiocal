"""Stage 3 - check (no LLM): turn extracted and located fields into canonical events and decide their status.

Applies the rules in context/rules.md: location, region, dates, missing fields, internal events, personal data.

Input:  data/<name>/located.json
Output: data/<name>/checked.json

Run on its own:  python src/check.py --dataset mock
"""

import argparse
import datetime as dt
import hashlib
import re

from common import SCHEMA_VERSION, Dataset, read_json, to_iso, write_json

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Norwegian phone numbers: 8 digits, optionally +47, digits separated by a space, dot or hyphen
# ("400 00 000", "400-00-000", "40.00.00.00", "+47 40000000"). No letter may follow, so ids and
# iCalendar dates (20261103T1800) are not matched.
PHONE = re.compile(r"(?<![\w.-])(?:\+47[ .-]?)?\d(?:[ .-]?\d){7}(?![ .-]?\d)(?!\w)")
# ...but dates and time ranges with 8 digits are kept: 2026-11-03, 12.11.2026, 2026-2027, 18.00-21.00
NOT_PHONE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{4}|\d{1,2}[.:]\d{2}-\d{1,2}[.:]\d{2}")
URL = re.compile(r"^https?://\S+$")


def remove_phones(text, replacement):
    return PHONE.sub(lambda m: m.group() if NOT_PHONE.fullmatch(m.group()) else replacement, text)


def redact(text):
    """Replace email addresses and phone numbers in a whole submission before it is sent to an LLM,
    which may be an online service. (Names of persons cannot be found by a pattern.)"""
    return remove_phones(EMAIL.sub("[fjernet]", text or ""), "[fjernet]")


def scrub(text):
    """Remove email addresses and phone numbers from a text field. Anything that is not text becomes None."""
    if not isinstance(text, str):
        return None
    cleaned = remove_phones(EMAIL.sub("", text), "")
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:") or None


def has_personal_data(text):
    return isinstance(text, str) and (bool(EMAIL.search(text)) or remove_phones(text, "") != text)


def parse_date(value):
    try:
        return dt.date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_time(value):
    match = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return dt.time(hour, minute) if hour < 24 and minute < 60 else None


def make_event(fields, source_ref, index, received, dataset):
    profile = dataset.profile
    issues = []

    title = scrub(fields.get("title"))
    if title:
        title = title[0].upper() + title[1:]
    organizer = fields.get("organizer")
    if has_personal_data(organizer):
        organizer = None
    organizer = scrub(organizer)
    contact_url = fields.get("contact_url")
    contact_url = contact_url.strip() if isinstance(contact_url, str) and URL.match(contact_url.strip()) else None
    category = fields.get("category").strip().lower() if isinstance(fields.get("category"), str) else "other"
    if category not in profile["categories"]:
        category = "other"
    recurrence = "weekly" if str(fields.get("recurrence") or "").lower() == "weekly" else None
    venue, place = scrub(fields.get("venue")), scrub(fields.get("place"))
    evidence = fields.get("evidence") if isinstance(fields.get("evidence"), dict) else {}
    evidence = {k: v for k, v in evidence.items() if isinstance(v, str)}
    location = fields.get("location") or {"status": "unknown", "municipality": None, "source": "none"}
    municipality = location["municipality"] if location["status"] in ("confirmed", "suggestion") else None

    day = parse_date(fields.get("date"))
    start_time = parse_time(fields.get("start_time"))
    end_time = parse_time(fields.get("end_time"))
    start = end = None
    if day:
        start = dt.datetime.combine(day, start_time or dt.time(0, 0))
        if recurrence:
            while start.date() < dataset.reference_date:
                start += dt.timedelta(days=7)
        if start_time and end_time:
            end = dt.datetime.combine(start.date(), end_time)
            if end <= start:
                end = None

    # Rules, in the order of context/rules.md. The first one that matches sets the status.
    status = "published"

    def flag(issue, new_status):
        nonlocal status
        issues.append(issue)
        rank = {"published": 0, "needs_review": 1, "rejected": 2}
        if rank[new_status] > rank[status]:
            status = new_status

    if fields.get("is_public") is False:
        flag("internal", "rejected")
    if not title or not day:
        flag("missing_required", "rejected")
    if location["status"] == "confirmed" and municipality not in profile["municipalities"]:
        flag("outside_region", "rejected")
    elif location["status"] == "suggestion":
        flag("unverified_location", "needs_review")
    elif location["status"] == "private":
        flag("private_address", "needs_review")
    elif location["status"] != "confirmed":
        flag("unknown_location", "needs_review")
    if day and received and day < received:
        flag("in_past", "rejected")
    elif start and not recurrence and start.date() < dataset.reference_date:
        flag("in_past", "rejected")
    if start and start.date() > dataset.window_end:
        flag("outside_window", "rejected")
    if day and not start_time:
        flag("missing_start_time", "needs_review")
    if not venue:
        flag("missing_venue", "needs_review")
    if not organizer:
        flag("missing_organizer", "needs_review")

    return {
        "schema_version": SCHEMA_VERSION,
        "id": hashlib.sha1(f"{source_ref}#{index}".encode("utf-8")).hexdigest()[:12],
        "title": title,
        "start": to_iso(start) if start else None,
        "end": to_iso(end) if end else None,
        "recurrence": recurrence,
        "venue": venue,
        "place": place,
        "municipality": municipality,
        "location": location,
        "category": category,
        "organizer": organizer,
        "contact_url": contact_url,
        "description": scrub(fields.get("description")),
        "status": status,
        "issues": issues,
        "duplicate_of": None,
        "merged_from": [],
        "source_ref": source_ref,
        "received": received.isoformat() if received else None,
        "evidence": evidence,
    }


def run(dataset, log=print):
    extracted = read_json(dataset.data_dir / "located.json")
    events, unprocessed = [], []
    for sub in extracted["submissions"]:
        received = dt.date.fromisoformat(sub["received"]) if sub.get("received") else None
        if sub.get("error"):
            unprocessed.append({"source_ref": sub["source_ref"], "status": "needs_review",
                                "issue": "extraction_failed", "detail": sub["error"]})
            continue
        if not sub["events"]:
            unprocessed.append({"source_ref": sub["source_ref"], "status": "rejected",
                                "issue": "no_event", "detail": sub.get("notes")})
            continue
        for index, fields in enumerate(sub["events"]):
            events.append(make_event(fields, sub["source_ref"], index, received, dataset))
    counts = {}
    for e in events:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    log(f"[check] {len(events)} events: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + (f"; {len(unprocessed)} submissions without events" if unprocessed else ""))
    out = {"events": events, "unprocessed": unprocessed}
    write_json(dataset.data_dir / "checked.json", out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    run(Dataset(parser.parse_args().dataset))


if __name__ == "__main__":
    main()
