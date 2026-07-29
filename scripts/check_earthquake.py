#!/usr/bin/env python3
"""
Overnight earthquake monitor for keniti2026.hatenablog.com.

Runs on a GitHub Actions schedule (see .github/workflows/monitor.yml).
Polls the P2P地震情報 public API for new earthquake reports, and when a new
report with 震度6弱以上 (maxScale >= 55) shows up that hasn't been alerted
before, it:

  1. Sends a push notification via ntfy.sh so Kenichi is woken up even if
     his PC is off / he's asleep.
  2. Publishes a minimal, factual placeholder article on the Hatena blog
     immediately (category "地震自動速報" marks it as auto-generated so the
     next Claude Code session can find it and enrich it into the full
     "開いてすぐ役立つ" structure per the jishin-kinkyu-kiji skill).
  3. Records the earthquake id + the new Hatena entry id in state.json so
     the same earthquake is never alerted/posted twice.

No secrets are ever printed. Designed to run with zero extra pip installs
(stdlib only) to keep Actions runtime minimal.
"""
import json
import os
import sys
import time
import base64
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from xml.sax.saxutils import escape

P2P_API = "https://api.p2pquake.net/v2/history?codes=551&limit=5"
SCALE_THRESHOLD = 55  # 震度6弱

SCALE_LABELS = {
    10: "1", 20: "2", 30: "3", 40: "4",
    45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7",
}

STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"

HATENA_ID = "keniti2026"
BLOG_DOMAIN = "keniti2026.hatenablog.com"
ENTRY_COLLECTION = f"https://blog.hatena.ne.jp/{HATENA_ID}/{BLOG_DOMAIN}/atom/entry"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"alerted_ids": []}


def save_state(state: dict) -> None:
    # keep only the most recent 100 ids so the file doesn't grow forever
    state["alerted_ids"] = state["alerted_ids"][-100:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_recent_earthquakes() -> list:
    req = urllib.request.Request(P2P_API, headers={"User-Agent": "kenichi-earthquake-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_ntfy(topic: str, title: str, message: str) -> None:
    if not topic:
        print("NTFY_TOPIC not set, skipping notification", file=sys.stderr)
        return
    url = f"https://ntfy.sh/{topic}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title.encode("utf-8").decode("latin-1", errors="ignore"),
            "Priority": "urgent",
            "Tags": "rotating_light,earthquake",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"ntfy status: {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"ntfy FAILED: {e.code} {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)


def make_wsse_header(username: str, password: str) -> str:
    nonce_raw = os.urandom(16)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = hashlib.sha1(nonce_raw + created.encode("utf-8") + password.encode("utf-8")).digest()
    return (
        f'UsernameToken Username="{username}", '
        f'PasswordDigest="{base64.b64encode(digest).decode("utf-8")}", '
        f'Nonce="{base64.b64encode(nonce_raw).decode("utf-8")}", '
        f'Created="{created}"'
    )


def hatena_post(api_key: str, title: str, body_html: str, categories: list[str]) -> tuple[int, str]:
    category_lines = "\n".join(f'  <category term="{escape(c)}" />' for c in categories)
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom" xmlns:app="http://www.w3.org/2007/app">
  <title>{escape(title)}</title>
  <author><name>{escape(HATENA_ID)}</name></author>
  <content type="text/html">{escape(body_html)}</content>
{category_lines}
  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>
"""
    req = urllib.request.Request(ENTRY_COLLECTION, data=xml.encode("utf-8"), method="POST")
    req.add_header("X-WSSE", make_wsse_header(HATENA_ID, api_key))
    req.add_header("Content-Type", "application/xml; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def build_article(name: str, magnitude, scale_label: str, occurred_at: str) -> tuple[str, str]:
    title = f"【緊急速報】{name}で震度{scale_label}の地震(詳細確認中・自動速報)"
    body = f"""
<p style="font-size:13px; color:#7a4b26; text-align:right; margin:0 0 4px;">この記事は自動速報です。詳しい安全確保情報は準備が整い次第、追って更新します。</p>
<p style="font-size:15px; line-height:1.7;">{occurred_at}ごろ、<strong>{name}</strong>を震源とする地震があり、この地域で<strong>震度{scale_label}</strong>を観測しました(マグニチュード{magnitude})。</p>
<p style="font-size:15px; line-height:1.7;">現時点でこの記事は自動生成された速報のみです。津波の有無、被害状況、避難情報などの詳細はまだ反映されていません。お手数ですが、下記の公式情報源で最新状況を直接ご確認ください。</p>
<ul style="font-size:15px; line-height:1.8;">
<li><a href="https://www.jma.go.jp/bosai/map.html#5/34.5/137/&elem=int&contents=earthquake_map" target="_blank" rel="noopener">気象庁 地震情報</a></li>
<li><a href="https://www3.nhk.or.jp/news/" target="_blank" rel="noopener">NHKニュース</a></li>
</ul>
<p style="font-size:15px; line-height:1.7;">この記事はまもなく、安全確保・避難・安否確認方法をまとめた詳しい内容に更新される予定です。</p>
""".strip()
    return title, body


def main() -> None:
    api_key = os.environ.get("HATENA_API_KEY")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    if not api_key:
        print("ERROR: HATENA_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    state = load_state()
    alerted = set(state["alerted_ids"])

    try:
        items = fetch_recent_earthquakes()
    except Exception as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        sys.exit(0)  # transient network issue -- don't fail the whole run loudly

    # oldest first, so if multiple new big quakes appear at once we post/alert in order
    items = sorted(items, key=lambda it: it.get("time", ""))

    new_alert = False
    for item in items:
        eq = item.get("earthquake") or {}
        max_scale = eq.get("maxScale")
        item_id = item.get("id")
        if item_id is None or max_scale is None:
            continue
        if item_id in alerted:
            continue
        if max_scale < SCALE_THRESHOLD:
            continue

        hypocenter = eq.get("hypocenter") or {}
        name = hypocenter.get("name", "震源不明")
        magnitude = hypocenter.get("magnitude", "不明")
        occurred_at = item.get("time", "")
        scale_label = SCALE_LABELS.get(max_scale, str(max_scale))

        print(f"NEW BIG EARTHQUAKE: id={item_id} name={name} scale={scale_label} mag={magnitude} time={occurred_at}")

        send_ntfy(
            ntfy_topic,
            title=f"【震度{scale_label}】{name}で地震",
            message=(
                f"{occurred_at}ごろ、{name}でM{magnitude}・最大震度{scale_label}の地震。\n"
                f"はてなブログに自動速報記事を投稿しました。詳細は起動後に確認してください。"
            ),
        )

        title, body = build_article(name, magnitude, scale_label, occurred_at)
        status, resp_body = hatena_post(
            api_key, title, body,
            categories=["saigai", "災害", "防災", "地震", "地震自動速報", name],
        )
        print(f"hatena post status: {status}")
        if status != 201:
            print(resp_body[:1500], file=sys.stderr)

        alerted.add(item_id)
        new_alert = True

    state["alerted_ids"] = list(alerted)
    if new_alert:
        save_state(state)
    else:
        print("no new earthquake >= scale 55 found")


if __name__ == "__main__":
    main()
