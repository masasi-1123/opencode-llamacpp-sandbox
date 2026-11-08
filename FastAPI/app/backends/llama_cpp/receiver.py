from datetime import datetime, timezone
import json
import httpx
from fastapi.responses import StreamingResponse, JSONResponse, Response
from config import logger, is_debug_logging

def _convert_tool_calls_to_ollama(tool_calls: list) -> list:
    """
    OpenAI 形式の tool_calls を Ollama ネイティブ形式に変換する。

    OpenAI 形式:
        [{"id": "call_0", "type": "function", "function": {"name": "...", "arguments": '{"key": "val"}'}}]
    Ollama 形式:
        [{"function": {"name": "...", "arguments": {"key": "val"}}}]

    主な違い:
        - arguments が JSON 文字列 → JSON オブジェクトに変換
        - id / type フィールドは不要 (Ollama は function キーのみ使用)
    """
    converted = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            converted.append(tc)
            continue
        fn = tc.get("function", {})
        args_raw = fn.get("arguments", {})
        # arguments が文字列の場合は JSON パースしてオブジェクトへ変換
        if isinstance(args_raw, str):
            try:
                args_obj = json.loads(args_raw) if args_raw.strip() else {}
            except json.JSONDecodeError:
                args_obj = {}
        else:
            args_obj = args_raw if isinstance(args_raw, dict) else {}
        converted.append({
            "function": {
                "name": fn.get("name", ""),
                "arguments": args_obj
            }
        })
    return converted

async def process_non_streaming_response(llm_res: httpx.Response, orig_model: str, is_chat_ollama: bool) -> Response:
    """
    llama.cpp からの非ストリーミング応答を受信・処理・形式変換する関数。
    """
    if llm_res.status_code >= 400:
        logger.error(f"[LLM RES ERROR] status={llm_res.status_code} body={llm_res.text[:300]}")
        try:
            return JSONResponse(content=llm_res.json(), status_code=llm_res.status_code)
        except Exception:
            return Response(content=llm_res.content, status_code=llm_res.status_code, media_type="application/json")

    try:
        res_json = llm_res.json()
    except Exception as e:
        logger.error(f"[LLM JSON PARSE ERROR] {e}, raw={llm_res.text[:200]}")
        return Response(content=llm_res.content, status_code=llm_res.status_code, media_type="application/json")

    choices = res_json.get("choices", [])
    choices_count = len(choices)
    has_content = False
    has_tool_calls = False
    has_reasoning = False
    finish_reason = None

    if choices:
        choice = choices[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason")
        has_content = bool(msg.get("content"))
        has_tool_calls = bool(msg.get("tool_calls"))
        reasoning = msg.get("reasoning_content") or msg.get("thinking")
        has_reasoning = bool(reasoning)

        if not msg.get("content") and not msg.get("tool_calls") and reasoning:
            msg["content"] = reasoning
            choice["message"] = msg
            has_content = True

    logger.info(
        f"[LLM RES] status={llm_res.status_code} choices_count={choices_count} "
        f"has_content={has_content} has_tool_calls={has_tool_calls} "
        f"has_reasoning={has_reasoning} finish_reason={finish_reason}"
    )

    if is_chat_ollama:
        msg_obj = choices[0].get("message", {}) if choices else {"role": "assistant", "content": ""}
        # OpenAI 形式の tool_calls を Ollama ネイティブ形式に変換
        # (arguments: JSON文字列 → JSON オブジェクト、id/type フィールドを除去)
        if "tool_calls" in msg_obj and msg_obj["tool_calls"]:
            msg_obj = dict(msg_obj)
            msg_obj["tool_calls"] = _convert_tool_calls_to_ollama(msg_obj["tool_calls"])
        ollama_res = {
            "model": orig_model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "message": msg_obj,
            "done": True,
            "done_reason": finish_reason or "stop"
        }
        return JSONResponse(content=ollama_res, status_code=200)

    return JSONResponse(content=res_json, status_code=200)

def create_streaming_response(llm_res: httpx.Response, client: httpx.AsyncClient, orig_model: str, is_chat_ollama: bool) -> StreamingResponse:
    """
    llama.cpp からのストリーミング応答を受信し、適切なフォーマット (Ollama NDJSON または OpenAI SSE) で送出する関数。
    """
    debug = is_debug_logging()

    if is_chat_ollama:
        async def stream_ollama_from_llama_cpp():
            done_sent = False
            # llama.cpp は tool_calls の arguments を複数チャンクに分割してストリーミングする。
            # 各チャンクを即座に送出すると Zoo Code が不完全な引数を受け取るため、
            # finish_reason を受け取るまで蓄積してから一括送出する。
            # key: index (int), value: {"id": ..., "type": ..., "function": {"name": ..., "arguments": ""}}
            tool_calls_acc: dict = {}
            has_tool_calls = False
            try:
                async for line in llm_res.aiter_lines():
                    if line is None:
                        continue
                    line_str = line.strip()
                    if not line_str:
                        continue
                    if debug:
                        logger.debug(f"[LLM OLLAMA CONVERT] {line_str[:120]}")

                    if line_str == "data: [DONE]":
                        # ツール呼び出しが蓄積済みの場合は完全な形で送出
                        if has_tool_calls and tool_calls_acc:
                            tool_calls_list = []
                            for k in sorted(tool_calls_acc.keys()):
                                acc = tool_calls_acc[k]
                                args_str = acc["arguments_str"]
                                try:
                                    args_obj = json.loads(args_str) if args_str.strip() else {}
                                except json.JSONDecodeError:
                                    args_obj = {}
                                tool_calls_list.append({
                                    "function": {"name": acc["name"], "arguments": args_obj}
                                })
                            tc_chunk = {
                                "model": orig_model,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "message": {"role": "assistant", "content": "", "tool_calls": tool_calls_list},
                                "done": True,
                                "done_reason": "tool_calls"
                            }
                            yield json.dumps(tc_chunk, ensure_ascii=False) + "\n"
                        else:
                            final_chunk = {
                                "model": orig_model,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "message": {"role": "assistant", "content": ""},
                                "done": True,
                                "done_reason": "stop"
                            }
                            yield json.dumps(final_chunk, ensure_ascii=False) + "\n"
                        done_sent = True
                        break

                    if line_str.startswith("data: "):
                        try:
                            chunk_data = json.loads(line_str[6:].strip())
                        except Exception:
                            continue

                        choices = chunk_data.get("choices", [])
                        if not choices:
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason")

                        content = delta.get("content", "")
                        reasoning = delta.get("reasoning_content") or delta.get("thinking", "")

                        # tool_calls チャンクの蓄積
                        # llama.cpp は {"index": 0, "id": "...", "function": {"name": "...", "arguments": "部分的なJSON"}}
                        # という形式を複数チャンクに分けて送ってくる。
                        # arguments は JSON 文字列の断片なので、全チャンクを結合してから
                        # json.loads でオブジェクトに変換する (Ollama 形式は arguments がオブジェクト)。
                        if "tool_calls" in delta:
                            has_tool_calls = True
                            for tc_chunk in delta["tool_calls"]:
                                idx = tc_chunk.get("index", 0)
                                if idx not in tool_calls_acc:
                                    # 蓄積バッファ: arguments_str は結合用の生文字列
                                    tool_calls_acc[idx] = {
                                        "name": tc_chunk.get("function", {}).get("name", ""),
                                        "arguments_str": ""
                                    }
                                acc = tool_calls_acc[idx]
                                fn = tc_chunk.get("function", {})
                                if fn.get("name"):
                                    acc["name"] = fn["name"]
                                # arguments は分割された文字列なので結合する
                                acc["arguments_str"] += fn.get("arguments", "")

                        # content / reasoning はリアルタイムにストリーミング送出（tool_calls は除く）
                        if (content or reasoning) and not has_tool_calls:
                            msg_obj = {"role": "assistant", "content": content or ""}
                            if reasoning:
                                msg_obj["thinking"] = reasoning
                            ollama_chunk = {
                                "model": orig_model,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "message": msg_obj,
                                "done": False
                            }
                            yield json.dumps(ollama_chunk, ensure_ascii=False) + "\n"

                        if finish_reason is not None:
                            if has_tool_calls and tool_calls_acc:
                                # 蓄積した arguments_str を JSON パースして Ollama 形式に変換
                                tool_calls_list = []
                                for k in sorted(tool_calls_acc.keys()):
                                    acc = tool_calls_acc[k]
                                    args_str = acc["arguments_str"]
                                    try:
                                        args_obj = json.loads(args_str) if args_str.strip() else {}
                                    except json.JSONDecodeError:
                                        logger.warning(f"[TOOL CALLS] Failed to parse arguments JSON: {args_str[:100]}")
                                        args_obj = {}
                                    tool_calls_list.append({
                                        "function": {"name": acc["name"], "arguments": args_obj}
                                    })
                                logger.info(
                                    f"[TOOL CALLS] Emitting {len(tool_calls_list)} tool call(s): "
                                    f"{[tc['function']['name'] for tc in tool_calls_list]}"
                                )
                                final_chunk = {
                                    "model": orig_model,
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                    "message": {"role": "assistant", "content": "", "tool_calls": tool_calls_list},
                                    "done": True,
                                    "done_reason": finish_reason
                                }
                            else:
                                final_chunk = {
                                    "model": orig_model,
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                    "message": {"role": "assistant", "content": ""},
                                    "done": True,
                                    "done_reason": finish_reason
                                }
                            yield json.dumps(final_chunk, ensure_ascii=False) + "\n"
                            done_sent = True
                            break
            finally:
                if not done_sent:
                    if has_tool_calls and tool_calls_acc:
                        tool_calls_list = []
                        for k in sorted(tool_calls_acc.keys()):
                            acc = tool_calls_acc[k]
                            args_str = acc["arguments_str"]
                            try:
                                args_obj = json.loads(args_str) if args_str.strip() else {}
                            except json.JSONDecodeError:
                                args_obj = {}
                            tool_calls_list.append({
                                "function": {"name": acc["name"], "arguments": args_obj}
                            })
                        final_chunk = {
                            "model": orig_model,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "message": {"role": "assistant", "content": "", "tool_calls": tool_calls_list},
                            "done": True,
                            "done_reason": "tool_calls"
                        }
                    else:
                        final_chunk = {
                            "model": orig_model,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "message": {"role": "assistant", "content": ""},
                            "done": True,
                            "done_reason": "stop"
                        }
                    yield json.dumps(final_chunk, ensure_ascii=False) + "\n"
                await llm_res.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_ollama_from_llama_cpp(),
            status_code=200,
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )

    else:
        async def stream_sse_passthrough():
            try:
                async for line in llm_res.aiter_lines():
                    if line is None:
                        continue
                    if debug and line:
                        logger.debug(f"[LLM SSE] {line[:120]}")
                    yield f"{line}\n"
            finally:
                await llm_res.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_sse_passthrough(),
            status_code=200,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
