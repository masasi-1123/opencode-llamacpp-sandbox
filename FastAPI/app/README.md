# LLM Proxy Service — Source Index

Zoo Code / Open WebUI などのクライアントから届くリクエストを、設定されたバックエンド（**llama.cpp** または **Ollama**）へ振り分けて転送する FastAPI アプリケーションのソースコードインデックス。

---

## アーキテクト概要

```mermaid
flowchart TD
    Client["クライアント (Zoo Code / Open WebUI 等)"]
    Main["app/main.py<br/>ルーティング / 振り分け"]
    Config["app/config.py<br/>環境変数・モデルエイリアス"]

    Main --> Config
    Main --> LlamaSender["backends/llama_cpp/sender.py"]
    Main --> OllamaSender["backends/ollama/sender.py"]

    LlamaSender --> LlamaReceiver["backends/llama_cpp/receiver.py"]
    OllamaSender --> OllamaReceiver["backends/ollama/receiver.py"]
    OllamaReceiver --> Formatter["app/formatter.py<br/>Ollama 用成形 / 解析"]
    LlamaSender --> Normalizer["app/normalizer.py<br/>messages 正規化"]

    LlamaSender -->|"POST /v1/chat/completions|" LlamaServer["llama-server (llama.cpp)"]
    OllamaSender -->|"POST /api/chat|" OllamaServer["Ollama サーバー"]
    LlamaServer --> LlamaReceiver
    OllamaServer --> OllamaReceiver
```

**フローの概要:**
1. [`main.py`](./main.py) が全エンドポイントを受け取り、[`config.py`](./config.py) でバックエンド種別判定
2. 対応するバックエンドの [`sender`](./backends) へリクエストを渡す
3. sender はリクエストを成形 / 正規化してから llama-server / Ollama へ送信
4. 返却されたレスポンスを [`receiver`](./backends) が受け取り、形式変換してからクライアントへ返す

---

## ファイル一覧

| ファイル | カテゴリ | 主な役割 |
|---------|---------|---------|
| [`main.py`](./main.py) | 本体 | エントリポイント。リクエスト受け取り・バックエンド振り分け・パス正規化 |
| [`config.py`](./config.py) | 本体 | 環境変数による接続先切り替え・モデルエイリアス解決・ログ設定 |
| [`formatter.py`](./formatter.py) | 本体 | **Ollama 専用**。OpenAI 形式リクエストの成形・Ollama レスポンスの解析（Tool Call 抽出） |
| [`normalizer.py`](./normalizer.py) | 本体 | **llama.cpp 専用**。`messages` の content 正規化・`tool_calls` の `type` 補完 |
| [`backends/llama_cpp/sender.py`](./backends/llama_cpp/sender.py) | llama.cpp sender | llama-server へのリクエスト送信（チャット / models / 一般パススルー） |
| [`backends/llama_cpp/receiver.py`](./backends/llama_cpp/receiver.py) | llama.cpp receiver | llama-server レスポンスの受信・Ollama 互換 NDJSON / OpenAI SSE への変換 |
| [`backends/ollama/sender.py`](./backends/ollama/sender.py) | Ollama sender | Ollama へのリクエスト送信（チャット / 一般パススルー） |
| [`backends/ollama/receiver.py`](./backends/ollama/receiver.py) | Ollama receiver | Ollama レスポンスの受信・蓄積・疑似ストリーム / 非ストリーミング返却 |

---

## 各ファイルの詳細

### 📄 `main.py` — メインプロキシ（ルーティング）

クライアントからの全リクエストを受け取り、バックエンドへ振り分けるエントリポイント。

- **`proxy_handler()`** — 唯一のルーター。`/{path:path}` で任意パス・任意メソッド（GET/POST/PUT/DELETE）を受信
- `config.get_backend_info()` で `LLM_BACKEND` 環境変数に基づき `llama-cpp` / `ollama` を判定し、対応する sender へ振り分け
- パス正規化: `v1/api/` プレフィックスを除去（例: `v1/api/chat` → `api/chat`）
- エンドポイント判定用フラグを算出:
  - `is_chat_openai` = `/v1/chat/completions`
  - `is_chat_ollama` = `/api/chat`

---

### 📄 `config.py` — 設定・エイリアス解決

環境変数ベースの設定管理与、モデル名マッピング。

- **`is_debug_logging()`** — 環境変数 `DEBUG_LLM_PROXY` が有効か判定
- **`get_backend_info()`** — `LLM_BACKEND`（代替: `LLM_TARGET` / `BACKEND_TYPE`）を判定し、バックエンド種別と接続先 URL を返却
  - デフォルト: `llama.cpp` → `http://llama-cpp:8080`、それ以外 `ollama` → `http://ollama:11434`
- **`resolve_model_alias()`** — クライアント指定のモデル名を llama-server の実在モデルIDへマッピング
  - `gemma4:e4b` / `gemma4:e2b` は Ollama 固有 Blob で llama-server 直接読み込み不可のため、デフォルトで `gemma4:12B` へマッピング
  - 各モデルを環境変数（`GEMMA4_E4B_MODEL_ID` 等）で上書き可能
  - マッピング表: `gemma4:e4b`, `gemma4:e2b`, `gemma4:12b`, `gemma4:26b`, `ornith`, `ornith:9b`, `ornith-1.5:9b`, `ornith-1.5:35b`, `qwen2.5-coder:14b`, `qwen3:14b`, `qwen3.5:9b`

---

### 📄 `formatter.py` — Ollama 用リクエスト成形 & レスポンス解析

**Ollama 専用**のモジュール。OpenAI 形式のリクエストを Ollama 形式に変換し、レスポンスから Tool Call を抽出。

- **`format_request_for_ollama()`** — Zoo Code（OpenAI 形式）リクエストを Ollama 形式へ成形
  - `content` がリスト形式（OpenAI multipart: `[{"type":"text","text":"..."}]`）の場合、text 要素を `\n` 結合した単一文字列へ変換
  - `tools` 存在時、ツール出力フォーマットを強制するシステムプロンプトを注入（`<tool_call>{...}</tool_call>` 形式を強制）
  - `options.num_ctx` を 131072（128k）以上に強制設定
- **`parse_response_from_ollama()`** — Ollama 応答テキストから Tool Call を抽出・OpenAI 互換形式へ変換
  - `<tool_call>{...}</tool_call>` パターンを正規表現で探索（優先）、なければ ```json ``` ブロックをフォールバック
  - Tool Call 検出時: タグ除去した思考テキストを残しつつ `tool_calls` を OpenAI 形式（`id` / `type` / `function`）へ再構成、`finish_reason` を `tool_calls`
  - Tool Call なし: 通常テキスト応答として `finish_reason: stop` で返却

---

### 📄 `normalizer.py` — messages 正規化

**llama.cpp 専用**のモジュール。llama-server が要求する形式に messages を正規化。

- **`normalize_messages()`** — 入力 messages を整形
  - `content` がリスト形式の場合、text 要素を `\n` 結合した単一文字列へ変換
  - assistant ロールで `content` が空の場合、`reasoning_content` / `thinking` で補完
  - `tool_calls` に `type` フィールドが無い場合、`{"type": "function"}` を補完（llama.cpp は `type` を必須とするため。Zoo Code は `{"function":{}}` 形式で送ってくる）

---

### 📄 `backends/llama_cpp/sender.py` — llama.cpp Sender

**llama.cpp (llama-server)** へのリクエストを構築・送信。

- **`handle_llama_cpp_proxy()`** — メインエントリー
  - エンドポイント判定:
    - チャット（`/v1/chat/completions` / `/api/chat`）→ `{base_url}/v1/chat/completions` へ送信
    - `/v1/models` / `/models` / `/api/tags` → `/v1/models` へ送信（`/api/tags` は Ollama 形式へ変換）
    - それ以外 → 汎用パススルー
  - **GET `/v1/models` / `/api/tags`**: リスト形式レスポンスを Ollama の `api/tags` 形式 `{models: [{name, model}]}` へ変換
  - **POST チャット**:
    - `resolve_model_alias()` でモデルIDマッピング
    - `normalize_messages()` で messages 正規化
    - ストリーミング指定（`stream`）で分岐:
      - 非ストリーミング → `process_non_streaming_response()` へ
      - ストリーミング → `create_streaming_response()` へ
  - 一般パススルー: ヘッダーをコピー（host / content-length 除去）して任意パスを転送

---

### 📄 `backends/llama_cpp/receiver.py` — llama.cpp Receiver

**llama.cpp** からのレスポンスを受信・形式変換。

- **`_convert_tool_calls_to_ollama()`** — OpenAI 形式 `tool_calls` を Ollama ネイティブ形式へ変換
  - `arguments`（JSON 文字列）を JSON オブジェクトへパース、`id` / `type` フィールドを除去
- **`process_non_streaming_response()`** — 非ストリーミング応答を処理
  - エラー時（status ≥ 400）はエラー内容を返却
  - `is_chat_ollama` の場合（`/api/chat` 経由）: レスポンスを Ollama 形式（`model` / `created_at` / `message` / `done` / `done_reason`）へ再構成、tool_calls を Ollama 形式へ変換
  - それ以外（OpenAI 直接）: 元レスポンスをそのまま返却
  - reasoning_content のみの場合、`content` へ補完
- **`create_streaming_response()`** — ストリーミング応答を処理（2つのモード）
  - **`is_chat_ollama`（Ollama NDJSON 出力）**:
    - llama-server の SSE をパースし、Ollama 互換 NDJSON に変換
    - **Tool Calls 対策**: llama.cpp は `arguments` を複数チャンクに分割して送信するため、`finish_reason` を受け取るまで全チャンクを蓄積（`tool_calls_acc`）してから一括送出（不完全な引数が届く問題を回避）
    - 通常応答（content/reasoning）はリアルタイムでストリーミング
  - **それ以外（OpenAI SSE パススルー）**: ラインをそのまま転送（`text/event-stream`）

---

### 📄 `backends/ollama/sender.py` — Ollama Sender

**Ollama** へのリクエストを構築・送信。

- **`handle_ollama_proxy()`** — メインエントリー
  - チャットの場合 → `{base_url}/api/chat` へ送信
  - それ以外 → 汎用パススルー
  - `format_request_for_ollama()` でリクエストを成形
  - **常に `stream: True` 強制**（Ollama はストリーミング前提の処理）
  - `process_ollama_chat_response()` へ渡す
  - 一般パススルー: 任意パスを透過転送

---

### 📄 `backends/ollama/receiver.py` — Ollama Receiver

**Ollama** からのレスポンスを受信・蓄積・変換。

- **`process_ollama_chat_response()`** — メインエントリー
  - Ollama の NDJSON をパースし、`thinking` / `content` を結合して `full_content` を構築、`tool_calls` を保持
  - `formatter.parse_response_from_ollama()` で Tool Call を解析
  - Tool Call 検出時: `finish_reason` を `tool_calls` に設定
  - ストリーミング指定（`is_stream`）で分岐:
    - **ストリーミング**: `text/event-stream`（OpenAI 互換）の疑似ストリームを生成（`chatcmpl-pseudo` ID）
    - **非ストリーミング**: OpenAI 形式の `chat.completion` JSON を返却
- **`create_passthrough_stream()`** — 一般リクエストの透過ストリーム転送（`llm_res` の生バイト列をそのまま出力）

---

## 環境変数リファレンス

| 変数名 | 既定値 | 用途 |
|--------|--------|------|
| `LLM_BACKEND` | `llama.cpp` | バックエンド種別（`llama.cpp` / `ollama`） |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama 接続先 |
| `LLAMA_CPP_BASE_URL` | `http://llama-cpp:8080` | llama-server 接続先 |
| `DEBUG_LLM_PROXY` | `false` | デバッグログ出力の有効無効 |
| `GEMMA4_E4B_MODEL_ID` | `gemma4:12B` | `gemma4:e4b` の変換先モデル |
| `GEMMA4_E2B_MODEL_ID` | `gemma4:12B` | `gemma4:e2b` の変換先モデル |
| `GEMMA4_12B_MODEL_ID` | `gemma4:12B` | `gemma4:12b` の変換先モデル |
| `GEMMA4_26B_MODEL_ID` | `gemma4:26B` | `gemma4:26b` の変換先モデル |
| `ORNITH_MODEL_ID` | `ornith` | `ornith` の変換先モデル |
| `ORNITH_1_5_MODEL_ID` | `ornith:9B` | `ornith:9b` の変換先モデル |
| `ORNITH_1_5_35B_MODEL_ID` | `ornith-1.5:35B` | `ornith-1.5:35b` の変換先モデル |
| `QWEN2_5_CODER_MODEL_ID` | `qwen2.5-coder:14B` | `qwen2.5-coder:14b` の変換先モデル |
| `QWEN3_MODEL_ID` | `qwen3:14B` | `qwen3:14b` の変換先モデル |
| `QWEN3_5_MODEL_ID` | `qwen3.5:9B` | `qwen3.5:9b` の変換先モデル |
