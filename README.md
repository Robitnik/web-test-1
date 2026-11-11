# web-test-1

Базове керування браузером на **patchright** (патчений Playwright, який не видає себе
як автоматизацію) + **справжній Google Chrome**. Python 3.13, `uv`.

## Швидкий старт

```bash
uv sync                               # .venv + залежності
scripts/install_chrome.sh             # Google Chrome у ~/.local/opt (без sudo)
#   або: scripts/install_chrome.sh --system   (через apt, з автооновленням)
uv run patchright install chromium    # запасний Chromium, якщо Chrome нема
cp .env.example .env                  # за потреби

uv run webtest doctor                 # що знайдено: Chrome, patchright, профіль
uv run webtest open https://example.com --shot out/example.png
uv run webtest check --sites sannysoft creepjs   # самоперевірка відбитка
```

## У коді

```python
import asyncio
from webtest import Browser, BrowserConfig

async def main():
    async with Browser(BrowserConfig.from_env()) as b:
        page = await b.page()
        status = await b.goto(page, "https://example.com")
        title = await b.eval_main(page, "document.title")   # головний світ сторінки
        await b.screenshot(page, "out/example.png")

asyncio.run(main())
```

## Структура

| Файл | Що робить |
|---|---|
| `src/webtest/config.py` | `BrowserConfig` з `.env` / змінних оточення |
| `src/webtest/chrome.py` | пошук справжнього Chrome (snap-Chromium свідомо пропускається) |
| `src/webtest/profile.py` | захист профілю: один профіль = один Chrome (`SingletonLock`) |
| `src/webtest/browser.py` | `Browser`: запуск, вкладки, `goto`+settle, скріни, `eval_main`/`eval_isolated` |
| `src/webtest/diagnostics.py` | відбиток очима скриптів сайту + явні ознаки автоматизації |
| `src/webtest/cli.py` | `webtest doctor / open / check` |
| `scripts/install_chrome.sh` | Chrome без sudo або через apt |
| `docs/PLAN.md` | ресерч і план робіт |

## Правила stealth (чому код саме такий)

- `launch_persistent_context` + справжній профіль, не одноразовий контекст.
- Справжній Chrome, не вбудований Chromium.
- `no_viewport=True`: вікно має реальний розмір, екран і в'юпорт узгоджені.
- **Жодного кастомного `user_agent` і заголовків**: розбіжність із Client Hints і TLS сама є сигналом.
- Видимий режим за замовчуванням; headless виявляється легше (на сервері: Xvfb, див. план).
- `page.evaluate` у patchright іде в **ізольованому світі**: глобалі сайту там не видно.
  Для них `eval_main()`.

## Тести

```bash
uv run pytest                                   # юніт-тести, без браузера
WEBTEST_LIVE=1 uv run pytest -m live            # справжній запуск (headless)
WEBTEST_LIVE=1 WEBTEST_HEADLESS=0 uv run pytest -m live   # видимий, сувора перевірка
```

Використовуйте лише на сайтах, де маєте на це право, з повагою до їхніх умов і robots.txt.
