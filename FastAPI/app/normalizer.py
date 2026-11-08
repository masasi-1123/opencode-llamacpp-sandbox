def normalize_messages(messages: list) -> list:
    """
    Zoo Code等から送られる messages 内の content がリスト形式 ([{"type":"text","text":"..."}]) の場合に
    単一の文字列へ結合する。また assistant ロールで content が空の場合は reasoning_content 等で補完。
    さらに、tool_calls 内の各エントリに "type": "function" が欠けている場合も補完する。
    (llama.cpp は "type" フィールドを必須とするため)
    """
    normalized = []
    for msg in messages:
        if not isinstance(msg, dict):
            normalized.append(msg)
            continue
        new_msg = msg.copy()
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            new_msg["content"] = "\n".join(text_parts)
        elif not content and new_msg.get("role") == "assistant":
            reasoning = new_msg.get("reasoning_content") or new_msg.get("thinking")
            if reasoning:
                new_msg["content"] = reasoning

        # tool_calls の "type" フィールド補完
        # Zoo Code は {"function": {...}} 形式で送ってくるが、
        # llama.cpp は {"type": "function", "function": {...}} を要求する
        if "tool_calls" in new_msg and isinstance(new_msg["tool_calls"], list):
            fixed_tool_calls = []
            for tc in new_msg["tool_calls"]:
                if isinstance(tc, dict) and "type" not in tc:
                    tc = {"type": "function", **tc}
                fixed_tool_calls.append(tc)
            new_msg["tool_calls"] = fixed_tool_calls

        normalized.append(new_msg)
    return normalized
