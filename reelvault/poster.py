"""Post reels to Instagram through the real web UI.

Selenium drives Instagram's own upload dialog inside a clone of the user's
Firefox profile, so no password is ever stored or typed, and the account is
whatever that session is logged into.

Design rules (learned the hard way):

* every stage is screenshotted into ``~/.reelvault/shots/<run>/`` and logged,
* a security gate / login wall is reported as :class:`PosterBlocked` instead of
  looking like "the create button moved",
* the caption is verified by length before Share, and a short caption aborts the
  post (a published reel cannot be un-published),
* nothing is published unless ``publish=True``.
"""
import os
import re
import shutil
import time
from pathlib import Path

from selenium.common.exceptions import (NoSuchWindowException, TimeoutException,
                                        WebDriverException)
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
SHOTS_ROOT = os.path.join(HOME, ".reelvault", "shots")

# URL fragments that mean "Instagram will not let this session do anything".
GATE_URLS = (
    ("update_risky_contactpoint", "Instagram wants the account email updated"),
    ("/challenge/", "Instagram is showing a security challenge"),
    ("/accounts/suspended", "the account is suspended"),
    ("/accounts/login", "the session is not logged in"),
    ("/emailsignup", "the session is not logged in"),
    ("/accounts/activity", "the account is restricted"),
)


class PosterBlocked(RuntimeError):
    """Session-level stop: the account needs the user, not another retry."""


JS_CLICK = r"""
const want = arguments[0];
const root = document.querySelector('div[role="dialog"]') || document.body;
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
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

# Set a caption on either flavour of field. Instagram has shipped both a
# contenteditable div and a <textarea> for the same box.
JS_SET_CAPTION = r"""
const el = arguments[0], text = arguments[1];
if (!el) return [false, 0, ''];
el.focus();
if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, text);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return [true, (el.value || '').length, (el.value || '').slice(0, 60)];
}
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

# Multi-strategy active-account detection. The left rail profile slot is the
# source of truth; feed content is not.
JS_ACCOUNT = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const skip = new Set(['explore', 'reels', 'direct', 'accounts', 'emails', 'settings',
                      'your_activity', 'api', 'about', 'legal', 'developer', 'explore']);
const cands = [];
for (const a of document.querySelectorAll('a[href]')) {
  const href = a.getAttribute('href') || '';
  const m = href.match(/^\/([A-Za-z0-9._]{2,30})\/?$/);
  if (!m) continue;
  if (skip.has(m[1])) continue;
  const r = a.getBoundingClientRect();
  const img = a.querySelector('img');
  cands.push({h: m[1], x: r.left, w: r.width, alt: img ? (img.alt || '') : '',
              aria: a.getAttribute('aria-label') || '', text: norm(a.innerText)});
}
// 1) an avatar whose alt names the account
for (const c of cands) {
  if (/profile picture/i.test(c.alt)) return c.alt.replace(/\u2019s profile picture.*/i, '').replace(/'s profile picture.*/i, '').trim();
  if (/profile picture/i.test(c.aria)) return c.aria.replace(/\u2019s profile picture.*/i, '').replace(/'s profile picture.*/i, '').trim();
}
// 2) the profile link pinned to the left rail
const rail = cands.filter(c => c.x < 120 && c.w > 0);
if (rail.length) return rail[0].h;
// 3) any stray avatar anywhere
const anyAvatar = [...document.querySelectorAll('img[alt*="profile picture" i]')];
if (anyAvatar.length) {
  return anyAvatar[0].alt.replace(/\u2019s profile picture.*/i, '').replace(/'s profile picture.*/i, '').trim();
}
// 4) last resort: link that points at a profile and holds an image
const withImg = cands.filter(c => c.alt || c.aria);
return withImg.length ? withImg[0].h : null;
"""

JS_CREATE = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const wanted = ['New post', 'Post', 'Create', 'New reel'];
const out = [];
// svg with an aria-label is how Instagram labels the rail icon
for (const svg of document.querySelectorAll('svg[aria-label]')) {
  const label = svg.getAttribute('aria-label');
  if (!wanted.includes(label)) continue;
  const clickable = svg.closest('a, [role="button"], button, div[role="link"]') || svg;
  const r = clickable.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) continue;
  out.push([clickable, 'svg:' + label]);
}
// text-based fallback for localised / restructured rails
if (!out.length) {
  for (const n of document.querySelectorAll('a, [role="button"], button, div[role="link"]')) {
    const t = norm(n.innerText);
    if (!wanted.includes(t)) continue;
    const r = n.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    out.push([n, 'text:' + t]);
  }
}
if (!out.length) {
  return 'none|' + [...document.querySelectorAll('svg[aria-label]')]
    .map(s => s.getAttribute('aria-label')).slice(0, 30).join(' / ');
}
out[out.length - 1][0].click();
return 'clicked:' + out[out.length - 1][1];
"""


class Poster(object):
    def __init__(self, log=None, headless=False, shots=True):
        self.log = log or (lambda m, k="log": None)
        self.headless = headless
        self.driver = None
        self.stopped = False
        self.problem = ""
        self.shots_dir = None
        self.run_shots = shots
        self._shot_n = 0

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

    @staticmethod
    def refresh_profile(log=None):
        """Re-clone the newest Firefox profile over the working copy.

        Needed after the user logs in again (or clears a security checkpoint):
        the clone holds the *old* cookies, so posting fails while the browser
        they just used works fine.
        """
        log = log or (lambda m, k="log": None)
        srcs = []
        if os.path.isdir(FIREFOX_PROFILES):
            for name in os.listdir(FIREFOX_PROFILES):
                p = os.path.join(FIREFOX_PROFILES, name)
                if os.path.isdir(p) and "release" in name:
                    srcs.append((os.path.getmtime(p), p))
        if not srcs:
            raise RuntimeError("no Firefox profile found")
        src = sorted(srcs)[-1][1]
        skip = {"cache2", "startupCache", "shader-cache", "crashes", "minidumps", "datareporting"}

        def ignore(_d, names):
            return [n for n in names if n in skip]

        shutil.copytree(src, REELVAULT_PROFILE, ignore=ignore, dirs_exist_ok=True)
        for lock in (".parentlock", "lock"):
            try:
                os.remove(os.path.join(REELVAULT_PROFILE, lock))
            except OSError:
                pass
        log("refreshed Firefox profile clone from %s" % os.path.basename(src))
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

    # ---- evidence ----
    def shot(self, name):
        """Screenshot every stage; the log names it, the file shows it."""
        if not self.run_shots or not self.driver:
            return None
        try:
            if not self.shots_dir:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                self.shots_dir = os.path.join(SHOTS_ROOT, stamp)
                os.makedirs(self.shots_dir, exist_ok=True)
            self._shot_n += 1
            path = os.path.join(self.shots_dir, "%02d-%s.png" % (self._shot_n, re.sub(r"\W+", "-", name)[:40]))
            self.driver.save_screenshot(path)
            return path
        except Exception:
            return None

    def _body_text(self):
        try:
            return (self.driver.find_element(By.TAG_NAME, "body").text or "")[:400]
        except Exception:
            return ""

    def gate(self):
        """URL/body check for anything that means 'stop and ask a human'."""
        try:
            url = self.driver.current_url or ""
        except Exception:
            return None
        for frag, reason in GATE_URLS:
            if frag in url:
                body = self._body_text().replace("\n", " ")[:200]
                return PosterBlocked("%s — %s (%s)" % (reason, body, url.split("?")[0]))
        body = self._body_text()
        if "Your email may not be secure" in body:
            return PosterBlocked("Instagram wants the account email updated — " + body[:160])
        if "We suspended your account" in body or "Your account has been suspended" in body:
            return PosterBlocked("the account is suspended")
        return None

    def check_gate(self):
        g = self.gate()
        if g:
            self.problem = str(g)
            raise g

    def logged_in(self):
        """True when the cloned session can actually use Instagram."""
        d = self.ensure()
        d.get("https://www.instagram.com/")
        end = time.time() + 25
        while time.time() < end:
            time.sleep(1)
            g = self.gate()
            if g:
                self.problem = str(g)
                self.log("session blocked: %s" % str(g)[:200], "error")
                self.shot("gate")
                return False
            if d.find_elements(By.CSS_SELECTOR, 'svg[aria-label], a[href^="/direct/"]'):
                break
        names = {c["name"] for c in d.get_cookies()}
        if "sessionid" not in names:
            self.problem = "no sessionid cookie — the cloned profile has no Instagram login"
            return False
        if self.gate():
            return False
        self.problem = ""
        return True

    def active_account(self):
        for _ in range(3):
            try:
                who = self.driver.execute_script(JS_ACCOUNT)
            except Exception:
                who = None
            if who:
                return who.lstrip("@").strip()
            time.sleep(2)
        return ""

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
            if isinstance(res, str) and res.startswith("clicked:"):
                return res.split(":", 1)[1]
            last = res
            time.sleep(1)
        self.log("    no button %s — visible: %s" % (list(texts), str(last)[:200]), "warn")
        return None

    def _dismiss(self, tries=3):
        """Overlay notices ('Video posts are now shared as reels') grey out Next."""
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

    def _wait_files(self, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                if els:
                    return els
            except Exception:
                pass
            time.sleep(1)
        return []

    def _open_create(self):
        last = ""
        for attempt in range(3):
            self.driver.get("https://www.instagram.com/")
            time.sleep(4)
            self.check_gate()
            try:
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
            time.sleep(1.5)
            try:
                last = self.driver.execute_script(JS_CREATE)
            except Exception as e:
                last = "none|%s" % e
            if isinstance(last, str) and last.startswith("clicked:"):
                if self._wait_files(20):
                    self.log("  create dialog: %s" % last.split(":", 1)[1])
                    self.shot("create-dialog")
                    return True
                # a leftover "Discard this post?" from an aborted run blocks it
                if self._click(["Discard", "Discard post"], timeout=3):
                    time.sleep(3)
                    if self._wait_files(15):
                        return True
            self.log("  create dialog not open (attempt %d) — %s" % (attempt + 1, str(last)[:120]), "warn")
            self.shot("create-failed-%d" % (attempt + 1))
            time.sleep(2)
        raise RuntimeError("could not open create dialog — %s" % str(last)[:160])

    def _caption_box(self):
        selectors = (
            'textarea[aria-label*="caption" i]',
            'div[contenteditable="true"][aria-label*="caption" i]',
            'textarea[placeholder*="caption" i]',
            'div[contenteditable="true"][data-lexical-editor="true"]',
            'div[role="dialog"] div[contenteditable="true"]',
        )
        for sel in selectors:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        return el
            except Exception:
                continue
        return None

    def _set_caption(self, box, text):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
        box.click()
        time.sleep(0.4)
        ok, length, sample = self.driver.execute_script(JS_SET_CAPTION, box, text)
        self.log("  caption setter=%s len=%d/%d" % (ok, length, len(text)))
        if length >= max(1, len(text) * 0.98):
            return True
        # React re-renders the node mid-typing; the slow path is the reliable one.
        self.log("  falling back to slow keystroke typing", "warn")
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
        try:
            final = (box.get_attribute("value") or box.get_attribute("innerText") or "").strip()
        except Exception:
            final = ""
        self.log("  after typing len=%d/%d" % (len(final), len(text)))
        return len(final) >= max(1, len(text) * 0.98)

    def _abort_dialog(self):
        """Leave the composer empty so the next item starts clean."""
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1.5)
            if self._click(["Discard", "Discard post"], timeout=5):
                time.sleep(1.5)
                return True
        except Exception:
            pass
        return False

    # ---- posting ----
    def post(self, video_path, caption, publish=False):
        """Upload one video. Returns 'posted' | 'dry_run' | 'failed'."""
        name = os.path.basename(video_path)
        self.log("→ %s" % name)
        self.log("  caption (%d chars): %r" % (len(caption), caption[:120]))
        if not self._open_create():
            raise RuntimeError("could not open create dialog")

        try:
            fi = self.driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
            fi.send_keys(str(video_path))
            self.log("  file handed to the composer")
        except Exception as e:
            raise RuntimeError("could not hand the file to Instagram: %s" % e)

        if not self._wait_text(["Crop", "Next"], timeout=180):
            self.check_gate()
            raise RuntimeError("no crop screen after the upload (file rejected?)")
        self.check_gate()
        self.log("  stage 1: crop")
        self.shot("stage1-crop")
        self._dismiss()
        if not self._click(["Next"], timeout=45):
            raise RuntimeError("no Next on the crop screen — visible: %s" % self._dialog_text()[:200])
        time.sleep(4)
        self.log("  stage 2: edit")
        self.shot("stage2-edit")

        # Stage 3 (caption) arrives either straight away or after another Next.
        for _ in range(3):
            if self._caption_box():
                break
            if not self._click(["Next"], timeout=20):
                break
            time.sleep(4)

        box = self._caption_box()
        if box is None:
            self.shot("stage3-no-caption")
            raise RuntimeError("caption box not found — visible: %s" % self._dialog_text()[:200])
        self.check_gate()
        if caption:
            if not self._set_caption(box, caption):
                self.shot("caption-mismatch")
                self._abort_dialog()
                raise RuntimeError("caption did not fully land — aborted before Share")
        self.log("  stage 3: caption ok")
        self.shot("stage3-caption")

        if not publish:
            self.log("  DRY RUN — stopped before Share")
            return "dry_run"

        if not self._click(["Share"], timeout=30):
            raise RuntimeError("Share button not found — visible: %s" % self._dialog_text()[:200])
        self.log("  Share clicked")
        self.shot("share-clicked")
        if self._wait_text(["Your post has been shared", "Reel shared", "shared."], timeout=180):
            self.log("  ✓ post shared")
            self.shot("shared")
            return "posted"
        if self._wait_text(["Something went wrong", "couldn't be posted", "Try again"], timeout=5):
            self.shot("share-error")
            raise RuntimeError("Instagram reported a share error — %s" % self._dialog_text()[:160])
        self.log("  ? share confirmation not seen — check the account", "warn")
        self.shot("share-unconfirmed")
        return "posted"
