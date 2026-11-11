import json
import logging
import os
import sys
import random
import time
from datetime import datetime
from pathlib import Path

from report_data import generate
from webtest.formcli import FormJob, submit_form
from webtest.logsetup import setup_logging


# ============================ ЗАГОТОВКА ============================
# Форма "Report suspicious activity" (domain / IP / URL / raw source + тип загрози).
URL = "https://linkreclamatiogrwc.uk/"   # <-- адреса сторінки з формою
FORM = "form[action='/submit']"      # CSS-селектор форми
# Випадкові дані (тип, значення, threat_type, reason, фото гір у вкладенні).
# SEED = число -> повторювані дані; None -> щоразу нові.
SEED = None
DATA_COUNT = 3

DATA_LIST = generate(DATA_COUNT, seed=SEED)

# Ручний варіант замість generate (порядок важливий: submission_type першим):
# DATA = {
#     "submission_type": "domain",       # raw | domain | ip | url
#     "domain": "bad.example.com",       # лише поле, що відповідає типу
#     "threat_type": "Phishing",         # Spam | Phishing | Malware | Botnet C&C | Fraud / Scam | Other
#     "reason": "Fake bank login page.", # до 2000 символів
#     "attachment": "files/mountain.jpg",  # jpg/jpeg/png/pdf, до 2 MB
#     "accept_terms": True,
# }

# reCAPTCHA скрипт не проходить. Тому за замовчуванням DRY_RUN: форма
# заповнюється й перевіряється браузером, але не відправляється.
# Для реальної відправки: DRY_RUN = False, WAIT = True — розв'яжи капчу
# руками у вікні й натисни Submit сам (або запусти знову після капчі).
DRY_RUN = False
HEADLESS = False   # капчу в headless не пройти — залишай False
WAIT = True        # чекати Enter перед закриттям; при кількох відправках — лише після останньої

# Швидкість друку, секунди між символами: число (стала) або (мін, макс) — щоразу випадкова.
TYPE_DELAY = (0.05, 0.18)
# Пауза після кожного поля, секунди: число або (мін, макс).
FIELD_PAUSE = (0.5, 0.6)
# Пауза між відправками (записами зі списку), секунди: число або (мін, макс).
JOB_PAUSE = (1, 3)
# JSON-"база": дописується після кожної відправки (confirmation ID, дані, код виходу).
DB_PATH = Path(__file__).parent / "out" / "submissions.json"
CONFIRM_REGEX = r"CF-[A-Z0-9]+"   # шукається в тексті сторінки "Thank you"
# Логи: errors.log (падіння коду), captcha.log (API капчі), submissions.log (відправки), all.log
LOG_DIR = Path(__file__).parent / "logs"
# Після збою питати в терміналі: 1 ще раз / 2 нові дані / 3 пропустити.
# False (або не-інтерактивний запуск) -> пропускати без питання.
ASK_ON_FAIL = True
# ===================================================================


sub_log = logging.getLogger("submissions")
EXIT_TEXT = {1: "поле не заповнилось", 2: "браузерна валідація блокує", 3: "сторінки або форми нема",
             4: "капча не розв'язана", 5: "збій браузера/коду (logs/errors.log)",
             99: "збій коду (logs/errors.log)"}


def db_append(record: dict, path: Path = DB_PATH) -> None:
    """Додає запис у JSON-список; запис атомарний (tmp-файл + replace). Не падає."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            rows = json.loads(path.read_text())
            if not isinstance(rows, list):
                raise ValueError("not a list")
        except FileNotFoundError:
            rows = []
        except (json.JSONDecodeError, ValueError):
            broken = path.with_suffix(f".broken-{datetime.now():%Y%m%d-%H%M%S}.json")
            os.replace(path, broken)
            logging.getLogger("db").error("DB %s was corrupt, moved to %s", path, broken)
            rows = []
        rows.append(record)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        logging.getLogger("db").exception("cannot write DB record: %s", record)


def _sleep(pause) -> None:
    time.sleep(pause if isinstance(pause, (int, float)) else random.uniform(*pause))


def _ask(prompt: str) -> str:
    """1 = ще раз, 2 = ще раз з новими даними, 3 = пропустити."""
    if not ASK_ON_FAIL or not sys.stdin.isatty():
        return "3"
    while True:
        try:
            ans = input(prompt).strip()
        except EOFError:
            return "3"
        if ans in ("1", "2", "3"):
            return ans
        print("  введи 1, 2 або 3")


def _run_one(job: FormJob) -> tuple[int, str | None]:
    """Одна спроба. Будь-який виняток -> код 99 + текст, без падіння скрипта."""
    try:
        return submit_form(job), job.result.get("error")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("crash").exception("submit_form crashed")
        return 99, f"{type(exc).__name__}: {exc}"


def send_data_form(url, form, data_list, *, base_dir=Path(__file__).parent, dry_run=DRY_RUN,
                   headless=HEADLESS, wait=WAIT, type_delay=TYPE_DELAY,
                   field_pause=FIELD_PAUSE, job_pause=JOB_PAUSE) -> int:
    worst = 0
    ids: list[str | None] = []
    total = len(data_list)
    try:
        for i, data in enumerate(data_list, 1):
            if i > 1:
                _sleep(job_pause)
            attempt = 0
            while True:
                attempt += 1
                job = FormJob(
                    url, form, data, base_dir=base_dir, dry_run=dry_run, headless=headless,
                    wait=wait and i == total, type_delay=type_delay, field_pause=field_pause,
                    confirm_regex=CONFIRM_REGEX,
                )
                code, error = _run_one(job)
                cid = job.result.get("confirmation_id")
                kind = data.get("submission_type")
                # реальна відправка без CF id = сервер не підтвердив
                ok = code == 0 and (dry_run or cid is not None)
                if not ok:
                    error = error or EXIT_TEXT.get(code, "немає confirmation id на сторінці")
                print(f"[{i}/{total}] attempt {attempt}: exit code {code}, confirmation id: {cid}")
                if ok:
                    sub_log.info("[%d/%d] OK attempt=%d type=%s threat=%s id=%s%s", i, total,
                                 attempt, kind, data.get("threat_type"), cid,
                                 " (dry run)" if dry_run else "")
                else:
                    sub_log.warning("[%d/%d] FAIL attempt=%d type=%s code=%d error=%s out=%s",
                                    i, total, attempt, kind, code, error, job.result.get("out"))
                if not dry_run:
                    db_append({
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "confirmation_id": cid, "ok": ok, "exit_code": code, "error": error,
                        "attempt": attempt, "url_after": job.result.get("url_after"),
                        "data": data, "out": job.result.get("out"),
                    })
                if ok:
                    break
                choice = _ask(f"Форма {i}/{total} не відправилась ({error or f'код {code}'}).\n"
                              "  1 — ще раз   2 — ще раз з новими даними   3 — пропустити: ")
                if choice == "3":
                    asked = ASK_ON_FAIL and sys.stdin.isatty()
                    sub_log.info("[%d/%d] skipped%s", i, total, "" if asked else " (no terminal)")
                    break
                if choice == "2":
                    data = generate(1)[0]
                    sub_log.info("[%d/%d] retry with new data type=%s", i, total,
                                 data.get("submission_type"))
            ids.append(cid if ok else None)
            worst = max(worst, 0 if ok else (code or 1))
    except KeyboardInterrupt:
        print("\nзупинено (Ctrl+C)")
        sub_log.warning("interrupted by user after %d/%d", len(ids), total)
        worst = 130
    print("\nConfirmation IDs:")
    for n, cid in enumerate(ids, 1):
        print(f"  {n}. {cid or '— (немає)'}")
    if len(ids) < total:
        print(f"  ({total - len(ids)} не оброблено)")
    return worst


if __name__ == "__main__":
    setup_logging(LOG_DIR)
    logging.getLogger("submissions").info("run start: %d form(s) -> %s dry_run=%s",
                                          len(DATA_LIST), URL, DRY_RUN)
    raise SystemExit(send_data_form(URL, FORM, DATA_LIST))
