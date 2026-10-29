# Datasets

Each dataset is a folder with:

```
<name>/
  dataset.json     name, description, deployment profile, reference date
  inbox/           one submission per file (.txt or .md)
  expected.json    the answer key: what the pipeline should do with each submission
```

| Dataset | Written by | Purpose |
|---|---|---|
| `mock/` | Claude (AI), clearly labelled | Development. Fictional groups, people, venues and web addresses. |
| `group/` | The project group | Evaluation. The pipeline is judged against this one. |

## Submission files
The first line is the date the submission was received; then a blank line; then the text exactly as the group sent it:

```
received: 2026-10-26

Sjakklubben Tenkepausen møtes hver tirsdag kl. 18.00 på Sel bibliotek …
```

The received date is needed to resolve relative dates ("neste torsdag") and to spot events that have already happened.

## dataset.json
```json
{
  "name": "group",
  "description": "…",
  "profile": "nord-gudbrandsdal",
  "reference_date": "2026-10-30"
}
```
`reference_date` is "today" for the run. The calendar window runs from this date for the number of days set in the profile (`window_days`). Fixing it makes runs reproducible.

## expected.json (answer key)
One entry per submission file:

```json
{
  "submissions": {
    "001_sjakk.txt": {
      "outcome": "published",
      "events": [{"title_contains": "sjakk", "start": "2026-11-03T18:00",
                  "municipality": "Sel", "recurrence": "weekly",
                  "contact_url": "https://example.org/otta-sjakklubb"}]
    },
    "003_kulturhus_program.txt": {"outcome": "duplicate", "duplicate_of": "002_maleklubb.txt"},
    "004_vinstra_konsert.txt":  {"outcome": "rejected", "issue": "outside_region"},
    "006_bokbad.txt":           {"outcome": "needs_review", "issue": "missing_start_time"},
    "009_songlag.txt": {
      "outcome": "published",
      "events": [{"title_contains": "julekonsert", "start": "2026-11-29T18:00", "municipality": "Lesja"}],
      "must_not_appear": ["Kari", "Prøvesen", "400 00 000"]
    }
  }
}
```

- `outcome`: `published`, `needs_review`, `rejected` or `duplicate`.
- `issue` (for `needs_review` / `rejected`): one of `missing_start_time`, `missing_venue`, `missing_required`, `unknown_location`, `outside_region`, `outside_window`, `in_past`, `internal`, `clash`, `missing_organizer`.
- `events` (for `published`): one entry per event. `start` is local time `YYYY-MM-DDTHH:MM`. For weekly activities, `start` is the first occurrence on or after `reference_date`. Optional: `recurrence`, `contact_url`.
- `title_contains`: a word that must appear in the title (case-insensitive).
- `must_not_appear`: strings (names, phone numbers, emails) that must not appear anywhere in the output.
- `category`, `tags` (optional): the kind of activity, for testing the planned decision model. Not checked by the validator; agreement with the key is reported as information. `category` is one of the profile's categories; `tags` maps each of the profile's tags to `true`/`false` (`free` and `newcomers` only if the text says so explicitly).
