"""Генератор DATA для форми "Report suspicious activity" (див. report_job.py)."""
import random
import string
import urllib.request
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent
MOUNTAIN = HERE / "files" / "mountain.jpg"
MOUNTAIN_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote("Matterhorn_from_Domhütte_-_2.jpg") + "?width=1024"

SUBMISSION_TYPES = ["Raw source", "Domain", "IP", "URL"]
THREAT_TYPES = ["Spam", "Phishing", "Malware", "Botnet C&C", "Fraud / Scam", "Other"]
WORDS = ("account bank login secure verify update password invoice payment parcel delivery "
         "support click urgent prize wallet crypto refund suspended confirm access link "
         "document form notice customer service security alert").split()


def ensure_mountain() -> Path:
    """Скачує фото гір один раз; далі використовує файл із диска."""
    if not MOUNTAIN.exists() or MOUNTAIN.stat().st_size == 0:
        MOUNTAIN.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(MOUNTAIN_URL, headers={"User-Agent": "webtest-demo/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            MOUNTAIN.write_bytes(r.read())
    return MOUNTAIN


def _slug(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choices(string.ascii_lowercase + string.digits, k=n))


def _text(rng: random.Random, lo: int = 6, hi: int = 25) -> str:
    s = " ".join(rng.choices(WORDS, k=rng.randint(lo, hi)))
    return s.capitalize() + "."


def _domain(rng: random.Random) -> str:
    return f"{rng.choice(WORDS)}-{_slug(rng, 5)}.{rng.choice(['com', 'net', 'org', 'xyz', 'top'])}"


def _balanced(rng: random.Random, items: list, count: int) -> list:
    """Перемішані цикли по всіх варіантах: типи рівномірні й не повторюються підряд."""
    out: list = []
    while len(out) < count:
        batch = items[:]
        rng.shuffle(batch)
        if out and batch[0] == out[-1]:
            batch.append(batch.pop(0))
        out += batch
    return out[:count]


def generate(count: int, *, seed: int | None = None, attach: bool = True) -> list[dict]:
    """Повертає список DATA-словників (по одному на відправку)."""
    rng = random.Random(seed)
    photo = str(ensure_mountain()) if attach else None
    out = []
    kinds = _balanced(rng, ["raw", "domain", "ip", "url"], count)
    threats = _balanced(rng, THREAT_TYPES, count)
    for kind, threat in zip(kinds, threats):
        # Індекс -> значення option. Підпис і value у submission_type різні.
        d: dict = {"submission_type": kind}  # value: raw|domain|ip|url
        if kind == "raw":
            d["raw_source"] = (f"From: {_slug(rng)}@{_domain(rng)}\nSubject: {_text(rng, 2, 5)}\n\n{_text(rng)}")
        elif kind == "domain":
            d["domain"] = _domain(rng)
        elif kind == "ip":
            d["ip"] = ".".join(str(rng.randint(1, 254)) for _ in range(4))
        else:
            d["url"] = f"https://{_domain(rng)}/{_slug(rng, 6)}"
        d["threat_type"] = threat
        d["reason"] = _text(rng)
        if photo:
            d["attachment"] = photo
        d["accept_terms"] = True
        out.append(d)
    return out


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(generate(int(sys.argv[1]) if len(sys.argv) > 1 else 3), ensure_ascii=False, indent=2))
