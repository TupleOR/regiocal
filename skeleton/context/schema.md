# Canonical event schema (version 3)

Every stage after `extract` works on events in this shape. It is the contract between the input side (submissions) and the output side (Markdown, `.ics`, website). Output adapters read only these fields.

| Field | Type | Required for `published` | Meaning |
|---|---|---|---|
| `schema_version` | `"3"` | yes | Version of this schema |
| `id` | string | yes | Stable id derived from the submission file and the event's position in it, so a re-run replaces rather than duplicates |
| `title` | string | yes | Title, in the language of the submission |
| `start` | ISO 8601 with offset, e.g. `2026-11-03T18:00:00+01:00` | yes | Start, Oslo local time |
| `end` | same, or null | no | End |
| `recurrence` | `"weekly"` or null | no | A regular activity; `start` is the first occurrence inside the calendar window |
| `venue` | string | yes | Venue |
| `place` | string or null | no | Village or place |
| `municipality` | string | yes | Must be one of the deployment profile's municipalities |
| `location` | object | yes, with `status: confirmed` | How the municipality was determined: `status` (confirmed / suggestion / unknown / private), `source` (region_list / address_register / place_register / business_register / agent_guess), `match` (the matching name), `lookup` (id of the saved register lookup), `reason`, `searches` |
| `category` | string | yes | One of the profile's categories (set by the decide stage) |
| `tags` | list of strings | yes (may be empty) | Profile tags that apply, e.g. `free`, `family` |
| `decisions` | object or null | no | The decide stage's answers: `backend`, `category`, and per tag `value` and `quote` |
| `organizer` | string | yes | An organisation or group, never a private person |
| `contact_url` | `http(s)` URL or null | no | The organiser's own public web page |
| `description` | string or null | no | Short, bokmål, no personal data |
| `status` | `published` / `needs_review` / `rejected` / `duplicate` | yes | Outcome of the check and de-dupe stages |
| `issues` | list of strings | yes | Why an event is not published, e.g. `missing_start_time`, `outside_region`, `unknown_location`, `unverified_location`, `in_past`, `internal`, `clash` |
| `duplicate_of` | event id or null | no | Set when `status` is `duplicate` |
| `merged_from` | list of submission files | yes | Other submissions merged into this event |
| `source_ref` | string | yes | The submission file the event came from, e.g. `inbox/001_sjakk.txt` |
| `received` | `YYYY-MM-DD` | yes | When the submission was received |
| `evidence` | object | yes | Exact quotes from the submission backing the title, date, venue and location |
