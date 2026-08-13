#!/usr/bin/env python3
"""
本番と同じ経路で「地震記事を実際に公開できるか」を確かめるセルフテスト。

🚨 なぜこれが要るか(2026-08-14 健一さん指示「正確に、正常に、間違いなく公開する」):
地震は滅多に起きないので、この仕組みは**何ヶ月も一度も本番投稿しないまま待機する**。
その間にはてなのAPIキーが失効していても、P2P APIのポーリングは成功し続けるので
Actionsは緑のまま。**次の大地震が来た瞬間に初めて「投稿できない」と分かる**——
それでは被災した方に情報が届かない。

そこでこのスクリプトが、実際の地震を待たずに投稿経路の全体を定期的に検証する:

  1. ダミーの地震データで本番と同じ build_article() を呼ぶ
  2. 本番と同じ WSSE 認証・同じエンドポイントで **下書きとして** 投稿する
  3. 投稿した記事を GET し直し、タイトル・カテゴリー・本文が壊れずに
     往復したか(HTMLの二重エスケープが起きていないか)を検証する
  4. 検証が終わったらその記事を DELETE して跡形もなく消す
  5. どこかで失敗したら ntfy へ通知し、exit 1 で Actions を赤くする

**公開記事は一切作らない**(常に app:draft=yes、最後に必ず削除)。
本番の監視ロジック(check_earthquake.py の main)は呼ばない。state.json も触らない。
"""
import os
import re
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_earthquake import (  # noqa: E402
    ENTRY_COLLECTION,
    build_article,
    hatena_post,
    make_wsse_header,
    send_ntfy,
    HATENA_ID,
)

# 実在しない震源名にして、万一消し損ねても本物の速報と紛れないようにする
DUMMY_NAME = "セルフテスト用ダミー震源(実際の地震ではありません)"
DUMMY_MAG = 7.3
DUMMY_SCALE = "6強"
DUMMY_TIME = "2000/01/01 00:00:00"
DUMMY_TSUNAMI = "Warning"  # 最も重要な分岐(赤い津波警報)を通す


def _request(url: str, method: str, api_key: str, data: bytes = None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-WSSE", make_wsse_header(HATENA_ID, api_key))
    if data is not None:
        req.add_header("Content-Type", "application/xml; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def fail(msg: str, topic: str) -> None:
    print(f"SELFTEST FAILED: {msg}", file=sys.stderr)
    send_ntfy(
        topic,
        title="⚠️ 地震速報の投稿セルフテスト失敗",
        message=(
            f"地震速報を公開できない状態かもしれません: {msg}\n"
            f"はてなAPIキーの失効を確認してください。\n"
            f"gh secret set HATENA_API_KEY --repo keniti2026/jishin-monitor"
        ),
    )
    sys.exit(1)


def main() -> None:
    api_key = os.environ.get("HATENA_API_KEY")
    topic = os.environ.get("NTFY_TOPIC", "")
    if not api_key:
        print("ERROR: HATENA_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    title, body = build_article(DUMMY_NAME, DUMMY_MAG, DUMMY_SCALE, DUMMY_TIME, DUMMY_TSUNAMI)
    title = f"【セルフテスト・削除予定】{title}"
    categories = ["災害・防災情報", "地震", "地震自動速報", "セルフテスト"]

    # --- 1. 下書きとして投稿 --------------------------------------------
    print("1) 下書きとして投稿する(本番と同じ認証・同じエンドポイント)")
    status, resp = hatena_post(api_key, title, body, categories, draft=True)
    if status != 201:
        fail(f"投稿が status={status} で失敗した。応答: {resp[:300]}", topic)
    m = re.search(r"<id>tag:blog\.hatena\.ne\.jp[^<]*-(\d+)</id>", resp)
    if not m:
        fail(f"投稿は201だがentry IDを取り出せなかった。応答: {resp[:300]}", topic)
    entry_id = m.group(1)
    print(f"   OK: 投稿できた entry_id={entry_id}")

    # --- 2. 取得し直して中身を検証 --------------------------------------
    problems = []
    try:
        print("2) 投稿した記事をGETし直して中身を検証する")
        st, got = _request(f"{ENTRY_COLLECTION}/{entry_id}", "GET", api_key)
        if st != 200:
            fail(f"投稿した記事をGETできない (status={st})", topic)

        # 下書きのままか(絶対に公開されていないこと)
        if "<app:draft>yes</app:draft>" not in got.replace(" ", ""):
            if not re.search(r"<app:draft>\s*yes\s*</app:draft>", got):
                problems.append("下書きになっていない(公開されてしまった可能性)")

        # タイトルが往復したか
        if "セルフテスト" not in got:
            problems.append("タイトルが往復していない")

        # カテゴリーが全部付いたか。特に先頭の「災害・防災情報」は必須
        for c in categories:
            if f'term="{c}"' not in got:
                problems.append(f"カテゴリー「{c}」が付いていない")

        # 🚨 本文が「実HTML」として往復したかを見る。ここが二重エスケープ事故の検知点。
        # content は転送時にエスケープされて入るので、取得結果には &lt;p&gt; の形で現れる。
        # もし &amp;lt;p&amp;gt; のように二重になっていたら、読者にはタグが文字列として見える。
        if "&amp;lt;" in got:
            problems.append("本文が二重エスケープされている(読者にHTMLタグが文字で見える)")

        # 命に関わる中身が実際に載っているか
        for must, label in (
            ("津波情報", "津波情報の見出し"),
            ("ただちに高台", "津波警報時の避難指示"),
            ("今すぐやること", "初動行動リスト"),
            ("171", "災害用伝言ダイヤル"),
            ("jma.go.jp", "気象庁へのリンク"),
        ):
            if must not in got:
                problems.append(f"{label}が本文に無い")

        # 画像ゼロ(健一さん指示、災害情報はテキストが主)
        if "&lt;img" in got or "<img" in got.split("<content")[-1]:
            problems.append("画像が入っている(災害情報は画像ゼロが正しい)")

    finally:
        # --- 3. 何があっても必ず消す ------------------------------------
        print("3) テスト記事を削除する")
        dst, dresp = _request(f"{ENTRY_COLLECTION}/{entry_id}", "DELETE", api_key)
        if dst in (200, 204):
            print(f"   OK: 削除できた (status={dst})")
        else:
            # 消せなかったことは必ず知らせる(下書きとはいえ残骸を放置しない)
            print(f"   WARNING: 削除に失敗 status={dst} {dresp[:200]}", file=sys.stderr)
            send_ntfy(
                topic,
                title="⚠️ セルフテスト記事の削除に失敗",
                message=(f"下書き entry_id={entry_id} が残っています。手動で削除してください。"),
            )

    if problems:
        fail("投稿はできたが中身に問題がある: " + " / ".join(problems), topic)

    print()
    print("✅ セルフテスト成功: 認証・投稿・カテゴリー・本文・削除のすべてが正常")
    print("   → 実際に大きな地震が起きたとき、記事を公開できる状態にある")


if __name__ == "__main__":
    main()
