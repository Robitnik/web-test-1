# ============================ ЗАГОТОВКА ============================
URL = "https://httpbin.org/forms/post"
FORM = "form"  # CSS-селектор форми

DATA = {  # name поля: значення. Порядок = порядок заповнення.
    "custname": "Olena Test",
    "custtel": "+380501234567",
    "custemail": "olena@example.com",
    "size": "Medium",                   # radio / select: value або підпис
    "topping": ["cheese", "Mushroom"],  # група чекбоксів: рівно ці
    "delivery": "19:30",                # date "2026-11-12", time "19:30", checkbox True
    "comments": "Test from webtest",
    # "attachment": "files/cv.pdf",     # файл: шлях (відносно цього файлу) або список шляхів
}

DRY_RUN = False    # True: заповнити й перевірити, не відправляти
HEADLESS = False   # True: без вікна
CAPTCHA = "auto"   # "auto": спершу безкоштовно, потім API · "free": без API · "off"
# ===================================================================

if __name__ == "__main__":
    from pathlib import Path

    from webtest.formcli import FormJob, submit_form

    raise SystemExit(submit_form(FormJob(
        URL, FORM, DATA, base_dir=Path(__file__).parent, dry_run=DRY_RUN, headless=HEADLESS,
        captcha=CAPTCHA,
    )))
