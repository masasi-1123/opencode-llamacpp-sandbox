import json
import httpx
from fastapi import Request
from fastapi.responses import Response
from config import logger
from formatter import format_request_for_ollama
from backends.ollama.receiver import process_ollama_chat_response, create_passthrough_stream

async def handle_ollama_proxy(
    path: str,
    clean_path: str,
    is_chat: bool,
    request: Request,
    base_url: str
) -> Response:
    """
    Ollama バックエンドに対するリクエストの構築および送信を担当するメインエントリー関数。
    """
    if is_chat:
        target_url = f"{base_url}/api/chat"
    else:
        target_url = f"{base_url}/{path}"

    body = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            raw_body = await request.body()
            if raw_body:
                body = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            logger.warning(f"[JSON PARSE ERROR] {e}")

    client = httpx.AsyncClient(timeout=None)
    if is_chat and body is not None:
        formatted_body = format_request_for_ollama(body)
        is_stream = formatted_body.get("stream", False)
        formatted_body["stream"] = True

        req = client.build_request("POST", target_url, json=formatted_body)
        response = await client.send(req, stream=True)

        return await process_ollama_chat_response(response, client, formatted_body, is_stream)

    # 一般パススルー
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    req = client.build_request(request.method, target_url, json=body, headers=headers)
    llm_res = await client.send(req, stream=True)

    return create_passthrough_stream(llm_res, client)
