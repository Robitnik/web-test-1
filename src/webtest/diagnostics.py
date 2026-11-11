"""Fingerprint self-check: what a site's own scripts would see.

Read in the MAIN world on purpose: that is where detection scripts run.
`red_flags()` lists only signals that are unambiguous automation tells.
"""

from __future__ import annotations

from typing import Any

from patchright.async_api import Page

FINGERPRINT_JS = """
async () => {
  const gl = (() => {
    try {
      const c = document.createElement('canvas').getContext('webgl');
      const ext = c && c.getExtension('WEBGL_debug_renderer_info');
      return ext ? {
        vendor: c.getParameter(ext.UNMASKED_VENDOR_WEBGL),
        renderer: c.getParameter(ext.UNMASKED_RENDERER_WEBGL),
      } : null;
    } catch (e) { return null; }
  })();
  let notif = null;
  try {
    const p = await navigator.permissions.query({name: 'notifications'});
    notif = {permission: Notification.permission, query: p.state};
  } catch (e) {}
  const uad = navigator.userAgentData;
  return {
    webdriver: navigator.webdriver,
    userAgent: navigator.userAgent,
    brands: uad ? uad.brands.map(b => b.brand) : null,
    platform: navigator.platform,
    languages: navigator.languages,
    plugins: navigator.plugins.length,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory ?? null,
    hasChromeObject: typeof window.chrome === 'object',
    screen: [screen.width, screen.height],
    window: [window.outerWidth, window.outerHeight],
    viewport: [window.innerWidth, window.innerHeight],
    webgl: gl,
    notifications: notif,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    cdcKeys: Object.keys(window).filter(k => /^cdc_|^\\$cdc_|__playwright|__pw/.test(k)),
  };
}
"""


async def fingerprint(page: Page) -> dict[str, Any]:
    return await page.evaluate(FINGERPRINT_JS, isolated_context=False)


def red_flags(fp: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if fp.get("webdriver"):
        flags.append("navigator.webdriver is true")
    if "Headless" in (fp.get("userAgent") or ""):
        flags.append("HeadlessChrome in userAgent")
    if any("Headless" in b for b in fp.get("brands") or []):
        flags.append("HeadlessChrome in userAgentData.brands")
    if not fp.get("hasChromeObject"):
        flags.append("window.chrome is missing")
    if fp.get("plugins") == 0:
        flags.append("navigator.plugins is empty")
    if fp.get("cdcKeys"):
        flags.append(f"automation globals on window: {fp['cdcKeys']}")
    renderer = ((fp.get("webgl") or {}).get("renderer") or "").lower()
    if "swiftshader" in renderer or "llvmpipe" in renderer:
        flags.append(f"software WebGL renderer: {renderer}")
    n = fp.get("notifications") or {}
    if n.get("permission") == "denied" and n.get("query") == "prompt":
        flags.append("Notification.permission/permissions.query mismatch (headless tell)")
    return flags
