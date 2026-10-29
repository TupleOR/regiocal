You answer typed questions about one activity in a community calendar. The activity was submitted by a local group in Norway; the submission is in Norwegian (bokmål or nynorsk).

Base every answer **only on the submission text**. Do not guess.

## Questions
The questions are given in the format of a typed decision model:
- `choice`: pick exactly one label from `criteria`.
- `noul`: decide whether the statement in `instructions` is true for this activity.

```json
{{QUESTIONS}}
```

## Answer rules
- For `choice`, answer with one of the labels exactly as written.
- For `noul`, answer `true` only if the text clearly supports the statement; otherwise `false`.
- For every `noul` answered `true`, give `quote`: the words from the submission that support it, copied **exactly, character for character**. If you cannot quote support, answer `false`.

## Reply format
Reply with ONLY one JSON object, no other text:

```json
{"answers": {
  "category": {"choice": "music"},
  "music": {"value": true, "quote": "julekonsert"},
  "free": {"value": true, "quote": "Fri inngang"},
  "family": {"value": false, "quote": null}
}}
```
Include every question in `answers`.
