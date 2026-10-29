"""Local website: a public calendar page and an operator page.

    python web.py              # then open http://localhost:8000
    python web.py --port 8080

The website is an adapter around the same pipeline: the Run button calls `python run.py`, and the
calendar page reads the same event store (data/<dataset>/events.json) as the other outputs.
It listens on 127.0.0.1 only, so it cannot be reached from other machines.
"""

import argparse
import datetime as dt
import html
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from common import DATASETS_DIR, Dataset, date_nb, from_iso, load_env, read_json, save_env  # noqa: E402
from llm import PROVIDERS, Client, LLMError, mask_key, test_connection  # noqa: E402
from render import SOURCES, SOURCES_NOTE, labels, occurrences, period_nb, time_range, where  # noqa: E402

STATUS_LABEL = {"published": "published", "needs_review": "needs review", "rejected": "rejected",
                "duplicate": "duplicate", "missing": "not processed yet"}
RUN_LOCK = threading.Lock()
e = html.escape

CSS = """
*{box-sizing:border-box} body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d2a33;background:#f5f7f6;line-height:1.45}
header{background:#1f4e5f;color:#fff;padding:18px 24px} header a{color:#cfe8ef;margin-right:16px;text-decoration:none} header h1{margin:4px 0 2px;font-size:1.5rem}
main{max-width:980px;margin:0 auto;padding:20px 24px 60px} h2{margin-top:32px;font-size:1.15rem;border-bottom:2px solid #d8e2e0;padding-bottom:4px}
.card{background:#fff;border:1px solid #dde5e3;border-radius:10px;padding:12px 16px;margin:8px 0}
.event{display:flex;gap:16px} .time{font-weight:700;min-width:52px;color:#1f4e5f} .meta{color:#52636c;font-size:.92rem}
.tag{display:inline-block;font-size:.75rem;padding:1px 8px;border-radius:99px;background:#e3efe9;color:#24543b;margin-left:6px}
.day{margin-top:22px;font-weight:700;color:#1f4e5f;text-transform:capitalize}
form.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:end} label{font-size:.85rem;color:#52636c;display:block}
select,input,textarea{font:inherit;padding:6px 8px;border:1px solid #c5d0cd;border-radius:6px;background:#fff} textarea{width:100%;min-height:140px}
button,.button{font:inherit;background:#1f4e5f;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer;text-decoration:none;display:inline-block}
button.secondary{background:#5d7680} table{width:100%;border-collapse:collapse;background:#fff;font-size:.9rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e3e9e7;vertical-align:top} th{background:#eef3f1}
.pass{color:#1d7a46;font-weight:700} .fail{color:#b3261e;font-weight:700} .muted{color:#6b7b82}
.s-published{color:#1d7a46} .s-needs_review{color:#9a6700} .s-rejected{color:#b3261e} .s-duplicate{color:#6b7b82}
pre{background:#14232b;color:#d7e3e8;padding:12px;border-radius:8px;overflow:auto;font-size:.82rem;max-height:420px}
.msg{padding:10px 14px;border-radius:8px;background:#e6f2ec;border:1px solid #b9dcc8} .msg.err{background:#fbeaea;border-color:#efc0bd}
footer{max-width:980px;margin:0 auto;padding:0 24px 30px;font-size:.8rem;color:#6b7b82} footer a{color:#52636c}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:700px){.grid{grid-template-columns:1fr}}
"""


def datasets():
    return sorted(p.name for p in DATASETS_DIR.iterdir() if (p / "dataset.json").exists())


def page(title, body, nav=True):
    links = '<a href="/">Kalender</a><a href="/admin">Operator</a>' if nav else ""
    return f"""<!doctype html><html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{CSS}</style></head>
<body><header>{links}<h1>{e(title)}</h1></header><main>{body}</main><footer>{sources_html()}</footer></body></html>"""


def sources_html():
    """Credit for the registers, as their licences require (see render.SOURCES)."""
    parts = [f'{e(s["credit"])} – <a href="{e(s["source_url"])}">{e(s["source_url"])}</a>, '
             f'<a href="{e(s["licence_url"])}">lisens</a>' for s in SOURCES]
    return "Kilder: " + ". ".join(parts) + ". " + e(SOURCES_NOTE)


def load_store(dataset):
    path = dataset.data_dir / "events.json"
    return read_json(path) if path.exists() else {"events": [], "unprocessed": []}


# ---------- public calendar (Norwegian) ----------

def calendar_page(query):
    names = datasets()
    name = query.get("dataset", ["mock"])[0]
    name = name if name in names else names[0]
    dataset = Dataset(name)
    events = [ev for ev in load_store(dataset)["events"] if ev["status"] == "published"]
    municipality = query.get("kommune", [""])[0]
    category = query.get("kategori", [""])[0]
    wanted_tag = query.get("passer", [""])[0]
    shown = [ev for ev in events if (not municipality or ev["municipality"] == municipality)
             and (not category or ev["category"] == category)
             and (not wanted_tag or wanted_tag in ev.get("tags", []))]
    profile = dataset.profile
    category_nb = {k: v["nb"] for k, v in profile["categories"].items()}
    tag_nb = {k: v["nb"] for k, v in profile["tags"].items()}

    options = lambda values, current, labels=None: "".join(  # noqa: E731
        f'<option value="{e(v)}"{" selected" if v == current else ""}>{e((labels or {}).get(v, v) or "Alle")}</option>'
        for v in [""] + values)
    body = [f'<p class="muted">{e(period_nb(dataset))} · {len(events)} arrangementer og aktiviteter sendt inn av lag '
            f'og foreninger i {e(dataset.profile["name"])}.</p>',
            f'<form class="filters" method="get"><input type="hidden" name="dataset" value="{e(name)}">'
            f'<div><label>Kommune</label><select name="kommune">{options(dataset.profile["municipalities"], municipality)}</select></div>'
            f'<div><label>Kategori</label><select name="kategori">{options(list(profile["categories"]), category, category_nb)}</select></div>'
            f'<div><label>Passer for / er</label><select name="passer">{options(list(profile["tags"]), wanted_tag, tag_nb)}</select></div>'
            f'<button>Vis</button> <a class="button secondary" href="/calendar.ics?dataset={e(name)}">Last ned til kalenderen din (.ics)</a> '
            f'<a class="button secondary" href="/events.json?dataset={e(name)}">Data (JSON)</a></form>']

    weekly = [ev for ev in shown if ev.get("recurrence") == "weekly"]
    if weekly:
        body.append("<h2>Faste aktiviteter – bli med!</h2>")
        for ev in weekly:
            start = from_iso(ev["start"])
            body.append(event_card(ev, f"Hver {date_nb(start.date()).split()[0]}", time_range(ev, start), profile))
    by_day = {}
    for ev in shown:
        for start in occurrences(ev, dataset):
            by_day.setdefault(start.date(), []).append((start, ev))
    body.append("<h2>Kalender</h2>")
    if not by_day:
        body.append('<p class="muted">Ingen arrangementer å vise. Kjør pipelinen fra operatørsiden først.</p>')
    for day in sorted(by_day):
        body.append(f'<div class="day">{e(date_nb(day))}</div>')
        for start, ev in sorted(by_day[day], key=lambda p: p[0]):
            body.append(event_card(ev, f"{start:%H:%M}", None, profile))
    return page(f"Hva skjer i {dataset.profile['name']}?", "".join(body))


def event_card(ev, when, extra, profile):
    link = f' · <a href="{e(ev["contact_url"])}">Mer info</a>' if ev.get("contact_url") else ""
    weekly = '<span class="tag">fast aktivitet</span>' if ev.get("recurrence") == "weekly" else ""
    desc = f'<div>{e(ev["description"])}</div>' if ev.get("description") else ""
    chips = "".join('<span class="tag">' + e(name) + "</span>" for name in labels(ev, profile).split(" · ") if name)
    return (f'<div class="card event"><div class="time">{e(when)}</div><div><strong>{e(ev["title"])}</strong>'
            f'{chips}{weekly}'
            f'<div class="meta">{e(where(ev))} · {e(ev["organizer"] or "")}{" · " + e(extra) if extra else ""}{link}</div>'
            f'{desc}</div></div>')


# ---------- operator page (English) ----------

def admin_page(query, message=None, error=False):
    names = datasets()
    name = query.get("dataset", ["mock"])[0]
    name = name if name in names else names[0]
    dataset = Dataset(name)
    env = load_env()
    provider = env.get("LLM_PROVIDER") or "replay"
    body = []
    if message:
        body.append(f'<p class="msg{" err" if error else ""}">{e(message)}</p>')

    # settings
    provider_options = "".join(f'<option value="{k}"{" selected" if k == provider else ""}>{e(v["label"])}</option>'
                               for k, v in PROVIDERS.items())
    presets = "".join(f'<li><b>{e(v["label"])}</b>: {e(v["base_url"] or "-")}'
                      f'{" · suggested model " + e(v["model"]) if v["model"] else ""}{" · needs API key" if v["needs_key"] else ""}</li>'
                      for v in PROVIDERS.values())
    key_note = f"saved: {mask_key(env.get('LLM_API_KEY'))}" if env.get("LLM_API_KEY") else "none saved"
    body.append(f"""<h2>1. Language model</h2>
<form method="post" action="/admin/settings?dataset={e(name)}" class="card"><div class="grid">
<div><label>Provider</label><select name="provider">{provider_options}</select></div>
<div><label>Model (empty = provider's suggestion)</label><input name="model" value="{e(env.get('LLM_MODEL', ''))}" style="width:100%"></div>
<div><label>API key ({e(key_note)}; leave empty to keep)</label><input name="api_key" type="password" autocomplete="off" style="width:100%"></div>
<div><label>Base URL (empty = provider's default)</label><input name="base_url" value="{e(env.get('LLM_BASE_URL', ''))}" style="width:100%"></div>
</div><p><button>Save settings</button> <button class="secondary" formaction="/admin/test?dataset={e(name)}">Test connection</button></p>
<p class="muted">The key is stored only in <code>.env</code> on this computer (never committed). Local models (Ollama, LM Studio) need no key.</p>
<details><summary class="muted">Provider defaults</summary><ul class="muted">{presets}</ul></details></form>""")

    # run
    log_path = dataset.data_dir / "last_run.log"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else "No run yet."
    dataset_options = "".join(f'<option{" selected" if n == name else ""}>{e(n)}</option>' for n in names)
    body.append(f"""<h2>2. Run the pipeline</h2>
<form method="post" action="/admin/run" class="card"><label>Dataset</label>
<select name="dataset">{dataset_options}</select> <button>Run now</button>
<span class="muted"> extract → check → de-dupe → render → validate. With a live model this takes a few minutes.</span></form>
<p class="muted">{e(dataset.meta.get('description', ''))}</p>""")
    validation_path = dataset.data_dir / "validation.json"
    if validation_path.exists():
        v = read_json(validation_path)
        rows = "".join(
            f'<tr><td class="{"pass" if c["passed"] else "fail"}">{"PASS" if c["passed"] else "FAIL"}</td>'
            f'<td>{c["check"]} {e(c["name"])}</td><td>{e(c["summary"])}'
            f'{"".join("<br>– " + e(p) for p in c["problems"])}</td></tr>' for c in v["checks"])
        body.append(f'<h3>Validation: <span class="{"pass" if v["passed"] else "fail"}">'
                    f'{"PASS" if v["passed"] else "FAIL"}</span></h3><table>{rows}</table>')
    body.append(f"<details><summary>Output of the last run</summary><pre>{e(log)}</pre></details>")

    # submissions
    store = load_store(dataset)
    rows = []
    for ref, received, text in dataset.submissions():
        mine = [ev for ev in store["events"] if ev["source_ref"] == ref]
        unprocessed = [u for u in store["unprocessed"] if u["source_ref"] == ref]
        if mine:
            cells = "<br>".join(f'<span class="s-{ev["status"]}">{e(STATUS_LABEL[ev["status"]])}</span> '
                                f'{e(ev["title"] or "")} {e(", ".join(ev["issues"]))}'
                                f'<br><span class="muted">{location_note(ev.get("location"))}</span>' for ev in mine)
        elif unprocessed:
            u = unprocessed[0]
            cells = f'<span class="s-{u["status"]}">{e(STATUS_LABEL[u["status"]])}</span> {e(u["issue"])}'
        else:
            cells = f'<span class="muted">{STATUS_LABEL["missing"]}</span>'
        rows.append(f'<tr><td>{e(ref.split("/", 1)[1])}</td><td>{received or "?"}</td><td>{cells}</td>'
                    f'<td class="muted">{e(text[:110])}{"…" if len(text) > 110 else ""}</td></tr>')
    body.append(f"""<h2>3. Submissions ({len(rows)})</h2>
<table><tr><th>File</th><th>Received</th><th>Outcome</th><th>Text</th></tr>{"".join(rows)}</table>""")

    # add submission
    body.append(f"""<h2>4. Add a submission</h2>
<form method="post" action="/admin/submit" class="card"><input type="hidden" name="dataset" value="{e(name)}">
<div class="grid"><div><label>Received</label><input type="date" name="received" value="{dt.date.today().isoformat()}"></div>
<div><label>Short name (used in the file name)</label><input name="slug" placeholder="e.g. korpskonsert"></div></div>
<label>Text, exactly as the group sent it</label><textarea name="text" required></textarea>
<p><button>Add to inbox</button> <span class="muted">Then run the pipeline. New submissions need a live model, not replay.</span></p></form>""")
    return page("Operator: community calendar", "".join(body))


SOURCE_LABEL = {"region_list": "named in the submission", "address_register": "address register",
                "place_register": "place-name register", "business_register": "business register"}


def location_note(loc):
    """One line for the operator: where the event is, and what that rests on."""
    if not loc:
        return ""
    if loc["status"] == "confirmed":
        return f"📍 {e(loc['municipality'] or '')}: {e(SOURCE_LABEL.get(loc['source'], loc['source']))} ({e(loc.get('match') or '')})"
    if loc["status"] == "suggestion":
        return (f"📍 suggestion: {e(loc['municipality'] or '?')}, not confirmed by a register. {e(loc.get('reason') or '')}"
                f" Searches: {e('; '.join(loc.get('searches', [])))}")
    if loc["status"] == "private":
        return "📍 private address: check before publishing"
    return f"📍 location unknown. {e(loc.get('reason') or '')}"


# ---------- actions ----------

def save_settings(form):
    env = load_env()
    provider = form.get("provider", "replay")
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    if any("\r" in v or "\n" in v for v in form.values()):
        raise ValueError("Settings must be on one line.")
    base_url = form.get("base_url", "").strip()
    if provider != (env.get("LLM_PROVIDER") or "replay") or base_url != env.get("LLM_BASE_URL", ""):
        env["LLM_API_KEY"] = ""  # never send a saved key to another provider or address
    env["LLM_PROVIDER"] = provider
    env["LLM_MODEL"] = form.get("model", "").strip()
    env["LLM_BASE_URL"] = base_url
    if form.get("api_key", "").strip():
        env["LLM_API_KEY"] = form["api_key"].strip()
    save_env(env)
    return env


def run_pipeline(name):
    if name not in datasets():
        raise ValueError("Unknown dataset")
    if not RUN_LOCK.acquire(blocking=False):
        return False, "A run is already in progress."
    try:
        result = subprocess.run([sys.executable, str(ROOT / "run.py"), "--dataset", name], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = result.stdout + result.stderr
        log_path = Dataset(name).data_dir / "last_run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        return result.returncode == 0, ("Run finished: validation passed." if result.returncode == 0
                                        else "Run finished: validation FAILED or a stage could not run. See below.")
    finally:
        RUN_LOCK.release()


def add_submission(form):
    name = form.get("dataset", "")
    if name not in datasets():
        raise ValueError("Unknown dataset")
    text = form.get("text", "").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("The submission text is empty.")
    received = dt.date.fromisoformat(form.get("received") or dt.date.today().isoformat())
    slug = "".join(c for c in form.get("slug", "").lower().replace(" ", "-") if c.isalnum() or c == "-")[:40] or "innsending"
    inbox = DATASETS_DIR / name / "inbox"
    number = 1 + max([int(p.name[:3]) for p in inbox.glob("[0-9][0-9][0-9]_*")] or [0])
    path = inbox / f"{number:03d}_{slug}.txt"
    path.write_text(f"received: {received.isoformat()}\n\n{text}\n", encoding="utf-8")
    return path.name


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        # Only answer requests addressed to this machine by name. This blocks DNS-rebinding
        # attacks, where a web page on another domain is made to point at 127.0.0.1.
        port = self.server.server_port
        if self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self.send(403, "Forbidden", "text/plain; charset=utf-8")
        return False

    def do_GET(self):
        if not self.host_ok():
            return
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/":
                self.send(200, calendar_page(query))
            elif url.path == "/admin":
                self.send(200, admin_page(query))
            elif url.path in ("/calendar.ics", "/events.json"):
                name = query.get("dataset", ["mock"])[0]
                filename = url.path.lstrip("/")
                path = Dataset(name if name in datasets() else "mock").output_dir / filename
                if not path.exists():
                    return self.send(404, "Run the pipeline first.", "text/plain; charset=utf-8")
                if filename == "calendar.ics":
                    self.send(200, path.read_bytes(), "text/calendar; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="aktiviteter.ics"'})
                else:  # the event data, for any other website, app or open-data portal
                    self.send(200, path.read_bytes(), "application/json; charset=utf-8",
                              {"Access-Control-Allow-Origin": "*"})
            else:
                self.send(404, page("Not found", "<p>Not found.</p>"))
        except Exception as ex:  # show the error instead of a dropped connection
            self.send(500, page("Error", f"<pre>{e(repr(ex))}</pre>"))

    def do_POST(self):
        if not self.host_ok():
            return
        # Only accept forms from this site, so other web pages can't drive the operator page.
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if urllib.parse.urlparse(origin).netloc != self.headers.get("Host", ""):
            return self.send(403, "Forbidden", "text/plain; charset=utf-8")
        url = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        except (ValueError, UnicodeDecodeError):
            return self.send(400, "Bad request", "text/plain; charset=utf-8")
        dataset = form.get("dataset") or urllib.parse.parse_qs(url.query).get("dataset", ["mock"])[0]
        query = {"dataset": [dataset if dataset in datasets() else "mock"]}
        try:
            if url.path == "/admin/settings":
                env = save_settings(form)
                self.send(200, admin_page(query, f"Saved. Provider: {PROVIDERS[env['LLM_PROVIDER']]['label']}."))
            elif url.path == "/admin/test":
                env = save_settings(form)
                client = Client(env["LLM_PROVIDER"], env.get("LLM_MODEL", ""), env.get("LLM_API_KEY", ""),
                                env.get("LLM_BASE_URL", ""))
                ok, message = test_connection(client)
                self.send(200, admin_page(query, message, error=not ok))
            elif url.path == "/admin/run":
                ok, message = run_pipeline(dataset)
                self.send(200, admin_page(query, message, error=not ok))
            elif url.path == "/admin/submit":
                filename = add_submission(form)
                self.send(200, admin_page(query, f"Added {filename} to the inbox. Run the pipeline to process it."))
            else:
                self.send(404, "Not found", "text/plain; charset=utf-8")
        except (LLMError, ValueError) as ex:
            self.send(200, admin_page(query, str(ex), error=True))

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[web] {self.command} {self.path}\n")


def main():
    parser = argparse.ArgumentParser(description="Local website for the community calendar.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Open http://localhost:{args.port}  (calendar)  and  http://localhost:{args.port}/admin  (operator).  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
