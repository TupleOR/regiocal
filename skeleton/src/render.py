"""Stage 6 - render (no LLM): output adapters that read the event store.

Each adapter reads only data/<name>/events.json and writes one format. Adding a new output
(JSON feed, info screen, …) means adding a function here; the rest of the pipeline is unchanged.

Output: output/<name>/calendar.md, output/<name>/calendar.ics

Run on its own:  python src/render.py --dataset mock
"""

import argparse
import json

from common import Dataset, MONTHS_NB, date_nb, from_iso, read_json, write_text
from common import occurrences as common_occurrences

WEEKDAYS_PLURAL_NB = ["mandager", "tirsdager", "onsdager", "torsdager", "fredager", "lørdager", "søndager"]


# ---------- sources: credit required by the registers' licences ----------
# Kartverket: CC BY 4.0 (credit "© Kartverket", link to source and licence, say if changed).
# Brønnøysundregistrene: NLOD 2.0 §5 (recommended wording below, link to licence and source, say if changed;
# §6: no misleading use, no use of their name as endorsement).

SOURCES = [
    {"name": "Kartverket", "data": "stedsnavn og adresser", "licence": "CC BY 4.0",
     "licence_url": "https://creativecommons.org/licenses/by/4.0/deed.no", "source_url": "https://www.kartverket.no",
     "credit": "Stedsnavn og adresser © Kartverket (CC BY 4.0)"},
    {"name": "Brønnøysundregistrene", "data": "Enhetsregisteret", "licence": "NLOD 2.0",
     "licence_url": "https://data.norge.no/nlod/no/2.0", "source_url": "https://data.brreg.no/enhetsregisteret",
     "credit": "Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av Brønnøysundregistrene"},
]
SOURCES_NOTE = ("Kommunen for hvert arrangement er utledet av denne tjenesten ved oppslag i registrene. "
                "Registereierne står ikke bak denne tjenesten.")


def sources_text():
    """One plain-text credit line, for formats without links (.ics)."""
    parts = [f"{s['credit']}, {s['source_url']}, lisens: {s['licence_url']}" for s in SOURCES]
    return "Kilder: " + ". ".join(parts) + ". " + SOURCES_NOTE


def sources_markdown():
    parts = [f"{s['credit']} – [{s['source_url']}]({s['source_url']}), [lisens]({s['licence_url']})" for s in SOURCES]
    return "*Kilder: " + ". ".join(parts) + ". " + SOURCES_NOTE + "*"


def published(store):
    return [e for e in store["events"] if e["status"] == "published"]


def occurrences(event, dataset):
    """Start times inside the calendar window (weekly activities repeat)."""
    return common_occurrences(event, dataset.window_end)


def where(event):
    venue, place, municipality = event.get("venue") or "", event.get("place") or "", event.get("municipality") or ""
    text = venue
    if place and place.lower() not in venue.lower():
        text = f"{venue}, {place}" if venue else place
    if municipality and municipality.lower() not in text.lower():
        text += f" ({municipality})"
    return text


def labels(event, profile):
    """Norwegian names of the event's category and tags, e.g. 'Musikk · Gratis'."""
    names = [profile["categories"].get(event.get("category"), {}).get("nb", "")]
    names += [profile["tags"][t]["nb"] for t in event.get("tags", []) if t in profile["tags"]
              and profile["tags"][t]["nb"] not in names]
    return " · ".join(n for n in names if n)


def time_range(event, start):
    text = f"kl. {start:%H:%M}"
    if event.get("end"):
        text += f"–{from_iso(event['end']):%H:%M}"
    return text


def period_nb(dataset):
    a, b = dataset.reference_date, dataset.window_end
    return f"{a.day}. {MONTHS_NB[a.month - 1]} – {b.day}. {MONTHS_NB[b.month - 1]} {b.year}"


# ---------- Markdown ----------

def render_markdown(store, dataset):
    events = published(store)
    region = dataset.profile["name"]
    lines = [f"# Aktiviteter i {region}", "",
             f"*{period_nb(dataset)} · {len(events)} arrangementer og aktiviteter, "
             f"satt sammen fra innsendte arrangementer.*", ""]

    weekly = sorted((e for e in events if e.get("recurrence") == "weekly"), key=lambda e: e["start"])
    if weekly:
        lines += ["## Faste aktiviteter – bli med!", ""]
        for e in weekly:
            start = from_iso(e["start"])
            line = (f"- **{e['title']}** – {WEEKDAYS_PLURAL_NB[start.weekday()]} {time_range(e, start)}"
                    f" · {where(e)} · {e['organizer']}")
            if e.get("contact_url"):
                line += f" · [Mer info]({e['contact_url']})"
            lines.append(line)
        lines.append("")

    by_day = {}
    for e in events:
        for start in occurrences(e, dataset):
            by_day.setdefault(start.date(), []).append((start, e))
    lines += ["## Kalender", ""]
    if not by_day:
        lines += ["Ingen arrangementer i perioden.", ""]
    for day in sorted(by_day):
        lines += [f"### {date_nb(day).capitalize()}", ""]
        for start, e in sorted(by_day[day], key=lambda pair: (pair[0], pair[1]["title"])):
            line = f"- **{start:%H:%M}** {e['title']} – {where(e)} · {e['organizer']}"
            if e.get("recurrence") == "weekly":
                line += " · *fast aktivitet*"
            if e.get("contact_url"):
                line += f" · [Mer info]({e['contact_url']})"
            if labels(e, dataset.profile):
                line += f" · _{labels(e, dataset.profile)}_"
            lines.append(line)
            if e.get("description"):
                lines.append(f"  {e['description']}")
        lines.append("")
    lines += ["---", "", sources_markdown(), ""]
    return "\n".join(lines)


# ---------- iCalendar (.ics) ----------

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Oslo
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def ics_escape(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line):
    """Lines longer than 75 octets are folded (RFC 5545)."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            out.append(current)
            current = " " + char
        else:
            current += char
    out.append(current)
    return out


def render_ics(store, dataset):
    stamp = f"{dataset.reference_date:%Y%m%d}T000000Z"  # fixed, so the file is reproducible
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aktivitetskalender//Walking skeleton//NB",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:Aktiviteter i {ics_escape(dataset.profile['name'])}",
             "X-WR-TIMEZONE:Europe/Oslo", f"X-WR-CALDESC:{ics_escape(sources_text())}"] + VTIMEZONE.splitlines()
    for e in sorted(published(store), key=lambda e: e["start"]):
        start = from_iso(e["start"])
        description = f"Arrangør: {e['organizer']}"
        if e.get("description"):
            description += "\n" + e["description"]
        if e.get("contact_url"):
            description += "\n" + e["contact_url"]
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@aktivitetskalender", f"DTSTAMP:{stamp}",
                  f"DTSTART;TZID=Europe/Oslo:{start:%Y%m%dT%H%M%S}"]
        if e.get("end"):
            lines.append(f"DTEND;TZID=Europe/Oslo:{from_iso(e['end']):%Y%m%dT%H%M%S}")
        if e.get("recurrence") == "weekly":
            # repeat only inside the checked window (UNTIL is in UTC; 22:59:59Z is end of day in Oslo in winter)
            lines.append(f"RRULE:FREQ=WEEKLY;UNTIL={dataset.window_end:%Y%m%d}T225959Z")
        lines += [f"SUMMARY:{ics_escape(e['title'])}", f"LOCATION:{ics_escape(where(e))}",
                  f"DESCRIPTION:{ics_escape(description)}",
                  "CATEGORIES:" + ",".join(ics_escape(c) for c in [e["category"]] + e.get("tags", []))]
        if e.get("contact_url"):
            lines.append(f"URL:{e['contact_url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in ics_fold(line)]
    return "\r\n".join(folded) + "\r\n"


# ---------- open data (JSON) ----------

PUBLIC_FIELDS = ["id", "title", "start", "end", "recurrence", "venue", "place", "municipality",
                 "category", "tags", "organizer", "contact_url", "description"]


def render_json(store, dataset):
    """The published events as plain, reusable data: public fields only (no evidence, no review notes).
    Any website, app, info screen or open-data portal can build on this file."""
    data = {
        "schema_version": store.get("schema_version"),
        "region": dataset.profile["name"],
        "municipalities": dataset.profile["municipalities"],
        "period": {"from": dataset.reference_date.isoformat(), "to": dataset.window_end.isoformat()},
        "categories": {k: v["nb"] for k, v in dataset.profile["categories"].items()},
        "tags": {k: v["nb"] for k, v in dataset.profile["tags"].items()},
        "sources": SOURCES,
        "sources_note": SOURCES_NOTE,
        "events": [{f: e.get(f) for f in PUBLIC_FIELDS}
                   for e in sorted(published(store), key=lambda e: e["start"])],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def run(dataset, log=print):
    store = read_json(dataset.data_dir / "events.json")
    write_text(dataset.output_dir / "events.json", render_json(store, dataset))
    write_text(dataset.output_dir / "calendar.md", render_markdown(store, dataset))
    write_text(dataset.output_dir / "calendar.ics", render_ics(store, dataset), newline="")
    log(f"[render] {len(published(store))} published events -> output/{dataset.name}/: "
        f"events.json (data), calendar.md, calendar.ics")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="mock")
    run(Dataset(parser.parse_args().dataset))


if __name__ == "__main__":
    main()
