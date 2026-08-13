#!/usr/bin/env python3
"""
Overnight earthquake monitor for keniti2026.hatenablog.com.

Runs on a GitHub Actions schedule (see .github/workflows/monitor.yml).
Polls the P2P地震情報 public API for new earthquake reports, and when a new
report with 震度6弱以上 (maxScale >= 55) shows up that hasn't been handled
before, it:

  1. Sends a push notification via ntfy.sh so Kenichi is woken up even if
     his PC is off / he's asleep.
  2. Publishes an immediately-useful article on the Hatena blog: tsunami
     status first (life-critical), then what to do right now, then the
     facts, then official sources and safety-confirmation numbers.
  3. Records the earthquake id in state.json so the same earthquake is
     never alerted/posted twice.

🚨 第一目標(2026-08-14 健一さん明示指示):
   **「大きな地震が起きたときに、被災した方が困らないように情報を届ける」**
   これを最優先にする。ブログの通常記事ルール(1記事1カテゴリ・画像3枚・出典元表記など)は
   この記事には適用しない。届くことと、開いてすぐ役に立つことを優先する。
   画像は入れない(災害情報はテキストが主 —— 2026-07-29/2026-08-14 健一さん指示)。

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

# 投稿は失敗しても諦めない。1回の実行で3回まで試し、それでも駄目なら state に
# 「投稿済み」を記録せずに終わるので、5分後の次回実行でまた最初から試行する。
POST_RETRIES = 3
POST_BACKOFF_SECONDS = (3, 8)  # 1回目失敗後3秒、2回目失敗後8秒あけて再試行

STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"

HATENA_ID = "keniti2026"
BLOG_DOMAIN = "keniti2026.hatenablog.com"
ENTRY_COLLECTION = f"https://blog.hatena.ne.jp/{HATENA_ID}/{BLOG_DOMAIN}/atom/entry"


def load_state() -> dict:
    state = {"alerted_ids": [], "posted_ids": [], "post_failed_notified": []}
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in state:
                    value = loaded.get(key)
                    if isinstance(value, list):
                        state[key] = value
        except (ValueError, OSError) as e:
            # 壊れた state で監視が止まる方が危険なので、読めなければ空で続行する
            print(f"WARNING: state.json unreadable ({e}), starting fresh", file=sys.stderr)
    return state


def save_state(state: dict) -> None:
    # keep only the most recent 100 ids so the file doesn't grow forever
    for key in ("alerted_ids", "posted_ids", "post_failed_notified"):
        state[key] = state.get(key, [])[-100:]
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
    except Exception as e:
        print(f"ntfy FAILED: {e}", file=sys.stderr)


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


def hatena_post(api_key: str, title: str, body_html: str, categories: list,
                draft: bool = False) -> tuple:
    category_lines = "\n".join(f'  <category term="{escape(c)}" />' for c in categories)
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom" xmlns:app="http://www.w3.org/2007/app">
  <title>{escape(title)}</title>
  <author><name>{escape(HATENA_ID)}</name></author>
  <content type="text/html">{escape(body_html)}</content>
{category_lines}
  <app:control>
    <app:draft>{"yes" if draft else "no"}</app:draft>
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
    except Exception as e:
        # タイムアウト・DNS障害など。HTTPErrorではないので 0 を返して呼び出し側でリトライさせる
        return 0, f"{type(e).__name__}: {e}"


def hatena_post_with_retry(api_key: str, title: str, body_html: str,
                           categories: list) -> tuple:
    """201が返るまで最大POST_RETRIES回試す。最後の (status, body) を返す。"""
    status, resp_body = 0, ""
    for attempt in range(1, POST_RETRIES + 1):
        status, resp_body = hatena_post(api_key, title, body_html, categories)
        print(f"hatena post attempt {attempt}/{POST_RETRIES}: status={status}")
        if status == 201:
            return status, resp_body
        print(f"  post failed: {resp_body[:400]}", file=sys.stderr)
        if attempt < POST_RETRIES:
            time.sleep(POST_BACKOFF_SECONDS[min(attempt - 1, len(POST_BACKOFF_SECONDS) - 1)])
    return status, resp_body


# 🚨 津波は命に関わるので、記事の一番上に、一番目立つ形で出す。
# P2P地震情報 v2 の earthquake.domesticTsunami の取りうる値に対応させてある。
TSUNAMI_INFO = {
    "None": ("この地震による津波の心配はありません。",
             "#2e7d32", "#e8f5e9"),
    "NonEffective": ("若干の海面変動が予想されますが、被害の心配はありません。",
                     "#2e7d32", "#e8f5e9"),
    "Unknown": ("津波の有無は、この速報の時点では分かっていません。海岸付近の方は念のため海から離れてください。",
                "#e65100", "#fff3e0"),
    "Checking": ("津波の有無は現在調査中です。海岸付近の方は、結果を待たずに海から離れてください。",
                 "#e65100", "#fff3e0"),
    "Watch": ("【津波注意報】ただちに海岸から離れ、高台へ避難してください。",
              "#c62828", "#ffebee"),
    "Warning": ("【津波警報・大津波警報】ただちに高台や避難ビルへ避難してください。すぐに逃げてください。",
                "#c62828", "#ffebee"),
}


def build_article(name, magnitude, scale_label, occurred_at, tsunami) -> tuple:
    """被災直後の人が開いて、その場ですぐ使える順番で組み立てる。

    並び順は「命に関わる順」: 津波 → 今すぐやること → 地震の事実 → 公式情報 → 安否確認。
    画像は入れない(災害情報はテキストが主、健一さん指示)。
    """
    title = f"【緊急速報】{name}で震度{scale_label}の地震｜津波情報と今すぐやるべきこと"

    tsunami_text, tsunami_color, tsunami_bg = TSUNAMI_INFO.get(
        tsunami,
        ("津波の有無は、この速報の時点では分かっていません。海岸付近の方は念のため海から離れてください。",
         "#e65100", "#fff3e0"),
    )

    body = f"""
<p style="font-size:20px; font-weight:bold; line-height:1.6; color:{tsunami_color}; background:{tsunami_bg}; border:2px solid {tsunami_color}; padding:14px; margin:0 0 16px;">津波情報：{tsunami_text}</p>

<p style="font-size:16px; line-height:1.8;">{occurred_at}ごろ、<strong>{name}</strong>を震源とする地震があり、最大震度<strong>{scale_label}</strong>を観測しました（マグニチュード{magnitude}）。</p>

<h2 style="font-size:19px; line-height:1.6; border-left:6px solid #c62828; padding-left:10px; margin:24px 0 12px;">今すぐやること</h2>

<ol style="font-size:16px; line-height:2.0;">
<li><strong>身の安全を最優先に。</strong>揺れが続く間は机の下などで頭を守る。</li>
<li><strong>海の近く・川沿いなら、すぐ高台へ。</strong>津波は繰り返し来ます。警報の続報を待たずに動く。</li>
<li><strong>火を止める。</strong>ガスの元栓、暖房器具、電気ストーブを切る。</li>
<li><strong>ガスのにおいがしたら火を使わない。</strong>換気して、その場で電気のスイッチも触らない。</li>
<li><strong>靴を履く。</strong>割れたガラスで足を切ると、その後の避難ができなくなります。</li>
<li><strong>ブレーカーを落としてから避難する。</strong>停電が復旧したときの通電火災を防ぎます。</li>
<li><strong>建物が傾いている・壁に大きな亀裂があるなら、中に留まらない。</strong></li>
</ol>

<h2 style="font-size:19px; line-height:1.6; border-left:6px solid #1565c0; padding-left:10px; margin:24px 0 12px;">家族の安否を確認する</h2>

<p style="font-size:16px; line-height:1.8;">被災地では電話がつながりにくくなります。声を聞こうと電話を掛け続けるより、次の手段のほうが確実に届きます。</p>

<ul style="font-size:16px; line-height:2.0;">
<li><strong>災害用伝言ダイヤル「171」</strong>— 「171」に電話し、案内に従って自宅の電話番号を入力すると、伝言を録音・再生できます。</li>
<li><strong>災害用伝言板（web171）</strong>：<a href="https://www.web171.jp/" target="_blank" rel="noopener">https://www.web171.jp/</a></li>
<li><strong>SNSやメッセージアプリの文字連絡</strong>— 音声通話より軽く、つながりやすいです。</li>
</ul>

<h2 style="font-size:19px; line-height:1.6; border-left:6px solid #1565c0; padding-left:10px; margin:24px 0 12px;">最新の状況を確認する（公式情報）</h2>

<p style="font-size:16px; line-height:1.8;">この記事は地震発生を自動で検知して出した速報です。被害状況・避難所・津波の続報は、必ず下記の公式情報でご確認ください。</p>

<ul style="font-size:16px; line-height:2.0;">
<li><a href="https://www.jma.go.jp/bosai/map.html#5/34.5/137/&amp;elem=int&amp;contents=earthquake_map" target="_blank" rel="noopener">気象庁 地震情報</a>（震度・津波の公式発表）</li>
<li><a href="https://www.jma.go.jp/bosai/tsunami/" target="_blank" rel="noopener">気象庁 津波情報</a></li>
<li><a href="https://www3.nhk.or.jp/news/" target="_blank" rel="noopener">NHKニュース</a></li>
<li><a href="https://www.jma.go.jp/bosai/map.html" target="_blank" rel="noopener">気象庁 防災情報マップ</a></li>
</ul>

<p style="font-size:14px; line-height:1.7; color:#555; border-top:1px solid #ccc; padding-top:12px; margin-top:24px;">この記事は地震発生を自動検知して公開した速報です。上の「今すぐやること」と安否確認の方法は、地震の規模によらず共通して有効な内容を載せています。個別の被害状況・避難指示は反映されていないため、お住まいの自治体と気象庁の発表を必ずご確認ください。記事は準備が整い次第、詳しい内容に更新します。</p>
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
    posted = set(state["posted_ids"])
    failed_notified = set(state["post_failed_notified"])

    try:
        items = fetch_recent_earthquakes()
    except Exception as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        sys.exit(0)  # transient network issue -- don't fail the whole run loudly

    # oldest first, so if multiple new big quakes appear at once we post/alert in order
    items = sorted(items, key=lambda it: it.get("time", ""))

    changed = False
    handled_any = False
    pending_post = []
    for item in items:
        eq = item.get("earthquake") or {}
        max_scale = eq.get("maxScale")
        item_id = item.get("id")
        if item_id is None or max_scale is None:
            continue
        if max_scale < SCALE_THRESHOLD:
            continue
        # 通知も投稿も済んでいる地震だけをスキップする。
        # 投稿だけ失敗している地震は、次回以降の実行で必ず再試行される。
        if item_id in alerted and item_id in posted:
            continue

        hypocenter = eq.get("hypocenter") or {}
        name = hypocenter.get("name") or "震源不明"
        magnitude = hypocenter.get("magnitude", "不明")
        # earthquake.time は地震の発生時刻、item.time は発表時刻。読者に見せるのは発生時刻。
        occurred_at = eq.get("time") or item.get("time", "")
        tsunami = eq.get("domesticTsunami") or "Unknown"
        scale_label = SCALE_LABELS.get(max_scale, str(max_scale))

        handled_any = True
        print(f"BIG EARTHQUAKE: id={item_id} name={name} scale={scale_label} "
              f"mag={magnitude} tsunami={tsunami} time={occurred_at}")

        if item_id not in alerted:
            send_ntfy(
                ntfy_topic,
                title=f"【震度{scale_label}】{name}で地震",
                message=(
                    f"{occurred_at}ごろ、{name}でM{magnitude}・最大震度{scale_label}の地震。\n"
                    f"津波: {tsunami}\n"
                    f"はてなブログに自動速報記事を投稿します。詳細は起動後に確認してください。"
                ),
            )
            alerted.add(item_id)
            changed = True

        if item_id in posted:
            continue

        title, body = build_article(name, magnitude, scale_label, occurred_at, tsunami)
        # 🚨 カテゴリー: 先頭が「カテゴリー」、2番目以降は検索用のタグ(はてなの仕様)。
        # 2026-08-09のサイト全体ルールに合わせ、先頭は日本語表示名の「災害・防災情報」にする。
        # 旧実装は廃止済みASCIIスラッグ `saigai` が先頭で、サイトのカテゴリー体系から
        # 外れたアーカイブURLへ入ってしまっていた(2026-08-14修正)。
        # 「1記事1カテゴリ」の原則より、被災者が探し当てられることを優先してタグを併記する
        # (2026-08-14 健一さん指示「必要ならカテゴリーを増やしてもいい」)。
        status, resp_body = hatena_post_with_retry(
            api_key, title, body,
            categories=["災害・防災情報", "地震", "地震自動速報", name, "津波", "安否確認"],
        )

        if status == 201:
            posted.add(item_id)
            changed = True
            print(f"posted OK: id={item_id}")
        else:
            # 投稿できていないので posted には入れない = 5分後の次回実行で再試行される。
            pending_post.append(item_id)
            print(f"ERROR: post failed after {POST_RETRIES} attempts (status={status}). "
                  f"次回実行(5分後)で再試行する", file=sys.stderr)
            if item_id not in failed_notified:
                send_ntfy(
                    ntfy_topic,
                    title="⚠️ 地震速報の投稿に失敗",
                    message=(
                        f"{name}・震度{scale_label}の速報記事をはてなブログへ投稿できませんでした"
                        f"(status={status})。5分後に自動で再試行します。\n"
                        f"復旧しない場合はAPIキーの失効を確認してください。"
                    ),
                )
                failed_notified.add(item_id)
                changed = True

    state["alerted_ids"] = list(alerted)
    state["posted_ids"] = list(posted)
    state["post_failed_notified"] = list(failed_notified)
    if changed:
        save_state(state)

    if pending_post:
        # 未投稿の地震が残っている = 被災者に記事がまだ届いていない。ログで目立たせる。
        print(f"WARNING: 未投稿の地震が {len(pending_post)}件 残っている(次回再試行): {pending_post}",
              file=sys.stderr)
        sys.exit(1)  # Actions上で赤く見えるようにする(再試行は次のcron実行が行う)
    if not handled_any:
        print("no new earthquake >= scale 55 found")


if __name__ == "__main__":
    main()
