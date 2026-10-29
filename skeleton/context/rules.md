# Rules applied by the pipeline

These are the rules the deterministic stages apply. The code in `src/` implements exactly these; if a rule changes, change it here and in the code together.

## Before any LLM call
Email addresses and phone numbers are replaced with `[fjernet]` in the submission text sent to the LLM (extract, locate, decide).

## Locate stage (`src/locate.py`, `src/lookup.py`)
Decides where each event is. The result is stored on the event as `location`, with a `status`:

| Situation | `status` | `source` |
|---|---|---|
| The event's location says it is at a private home ("hjemme hos", "heime hjå", "privatbolig", "hjem til meg") | `private` | none |
| The location quote names exactly one municipality or known place of the profile, and that word is in the submission | `confirmed` | `region_list` |
| Otherwise the LLM agent searches the official registers (address, place name, business; at most 5 lookups) and cites the lookup that shows the municipality. Searches with words that are not in the submission are refused. Code accepts the answer only if every hit of that lookup matching the search (for place names: settlements such as towns and villages, if any) is in that one municipality | `confirmed` | `address_register` / `place_register` / `business_register` |
| The agent answered, but the code check failed | `suggestion` | `agent_guess` |
| No answer | `unknown` | none |

Lookups keep only the query and, per hit, the name, type and municipality. The business search is limited to the region and returns only names made of the searched words plus a legal suffix (AS, ENK, ...) or a place name of the region. Nothing is remembered between runs apart from the saved lookups used for replay.

## Check stage (`src/check.py`)
Applied to every event the extract stage returned. Every matching rule is recorded in `issues`; the most severe one sets the status (`rejected` over `needs_review` over `published`).

| Rule | Result |
|---|---|
| The LLM marked the event as not public (board meeting, members only) | `rejected`, `internal` |
| No title or no date | `rejected`, `missing_required` |
| Location `confirmed`, but outside the profile's municipalities | `rejected`, `outside_region` |
| Location only a `suggestion` | `needs_review`, `unverified_location` |
| Location `private` | `needs_review`, `private_address` |
| Location `unknown` | `needs_review`, `unknown_location` |
| Start date is before the date the submission was received, or (one-off events) before the reference date | `rejected`, `in_past` |
| Start is after the end of the calendar window | `rejected`, `outside_window` |
| No start time | `needs_review`, `missing_start_time` |
| No venue | `needs_review`, `missing_venue` |
| No organiser (or the organiser field held personal data) | `needs_review`, `missing_organizer` |
| Category not in the profile's list | category set to `other` (not an error) |
| Otherwise | `published` |

Weekly activities: `start` is moved forward week by week to the first occurrence inside the calendar window.

**Personal data:** every text field is scrubbed. Email addresses and phone numbers (8 digits, optionally `+47`, separated by spaces, dots or hyphens; dates and time ranges such as `18.00-21.00` are kept) are removed. `contact_url` is kept only if it is an `http(s)` URL. An `organizer` that contains an email or phone number is cleared (→ `missing_organizer`). Names of private persons cannot be detected by a pattern: the extract prompt forbids them, and the validator checks the answer key's `must_not_appear` list.

A submission for which the LLM returned no events is recorded as `rejected` (`no_event`); one where extraction failed is `needs_review` (`extraction_failed`). Titles get a capital first letter.

## De-dupe stage (`src/dedupe.py`)
Applied to events that are `published` or `needs_review`.

- Two events are **the same event** if they start at the same time, in the same municipality, at the same venue, and their titles overlap. The one from the earliest-received submission is kept; the other gets `status: duplicate` and `duplicate_of`, and its file is added to the kept event's `merged_from`.
- Two events are a **clash** if they start at the same time at the same venue but are *different* events. Both go to `needs_review` with the issue `clash`, for the operator to resolve.

"Same venue" and "titles overlap" compare normalised words: lower-case, punctuation removed, common words (`i`, `på`, `og`, `med`, …) and place names dropped. Two word sets overlap when at least half the words of the shorter one are in the other.

## Decide stage (`src/decide.py`)
Applied to `published` and `needs_review` events. The typed questions are built from the profile: one `choice` question (category) and one yes/no statement per tag. The answers are checked by code:

| Rule | Result |
|---|---|
| The category answer is not one of the profile's labels | `other` |
| A tag answered "true" | the tag is kept |
| A tag marked `needs_quote` in the profile ("free", "newcomers") without a quote that appears in the submission | the tag is dropped |
| No usable answer at all | `other`, no tags (never blocks publishing) |

Category and tags are low-stakes, so uncertain answers never send an event to review.

## Validate stage (`src/validate.py`)
See `README.md` → *What the validator checks*.
