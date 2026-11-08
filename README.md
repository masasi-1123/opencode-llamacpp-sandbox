# 🛡️ OpenCode + llama.cpp Docker Sandbox

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

ローカルLLM（**llama.cpp server-cuda**）と自律型AIコーディングエージェント（**OpenCode**）を安全・快適に動かすための**隔離サンドボックス環境**です。

プロキシ不要で、OpenCodeがllama.cppのOpenAI互換エンドポイント（`/v1`）へ直結。
`docker compose up -d` だけで、推論基盤と使い捨て開発コンテナが一発起動します。

---

## 🌟 主な特徴

1. **🛡️ ホスト完全隔離の使い捨てサンドボックス**:
   - OpenCodeはコンテナ（`opencode-sandbox`）内で非rootユーザーとして動作。
   - `rm -rf` や未知のグローバルパッケージ導入があっても、ホストOS（Windows / WSL）は100%無傷。
   - 生成された成果物は `./workspace` にマウントされ、ホスト側のエディタ（VS Code等）からそのまま閲覧・コミット可能。

2. **⚡ VRAM 12GBで35B級MoEモデルを高速実行**:
   - `llama.cpp` のプリセット設定（`presets.ini`）により、MoEモデル（Ornith-1.5:35B等）のExpert層をCPUメインRAMへオフロード（`n-cpu-moe = 999`）。
   - KVキャッシュを4bit量子化（`cache-type-k/v = q4_0`）し、64kコンテキストでもVRAM溢れ（OOM）を回避。
   - Ollamaでダウンロード済みのモデルBlobを二重保存なしで直接参照可能。

3. **🚀 プロキシ不要のシンプル・ダイレクト接続**:
   - OpenCodeの `@ai-sdk/openai-compatible` プロバイダが、`llama-cpp:8080/v1` に直接接続。余計なコンテナを挟みません。

4. **🎛️ ワンタッチ・モデル切り替え (`switch-model`)**:
   - コンテナ内で `switch-model` を叩くだけで、llama.cpp上の登録モデル一覧を取得し、対話式に即座に切り替え可能。

---

## 🏗️ 全体アーキテクチャ

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Docker Compose 一括管理                          │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────┐          │
│  │  opencode-sandbox (コンテナ内 / Ubuntu 24.04 + Node.js)   │          │
│  │  ・OpenCode (自律コーディングTUI)                         │          │
│  │  ・switch-model (対話式モデル切り替えCLI)                 │          │
│  │  ・./workspace (ホスト同期マウント)                      │          │
│  └──────────────────────────┬───────────────────────────────┘          │
│                             │ HTTP (OpenAI 互換 API :8080/v1)          │
│                             ▼                                          │
│  ┌──────────────────────────────────────────────────────────┐          │
│  │  llama-cpp (server-cuda :8081)                           │          │
│  │                                                          │          │
│  │  ┌──────────────────────┐    ┌──────────────────────┐    │          │
│  │  │ GPU (VRAM 12GB)      │    │ CPU + メインRAM      │    │          │
│  │  │ ・Attention / 主幹層 │    │ ・MoE Expert層       │    │          │
│  │  │ ・KVキャッシュ (q4_0)│    │   (n-cpu-moe=999)    │    │          │
│  │  └──────────────────────┘    └──────────────────────┘    │          │
│  └──────────────────────────────────────────────────────────┘          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 前提条件

- **OS**: Linux または Windows (WSL2 / Ubuntu)
- **GPU**: NVIDIA GPU (VRAM 12GB以上推奨)
- **ソフトウェア**:
  - Docker Engine & Docker Compose v2
  - NVIDIA Driver & NVIDIA Container Toolkit

---

## 🚀 クイックスタート

### 1. リポジトリのクローン
```bash
git clone <your-repository-url>
cd opencode-llamacpp-sandbox
```

### 2. 環境の起動（1コマンド）
```bash
docker compose up -d --build
```
※ 初回はサンドボックス用コンテナのビルドとllama.cppイメージのプルが行われます。

### 3. サンドボックスコンテナに入る
```bash
docker compose exec -it opencode-sandbox bash
```

### 4. （任意）モデルの確認・切り替え
コンテナ内のターミナルで `switch-model` を実行します：
```bash
switch-model
```
対話式メニューが表示され、番号を入力するだけで設定ファイル（`opencode.jsonc`）が自動更新されます。

### 5. OpenCode を起動する
作業ディレクトリ（`/home/workspace`）で OpenCode を起動します：
```bash
opencode
```
ターミナル上にTUIが表示され、日本語でプロンプトを入力すればAIが自律的にコード生成・修正・実行テストを行ってくれます。

生成されたファイルはホスト側の `./workspace` フォルダとリアルタイム同期されます。

---

## ⚙️ モデルの追加・変更方法

### GGUFモデルを自前で用意する場合
1. `./models` ディレクトリに `.gguf` ファイルを配置します。
2. `presets.ini` にモデル設定を追記します：
   ```ini
   [my-model]
   model = /models/your-model.gguf
   n-gpu-layers = 99
   ```
3. `docker compose restart llama-cpp` で反映されます。

---

## 🛑 停止とリセット

```bash
# コンテナの停止
docker compose down

# 開発環境が汚れたため初期状態にリセットしたい場合
docker compose restart opencode-sandbox
```

---

## 📄 ライセンス

本プロジェクトは [MIT License](LICENSE) の下で公開されています。
