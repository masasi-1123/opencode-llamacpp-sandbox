#!/usr/bin/env python3
"""
switch-model.py
================
llama.cpp (または FastAPI プロキシ) から現在利用可能なモデル一覧を動的に取得し、
OpenCode や cline-sandbox の設定を簡単に切り替えるためのツール。

使用方法:
  switch-model                  # 対話式メニューでモデルを選択
  switch-model <モデル名または番号> # 指定したモデルに直接切り替え
  switch-model --list           # 利用可能なモデル一覧を表示
  switch-model --current        # 現在選択されているモデルを表示
"""

import sys
import os
import json
import re
import urllib.request
import urllib.error

# スクリプト自身のディレクトリ
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 候補となる llama.cpp / FastAPI のエンドポイント
CANDIDATE_URLS = [
    os.getenv("LLAMA_CPP_URL"),
    os.getenv("LLM_BASE_URL"),
    "http://llama-cpp:8080/v1",       # コンテナ間通信 (llama.cpp 直接)
    "http://fastapi:8000/v1",         # コンテナ間通信 (FastAPI 経由)
    "http://localhost:8081/v1",       # ホストマシン/WSLから直接 (llama.cpp)
    "http://localhost:8000/v1",       # ホストマシン/WSLから直接 (FastAPI)
    "http://127.0.0.1:8081/v1",
    "http://127.0.0.1:8000/v1",
]
CANDIDATE_URLS = [url for url in CANDIDATE_URLS if url]

def resolve_config_paths():
    """環境に応じた opencode.jsonc および .env の絶対パスを決定する"""
    opencode_candidates = [
        # コンテナ内の標準パス
        "/home/openvscode-server/.config/opencode/opencode.jsonc",
        # ホストWSLの絶対パス
        "/mnt/e/Docker/WSL/cline-sandbox/opencode-config/opencode.jsonc",
        # スクリプト相対
        os.path.join(SCRIPT_DIR, "opencode-config", "opencode.jsonc"),
        os.path.join(SCRIPT_DIR, "..", "opencode-config", "opencode.jsonc"),
    ]
    env_candidates = [
        # ホストWSLの絶対パス
        "/mnt/e/Docker/WSL/cline-sandbox/.env",
        # スクリプト相対
        os.path.join(SCRIPT_DIR, ".env"),
        os.path.join(SCRIPT_DIR, "..", ".env"),
        "/home/workspace/.env",
    ]

    opencode_path = None
    for p in opencode_candidates:
        if os.path.exists(p):
            opencode_path = p
            break
    if not opencode_path:
        opencode_path = opencode_candidates[0]

    env_path = None
    for p in env_candidates:
        if os.path.exists(p):
            env_path = p
            break

    return opencode_path, env_path

OPENCODE_CONFIG_PATH, ENV_FILE_PATH = resolve_config_paths()


def fetch_models():
    """llama.cpp または FastAPI からモデル一覧を取得する"""
    last_err = None
    for base_url in CANDIDATE_URLS:
        url = f"{base_url.rstrip('/')}/models"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "switch-model/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models_raw = data.get("data", [])
                    return models_raw, base_url
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"llama.cpp / FastAPI サーバーに接続できませんでした。最新のエラー: {last_err}")


def get_current_model():
    """現在設定されているモデル名を取得する"""
    if OPENCODE_CONFIG_PATH and os.path.exists(OPENCODE_CONFIG_PATH):
        try:
            with open(OPENCODE_CONFIG_PATH, "r", encoding="utf-8") as f:
                content = f.read()
                m = re.search(r'"model"\s*:\s*"[^"/]+/([^"]+)"', content)
                if m:
                    return m.group(1)
                m2 = re.search(r'"model"\s*:\s*"([^"]+)"', content)
                if m2:
                    return m2.group(1)
        except Exception:
            pass

    if ENV_FILE_PATH and os.path.exists(ENV_FILE_PATH):
        try:
            with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("DEFAULT_MODEL="):
                        return line.strip().split("=", 1)[1].strip()
        except Exception:
            pass

    return "ornith-1.5:35B"


def update_opencode_config(new_model_id, all_models):
    """opencode.jsonc を更新する"""
    os.makedirs(os.path.dirname(OPENCODE_CONFIG_PATH), exist_ok=True)

    # コンテナ内からのアクセスを前提とし、llama.cppへ直接接続
    target_base_url = os.getenv("LLAMA_CPP_BASE_URL", "http://llama-cpp:8080/v1")

    # モデルディクショナリを生成
    models_dict = {}
    for m in all_models:
        mid = m.get("id")
        if mid:
            models_dict[mid] = {"name": mid}

    # 選択モデルがなければ追加
    if new_model_id not in models_dict:
        models_dict[new_model_id] = {"name": new_model_id}

    config_obj = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "local-llm": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Local LLM (FastAPI / llama.cpp)",
                "options": {
                    "baseURL": target_base_url,
                    "apiKey": "dummy"
                },
                "models": models_dict
            }
        },
        "model": f"local-llm/{new_model_id}",
        "agent": {
            "build": {
                "prompt": "あなたは優秀なAIプログラミングアシスタントです。ユーザーとのすべてのコミュニケーションは日本語で行ってください。コードのコメントやファイル名などの技術的な部分は英語のままで問題ありませんが、説明・回答・提案はすべて日本語で行ってください。"
            }
        }
    }

    with open(OPENCODE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def update_env_file(new_model_id):
    """ .env の DEFAULT_MODEL を更新する """
    if not ENV_FILE_PATH or not os.path.exists(ENV_FILE_PATH):
        return

    lines = []
    found = False
    with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("DEFAULT_MODEL="):
                lines.append(f"DEFAULT_MODEL={new_model_id}\n")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"\n# Default active model\nDEFAULT_MODEL={new_model_id}\n")

    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def format_size(bytes_val):
    if not bytes_val or bytes_val <= 0:
        return "-"
    gb = bytes_val / (1024 ** 3)
    return f"{gb:.1f} GB"


def format_params(n_params):
    if not n_params or n_params <= 0:
        return "-"
    b = n_params / (10 ** 9)
    return f"{b:.1f}B"


def list_models_ui(models, current_model, connected_url):
    print("\n" + "=" * 70)
    print(f" 🦙 llama.cpp モデル一覧 (接続先: {connected_url})")
    print("=" * 70)
    print(f" {'No.':<4} {'ステータス':<10} {'モデルID':<24} {'サイズ':<9} {'パラメータ':<8} {'量子化'}")
    print("-" * 70)

    for idx, m in enumerate(models, 1):
        mid = m.get("id", "")
        status = m.get("status", {}).get("value", "unloaded")
        meta = m.get("meta", {})
        size = format_size(meta.get("size", 0))
        params = format_params(meta.get("n_params", 0))
        ftype = meta.get("ftype", "-")

        status_str = "🟢 LOADED" if status == "loaded" else "⚪ unloaded"
        prefix = "👉" if mid.lower() == current_model.lower() else "  "

        print(f"{prefix}{idx:<2}  {status_str:<10} {mid:<24} {size:<9} {params:<8} {ftype}")

    print("-" * 70)
    print(f" 👉 現在選択中: {current_model}")
    print("=" * 70 + "\n")


def main():
    args = sys.argv[1:]

    try:
        models, connected_url = fetch_models()
    except Exception as e:
        print(f"❌ エラー: {e}", file=sys.stderr)
        sys.exit(1)

    current_model = get_current_model()

    if "--current" in args:
        print(current_model)
        return

    if "--list" in args:
        list_models_ui(models, current_model, connected_url)
        return

    # 引数で直接モデル名または番号が指定された場合
    target_model = None
    if len(args) == 1 and not args[0].startswith("-"):
        arg_val = args[0]
        if arg_val.isdigit():
            idx = int(arg_val) - 1
            if 0 <= idx < len(models):
                target_model = models[idx]["id"]
            else:
                print(f"❌ 無効な番号です: {arg_val}", file=sys.stderr)
                sys.exit(1)
        else:
            matching = [m["id"] for m in models if m["id"].lower() == arg_val.lower()]
            if matching:
                target_model = matching[0]
            else:
                partial = [m["id"] for m in models if arg_val.lower() in m["id"].lower()]
                if len(partial) == 1:
                    target_model = partial[0]
                elif len(partial) > 1:
                    print(f"⚠️ 複数のモデルが該当しました: {', '.join(partial)}")
                    sys.exit(1)
                else:
                    target_model = arg_val

    # 対話モード
    if not target_model:
        list_models_ui(models, current_model, connected_url)
        try:
            choice = input(f"変更するモデルの番号 (1-{len(models)}) またはモデル名を入力 [Enterでキャンセル]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nキャンセルしました。")
            return

        if not choice:
            print("キャンセルしました。")
            return

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                target_model = models[idx]["id"]
            else:
                print(f"❌ 無効な番号です: {choice}")
                sys.exit(1)
        else:
            matching = [m["id"] for m in models if m["id"].lower() == choice.lower()]
            if matching:
                target_model = matching[0]
            else:
                target_model = choice

    # 設定ファイルの更新
    print(f"\n🔄 モデルを '{target_model}' に切り替えています...")
    try:
        update_opencode_config(target_model, models)
        print(f"  ✅ OpenCode設定更新: {OPENCODE_CONFIG_PATH}")
    except Exception as e:
        print(f"  ⚠️ OpenCode設定の更新に失敗: {e}")

    if ENV_FILE_PATH:
        try:
            update_env_file(target_model)
            print(f"  ✅ 環境変数更新: {ENV_FILE_PATH}")
        except Exception as e:
            print(f"  ⚠️ .env の更新に失敗: {e}")

    print(f"\n✨ 切り替え完了！ 現在のモデル: {target_model}")
    print("💡 Zoo Code / Cline をお使いの場合:")
    print(f"   Model ID: {target_model}")
    print(f"   Base URL: http://fastapi:8000/v1 (コンテナ内) または http://localhost:8000/v1 (ホスト)")
    print()


if __name__ == "__main__":
    main()
