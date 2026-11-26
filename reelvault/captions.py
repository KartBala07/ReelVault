"""Caption building for posting reels back to Instagram.

Scraped reels usually arrive with an **empty** `description` (Instagram returns
one for a minority of posts), so handing `meta["description"]` straight to the
composer publishes either nothing or a filename. This module builds the caption
from a template with explicit placeholders, always credits the original account,
appends hashtags that are not already in the text, and enforces Instagram's
2,200-character limit at a word boundary.

Placeholders (unknown ones are left untouched, so `{braces}` in real captions
survive):

    {caption}   original description, or the title when the description is empty
    {source}    the account the reel was scraped from, without '@'
    {credit}    "via @<source>", or "" when unknown
    {hashtags}  " #tag #tag" for tags not already present in the text
    {likes}     223.8k  (compact)
    {shortcode} DcWybFOqvDm
    {url}       the original post URL
    {date}      2026-08-22
"""
import re

MAX_CAPTION = 2200

HASHTAG_RE = re.compile(r"#([\w\u00c0-\u024f]{1,60})", re.UNICODE)
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

DEFAULT_TEMPLATE = "{caption}\n\n{credit} {hashtags}"


def pretty_likes(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000.0)
    if n >= 1_000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def hashtags_in(text):
    """Hashtags already present, lowercased, in order of appearance."""
    seen = []
    for tag in HASHTAG_RE.findall(text or ""):
        low = tag.lower()
        if low not in seen:
            seen.append(low)
    return seen


def parse_tags(tags):
    """Accept 'a, b #c' or ['a', '#b'] -> ['a', 'b'] (lowercased, de-duped)."""
    if isinstance(tags, str):
        raw = re.split(r"[\s,]+", tags)
    else:
        raw = list(tags or [])
    out = []
    for t in raw:
        t = str(t).strip().lstrip("#").strip()
        if t and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out


def clean(text, limit=MAX_CAPTION):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # drop lines left over as bare separators, but keep intentional blank lines
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln == "" or ln.strip(" .,-–—|·")]
    text = "\n".join(lines).strip()
    if len(text) > limit:
        cut = text[:limit]
        space = cut.rfind(" ")
        if space > limit * 0.7:
            cut = cut[:space]
        text = cut.rstrip(" ,.;-") + "…"
    return text


def values(meta, source=None):
    meta = meta or {}
    desc = (meta.get("description") or "").strip()
    title = (meta.get("title") or "").strip()
    src = (source or meta.get("username") or "").strip().lstrip("@")
    return {
        "caption": desc or title,
        "source": src,
        "credit": ("via @%s" % src) if src else "",
        "hashtags": "",
        "likes": pretty_likes(meta.get("likes")),
        "shortcode": meta.get("shortcode") or "",
        "url": meta.get("post_url") or "",
        "date": (meta.get("date") or "")[:10],
    }


def _render(template, vals):
    def sub(m):
        key = m.group(1)
        return str(vals[key]) if key in vals else m.group(0)
    return PLACEHOLDER_RE.sub(sub, template or "")


def build(meta, template="", tags=None, source=None, limit=MAX_CAPTION):
    """Return (caption, info) where info explains what went into it."""
    vals = values(meta, source)
    tpl = (template or "").strip() or DEFAULT_TEMPLATE
    # First pass with no hashtags, so taps already inside the caption are not
    # duplicated at the end.
    draft = _render(tpl, vals)
    present = hashtags_in(draft)
    extra = [t for t in parse_tags(tags) if t.lower() not in present]
    vals["hashtags"] = (" " + " ".join("#" + t for t in extra)) if extra else ""
    caption = _render(tpl, vals)
    if extra and "{hashtags}" not in tpl:
        caption = caption.rstrip() + " " + vals["hashtags"].strip()
    caption = clean(caption, limit)
    info = {
        "chars": len(caption),
        "from_caption": bool(vals["caption"]),
        "source": vals["source"],
        "tags": extra,
        "truncated": len(_render(tpl, vals)) > limit,
    }
    return caption, info
