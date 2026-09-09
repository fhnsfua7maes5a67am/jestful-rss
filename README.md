# Jestful → Feedly RSS（PC不要・全作品・サムネイル付き）

Jestful の更新漫画を、GitHub Actions で15分ごとに自動確認し、
GitHub Pages 上の RSS を Feedly で購読するためのツールです。

## 特徴

- Jestful の特定作品ではなく **全作品が対象**
- PCを起動しておく必要なし
- GitHub Actions が15分ごとに自動実行
- 初回は既存作品を通知せず「現在位置」だけ記録
- 2回目以降、前回以降に更新された漫画だけRSSへ追加
- 同じChapterは重複通知しない
- 最新Chapterへ直接リンク
- 作品ページから表紙画像を取得
- RSSに `media:thumbnail` / `media:content` と HTML `<img>` を両方出力
- Feedlyでサムネイルとして認識されやすい構成

---

# セットアップ

## 1. GitHubで新しいリポジトリを作る

GitHubで新規リポジトリを作成します。

例:

    jestful-rss

FeedlyからRSSを取得できるよう、最も簡単なのは Public リポジトリです。

## 2. このZIPの中身をリポジトリ直下へアップロード

以下が直下にある状態にします。

    .github/
    data/
    docs/
    jestful_feed.py
    config.json
    requirements.txt

`.github/workflows/update-feed.yml` も必ずアップロードしてください。

## 3. GitHub Actionsの書き込み権限を有効化

GitHubリポジトリ:

    Settings
      → Actions
      → General
      → Workflow permissions

で

    Read and write permissions

を選び、Saveします。

## 4. GitHub Pagesを有効化

リポジトリ:

    Settings
      → Pages

Build and deployment:

    Source: Deploy from a branch
    Branch: main
    Folder: /docs

を選択して Save。

## 5. 初回実行

GitHub:

    Actions
      → Update Jestful Feed
      → Run workflow

を1回実行します。

初回は既存作品をFeedに大量投入しません。
現在の先頭側を基準として `data/state.json` を作成し、
空の `docs/feed.xml` を作ります。

その後は15分おきに自動実行されます。

## 6. Feedlyに登録

GitHubユーザー名が:

    exampleuser

リポジトリ名が:

    jestful-rss

ならRSS URLは:

    https://exampleuser.github.io/jestful-rss/feed.xml

です。

このURLをFeedlyの「Follow Sources」で追加してください。

---

# サムネイルについて

更新を検出した漫画についてだけ作品ページを取得し、以下の順で画像を探します。

1. `og:image`
2. `twitter:image`
3. 作品ページ内の画像候補

取得した画像をRSSに:

- `media:thumbnail`
- `media:content`
- `<description>` 内の `<img>`

の3形態で入れます。

Jestful側の画像URLが後から無効になった場合、その古いFeed項目の画像が
表示されなくなる可能性はあります。

---

# 更新頻度

標準:

    15分ごと

`.github/workflows/update-feed.yml` の:

    cron: "*/15 * * * *"

で指定しています。

GitHub Actionsのscheduleは厳密なタイマーではなく、
混雑時などに実行開始が遅れる場合があります。

---

# 仕組み

Jestfulの更新一覧は Last update DESC なので、
毎回全456ページを取得する必要はありません。

前回チェック時に見えていた項目を境界として記録し、
次回は1ページ目からその境界に到達するまでだけ確認します。

そのため、

    全作品を監視対象にする

ことと、

    毎回全ページを巡回しない

ことを両立しています。

---

# ファイル

- `jestful_feed.py`
  - RSS生成本体
- `.github/workflows/update-feed.yml`
  - 15分ごとのGitHub Actions
- `data/state.json`
  - 前回状態（初回Actions実行時に生成）
- `docs/feed.xml`
  - Feedlyに登録するRSS
- `config.json`
  - 動作設定

---

# 大量更新時の安全策

前回の境界が `max_scan_pages`（標準40ページ）以内で見つからなかった場合は、
大量の誤通知を避けるため、その回のRSS追加を停止します。

必要なら `config.json` の:

    "max_scan_pages": 40

を増やせます。
