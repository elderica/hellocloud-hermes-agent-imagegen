# hellocloud-hermes-agent-imagegen

[Hermes Agent](https://hermes-agent.nousresearch.com/) 用の **HelloCloud 画像生成バックエンドプラグイン** です。
[HelloCloud](https://router.hello.ai) の OpenAI 互換画像生成 API (`/v1/images/generations`) 経由で
**Krea 2 Medium Turbo** (`krea-2-medium-turbo`)、
**Krea 2 Turbo Uncensored** (`krea-2-turbo-uncensored`)、
**MiniMax H3 Image** (`minimax-h3-image`) を利用できます。

- テキスト → 画像 (text-to-image、全モデル)
- 参照画像 1 枚 → 画像 (image-to-image、`krea-2-medium-turbo` のみ)
- MiniMax H3 Image は 1K / 2K / 4K、PNG / JPEG に対応
- 出力は base64 を `$HERMES_HOME/cache/images/` に保存
- API キーは **環境変数 `HELLOCLOUD_API_KEY`** から取得 (`.env` への自動書き込みはしない)

## 対応モデル

| モデル ID | 解像度 | 画像形式 | 参照画像 | 料金 |
|-----------|--------|----------|----------|------|
| `krea-2-medium-turbo` (既定) | 1K | PNG | 最大 1 枚 | 画像トークン課金 (1 枚 約 ¥3) |
| `krea-2-turbo-uncensored` | 1K / 2K | PNG | 非対応 | 1 枚 ¥10 (1K / 2K 同額) |
| `minimax-h3-image` | 1K / 2K / 4K | PNG / JPEG | 非対応 | 画像 ¥800/M tok + テキスト入力 ¥600/M tok |

このプラグインはどのモデルも 1 リクエスト 1 枚、既定の 1K・PNG で生成します。
`krea-2-turbo-uncensored` の 2K 出力や 1 リクエスト最大 4 枚の生成は、API を直接
利用する場合の仕様です。MiniMax H3 Image の解像度は API の `resolution` で指定します。

> HelloCloud は [OpenAI 互換](https://router.hello.ai/docs) で、`/v1/images/generations` は
> OpenAI SDK の `images.generate` とそのまま互換です。本プラグインはそれを
> Hermes の `image_generate` ツールから呼べる形に包んだものです。

---

## 仕組み

Hermes の `image_generate` ツール自体は**内蔵**のものです。画像生成の「実体」は
**プラグイン単位で差し替え可能**なプロバイダーとして実装されており、このプラグインは
その 1 つ (`provider: hellocloud`) です。

```
image_generate (Hermes 内蔵ツール)
        │
        └─ image_gen.provider の設定でプロバイダー選択
               ├─ (内蔵) fal / openai / openrouter / krea / meta-ai / deepinfra / xai / ...
               └─ (このプラグイン) hellocloud  ← HelloCloud / krea-2-medium-turbo
```

### 依存

- Hermes Agent (本プラグインは `agent.image_gen_provider` の公開 API にのみ依存)
- Python 標準ライブラリ + `openai` (Hermes に同梱)

Hermes の非公開内部モジュールには触れず、バージョンアップ後も動くよう自包含にしています。

---

## インストール

### 事前準備

1. **HelloCloud API キー** を取得 ([router.hello.ai](https://router.hello.ai))。
   画像生成は**有料キー** (`hello_...`) が必要で、無料トライアルキーでは
   `/v1/images/generations` を呼べません。
2. キーを**環境変数 `HELLOCLOUD_API_KEY`** に設定します。

   ```bash
   export HELLOCLOUD_API_KEY=hello_xxxxxxxxxxxx
   ```

   持続させる場合、Hermes の `.env` (`~/.hermes/.env`、またはアクティブ
   プロファイルの `.env`) に `HELLOCLOUD_API_KEY=hello_...` を追記してください。
   本プラグインは `.env` を自動で書き込まず、環境変数から読む想定です。

### プラグインの取得

```bash
hermes plugins install elderica/hellocloud-hermes-agent-imagegen
```

インストール時に「有効化しますか?」と聞かれるので `y` (あるいは `--enable` 付きで
非対話)。

> リポジトリがプライベートの場合、`hermes plugins install` は非認証の
> `https` クローンを使うため、**リポジトリを公開**するか、
> 認証付きの Git URL を指定してください:
>
> ```bash
> hermes plugins install https://<token>@github.com/elderica/hellocloud-hermes-agent-imagegen.git
> ```

### 設定

`config.yaml` (または `hermes config set`) に以下を追加します。

```yaml
image_gen:
  provider: hellocloud
  model: krea-2-medium-turbo      # 任意 (既定)。krea-2-turbo-uncensored / minimax-h3-image も選択可
  hellocloud:
    # base_url: https://api.router.hello.ai/v1   # 任意 (既定)
    # output_format: jpeg                        # minimax-h3-image では png / jpeg
```

`hermes config set` 版:

```bash
hermes config set image_gen.provider hellocloud
hermes config set image_gen.model krea-2-medium-turbo
# Krea 2 Turbo Uncensored に切り替える場合:
# hermes config set image_gen.model krea-2-turbo-uncensored
# MiniMax H3 Image を JPEG で生成する場合:
# hermes config set image_gen.model minimax-h3-image
# hermes config set image_gen.hellocloud.output_format jpeg --force
# PNG に戻す場合:
# hermes config set image_gen.hellocloud.output_format png --force
```

> ユーザープラグインは**オプトイン**です。`plugins.enabled` に
> `hellocloud-imagegen` を含める必要があります (インストール時の確認で `y` を
> 選べば自動で書かれます)。手動で:
>
> ```bash
> hermes plugins enable hellocloud-imagegen
> ```

### 反映

プロバイダーの切替は**セッション単位**です。CLI は再起動、ゲートウェイ /
デスクトップは `/reset` (新規セッション) で新しいプロバイダーが有効になります。

---

## 使い方

### Hermes 内から

エージェントに画像生成を依頼するだけです。`image_generate` ツールが
`image_gen.provider: hellocloud` を検出して HelloCloud にルーティングします。

```
「朝の水面に浮かぶ赤い紙船を水彩風で描いて」
```

生成された画像は `$HERMES_HOME/cache/images/hellocloud_*.png` として保存され、
MiniMax H3 Image で JPEG を選んだ場合は `hellocloud_*.jpg` となります。
チャットには添付として届きます。

### OpenAI SDK で直接 (参照用)

プラグインなしで HelloCloud を直接叩く場合:

```python
from openai import OpenAI
import base64

client = OpenAI(
    base_url="https://api.router.hello.ai/v1",
    api_key="hello_...",   # HELLOCLOUD_API_KEY
)

resp = client.images.generate(
    model="krea-2-medium-turbo",
    prompt="A red paper boat on a calm pond, soft morning light",
    size="1536x1024",      # 3:2。省略時は 1024x1024 (1:1)
)
open("out.png", "wb").write(base64.b64decode(resp.data[0].b64_json))
```

---

## 設定項目

| 場所 | キー | 既定 | 説明 |
|------|------|------|------|
| 環境変数 | `HELLOCLOUD_API_KEY` | (必須) | HelloCloud API キー (`hello_...`) |
| `config.yaml` | `image_gen.provider` | — | `hellocloud` に設定して有効化 |
| `config.yaml` | `image_gen.model` | `krea-2-medium-turbo` | 利用するモデル ID |
| `config.yaml` | `image_gen.hellocloud.base_url` | `https://api.router.hello.ai/v1` | API ベース URL |
| `config.yaml` | `image_gen.hellocloud.output_format` | `png` | MiniMax H3 Image の出力形式 (`png` / `jpeg`) |
| 環境変数 | `HELLOCLOUD_IMAGE_BASE_URL` | (同上) | base_url の環境変数上書き |
| 環境変数 | `HELLOCLOUD_IMAGE_MODEL` | (model の上) | モデル ID の環境変数上書き |
| 環境変数 | `HELLOCLOUD_IMAGE_OUTPUT_FORMAT` | (output_format の上) | 出力形式の環境変数上書き |

### アスペクト比

Hermes の `image_generate` は `aspect_ratio` をセマンティック値
(`landscape` / `square` / `portrait`) で受け取り、内部で HelloCloud が受理する
`size` に変換します:

| aspect_ratio | size | 比 |
|--------------|------|----|
| `landscape` (既定) | `1536x1024` | 3:2 |
| `square` | `1024x1024` | 1:1 |
| `portrait` | `1024x1536` | 2:3 |

HelloCloud の `krea-2-medium-turbo` は `1:1 / 4:3 / 3:2 / 16:9 / 4:5 / 2:3 / 9:16`
を対応します。`16:9` 等のより狭い比は HelloCloud 側で `aspect_ratio` として
指定できます (Hermes 標準の 3 値に収まらないため、このプラグインでは未提供)。

### 画像形式

`minimax-h3-image` は `image_gen.hellocloud.output_format` に `png` (既定) または
`jpeg` を設定できます。`HELLOCLOUD_IMAGE_OUTPUT_FORMAT` でも上書きできます。
Krea 2 の 2 モデルは PNG のみです。対応しない形式を指定した場合は API 呼び出し前に
エラーを返します。4K PNG は応答が大きくなるため、4K では JPEG が推奨されます。

### image-to-image (参照画像 1 枚)

`image_generate` の `image_url` に画像 (ローカルパス / URL / data URL) を渡すと、
HelloCloud の `input_references` に変換されて image-to-image が実行されます。
**`krea-2-medium-turbo` で最大 1 枚**です。`krea-2-turbo-uncensored` と
`minimax-h3-image` は参照画像に対応していないため、参照画像を指定した場合は
API 呼び出し前にエラーを返します。

---

## 課金・制限

- `krea-2-medium-turbo` は生成画像のトークン数 × 単価で課金されます
  (1K 1 枚 ≈ 4,175 image tokens ≈ 約 ¥3)。プロンプトのテキストトークンは課金されません。
- `krea-2-turbo-uncensored` は 1 枚 ¥10 で、1K / 2K は同額です。
  ZDR Go Uncensored の定額枠でも利用できます。
- `minimax-h3-image` は画像出力 ¥800 / 1M tok とテキスト入力 ¥600 / 1M tok で
  課金されます。1K の 1:1 は画像分が約 ¥0.8 です。定額プランの対象外です。
- 失敗したリクエストは課金されません。
- このプラグインは 1 リクエスト = 1 枚 (`n=1`) で生成します。
  `krea-2-turbo-uncensored` を API から直接使う場合は最大 4 枚を指定できます。
- `stream` / `response_format: url` は非対応 (400)。
  `output_format` を受け付けるのは `minimax-h3-image` だけで、Krea 2 では 400 になります。
- `krea-2-medium-turbo` は定額プランの対象外で、クレジットから消費します。

詳細は [HelloCloud ドキュメント](https://router.hello.ai/docs) を参照してください。

---

## トラブルシューティング

| 症状 | 原因 / 対処 |
|------|------------|
| `HELLOCLOUD_API_KEY が設定されていません` | 環境変数にキーがない。`export HELLOCLOUD_API_KEY=hello_...` または `.env` に追記 |
| `HelloCloud が API キーを拒否しました (HTTP 401/403)` | トライアルキーまたは無効キー。有料キー (`hello_...`) を使用 |
| `openai パッケージが必要です` | `pip install openai` (Hermes には通常同梱) |
| `image_gen.provider='hellocloud' is set but no plugin registered that name` | プラグインがインストール / 有効化されていない。`hermes plugins list` で確認し、`hermes plugins enable hellocloud-imagegen` を実行 |
| 変更が反映されない | セッション単位で反映される。CLI は再起動、ゲートウェイは `/reset` |

---

## 免責

本プラグインは HelloCloud の API を利用します。利用条件・料金・可用性は
[HelloCloud](https://router.hello.ai) の各ページに準じます。

## ライセンス

[MIT](./LICENSE)
