import json
import httpx
from fastapi.responses import StreamingResponse, JSONResponse, Response
from formatter import parse_response_from_ollama

async def process_ollama_chat_response(
    response: httpx.Response,
    client: httpx.AsyncClient,
    formatted_body: dict,
    is_stream: bool
) -> Response:
    """
    Ollama サーバーからのチャットレスポンスを受信・蓄積・パースし、疑似ストリームまたは非ストリーミング応答を返却する関数。
    """
    full_content = ""
    native_tool_calls = []

    try:
        async for line in response.aiter_lines():
            if not line:
                continue
            line_str = line.strip()
            if line_str.startswith("data: "):
                line_str = line_str[6:].strip()
                if line_str == "[DONE]":
                    break
            try:
                chunk_json = json.loads(line_str)
                if "message" in chunk_json and isinstance(chunk_json["message"], dict):
                    msg = chunk_json["message"]
                    if "thinking" in msg and msg["thinking"]:
                        full_content += msg["thinking"]
                    if "content" in msg and msg["content"]:
                        full_content += msg["content"]
                    if "tool_calls" in msg and msg["tool_calls"]:
                        native_tool_calls = msg["tool_calls"]
            except Exception:
                continue
    finally:
        await response.aclose()
        await client.aclose()

    parsed_result = parse_response_from_ollama(full_content)
    if native_tool_calls:
        parsed_result["message"]["tool_calls"] = native_tool_calls
        clean_content = parsed_result["message"].get("content", full_content)
        parsed_result["message"]["content"] = clean_content if clean_content else None
        parsed_result["finish_reason"] = "tool_calls"

    if is_stream:
        async def generate_pseudo_stream():
            content_chunk = {
                "id": "chatcmpl-pseudo",
                "object": "chat.completion.chunk",
                "model": formatted_body.get("model", "unknown"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}]
            }
            if "tool_calls" in parsed_result["message"]:
                content_chunk["choices"][0]["delta"]["tool_calls"] = parsed_result["message"]["tool_calls"]
            else:
                content_chunk["choices"][0]["delta"]["content"] = parsed_result["message"]["content"]

            yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n"
            finish_chunk = {
                "id": "chatcmpl-pseudo",
                "object": "chat.completion.chunk",
                "model": formatted_body.get("model", "unknown"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": parsed_result["finish_reason"]}]
            }
            yield f"data: {json.dumps(finish_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate_pseudo_stream(), media_type="text/event-stream")
    else:
        final_response = {
            "id": "chatcmpl-pseudo",
            "object": "chat.completion",
            "model": formatted_body.get("model", "unknown"),
            "choices": [{"index": 0, "message": parsed_result["message"], "finish_reason": parsed_result["finish_reason"]}]
        }
        return JSONResponse(content=final_response)

def create_passthrough_stream(llm_res: httpx.Response, client: httpx.AsyncClient) -> StreamingResponse:
    """
    一般のリクエストに対する透過プロキシストリーム。
    """
    async def pass_through_stream():
        try:
            async for chunk in llm_res.aiter_raw():
                yield chunk
        finally:
            await llm_res.aclose()
            await client.aclose()

    return StreamingResponse(pass_through_stream(), status_code=llm_res.status_code, headers=dict(llm_res.headers))
