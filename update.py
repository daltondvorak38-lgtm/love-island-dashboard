#!/usr/bin/env python3
"""
Love Island S8 — Villa Intelligence Dashboard
Live-data updater.

Pipeline:
  1. Scrape recent fan discussion (Reddit r/LoveIslandUSA) and news/recaps
     (Google News RSS for "Love Island USA").
  2. Send the current dashboard state (data.json) + the fresh source text to
     Claude, asking it to return an UPDATED dashboard JSON in the same schema.
  3. Reassert immutable factual "anchor" fields (name, age, job, hometown,
     photo, colour, sex, entered) so the model can never corrupt them.
  4. Write data.json only if the result is valid. On any failure the previous
     data.json is left untouched so the site keeps showing the last good data.

Runs locally or in GitHub Actions. Configured entirely via environment vars:
  ANTHROPIC_API_KEY   (required)  - Claude API key
  REDDIT_CLIENT_ID    (optional)  - Reddit app id  (more reliable scraping)
  REDDIT_CLIENT_SECRET(optional)  - Reddit app secret
  WIKI_ARTICLE        (optional)  - overrides the season; not required here
"""

import os
import sys
import json
import datetime
import urllib.parse

import requests
import feedparser

# Use the OS certificate store when available (fixes local TLS-interception by
# antivirus/corporate proxies on Windows). Harmless / no-op on CI runners.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

# --- config ---------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")

SUBREDDIT = "LoveIslandUSA"
REDDIT_POST_LIMIT = 25
REDDIT_COMMENTS_PER_POST = 12
NEWS_QUERY = '"Love Island USA"'
NEWS_MAX = 15
HTTP_TIMEOUT = 20
USER_AGENT = "love-island-dashboard/1.0 (by /u/villa-intel-bot)"

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 16000

# Immutable factual fields. Whatever the model returns, these win.
# Keyed by islander id. (No status/editorial text here — those are dynamic.)
ANCHORS = {
    "trinity": {"name": "Trinity Tatum", "age": 22, "job": "Model", "home": "Newport News, Virginia", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 1, "color": "#ff5d7e", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-trinity-en-052826-5d6dd6.png"},
    "bryce": {"name": "Bryce Dettloff", "age": 29, "job": "DJ & handyman", "home": "Los Angeles, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 1, "color": "#5b8def", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-bryce-en-052826-fbf5fc.png"},
    "gabriel": {"name": "Gabriel Vasconcelos", "age": 26, "job": "Model", "home": "Miami, FL (Rio de Janeiro)", "flag": "\U0001F1E7\U0001F1F7", "sex": "M", "entered": 1, "color": "#34e3c8", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-gabriel-en-052826-4b30b3.png"},
    "beatriz": {"name": "Beatriz Hatz", "age": 25, "job": "Paralympic track athlete", "home": "San Diego, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 1, "color": "#ffd166", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-beatriz-en-052826-fa46b3.png"},
    "sincere": {"name": "Sincere Rhea", "age": 25, "job": "Athlete", "home": "Cape May, New Jersey", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 1, "color": "#ff8a5b", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-sincere-en-052826-a1cfdb.png"},
    "melanie": {"name": "Melanie Moreno", "age": 24, "job": "Bikini store manager", "home": "Los Angeles, California", "flag": "\U0001F1E9\U0001F1F4", "sex": "F", "entered": 1, "color": "#ff5d7e", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-melanie-en-052826-4c9d16.png"},
    "kc": {"name": "KC Chandler", "age": 23, "job": "Nursing assistant", "home": "Fresno, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 1, "color": "#3ddc84", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-kc-en-052826-1ca4ac.png"},
    "aniya": {"name": "Aniya Harvey", "age": 23, "job": "Marketing", "home": "Tyrone, Georgia", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 1, "color": "#5b8def", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-aniya-en-052826-d1f53b.png"},
    "zach": {"name": "Zach Georgiou", "age": 26, "job": "Digital creator", "home": "Birmingham, England", "flag": "\U0001F1EC\U0001F1E7", "sex": "M", "entered": 1, "color": "#34e3c8", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-zach-en-052826-815ea7.png"},
    "kayda": {"name": "Kayda Reese Bosse", "age": 22, "job": "Server / former model", "home": "Manchester, New Hampshire", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 1, "color": "#ff9f43", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/love-island-usa-season-8-kayda.jpg"},
    "corbin": {"name": "Corbin Mims", "age": 22, "job": "Influencer / villa-rental owner", "home": "Miami, Florida", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 4, "color": "#ff8a5b", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_corbin_4277.jpg"},
    "kenzie": {"name": "Kenzie Annis", "age": 24, "job": "Nurse (new grad)", "home": "Kennesaw, Georgia", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 1, "color": "#ffd166", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-kenzie-en-052826-81070b.png"},
    "sol": {"name": "Sol Mýa", "age": 24, "job": "Model & artist", "home": "Orange, CA (lives in LA)", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 6, "color": "#ff5d7e", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_sol_4277.jpg"},
    "jen": {"name": "Jen Terry", "age": 23, "job": "Model", "home": "Melbourne, FL (lives in LA)", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 6, "color": "#ff8a5b", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_jen_4277.jpg"},
    "caleb": {"name": "Caleb McDaniel", "age": 21, "job": "Model & fitness", "home": "Asheboro, NC (lives in Charleston)", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 6, "color": "#5b8def", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_caleb_4277.jpg"},
    "sean": {"name": "Sean Reifel", "age": 29, "job": "Police officer", "home": "Easton, Pennsylvania", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 1, "color": "#5c5775", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-sean-en-052826-3996ff.png"},
    "vasana": {"name": "Vasana Montgomery", "age": 25, "job": "Business owner", "home": "Beaverton, Oregon", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 0, "color": "#ff4d5e", "photo": "https://media-cldnry.s-nbcnews.com/image/upload/t_fit-760w,f_auto,q_auto:best/rockcms/2026-05/love-island-season-8-vasana-en-052826-a39f47.png"},
    # Casa Amor bombshells (entered Day 17, Episode 17)
    "gal": {"name": "Gal Tshnieder", "age": 29, "job": "Surf shop co-founder", "home": "Los Angeles, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 17, "color": "#06b6d4", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_gal_4277.jpg"},
    "dylan": {"name": "Dylan Wrona", "age": 24, "job": "Model", "home": "Los Angeles, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 17, "color": "#8b5cf6", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_dylan_4277.jpg"},
    "jaiden": {"name": "Jaiden Bacciocco", "age": 22, "job": "Recent college graduate", "home": "Newbury Park, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 17, "color": "#f59e0b", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_jaiden_4277.jpg"},
    "parmida": {"name": "Parmida Keshani", "age": 27, "job": "Personal trainer", "home": "San Antonio, Texas", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 17, "color": "#ec4899", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_parmida_4277.jpg"},
    "tierra": {"name": "Tierra Davis", "age": 25, "job": "Nanny", "home": "Los Angeles, California", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 17, "color": "#10b981", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_tierra_4277.jpg"},
    "carl": {"name": "Carl Schmidt", "age": 28, "job": "Personal trainer", "home": "Denver, Colorado", "flag": "\U0001F1FA\U0001F1F8", "sex": "M", "entered": 17, "color": "#f97316", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_carl_4277.jpg"},
    "amora": {"name": "Amora Cacheé", "age": 21, "job": "Student", "home": "Miami, Florida", "flag": "\U0001F1FA\U0001F1F8", "sex": "F", "entered": 17, "color": "#e11d48", "photo": "https://www.peacocktv.com/sites/peacock/files/styles/scale_862/public/2026/06/pea_lis8_characterportrait_titlesocial_textless_1920x1080_amora_4277.jpg"},
}


# --- scraping -------------------------------------------------------------
def reddit_token():
    """Userless OAuth token if app creds are present, else None."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    try:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print(f"  reddit auth failed ({e}); falling back to public JSON", file=sys.stderr)
        return None


def fetch_reddit():
    """Top posts of the last day + a few top comments each."""
    token = reddit_token()
    if token:
        base = "https://oauth.reddit.com"
        headers = {"User-Agent": USER_AGENT, "Authorization": f"bearer {token}"}
    else:
        base = "https://www.reddit.com"
        headers = {"User-Agent": USER_AGENT}

    out = []
    try:
        listing = requests.get(
            f"{base}/r/{SUBREDDIT}/top.json",
            params={"t": "day", "limit": REDDIT_POST_LIMIT},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        listing.raise_for_status()
        posts = listing.json()["data"]["children"]
    except Exception as e:
        print(f"  reddit listing failed: {e}", file=sys.stderr)
        return out

    for child in posts:
        p = child.get("data", {})
        if p.get("stickied"):
            continue
        title = (p.get("title") or "").strip()
        body = (p.get("selftext") or "").strip()
        score = p.get("score", 0)
        entry = {"title": title, "score": score, "body": body[:600], "comments": []}
        # pull a handful of top comments
        try:
            cm = requests.get(
                f"{base}/r/{SUBREDDIT}/comments/{p.get('id')}.json",
                params={"limit": REDDIT_COMMENTS_PER_POST, "sort": "top", "depth": 1},
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            cm.raise_for_status()
            blocks = cm.json()
            if len(blocks) > 1:
                for c in blocks[1]["data"]["children"]:
                    cd = c.get("data", {})
                    txt = (cd.get("body") or "").strip()
                    if txt and txt != "[deleted]":
                        entry["comments"].append(txt[:300])
                    if len(entry["comments"]) >= REDDIT_COMMENTS_PER_POST:
                        break
        except Exception as e:
            print(f"  reddit comments failed for {p.get('id')}: {e}", file=sys.stderr)
        out.append(entry)
    print(f"  reddit: {len(out)} posts")
    return out


def fetch_news():
    """Recent news / recap headlines via Google News RSS (no key needed)."""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(NEWS_QUERY + " when:3d")
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    out = []
    try:
        feed = feedparser.parse(url)
        for e in feed.entries[:NEWS_MAX]:
            out.append({
                "title": getattr(e, "title", ""),
                "summary": getattr(e, "summary", "")[:500],
                "published": getattr(e, "published", ""),
                "source": getattr(getattr(e, "source", None), "title", ""),
            })
    except Exception as e:
        print(f"  news fetch failed: {e}", file=sys.stderr)
    print(f"  news: {len(out)} items")
    return out


def build_source_text(reddit, news):
    parts = ["=== RECENT NEWS / RECAPS (Google News) ==="]
    for n in news:
        src = f" [{n['source']}]" if n.get("source") else ""
        parts.append(f"- {n['title']}{src}: {n['summary']}")
    parts.append("\n=== REDDIT r/LoveIslandUSA (top, last 24h) ===")
    for p in reddit:
        parts.append(f"\n## ({p['score']} upvotes) {p['title']}")
        if p["body"]:
            parts.append(f"   {p['body']}")
        for c in p["comments"]:
            parts.append(f"   > {c}")
    return "\n".join(parts)


# --- Claude ---------------------------------------------------------------
SYSTEM_PROMPT = """You are the editor of a Love Island USA Season 8 fan dashboard.
You receive the CURRENT dashboard data (JSON) plus fresh source material scraped
from Reddit and entertainment news. Produce an UPDATED dashboard JSON.

Rules:
- Return ONE JSON object only. No markdown, no commentary, no code fences.
- Keep the EXACT same schema and keys as the current data.
- Only change things the sources actually support. If the sources say nothing
  new about an islander or couple, keep their existing entry unchanged.
- You MAY rewrite editorial fields (traits, strength, crit, story for islanders;
  how/events/strength and the 0-100 scores e/l/a_/c/overall for couples) to
  reflect the latest vibe and fan sentiment. Be confident, vivid and editorial —
  this is entertainment, not journalism.
- Update `status` for islanders only on clear evidence: "active", "bombshell",
  "dumped", or "removed". Add `exited` (day number) when someone leaves.
- Keep `couples` in sync with reality: add new couples, drop broken ones.
- Keep `edges` in sync with couples (t = current|former|bomb|rivalry).
- Append new drama to `drama` and new entries to `log` and per-islander `history`.
  Keep older entries; don't delete history.
- Update the top-level `status` summary (episode, day, counts, lastRecouple,
  lastDumping, bombshells, newArrivals).
- NEVER invent islanders who aren't already in the data. NEVER change a person's
  name, age, job, hometown, flag, sex, photo, color or entered day.
- Scores are 0-100 integers. Keep all string values concise (one short sentence).
"""


def call_claude(current_data, source_text):
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_msg = (
        "CURRENT DASHBOARD DATA:\n```json\n"
        + json.dumps(current_data, ensure_ascii=False)
        + "\n```\n\nFRESH SOURCE MATERIAL:\n"
        + source_text
        + "\n\nReturn the full updated dashboard JSON object now."
    )
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    # tolerate accidental code fences
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)


# --- merge / validate -----------------------------------------------------
def enforce_anchors(data):
    """Reassert immutable factual fields; drop unknown islanders."""
    cleaned = []
    seen = set()
    for isl in data.get("islanders", []):
        iid = isl.get("id")
        if iid in ANCHORS:
            isl.update(ANCHORS[iid])  # factual fields always win
            cleaned.append(isl)
            seen.add(iid)
    # make sure no one silently disappeared — re-add from previous data
    return cleaned, seen


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    for key in ("status", "islanders", "couples", "timeline", "drama", "log", "history", "edges"):
        if key not in data:
            raise ValueError(f"missing key: {key}")
    if not isinstance(data["islanders"], list) or len(data["islanders"]) < 10:
        raise ValueError("islanders list looks wrong")
    ids = {i.get("id") for i in data["islanders"]}
    for c in data["couples"]:
        if c.get("a") not in ids or c.get("b") not in ids:
            raise ValueError(f"couple references unknown islander: {c}")
    return True


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        current = json.load(f)
    prev_islanders = {i["id"]: i for i in current.get("islanders", [])}

    print("Scraping sources...")
    reddit = fetch_reddit()
    news = fetch_news()
    if not reddit and not news:
        print("No source material gathered — leaving data.json untouched.", file=sys.stderr)
        sys.exit(0)
    source_text = build_source_text(reddit, news)

    print("Calling Claude...")
    try:
        updated = call_claude(current, source_text)
    except Exception as e:
        print(f"Claude call/parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    # enforce factual anchors, re-add any dropped islanders from previous state
    cleaned, seen = enforce_anchors(updated)
    for iid, isl in prev_islanders.items():
        if iid not in seen:
            isl.update(ANCHORS.get(iid, {}))
            cleaned.append(isl)
    updated["islanders"] = cleaned

    try:
        validate(updated)
    except Exception as e:
        print(f"Validation failed, keeping previous data.json: {e}", file=sys.stderr)
        sys.exit(1)

    updated["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    used = []
    if news:
        used.append(f"google-news({len(news)})")
    if reddit:
        used.append(f"reddit({len(reddit)})")
    updated["sources_used"] = used

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)
    print(f"Wrote data.json — {len(updated['islanders'])} islanders, "
          f"{len(updated['couples'])} couples, {len(updated['drama'])} drama events.")


if __name__ == "__main__":
    main()
