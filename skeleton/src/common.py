"""Shared helpers: paths, JSON files, configuration, dates and text normalisation."""

import datetime as dt
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the skeleton/ folder
CONTEXT_DIR = ROOT / "context"
PROFILES_DIR = ROOT / "profiles"
DATASETS_DIR = ROOT / "datasets"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
ENV_FILE = ROOT / ".env"

SCHEMA_VERSION = "3"

# Print Norwegian characters correctly on every console (Windows defaults to a legacy code page).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


# ---------- files ----------

def read_json(path):
    with open(path, encoding="utf-8-sig") as f:  # "-sig": also accepts files saved with a BOM (Windows)
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text, newline="\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


# ---------- configuration ----------

def load_env(path=ENV_FILE):
    """Read KEY=VALUE lines from .env; real environment variables win."""
    values = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def save_env(values, path=ENV_FILE):
    lines = ["# Written by the website's settings page. Never commit this file."]
    for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        lines.append(f"{key}={values.get(key, '')}")
    write_text(path, "\n".join(lines) + "\n")


def load_profile(profile_id):
    return read_json(PROFILES_DIR / f"{profile_id}.json")


class Dataset:
    """A dataset folder: dataset.json, inbox/ and expected.json."""

    def __init__(self, name):
        self.name = name
        self.dir = DATASETS_DIR / name
        if not self.dir.exists():
            raise SystemExit(f"Dataset not found: {self.dir}")
        self.meta = read_json(self.dir / "dataset.json")
        self.profile = load_profile(self.meta["profile"])
        self.inbox = self.dir / "inbox"
        self.expected_path = self.dir / "expected.json"
        self.data_dir = DATA_DIR / name
        self.output_dir = OUTPUT_DIR / name
        self.reference_date = dt.date.fromisoformat(self.meta["reference_date"])
        self.window_end = self.reference_date + dt.timedelta(days=self.profile.get("window_days", 31))

    def submissions(self):
        """Return [(source_ref, received_date, text)] for every file in the inbox."""
        items = []
        for path in sorted(self.inbox.glob("*")):
            if path.suffix.lower() not in (".txt", ".md") or not path.is_file():
                continue
            received, text = parse_submission(path.read_text(encoding="utf-8-sig"))
            items.append((f"inbox/{path.name}", received, text))
        return items

    def expected(self):
        if self.expected_path.exists():
            return read_json(self.expected_path).get("submissions", {})
        return {}


def parse_submission(raw):
    """Split a submission file into (received_date, text). The first line is 'received: YYYY-MM-DD'."""
    lines = raw.lstrip("﻿").splitlines()
    received = None
    if lines and lines[0].lower().startswith("received:"):
        try:
            received = dt.date.fromisoformat(lines[0].split(":", 1)[1].strip())
        except ValueError:
            received = None  # malformed header: the extract stage records the submission as failed
        lines = lines[1:]
    return received, "\n".join(lines).strip()


# ---------- dates (Europe/Oslo without needing a time-zone database) ----------

def _last_sunday(year, month):
    day = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def oslo_offset(local):
    """UTC offset for a local Oslo date-time: +02:00 in summer time, otherwise +01:00.
    Summer time runs from the last Sunday of March to the last Sunday of October (EU rule)."""
    start = dt.datetime.combine(_last_sunday(local.year, 3), dt.time(2))
    end = dt.datetime.combine(_last_sunday(local.year, 10), dt.time(3))
    return dt.timedelta(hours=2) if start <= local < end else dt.timedelta(hours=1)


def to_iso(local):
    """Local naive datetime -> ISO 8601 string with the Oslo offset."""
    return local.replace(tzinfo=dt.timezone(oslo_offset(local))).isoformat()


def from_iso(text):
    """ISO string with offset -> naive local datetime."""
    return dt.datetime.fromisoformat(text).replace(tzinfo=None)


def occurrences(event, window_end):
    """Local start times of an event up to window_end: one for one-off events, every week for weekly ones."""
    start = from_iso(event["start"])
    if event.get("recurrence") != "weekly":
        return [start]
    times = []
    while start.date() <= window_end:
        times.append(start)
        start += dt.timedelta(days=7)
    return times


WEEKDAYS_NB = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS_NB = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"]


def date_nb(day):
    """2026-11-03 -> 'tirsdag 3. november'."""
    return f"{WEEKDAYS_NB[day.weekday()]} {day.day}. {MONTHS_NB[day.month - 1]}"


# ---------- text ----------

STOPWORDS = {"i", "på", "pa", "og", "med", "til", "for", "av", "ved", "the", "en", "et", "ei",
             "om", "fra", "frå", "kl", "der", "som", "det", "den", "de"}


def words(text, drop=()):
    """Normalised set of meaningful words: lower-case, no punctuation, no stopwords."""
    text = unicodedata.normalize("NFC", (text or "").lower())
    tokens = re.findall(r"[\wæøå]+", text)
    drop = {d.lower() for d in drop}
    return {t for t in tokens if t not in STOPWORDS and t not in drop and not t.isdigit()}


def _word_match(w, others):
    # Exact, or one word contains the other for longer words ("juleutstilling" ~ "utstilling").
    return w in others or (len(w) >= 5 and any(len(o) >= 5 and (w in o or o in w) for o in others))


def overlap(a, b):
    """Share of the smaller word set that has a match in the other (0..1)."""
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(1 for w in small if _word_match(w, large)) / len(small)


def norm_name(text):
    """'Sel kommune' / ' sel ' / 'Namsos - Nåavmesjenjaelmie' -> 'sel' / 'namsos', for matching names.
    (Some municipalities have an official Sami or Kven name after ' - '.)"""
    text = unicodedata.normalize("NFC", (text or "")).split(" - ")[0].strip().casefold()
    return re.sub(r"\s+kommune$", "", text).strip()


def squash(text):
    """Lower-case and collapse whitespace, for 'does this quote appear in the text' checks."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().lower()
