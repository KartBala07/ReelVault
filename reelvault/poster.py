"""Post reels to Instagram through the real web UI.

Ported from InstaReelPoster (KartBala07): Selenium drives Instagram's own upload
dialog inside a clone of the user's Firefox profile, so no password is ever
stored or typed, and the account is whatever that session is logged into.

Everything here is dry-run by default — `publish=False` walks the whole flow and
stops before the Share button.
"""
import os
import re
import shutil
import time
from pathlib import Path

from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.webdriver import Firefox
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

HOME = os.path.expanduser("~")
REELVAULT_PROFILE = os.path.join(HOME, ".reelvault", "firefox-profile")
LEGACY_PROFILE = os.path.join(HOME, "InstaReelPoster", ".firefox-profile")
FIREFOX_PROFILES = os.path.join(HOME, "Library", "Application Support", "Firefox", "Profiles")

JS_CLICK = """
const want = arguments[0];
const root = document.querySelector('div[role="dialog"]') || document.body;
const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
const nodes = [...root.querySelectorAll('button, [role="button"], div[role="button"]')];
const hits = [];
for (const n of nodes) {
  const t = norm(n.innerText);
  if (!want.includes(t)) continue;
  if (n.getAttribute('aria-disabled') === 'true' || n.disabled) continue;
  const r = n.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) continue;
  hits.push([n, t]);
}
if (!hits.length) return 'none|' + nodes.map(n => norm(n.innerText)).filter(Boolean).slice(0, 25).join(' / ');
const target = hits[hits.length - 1];
target[0].scrollIntoView({block: 'center'});
target[0].click();
return 'clicked:' + target[1];
"""

JS_SET_CAPTION = """
const el = arguments[0], text = arguments[1];
el.focus();
const sel = window.getSelection();
const range = document.createRange();
range.selectNodeContents(el);
sel.removeAllRanges();
sel.addRange(range);
try { document.execCommand('delete', false, null); } catch (e) {}
let ok = false;
try { ok = document.execCommand('insertText', false, text); } catch (e) {}
return [ok, (el.innerText || '').trim().length, (el.innerText || '').trim().slice(0, 60)];
"""


class Poster(object):
    def __init__(self, log=None, headless=False):
        self.log = log or (lambda m, k="log": None)
        self.headless = headless
        self.driver = None
        self.stopped = False

    # ---- session ----
    @staticmethod
    def profile_path():
        if os.path.isdir(REELVAULT_PROFILE):
            return REELVAULT_PROFILE
        return LEGACY_PROFILE if os.path.isdir(LEGACY_PROFILE) else None

    @staticmethod
    def clone_profile(log=None):
        """Copy the user's Firefox profile (logins included) for Selenium to use."""
        log = log or (lambda m, k="log": None)
        if os.path.isdir(REELVAULT_PROFILE):
            return REELVAULT_PROFILE
        if os.path.isdir(LEGACY_PROFILE):
            log("using existing InstaReelPoster Firefox profile")
            return LEGACY_PROFILE
        srcs = []
        if os.path.isdir(FIREFOX_PROFILES):
            for name in os.listdir(FIREFOX_PROFILES):
                p = os.path.join(FIREFOX_PROFILES, name)
                if os.path.isdir(p) and "release" in name:
                    srcs.append((os.path.getmtime(p), p))
        if not srcs:
            raise RuntimeError("no Firefox profile found — log in to Instagram in Firefox first")
        src = sorted(srcs)[-1][1]
        log("cloning Firefox profile %s (logins included)" % os.path.basename(src))
        skip = {"cache2", "startupCache", "shader-cache", "crashes", "minidumps", "datareporting"}

        def ignore(_d, names):
            return [n for n in names if n in skip]

        shutil.copytree(src, REELVAULT_PROFILE, ignore=ignore, dirs_exist_ok=True)
        for lock in (".parentlock", "lock"):
            try:
                os.remove(os.path.join(REELVAULT_PROFILE, lock))
            except OSError:
                pass
        return REELVAULT_PROFILE

    def start(self):
        profile = self.clone_profile(self.log)
        if not profile:
            raise RuntimeError("no Firefox profile available")
        for lock in (".parentlock", "lock"):
            try:
                os.remove(os.path.join(profile, lock))
            except OSError:
                pass
        opts = Options()
        if self.headless:
            opts.add_argument("-headless")
        opts.add_argument("-no-remote")
        opts.add_argument("-new-instance")
        opts.add_argument("-profile")
        opts.add_argument(profile)
        opts.add_argument("-width=1400")
        opts.add_argument("-height=1000")
        opts.set_preference("dom.webdriver.enabled", False)
        opts.set_preference("useAutomationExtension", False)
        try:
            self.driver = Firefox(options=opts)
        except Exception:
            # Selenium Manager sometimes needs an explicit service on macOS
            gd = shutil.which("geckodriver")
            self.driver = Firefox(options=opts, service=Service(gd) if gd else None)
        self.driver.set_page_load_timeout(60)
        return self.driver

    def stop(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    def ensure(self):
        """Return a live driver, relaunching if the window died."""
        if self.driver:
            try:
                if self.driver.window_handles:
                    return self.driver
            except Exception:
                pass
            self.stop()
        return self.start()

    def logged_in(self):
        d = self.ensure()
        d.get("https://www.instagram.com/")
        time.sleep(6)
        names = {c["name"] for c in d.get_cookies()}
        return "sessionid" in names and "/accounts/login" not in d.current_url

    def active_account(self):
        try:
            return self.driver.execute_script("""
              for (const a of document.querySelectorAll('a[href]')) {
                const r = a.getBoundingClientRect();
                if (r.left < 120 && r.width > 0 && /^\\/[A-Za-z0-9._]+\\/$/.test(a.getAttribute('href'))
                    && !['/reels/','/explore/','/direct/inbox/'].includes(a.getAttribute('href'))) {
                  const img = a.querySelector('img');
                  if (img && img.alt) return img.alt.replace("'s profile picture", "");
                }
              }
              return null;
            """) or "unknown"
        except Exception:
            return "unknown"

    # ---- ui helpers ----
    def _dialog_text(self):
        out = []
        try:
            for dlg in self.driver.find_elements(By.CSS_SELECTOR, 'div[role="dialog"]'):
                t = dlg.text.strip()
                if t:
                    out.append(" | ".join(x for x in t.split("\n") if x.strip())[:400])
        except Exception:
            pass
        return "  ||  ".join(out)

    def _click(self, texts, timeout=25):
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            if self.stopped:
                return None
            try:
                res = self.driver.execute_script(JS_CLICK, list(texts))
            except Exception as e:
                res = "none|%s" % e
            if res.startswith("clicked:"):
                return res.split(":", 1)[1]
            last = res
            time.sleep(1)
        self.log("    no button %s — visible: %s" % (texts, last[:200]))
        return None

    def _dismiss(self, tries=3):
        for _ in range(tries):
            got = self._click(["OK", "Ok", "Got it", "Dismiss", "Close"], timeout=3)
            if not got:
                return
            time.sleep(1.2)

    def _wait_text(self, texts, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            dt = self._dialog_text()
            try:
                dt += " " + (self.driver.find_element(By.TAG_NAME, "body").text or "")
            except Exception:
                pass
            if any(t.lower() in dt.lower() for t in texts):
                return True
            if self.stopped:
                return False
            time.sleep(1)
        return False

    def _open_create(self):
        for attempt in range(3):
            self.driver.get("https://www.instagram.com/")
            time.sleep(5)
            try:
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
            time.sleep(2)
            for sel in ('svg[aria-label="Post"]', 'svg[aria-label="New post"]', 'svg[aria-label="Create"]'):
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    els[0].click()
                    time.sleep(4)
                    if self.driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]'):
                        return True
            self.log("  create dialog not open (attempt %d)" % (attempt + 1))
        return False

    def _set_caption(self, box, text):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
        box.click()
        time.sleep(0.4)
        ok, length, sample = self.driver.execute_script(JS_SET_CAPTION, box, text)
        self.log("  caption insertText=%s len=%d/%d" % (ok, length, len(text)))
        if length >= len(text) * 0.98:
            return True
        from selenium.webdriver.common.action_chains import ActionChains
        box.click()
        time.sleep(0.3)
        ActionChains(self.driver).key_down(Keys.COMMAND).send_keys("a").key_up(Keys.COMMAND) \
            .send_keys(Keys.DELETE).perform()
        time.sleep(0.5)
        for ch in text:
            box.send_keys(ch)
            time.sleep(0.045)
        time.sleep(1)
        final = (box.get_attribute("innerText") or "").strip()
        self.log("  after typing len=%d/%d" % (len(final), len(text)))
        return len(final) >= len(text) * 0.98

    # ---- posting ----
    def post(self, video_path, caption, publish=False):
        """Upload one video. Returns 'posted' | 'dry_run' | 'failed'."""
        self.log("→ %s" % os.path.basename(video_path))
        self.log("  caption: %r" % (caption[:90],))
        if not self._open_create():
            raise RuntimeError("could not open create dialog")

        fi = self.driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
        fi.send_keys(str(video_path))
        if not self._wait_text(["Crop", "Next"], timeout=180):
            raise RuntimeError("no crop screen after handing over the file")
        self.log("  stage 1: crop")
        self._dismiss()
        if not self._click(["Next"], timeout=45):
            raise RuntimeError("no Next on crop screen")
        time.sleep(4)

        for i in range(2):
            if self.driver.find_elements(
                    By.CSS_SELECTOR,
                    'textarea[aria-label*="caption" i], div[contenteditable="true"][aria-label*="caption" i]'):
                break
            if not self._click(["Next"], timeout=25):
                break
            time.sleep(4)

        box = None
        for sel in ('textarea[aria-label*="caption" i]',
                    'div[contenteditable="true"][aria-label*="caption" i]',
                    'textarea[aria-label*="Write a caption" i]'):
            els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                box = els[0]
                break
        if box is None:
            raise RuntimeError("caption box not found")
        if not self._set_caption(box, caption):
            raise RuntimeError("caption did not fully land")

        if not publish:
            self.log("  DRY RUN — stopped before Share")
            return "dry_run"

        if not self._click(["Share"], timeout=30):
            raise RuntimeError("Share button not found")
        self.log("  Share clicked")
        if self._wait_text(["Your post has been shared", "Reel shared", "shared."], timeout=180):
            self.log("  ✓ post shared")
        else:
            self.log("  ? share confirmation not seen", "warn")
        return "posted"
