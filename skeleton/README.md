# Community activity calendar: walking skeleton

An agentic workflow that turns activity submissions from local groups in **Nord-Gudbrandsdal** (Dovre, Lesja, Lom, Sel, Skjåk, Vågå) into one checked set of **structured event data** (title, time, place, municipality, organiser, category, tags). The data is the product: a calendar is one use of it, a website, a phone calendar or an open-data portal are others. Groups send their events in whatever form they have. The workflow extracts the details with an LLM, lets an LLM agent work out *where* each event is by searching Norway's official registers, checks everything in code, removes duplicates, sorts each event into a category with tags ("music", "free", "for children"), publishes the data (`events.json`) plus a Markdown calendar, an `.ics` file and a local website, and validates the result.

```
datasets/<name>/inbox/*.txt
  -> 1 extract   (LLM)          event fields + exact quotes from the submission as evidence
  -> 2 locate    (LLM + tools)  where is it? searches the address, place-name and business registers
                                (only words from the submission); code accepts the answer only if
                                every matching hit is in that one municipality, otherwise -> review
  -> 3 check     (code)         region, dates, missing fields, internal events, strips personal data
  -> 4 de-dupe   (code)         same event sent twice -> merged; two events in one hall at one time -> review
  -> 5 decide    (decision port) typed questions: category + tags (music, free, for children, ...);
                                code checks every answer
  -> 6 render    (code)         output/<name>/events.json (the data) + calendar.md + calendar.ics
  -> 7 validate  (code)         8 checks + answer key; exit code 1 on failure
```

## Requirements
- **Python 3.9 or newer.** Standard library only: nothing to install.
- Optional: an API key for an LLM provider, **or** a local model (Ollama / LM Studio). Not needed to reproduce the committed run.

## Reproduce the run (no API key needed)
```bash
cd skeleton
python run.py          # Windows: use `py run.py` if `python` is not found
```
With no LLM configured, the pipeline **replays** the LLM responses and register lookups saved from the real run (`data/mock/llm_responses/`, `data/mock/lookups/`), so the result is exactly reproducible offline. Expected ending:

```
Validation report - dataset 'mock' (Nord-Gudbrandsdal)
  PASS  1 schema ...
  ...
  PASS  8 answer key   17 submissions; published: precision 100%, completeness 100%
RESULT: PASS - all checks passed
```
Exit code 0 means validation passed; 1 means a check failed. The failing check and the reason are printed.

`./run.sh` does the same for bash users.

**The group's evaluation set** (19 submissions written by the project group, not by AI) replays the same way:
```bash
python run.py --dataset group
```
It ends with `RESULT: FAIL - answer key` (exit code 1), and that is the honest result: precision 100 %, completeness 60 %. Nothing is published wrongly, but two of five events that should be published go to review instead. "i parken på Otta" is not taken as a venue, and "fiskekveld" / "fiskarkveld" (bokmål / nynorsk) are seen as a clash instead of a duplicate. Both are on the plan towards the demo.

## For reviewers: checking it against the brief (about 10 minutes, offline)
Clone the repository, `cd skeleton`, and use `python` (or `py` on Windows). Nothing to install, no key needed.

| Criterion | How to check it |
|---|---|
| **One path runs end to end** | `python run.py` reads `datasets/mock/inbox/*.txt`, runs all seven stages, writes `output/mock/` and ends with `RESULT: PASS`. |
| **1. Checkable output** | The last stage is a validator with 8 checks. `python run.py; echo $?` (PowerShell: `$LASTEXITCODE`) gives 0. To see a failure, delete one event line from `output/mock/calendar.md`, then run `python src/validate.py --dataset mock`: it fails check 7 with exit code 1. `python run.py` restores the file. |
| **2. More than one stage** | Each stage runs on its own and writes a file in `data/mock/`: `python src/extract.py`, `src/locate.py`, `src/check.py`, `src/dedupe.py`, `src/decide.py`, `src/render.py`, `src/validate.py` (all take `--dataset mock`). |
| **3. Uses tools** | Files throughout (`data/mock/`); the locate agent calls three public register APIs (saved in `data/mock/lookups/`, one JSON file per lookup); the model is called over HTTP (saved in `data/mock/llm_responses/`); the validator's exit code is for shells and schedulers. |
| **4. Runs automatically** | One command runs every stage without a person in between. `python web.py` → http://127.0.0.1:8000/admin has the same Run button. |
| **README and context files** | This file; `context/` holds the instructions, rules and schema the models get; `profiles/` holds the region. |
| **No personal data** | `datasets/mock/inbox/009_songlag.txt` has a contact person, phone and email; search `output/mock/` for "Prøvesen" or "400 00 000": nothing (check 6 verifies this on every run). |
| **Respect the sources** | Input is submitted by the groups themselves (no scraping); registers are official open APIs; their licence credits are in every output (see *Data sources and licences*). |
| **Local context** | `output/mock/calendar.md` is in bokmål; `009` is nynorsk input; place names follow Kartverket. |
| **Honest evaluation** | `python run.py --dataset group` runs the group's own 19 submissions and **fails** check 8 on real mistakes the system made (precision 100 %, completeness 60 %); see above. |

Live runs (your own submissions, a model of your choice) are described next.

## Run with a live LLM (and where the API key goes)
**Recommended for real use:** an online large model (Anthropic Claude, OpenAI, Gemini, …) with the administrator's own API key. Local models (Ollama, LM Studio) are the option when no text may leave the machine.

Choose a provider and add your key in **one** of three ways:

1. **Website:** `python web.py`, open http://localhost:8000/admin, choose a provider, paste the key, then *Save* and *Test connection*.
2. **`.env` file:** copy `.env.example` to `.env` and fill it in:
   ```
   LLM_PROVIDER=anthropic
   LLM_MODEL=claude-opus-5-5
   LLM_API_KEY=sk-ant-...
   ```
3. **Environment variables** with the same names (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`).

`.env` is git-ignored. Never commit a key.

| `LLM_PROVIDER` | Needs key | Notes |
|---|---|---|
| `anthropic` | yes | Anthropic Messages API; suggested model `claude-opus-5-5` |
| `openai`, `gemini`, `mistral`, `openrouter` | yes | OpenAI-compatible API; set `LLM_MODEL` |
| `ollama` | no | local; e.g. `LLM_MODEL=gemma3-27b-local`; nothing leaves your machine |
| `lmstudio` | no | local; set `LLM_MODEL` to the loaded model |
| `replay` (or empty) | no | replays saved responses, offline |

Then run `python run.py`. Every live response is saved to `data/<dataset>/llm_responses/` and every register lookup to `data/<dataset>/lookups/`, so the run can be replayed later. The locate stage needs internet access to the registers (`ws.geonorge.no`, `api.kartverket.no`, `data.brreg.no`; open, no key). Different models give different results, and the validator shows exactly where a model falls short.

## The website
```bash
python web.py                  # http://localhost:8000
```
- **`/`, the calendar page** (Norwegian): every published event, filterable by municipality, category and tag (e.g. *Gratis*, *For barn og familier*), plus regular activities, an `.ics` download for Google, Outlook or Apple Calendar, and the event data as JSON (`/events.json`) for other systems.
- **`/admin`, the operator page:**
  1. choose the LLM and enter the key;
  2. run the pipeline and see the validation report;
  3. see each submission's outcome (published / needs review / rejected / duplicate, and why);
  4. add a new submission.

The site listens on `127.0.0.1` only. The Run button calls the same `run.py`.

## Folders
```
run.py, run.sh       run the whole pipeline
web.py               local website
src/                 one module per stage (each also runs alone, e.g. python src/validate.py)
  llm.py             provider adapters: anthropic, openai-compatible, replay
  locate.py          the locate agent: fast path, tool loop, code check of its answer
  decide.py          decision port: typed questions (category + tags); stand-in backend = the LLM
  lookup.py          register lookups: Kartverket addresses + place names, Brønnøysund businesses
context/             what the agent is given and the rules the code applies
  extract_instructions.md   the extract prompt (template filled from the profile)
  locate_instructions.md    the locate agent's prompt: tools, rules, reply format
  decide_instructions.md    how the stand-in backend answers the typed questions
  schema.md          the canonical event schema
  rules.md           check, de-dupe and validate rules
profiles/            deployment profile: region, municipalities, places, categories + tags, window
datasets/mock/       17 AI-written mock submissions + answer key (development)
datasets/group/      the group's own evaluation set (19 submissions + answer key)
data/<dataset>/      extracted.json -> located.json -> checked.json -> events.json (the event store),
                     validation.json, llm_responses/, lookups/
output/<dataset>/    events.json (published event data, public fields only), calendar.md, calendar.ics
```

## What the validator checks
| # | Check | Fails when |
|---|---|---|
| 1 | schema | a field is missing or malformed, or a published event lacks title/start/venue/municipality/category/organiser |
| 2 | attribution | an event's source file doesn't exist, its evidence quotes (title, date, venue) are missing, too short or not in the submission, or a promise-making tag has no supporting quote (whole words from the submission, containing a cue word such as "gratis" or "velkommen") |
| 3 | region | a published event is outside the region, its location isn't confirmed, or the evidence doesn't hold up (the named place isn't in the text; the saved register lookup doesn't show that municipality, shows other municipalities too, or searched for words that aren't in the submission) |
| 4 | dates | a published event starts before it was received or outside the calendar window |
| 5 | duplicates | two published events are the same event (same municipality, venue and start, weekly activities compared on every occurrence, overlapping titles) |
| 6 | privacy | an email address, phone number or a name from the answer key's `must_not_appear` list is in the output |
| 7 | outputs | `events.json`, `calendar.md` or `calendar.ics` is missing, lacks a published event (or `events.json` has extra ones), lacks the source credits the registers' licences require, or has garbled Norwegian characters |
| 8 | answer key | a submission's outcome, start time, municipality, recurrence or link differs from `expected.json`, or it published more events than expected (reports precision and completeness, and, for information, how often category and tags agree with the key) |

## The decision port (category and tags)
Stage 5 asks typed questions about each event, in the format used by decision models such as Laya and Jev:
a `choice` question for the category and a yes/no (`noul`) statement per tag. See them with

```bash
python src/decide.py --show-questions
```

The categories and tags are configuration in the profile (labels, Norwegian names, descriptions). In this skeleton the configured LLM answers the questions as a **stand-in**. Code keeps a promise-making tag ("Gratis", "Nye deltakere velkommen") only if its quote is in the submission as whole words and contains one of the tag's cue words from the profile ("gratis", "fri inngang", "velkommen", ...). Agreement with the answer key is reported by check 8 (information only). The plan is to put a calibrated decision model (Laya locally, or Jev) behind the same port, returning probabilities.

## Use another region or your own submissions
- **Region:** copy `profiles/nord-gudbrandsdal.json`, change the municipalities and places, and point a dataset's `dataset.json` at it.
- **Submissions:** create `datasets/<name>/` as described in `datasets/README.md`, then run `python run.py --dataset <name>` with a live LLM.

## Data sources and licences
The locate stage looks up places in three open registers. No key, agreement or payment is needed; the licences require a credit and a link to the licence.

| Source | API | Licence | Credit used |
|---|---|---|---|
| Kartverket, place names (SSR) and addresses | `api.kartverket.no/stedsnavn/v1`, `ws.geonorge.no/adresser/v1` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ([terms](https://www.kartverket.no/api-og-data/vilkar-for-bruk)) | "Stedsnavn og adresser © Kartverket (CC BY 4.0)" |
| Brønnøysundregistrene, Enhetsregisteret (sub-units) | `data.brreg.no/enhetsregisteret/api` | [NLOD 2.0](https://data.norge.no/nlod/no/2.0) | "Inneholder data under Norsk lisens for offentlige data (NLOD) tilgjengeliggjort av Brønnøysundregistrene" (the licence's recommended wording) |

- **Where the credit appears:** the website footer, `calendar.md`, `calendar.ics` (`X-WR-CALDESC`) and `events.json` (`sources`). Each credit links to its source and licence. Validator check 7 fails if one of them is missing.
- **Changes are stated:** "Kommunen for hvert arrangement er utledet av denne tjenesten ved oppslag i registrene." Both licences ask for this.
- **No endorsement:** "Registereierne står ikke bak denne tjenesten." NLOD §6 forbids implying that the licensor endorses the service.
- **Fair use:** at most 5 lookups per event, only when the text doesn't already name a place in the region. One search term per request, no bulk downloading, a User-Agent that names the project, and every answer cached so the same search is never repeated.
- Changing the credit text: `SOURCES` in `src/render.py`. The validator's list (`CREDITS` in `src/validate.py`) is written separately on purpose.

## Notes
- The mock dataset is **written by an AI** for development and is clearly labelled. All groups, people and addresses in it are fictional (`example.org`, fake phone numbers).
- The committed run used **Gemma 3 27B running locally in Ollama**, so the saved responses cost nothing and no submission text left the machine (only the agent's short search texts went to the public registers).
- **Privacy towards the LLM:** email addresses and phone numbers ("400 00 000", "400-00-000", "40.00.00.00", "+47 …") are removed from every submission *before* it is sent to the LLM. Names of persons cannot be removed by a pattern, so an administrator using an online model must accept that provider's data terms (or use a local model).
- **Privacy in lookups:** the agent may only search for words that are in the submission; other searches are refused. Only the search text and, per hit, the name, type and municipality are kept. The business search is limited to the region and keeps only names made of the searched words plus a legal suffix (AS, ENK, ...) or a place name of the region, so a business named after a person ("Kari Nordmann ENK") is dropped unless the submission already had the full name. Saved lookups (`data/<dataset>/lookups/`) are committed for replay, so check them before committing a live run. Venues are not remembered between runs.
- **Private homes:** an event whose location says "hjemme hos", "heime hjå", "privatbolig" and similar goes to review instead of being published.
