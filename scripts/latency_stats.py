#!/usr/bin/env python3
"""
検知の遅れ(monitor.yml の実際の実行間隔)を計測して docs/latency-log.csv へ1行追記する。

🚨 なぜ自動で貯めるか(2026-08-14 健一さん指示):
「最初から100%完璧なものは出来ない。何回もテストを重ね、不具合を見つけ修正して工夫して
  完成度を高める。**テストを重ねながらデータを取って改善していくように**」

改善の効果は1回の測定では分からない(GitHubの負荷は日によって変わる)。月1回の測定だけだと
施策1つの判定に1ヶ月かかり、候補を4つ試すのに4ヶ月かかってしまう。そこで毎週のセルフテストの
ついでにこのスクリプトが自動で1点ずつデータを貯める。月次レビューのときに**推移**が読めるので、
「たまたま良かった日」と「本当に効いた施策」を区別できる。

目標値: 地震発生から記事公開まで **30分以内**。この数字は健一さんの実体験(北海道胆振東部地震の
ブラックアウトで、携帯基地局のバッテリーが尽きて停電から約30分でスマホの電波が途絶えた)に由来する。

失敗しても本体(セルフテスト)を落とさない —— 計測はあくまで補助なので、
エラーは握りつぶして exit 0 で終わる。
"""
import csv
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPO = "keniti2026/jishin-monitor"
WORKFLOW = "monitor.yml"
CSV_PATH = Path(__file__).resolve().parent.parent / "docs" / "latency-log.csv"
TARGET_MINUTES = 30  # 被災した方に届く猶予

HEADER = [
    "measured_at_utc", "sample_count", "period_start_utc", "period_end_utc",
    "median_min", "min_min", "max_min", "over30_pct", "cron", "note",
]


def fetch_runs(token: str) -> list:
    url = (f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}"
           f"/runs?event=schedule&per_page=100&status=completed")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "kenichi-earthquake-monitor-latency/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("workflow_runs", [])


def current_cron() -> str:
    """monitor.yml から現在の cron 設定を読む(どの設定での実測値かを残すため)。"""
    p = Path(__file__).resolve().parent.parent / ".github" / "workflows" / WORKFLOW
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("- cron:"):
                return s.split("cron:", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return "unknown"


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("latency_stats: no token, skipping", file=sys.stderr)
        return

    try:
        runs = fetch_runs(token)
    except Exception as e:
        print(f"latency_stats: fetch failed ({e}), skipping", file=sys.stderr)
        return

    times = sorted(
        datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        for r in runs if r.get("created_at")
    )
    if len(times) < 3:
        print(f"latency_stats: not enough samples ({len(times)}), skipping", file=sys.stderr)
        return

    gaps = [(times[i + 1] - times[i]).total_seconds() / 60 for i in range(len(times) - 1)]
    gaps_sorted = sorted(gaps)
    n = len(gaps_sorted)
    median = gaps_sorted[n // 2] if n % 2 else (gaps_sorted[n // 2 - 1] + gaps_sorted[n // 2]) / 2
    over = sum(1 for g in gaps if g > TARGET_MINUTES)

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        len(times),
        times[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
        times[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        f"{median:.1f}",
        f"{min(gaps):.1f}",
        f"{max(gaps):.1f}",
        f"{100.0 * over / len(gaps):.1f}",
        current_cron(),
        "OK(30分以内)" if max(gaps) <= TARGET_MINUTES else "要改善(30分超あり)",
    ]

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new_file:
            w.writerow(HEADER)
        w.writerow(row)

    print(f"latency_stats: サンプル{len(times)}件 / 間隔 中央値{median:.0f}分 "
          f"最小{min(gaps):.0f}分 最大{max(gaps):.0f}分 / "
          f"30分超 {100.0 * over / len(gaps):.0f}% / cron={row[8]}")
    if max(gaps) > TARGET_MINUTES:
        print(f"   → まだ30分要件を満たしていない(最大{max(gaps):.0f}分)。"
              f"docs/latency-experiments.md の次の施策を検討すること")


if __name__ == "__main__":
    main()
