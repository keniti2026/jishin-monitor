# jishin-monitor

夜間・不在時でも震度6弱以上の地震を見逃さないための、無料の常時監視の仕組み。

## 仕組み

GitHub Actionsが10分おきに [P2P地震情報](https://www.p2pquake.net/) の公開APIをチェックし、
震度6弱以上の新しい地震を検知すると:

1. [ntfy.sh](https://ntfy.sh/) 経由でスマホへプッシュ通知を送る
2. keniti2026.hatenablog.com に、最低限の事実だけをまとめた速報記事を即座に自動公開する
   (カテゴリ「地震自動速報」を付与。詳しい安全確保情報への肉付けは、後でKenichi Claude Code
   セッション側の `jishin-kinkyu-kiji` スキルが引き継いで行う想定)
3. 同じ地震を二重に投稿しないよう `state.json` に記録する

## 必要なGitHub Secrets

| Secret名 | 内容 |
|---|---|
| `HATENA_API_KEY` | はてなブログのAtomPub APIキー |
| `NTFY_TOPIC` | ntfy.shの通知トピック名(第三者に推測されにくい文字列にすること) |

## 通知の受け取り方

スマホに ntfy アプリ(iOS/Android無料)を入れ、上記と同じトピック名を購読するだけ。
アカウント登録不要。

## 手動テスト

GitHubリポジトリの Actions タブから `Earthquake Monitor` を選び、
「Run workflow」で手動実行できる(`workflow_dispatch`)。
