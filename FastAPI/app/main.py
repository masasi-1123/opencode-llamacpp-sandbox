from fastapi import FastAPI, Request
from config import get_backend_info
from backends.llama_cpp.sender import handle_llama_cpp_proxy
from backends.ollama.sender import handle_ollama_proxy

app = FastAPI(title="LLM Proxy Service")

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_handler(path: str, request: Request):
    """
    クライアント (Zoo Code, Open WebUI等) からのリクエストを受け取り、
    設定されているバックエンド (llama.cpp / Ollama) の送信・受信ハンドラーへ割り振りを行うメインプロキシ。
    """
    backend_type, base_url = get_backend_info()

    # パスの正規化 (v1/api/chat -> api/chat など)
    if path.startswith("v1/api/"):
        path = path[3:]
    clean_path = path.strip("/")

    is_chat_openai = (clean_path == "v1/chat/completions")
    is_chat_ollama = (clean_path == "api/chat")
    is_chat = is_chat_openai or is_chat_ollama

    if backend_type == "llama-cpp":
        return await handle_llama_cpp_proxy(
            path=path,
            clean_path=clean_path,
            is_chat_openai=is_chat_openai,
            is_chat_ollama=is_chat_ollama,
            request=request,
            base_url=base_url
        )
    else:
        return await handle_ollama_proxy(
            path=path,
            clean_path=clean_path,
            is_chat=is_chat,
            request=request,
            base_url=base_url
        )