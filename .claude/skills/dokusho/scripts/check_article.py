#!/usr/bin/env python3
"""読書スキルの記事チェック。

使い方: python3 check_article.py article.md
見出し構成・各部の文字数・「あんた」禁止・一人称・引用数を確認する。
FAIL が1つでもあれば終了コード1。
"""
import re
import sys

SECTIONS = [
    ("著者情報", 400, 800),
    ("関連情報", 400, 800),
    ("あらすじ", 3000, 3500),
    ("感想", 1800, 2200),
    ("本を読んで思う事", 1800, 2200),
]
BANNED = ["あんた", "アンタ", "ｱﾝﾀ"]
MAX_QUOTES = 6  # 本文引用2〜3か所＋セリフ以外の「」用に少し余裕を持たせる


def body_len(text):
    """空白・改行・Markdown記号・URLを除いた文字数。"""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s*#+.*$", "", text, flags=re.M)  # 小見出しは数えない
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)  # 表は数えない
    text = re.sub(r"[*_`>#|\-]", "", text)
    text = re.sub(r"\s", "", text)
    return len(text)


def split_sections(md):
    """`## 1. 著者情報` のようなH2見出しで区切る。"""
    parts = {}
    order = []
    current = None
    buf = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(?:\d+[.．]\s*)?(.+?)\s*$", line)
        if m and not line.startswith("###"):
            if current:
                parts[current] = "\n".join(buf)
            title = m.group(1)
            current = next((name for name, _, _ in SECTIONS if title.startswith(name)), title)
            order.append(current)
            buf = []
        elif current:
            buf.append(line)
    if current:
        parts[current] = "\n".join(buf)
    return parts, order


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    md = open(sys.argv[1], encoding="utf-8").read()
    parts, order = split_sections(md)
    fails = 0

    expected = [name for name, _, _ in SECTIONS]
    found = [s for s in order if s in expected]
    if found == expected:
        print("OK   見出し構成: 5部がこの順で揃っている")
    else:
        print(f"FAIL 見出し構成: 期待 {expected} / 実際 {found}")
        fails += 1

    for name, lo, hi in SECTIONS:
        if name not in parts:
            continue
        n = body_len(parts[name])
        status = "OK  " if lo <= n <= hi else "FAIL"
        if status == "FAIL":
            fails += 1
        print(f"{status} {name}: {n}字（目安 {lo}〜{hi}字）")

    for word in BANNED:
        hits = [i + 1 for i, line in enumerate(md.splitlines()) if word in line]
        if hits:
            print(f"FAIL 禁止語「{word}」: {len(hits)}行（行番号 {hits[:10]}）")
            fails += 1
    if not any(w in md for w in BANNED):
        print("OK   禁止語「あんた」: なし")

    for name in ("感想", "本を読んで思う事"):
        if name in parts and "俺" not in parts[name]:
            print(f"FAIL 一人称: 「{name}」に「俺」が出てこない")
            fails += 1

    quotes = re.findall(r"「[^」]{15,}」", md)
    status = "OK  " if len(quotes) <= MAX_QUOTES else "WARN"
    print(f"{status} 長めの「」引用: {len(quotes)}か所（目安 {MAX_QUOTES}以下）")

    print(f"\n結果: FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
