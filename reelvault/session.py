"""Instagram session import: turn a local browser's cookies into a Netscape jar.

Supports Firefox (plaintext DB, no extra deps), Safari and Chrome/Brave
(the latter two need the `cryptography` package).
Never prints secret values — only key names and lengths.
"""
import glob
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HOME = os.path.expanduser("~")
KEY_NAMES = ("sessionid", "ds_user_id", "csrftoken", "mid", "ig_did")


def _netscape(host, name, value, path="/", secure=True, expires=0):
    return "\t".join([
        host, "TRUE" if host.startswith(".") else "FALSE", path or "/",
        "TRUE" if secure else "FALSE", str(int(expires or 0)), name,
        str(value).replace("\n", "").replace("\t", ""),
    ])


def _ff_profile_dirs():
    return sorted(glob.glob(os.path.join(
        HOME, "Library/Application Support/Firefox/Profiles/*/cookies.sqlite")))


def from_firefox():
    out = []
    for db in _ff_profile_dirs():
        try:
            tmp = tempfile.mkdtemp()
            dst = os.path.join(tmp, "c.sqlite")
            shutil.copy2(db, dst)
            con = sqlite3.connect(dst)
            rows = con.execute(
                "SELECT host,name,value,path,expiry,isSecure FROM moz_cookies "
                "WHERE host LIKE '%instagram%'").fetchall()
            con.close()
            shutil.rmtree(tmp, ignore_errors=True)
            if any(r[1] == "sessionid" and r[2] for r in rows):
                out.append(("firefox", os.path.basename(os.path.dirname(db)), rows))
        except Exception:
            continue
    return out


def _chromium_key(label):
    pw = subprocess.check_output(
        ["security", "find-generic-password", "-s", "%s Safe Storage" % label, "-w"]).strip()
    return hashlib.pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, 16)


def from_chromium(browser, rel_base, store_name):
    db = os.path.join(HOME, rel_base, "Default/Cookies")
    if not os.path.exists(db):
        return []
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = _chromium_key(store_name)
    except Exception:
        return []
    try:
        tmp = tempfile.mkdtemp()
        dst = os.path.join(tmp, "Cookies")
        shutil.copy2(db, dst)
        con = sqlite3.connect(dst)
        raw = con.execute(
            "SELECT host_key,name,encrypted_value,value,path,expires_utc,is_secure "
            "FROM cookies WHERE host_key LIKE '%instagram%'").fetchall()
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        return []

    rows = []
    for host, name, enc, val, path, exp, secure in raw:
        value = val
        if not value and enc and enc[:3] in (b"v10", b"v11"):
            dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
            buf = dec.update(enc[3:]) + dec.finalize()
            buf = buf[:-buf[-1]] if buf else buf
            if len(buf) > 32 and buf[:32] == hashlib.sha256(host.encode()).digest():
                buf = buf[32:]
            value = buf.decode("utf-8", "replace")
        if not value:
            continue
        unix = int(exp / 1000000 - 11644473600) if exp else 0
        rows.append((host, name, value, path, max(unix, 0), secure))
    if any(r[1] == "sessionid" for r in rows):
        return [(browser, "Default", rows)]
    return []


def from_safari():
    try:
        import browser_cookie3
    except Exception:
        return []
    try:
        cj = browser_cookie3.safari(domain_name="instagram.com")
        rows = [(c.domain or ".instagram.com", c.name, c.value, c.path or "/",
                 int(c.expires or 0), bool(c.secure)) for c in cj]
        if any(r[1] == "sessionid" for r in rows):
            return [("safari", "default", rows)]
    except Exception:
        pass
    return []


def discover():
    """Return [(browser, profile, rows)] for every browser holding a session."""
    found = []
    found += from_firefox()
    found += from_chromium("brave", "Library/Application Support/BraveSoftware/Brave-Browser", "Brave")
    found += from_chromium("chrome", "Library/Application Support/Google/Chrome", "Chrome")
    found += from_safari()
    return found


def write_jar(rows, path):
    lines = ["# Netscape HTTP Cookie File"]
    for host, name, value, cpath, exp, secure in rows:
        lines.append(_netscape(host, name, value, cpath, secure, exp))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path


def import_jar(path):
    """Find a session and write it to `path`. Returns (browser, profile, keys)."""
    found = discover()
    if not found:
        raise RuntimeError(
            "No Instagram login found on this Mac. Log in to instagram.com in "
            "Firefox, Safari, Chrome or Brave first.")
    browser, profile, rows = found[0]
    write_jar(rows, path)
    keys = [k for k in KEY_NAMES if k in {r[1] for r in rows}]
    return browser, profile, keys
