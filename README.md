# うきのわ 助成金カレンダー

熊本県宇城市の市民活動団体「うきのわ」のために、非営利団体向けの助成金・補助金の情報を毎週月曜の朝に自動で集め、整理して届ける仕組みです。

- **募集中の一覧**: <https://nexa-eng.github.io/ukinowa-grants/>
- **週報**: <https://nexa-eng.github.io/ukinowa-grants/reports/>
- **カレンダー登録**: <https://nexa-eng.github.io/ukinowa-grants/subscribe.html> （手元のカレンダーに登録すると、締切の21日前と7日前に予定として出ます）
- **データ**: <https://nexa-eng.github.io/ukinowa-grants/grants.json>
- **収集ログ**（判定前の全件と判断）: <https://nexa-eng.github.io/ukinowa-grants/collected/>

## 使い方（受け取る側）

1. 毎週月曜に「助成金の週報」のメールが届きます。本文のリンクから週報ページを開きます
2. 週報には「今週の新着」と「締切が60日以内」が、合う度（100点満点）つきで並びます
3. 応募するかどうかは人が決めます。必ず公式ページで条件と締切を確認してください
4. カレンダーは「購読」で使います（取り込みはその時点のコピーで更新されません）。手順は <https://nexa-eng.github.io/ukinowa-grants/subscribe.html>

## 仕組み

```
毎週月曜 05:00（JST）
  情報源を巡回（県・市の新着、J-Net21、共同募金会、日本財団、休眠預金、CANPAN、KVOAD、パレア）
  → 規則で粗く絞る（助成・補助・募集 などの語、入札・求人は除外）
  → 新しいものだけ Claude が本文を読み、助成かどうか・締切・上限額・対象・合う度を取り出す
  → 公開ページ・週報・カレンダー・JSON を生成して、このリポジトリに保存
  → 週報のリンクをメールで送信
```

- ブランチの分け方: `main` はコードと設定（変更は PR 必須）。`data` は生成物（`state/`・`docs/`）で、定期実行のボットだけが書き込む。公開ページは `data` ブランチの `docs/` から配信
- 情報源は `config/sources.json`、うきのわの関心（地域・事業・テーマ語）は `config/profile.json`
- 一度判定したページは `state/` に記録し、二度と判定しません。締切が過ぎたものは一覧から外れます
- ページ本文は判定のためだけに読み、保存しません。保存するのは要約と出典 URL です
- 情報源のサイトには週1回だけアクセスし、同じサイトには1.5秒以上の間隔を空け、robots.txt を守り、連絡先入りの名前（User-Agent）を名乗ります。http/https 以外や内部アドレスには接続せず、1ページ2MB まで・HTML/XML だけを読みます
- AI の出力（締切・金額・リンクなど）は公開前に検証します。申請リンクは元ページと同じサイトのときだけ採用し、それ以外は元ページに戻します
- 取得や判定に失敗したページは次回に再試行し、3回失敗したら諦めます。「助成ではない」と判定したページも1年後に見直します（年度更新の取りこぼし対策）
- 失敗（情報源の全滅、判定ゼロ、メール送信失敗）は実行を赤にし、週報の末尾にも件数を載せます

## 運用（Nexa 向け）

### 必要な設定（リポジトリの Settings → Secrets and variables → Actions）

| 名前 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude の API キー（判定に使う。費用は Nexa 負担） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | 送信用メールサーバー。Gmail なら `smtp.gmail.com` / `465` / アドレス / アプリパスワード |
| `MAIL_FROM` | 差出人 |
| `MAIL_TO` | 宛先（カンマ区切り） |

メールの設定がなければ送信だけスキップされ、ページとカレンダーは更新されます。

設定はローカルの `.env`（git 管理外。ひな形は `.env.example`）を正とし、次の1行で7つまとめて登録・再登録できます。

```bash
gh secret set -f .env
```

ローカルで AI 判定を試すときも同じファイルを読みます: `set -a; . ./.env; set +a`

### 手で動かす

GitHub の Actions タブから「weekly」を「Run workflow」で実行できます。ローカルでは:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m grants.run --collect-only       # 収集と規則の絞り込みだけ（判定も状態更新もしない）
python -m grants.run --no-llm --no-mail   # 規則だけで確認（API キー不要）
python -m grants.run --llm --no-mail      # AI 判定あり（ANTHROPIC_API_KEY が必要）
```

### テスト

```bash
pip install pytest && python -m pytest -q
```

PR では CI（テストとコンパイル確認）が自動で走ります。

### 情報源を足す

`config/sources.json` に1件追加します。`kind` は `rss` か `html`。専用の助成情報ページなら `dedicated: true`（キーワードの絞り込みを通さず AI 判定に回す。除外語と除外 URL パターンは効く）。公開ページに載せてよい情報源なら `public_ok: true`。

### 精度の測り方

最初の1か月は、出てきた助成をうきのわの事務局と手で確認します。

| 指標 | 目標 |
|---|---|
| 見逃し（知っていたのに出なかった） | ゼロ。これが最重要。あれば情報源を足す |
| 混入（関係ないもの） | 半分未満。多ければ `profile.json` の語と AI への指示を直す |
| 締切前に出たか | 遅れゼロ |

## 置かないもの

個人情報、運営の内部文書、応募の判断。助成情報はもともと公開情報です。

## ライセンス

- **コード**（`grants/`、`config/`、`tests/`、ワークフロー）: [MIT License](LICENSE)。Copyright (c) 2026 Nexa Engineering Co., Ltd.
- **生成データ**（`docs/` の一覧・週報・`grants.json`・`grants.ics`、`state/`）: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.ja)。出典として「うきのわ 助成金カレンダー（Nexa Engineering）」と表示すれば、転載・加工・再配布ができます
- 元の助成情報の権利は、それぞれの財団・自治体・団体にあります。このリポジトリは要約と出典 URL だけを保存し、本文は保存しません
- 「うきのわ」の名称と写真・ロゴは、このライセンスの対象外です。団体の許可なく使えません
- 内容の正確性は保証しません。応募の可否・締切・条件は必ず公式ページで確認してください
