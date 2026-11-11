# web-test-1

Browser automation on **patchright** (a patched Playwright that does not announce itself
as automation) driving a **real Google Chrome**. Python 3.13, managed with `uv`.

## Setup

```bash
uv sync                               # create .venv and install dependencies
scripts/install_chrome.sh             # Google Chrome into ~/.local/opt (no sudo)
#   or: scripts/install_chrome.sh --system   (via apt, with auto-updates)
uv run patchright install chromium    # fallback Chromium in case Chrome is missing
cp .env.example .env                  # optional: profile, proxy, captcha API key
```

## Run commands

### Browser tools (`webtest`)

```bash
uv run webtest doctor                                  # what was found: Chrome, patchright, profile
uv run webtest open https://example.com --shot out/example.png
uv run webtest open https://example.com --wait         # keep the window open until Enter
uv run webtest check --sites sannysoft creepjs         # fingerprint self-check
uv run webtest form-scan https://site/contact --form "form#contact"   # list fields + fill keys
uv run webtest captcha-balance                         # captcha API account balance
uv run webtest captcha-detect https://site --form "#f" # which captcha, without solving
```

### Submit one form (`webform`)

```bash
# URL  form selector  data (JSON string, a .json file, or "-" for stdin)
uv run webform https://site/contact "form#contact" '{"email": "a@b.c", "cv": "cv.pdf"}'
uv run webform https://site/contact "form#contact" data.json --dry-run   # fill + validate only
uv run webform https://site/contact "form#contact" data.json --human     # type char by char
uv run webform https://site/contact "form#contact" data.json --captcha free  # never pay the API
uv run webtest submit ...                                                # same as webform
```

Options: `--button SEL`, `--dry-run`, `--human`, `--headless`, `--profile DIR`,
`--timeout MS`, `--settle-ms MS`, `--out DIR`, `--wait`, `--captcha auto|free|off`,
`--captcha-action NAME`. See `uv run webform --help`.

### Python job files

```bash
uv run python form_job.py      # generic template: edit the block at the top, then run
uv run python report_job.py    # "Report suspicious activity" form: N random submissions
python3 report_data.py 5       # print 5 generated DATA records as JSON (no browser)
```

### Tests

```bash
uv run pytest                                             # unit tests, no browser
WEBTEST_LIVE=1 uv run pytest -m live                      # real browser, headless
WEBTEST_LIVE=1 WEBTEST_HEADLESS=0 uv run pytest -m live   # visible window, strict checks
```

## Batch job: `report_job.py`

Fills and submits the "Report suspicious activity" form `DATA_COUNT` times with random
data from [`report_data.py`](report_data.py). Settings live in the block at the top of the file:

| Setting | Meaning |
| --- | --- |
| `URL`, `FORM` | page and CSS selector of the form |
| `DATA_COUNT`, `SEED` | number of submissions; a fixed seed gives repeatable data |
| `DRY_RUN` | `True`: fill and validate, do not submit |
| `HEADLESS` | `False` recommended (captchas pass far more often in a visible window) |
| `WAIT` | wait for Enter before closing; with several submissions, only after the last |
| `TYPE_DELAY` | seconds between typed characters: a number or a `(min, max)` range |
| `FIELD_PAUSE` | seconds after each field: a number or `(min, max)` |
| `JOB_PAUSE` | seconds between submissions: a number or `(min, max)` |
| `DB_PATH` | JSON "database" of submissions (default `out/submissions.json`) |
| `CONFIRM_REGEX` | pattern for the confirmation ID on the result page (`CF-[A-Z0-9]+`) |
| `LOG_DIR` | log folder (default `logs/`) |
| `ASK_ON_FAIL` | ask what to do after a failed submission (see below) |

**Generated data** (`generate(count, seed=None, attach=True)`): submission types and threat
types are shuffled cycles, so every type appears and none repeats twice in a row. Text
fields get random values; the attachment is a mountain photo downloaded once to
`files/mountain.jpg`.

**After each real submission** a record goes to `out/submissions.json`: time, confirmation
ID, `ok`, exit code, error, attempt number, data, report folder. At the end, all
confirmation IDs are printed.

**On failure** (non-zero exit code, or no confirmation ID on the result page):

```text
Форма 2/3 не відправилась (капча не розв'язана).
  1 — ще раз   2 — ще раз з новими даними   3 — пропустити:
```

(`1` retry with the same data, `2` retry with freshly generated data, `3` skip.)

Without a terminal, or with `ASK_ON_FAIL = False`, failures are skipped. One failed
submission never stops the rest; Ctrl+C stops cleanly and still prints the ID summary.

### Logs (`logs/`)

| File | Contents |
| --- | --- |
| `errors.log` | code crashes with full tracebacks, including uncaught exceptions |
| `captcha.log` | captcha solved / API errors / solver crashes, with sitekey and page |
| `submissions.log` | one line per attempt: OK with ID or FAIL with reason; retries, skips |
| `all.log` | everything together, to read a run end to end |

Files rotate at 2 MB (5 kept). Set up with `webtest.logsetup.setup_logging("logs")`.

## Using the library

```python
import asyncio
from webtest import Browser, BrowserConfig

async def main():
    async with Browser(BrowserConfig.from_env()) as b:
        page = await b.page()
        status = await b.goto(page, "https://example.com")
        title = await b.eval_main(page, "document.title")   # the page's main world
        await b.screenshot(page, "out/example.png")

asyncio.run(main())
```

### Forms (`webtest.forms`)

```python
from datetime import date
from pathlib import Path
from webtest.forms import fill_form, submit

form = page.locator("form#signup")
results = await fill_form(form, {
    "First name": "Olena",              # label / name / id / placeholder, auto-detected
    "placeholder:Password": "s3cret",   # or an explicit prefix: label: name: id: placeholder:
    "Birthday": date(1994, 5, 17),      # date/time/datetime-local/month/week from Python objects
    "color": "#ff8800", "volume": 70,   # color, range
    "I agree": True,                    # checkbox, role=switch
    "size": "Medium",                   # radio group: value or label
    "topics": ["seo", "Development"],   # checkbox group: exactly these checked
    "country": "United States",         # select; a list for multiple
    "Avatar": Path("me.png"),           # input[type=file]; a list for multiple
    "#upload_btn": Path("cv.pdf"),      # a button that opens the file chooser
    "Fruit": "Banana",                  # custom role=combobox / autocomplete
    "Notes": "Hello",                   # contenteditable
}, human=True, type_delay=(0.05, 0.18), field_pause=(0.5, 1.5))
await submit(form)
```

- Every field is **read back** after filling: `FieldResult.ok` reflects what the page holds,
  not what we intended (phone masks, clamped ranges, rejected options show as `ok=False`).
- **Invisible text fields are refused** (`HoneypotSuspected`): anti-bot forms place fields
  off-screen, and filling one flags the whole submission. Hidden file inputs and styled
  checkboxes are normal UI and are handled (a checkbox is clicked through its label).
- `<input type=hidden>` and disabled fields are refused; hidden only with `allow_hidden=True`.
- `type_delay` / `field_pause`: seconds, a number or a `(min, max)` range.

### What `webform` does

Opens the page → fills fields in the given order (a relative file path is looked up in the
current folder, then next to the JSON file) → **runs the browser's own validation**
(required, email, pattern) and does not submit a form the browser would reject → solves a
captcha if there is one → submits → shows which POSTs/navigations actually happened and
what the server answered. Output: `out/webform-*/report.json` + `after.png`.

Exit codes: `0` sent · `1` field not found / did not keep its value · `2` browser validation
blocks · `3` page or form missing · `4` captcha not solved · `5` crashed (see `logs/`).

## Captcha (`webtest.captcha`)

`.env`: `CAPTCHA_KEY=` (or `captcha_key`), `CAPTCHA_PROVIDER=2captcha|anticaptcha|capmonster`.

```python
from webtest.captcha import solve_form_captcha

result = await solve_form_captcha(page, page.locator("form#contact"))
# None -> no captcha; result.method == "auto" (passed by itself) or "api" (solved by the service)
```

- **Detects:** reCAPTCHA v2 (checkbox, invisible, enterprise), reCAPTCHA v3, hCaptcha,
  Cloudflare Turnstile, image captcha with an answer field. Arkose/GeeTest/DataDome: an
  explicit refusal.
- **Free first:** Turnstile often passes by itself in real Chrome; the reCAPTCHA/hCaptcha
  checkbox is clicked, and if no image challenge appears the token is already there. The
  API is used only after that.
- The token is written where the widget itself writes it, and **the widget callback is
  called** (`data-callback` or `___grecaptcha_cfg`): many sites enable the button or submit
  the form only from it. `webform` notices this and does not submit a second time.
- Image captcha: a screenshot of the element (exactly what a person sees), no re-download.

## Project layout

| File | Purpose |
| --- | --- |
| `src/webtest/config.py` | `BrowserConfig` from `.env` / environment variables |
| `src/webtest/chrome.py` | finds real Chrome (snap Chromium is skipped on purpose) |
| `src/webtest/profile.py` | profile guard: one profile = one Chrome (`SingletonLock`) |
| `src/webtest/browser.py` | `Browser`: launch, tabs, `goto`+settle, screenshots, `eval_main`/`eval_isolated` |
| `src/webtest/forms.py` | fills every field type + files, reads values back, honeypot guard |
| `src/webtest/formcli.py` | `webform`: one form run end to end, `FormJob` |
| `src/webtest/captcha/` | captcha detection, free solving, API client |
| `src/webtest/logsetup.py` | log files in `logs/` |
| `src/webtest/diagnostics.py` | fingerprint as site scripts see it + obvious automation signs |
| `src/webtest/cli.py` | `webtest` subcommands |
| `form_job.py` | generic single-form template |
| `report_job.py`, `report_data.py` | batch job and random data generator for the report form |
| `scripts/install_chrome.sh` | Chrome without sudo or via apt |
| `docs/PLAN.md` | research and work plan |

## Stealth rules (why the code looks like this)

- `launch_persistent_context` with a real profile, not a throwaway context.
- Real Chrome, not the bundled Chromium.
- `no_viewport=True`: the window has a real size; screen and viewport agree.
- **No custom `user_agent` or headers**: a mismatch with Client Hints and TLS is a signal itself.
- Headed by default; headless is easier to detect (on a server use Xvfb, see the plan).
- `page.evaluate` in patchright runs in an **isolated world**: site globals are not visible
  there. Use `eval_main()` for them.

Use only on sites you are allowed to test, respecting their terms and robots.txt.
