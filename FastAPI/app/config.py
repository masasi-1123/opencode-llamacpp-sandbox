import logging
import os

# ログ出力用の設定を初期化
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_proxy")

def is_debug_logging() -> bool:
    """環境変数 DEBUG_LLM_PROXY が true に設定されているか判定"""
    return os.getenv("DEBUG_LLM_PROXY", "false").lower() in ("true", "1", "yes")

def get_backend_info():
    """
    環境変数 LLM_BACKEND (または LLM_TARGET, BACKEND_TYPE) に基づき接続先情報を取得
    - "ollama" -> ( "ollama", OLLAMA_BASE_URL )
    - "llama.cpp" / "llama-cpp" / "llamacpp" -> ( "llama-cpp", LLAMA_CPP_BASE_URL )
    """
    backend = os.getenv("LLM_BACKEND", os.getenv("LLM_TARGET", os.getenv("BACKEND_TYPE", "llama.cpp"))).strip().lower()
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
    llama_cpp_url = os.getenv("LLAMA_CPP_BASE_URL", "http://llama-cpp:8080").rstrip("/")

    if backend in ["llama-cpp", "llama.cpp", "llamacpp", "llama"]:
        return "llama-cpp", llama_cpp_url
    else:
        return "ollama", ollama_url

def resolve_model_alias(requested_model: str) -> str:
    """
    Zoo Codeなどから送られてきたモデル名を、llama.cpp側のモデルID / プリセット名へマッピングする。
    gemma4:e4b や gemma4:e2b は Ollama固有Blobでllama-server直接読み込み不可のため、
    デフォルトで動作確認済みの gemma4:12B や ornith:9B へマッピング。
    環境変数 GEMMA4_E4B_MODEL_ID 等で指定があればそれを優先。
    """
    model_map = {
        "gemma4:e4b": os.getenv("GEMMA4_E4B_MODEL_ID", os.getenv("GEMMA4_MODEL_ID", "gemma4:12B")),
        "gemma4:e2b": os.getenv("GEMMA4_E2B_MODEL_ID", "gemma4:12B"),
        "gemma4:12b": os.getenv("GEMMA4_12B_MODEL_ID", "gemma4:12B"),
        "gemma4:26b": os.getenv("GEMMA4_26B_MODEL_ID", "gemma4:26B"),
        "ornith": os.getenv("ORNITH_MODEL_ID", "ornith"),
        "ornith:9b": os.getenv("ORNITH_MODEL_ID", "ornith:9B"),
        "ornith-1.5:9b": os.getenv("ORNITH_1_5_MODEL_ID", "ornith-1.5:9B"),
        "ornith-1.5:35b": os.getenv("ORNITH_1_5_35B_MODEL_ID", "ornith-1.5:35B"),
        "qwen2.5-coder:14b": os.getenv("QWEN2_5_CODER_MODEL_ID", "qwen2.5-coder:14B"),
        "qwen3:14b": os.getenv("QWEN3_MODEL_ID", "qwen3:14B"),
        "qwen3.5:9b": os.getenv("QWEN3_5_MODEL_ID", "qwen3.5:9B"),
    }
    req_lower = requested_model.lower()
    return model_map.get(requested_model, model_map.get(req_lower, requested_model))
