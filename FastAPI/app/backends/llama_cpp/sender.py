import json
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from config import logger, resolve_model_alias
from normalizer import normalize_messages
from backends.llama_cpp.receiver import process_non_streaming_response, create_streaming_response

async def handle_llama_cpp_proxy(
    path: str,
    clean_path: str,
    is_chat_openai: bool,
    is_chat_ollama: bool,
    request: Request,
    base_url: str
) -> Response:
    """
    llama.cpp バックエンドに対するリクエストの構築および送信を担当するメインエントリー関数。
    """
    is_chat = is_chat_openai or is_chat_ollama

    # エンドポイント判定
    if is_chat:
        target_url = f"{base_url}/v1/chat/completions"
    elif clean_path in ["v1/models", "models"]:
        target_url = f"{base_url}/v1/models"
    elif clean_path == "api/tags":
        target_url = f"{base_url}/v1/models"
    else:
        target_url = f"{base_url}/{path}"

    # GET /v1/models または GET /models または GET /api/tags
    if request.method == "GET" and clean_path in ["v1/models", "models", "api/tags"]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(target_url)
                if res.status_code != 200:
                    return Response(content=res.content, status_code=res.status_code, media_type="application/json")
                
                data = res.json()
                # Ollamaの /api/tags 形式への変換リクエストの場合
                if clean_path == "api/tags":
                    models_list = data.get("data", []) if isinstance(data, dict) else []
                    ollama_tags = {
                        "models": [
                            {"name": m.get("id", "unknown"), "model": m.get("id", "unknown")}
                            for m in models_list
                        ]
                    }
                    return JSONResponse(content=ollama_tags, status_code=200)
                
                return JSONResponse(content=data, status_code=200)
            except Exception as e:
                logger.error(f"[MODELS PROXY ERROR] {e}")
                return JSONResponse(content={"error": {"message": str(e)}}, status_code=502)

    # チャットリクエスト (/v1/chat/completions または /api/chat)
    if is_chat and request.method in ["POST", "PUT", "PATCH"]:
        try:
            raw_body = await request.body()
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as e:
            logger.error(f"[JSON PARSE ERROR] {e}")
            return JSONResponse(content={"error": {"message": "Invalid JSON body"}}, status_code=400)

        # モデルIDのマッピング
        orig_model = body.get("model", "")
        target_model = resolve_model_alias(orig_model)
        body["model"] = target_model

        # messages の正規化
        if "messages" in body and isinstance(body["messages"], list):
            body["messages"] = normalize_messages(body["messages"])

        is_stream = body.get("stream", False)
        messages = body.get("messages", [])
        tools = body.get("tools", [])
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")

        logger.info(
            f"[LLM REQ] backend=llama-cpp orig_model='{orig_model}' target_model='{target_model}' "
            f"is_chat_ollama={is_chat_ollama} msgs_count={len(messages)} tools_count={len(tools)} stream={is_stream} "
            f"temp={temperature} max_tokens={max_tokens}"
        )

        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        # --- 非ストリーミング処理 ---
        if not is_stream:
            async with httpx.AsyncClient(timeout=None) as client:
                try:
                    llm_res = await client.post(target_url, json=body, headers=headers)
                except Exception as e:
                    logger.error(f"[LLM REQ ERROR] Failed to connect to llama-server: {e}")
                    return JSONResponse(
                        content={"error": {"message": f"Proxy connection failed: {str(e)}", "type": "proxy_error"}},
                        status_code=502
                    )

                return await process_non_streaming_response(llm_res, orig_model, is_chat_ollama)

        # --- ストリーミング処理 ---
        else:
            client = httpx.AsyncClient(timeout=None)
            req = client.build_request("POST", target_url, json=body, headers=headers)
            try:
                llm_res = await client.send(req, stream=True)
            except Exception as e:
                await client.aclose()
                logger.error(f"[LLM STREAM REQ ERROR] {e}")
                return JSONResponse(content={"error": {"message": str(e)}}, status_code=502)

            if llm_res.status_code >= 400:
                error_bytes = await llm_res.aread()
                await llm_res.aclose()
                await client.aclose()
                logger.error(f"[LLM STREAM ERROR] status={llm_res.status_code} body={error_bytes.decode('utf-8', errors='ignore')[:300]}")
                try:
                    return JSONResponse(content=json.loads(error_bytes.decode("utf-8")), status_code=llm_res.status_code)
                except Exception:
                    return Response(content=error_bytes, status_code=llm_res.status_code, media_type="application/json")

            return create_streaming_response(llm_res, client, orig_model, is_chat_ollama)

    # 一般パススルー
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    async with httpx.AsyncClient(timeout=None) as client:
        req = client.build_request(request.method, target_url, headers=headers)
        llm_res = await client.send(req, stream=True)
        return StreamingResponse(llm_res.aiter_raw(), status_code=llm_res.status_code, headers=dict(llm_res.headers))
