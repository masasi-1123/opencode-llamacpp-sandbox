import json
import re
from typing import Dict, Any, List

def format_request_for_ollama(request_body: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    概要: Zoo codeからのリクエスト(OpenAI形式)をOllama向けに成形する関数。
          特定のモデル(gemma4, qwen)の場合のみ、プロンプトを改変しTool使用のフォーマットを強制します。
    引数:
        request_body (Dict[Any, Any]): Zoo codeから送られてきたJSONリクエスト本体
    返却値: 
        Dict[Any, Any]: Ollamaに送信するためのJSONリクエスト本体(成形済み)
    """
    # 変更を加えないように、リクエスト内容をコピーして新しい辞書を作成する
    formatted_body = request_body.copy()
    
    # リクエストからモデル名を取得 (例: "gemma4:12b")
    model_name = formatted_body.get("model", "")
    
    # メッセージのcontentがリスト形式(OpenAI multipart形式)の場合、Ollamaが受け付ける文字列に変換する
    # Zoo codeは content: [{"type": "text", "text": "..."}, ...] という形式で送信してくる
    normalized_messages = []
    for msg in formatted_body.get("messages", []):
        normalized_msg = msg.copy()
        content = msg.get("content")
        # contentがリスト(配列)の場合の分岐
        if isinstance(content, list):
            # typeがtextの要素のtextフィールドを結合して1つの文字列にする
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            normalized_msg["content"] = "\n".join(text_parts)
        normalized_messages.append(normalized_msg)
    formatted_body["messages"] = normalized_messages

    # Ollamaのネイティブツール機能を使うため、tools はそのまま渡す
    # ただし、モデルが確実に指定フォーマットで出力するようにシステムプロンプトを注入する
    if "tools" in formatted_body and formatted_body["tools"]:
        tool_instruction = (
            "【重要】ツール（関数）を呼び出す場合は、必ず以下のXMLタグとJSONを用いたフォーマットで出力してください。\n"
            "<tool_call>{\"name\": \"関数名\", \"arguments\": {\"引数名\": \"値\"}}</tool_call>\n"
            "これ以外の形式（例：マークダウンのコードブロックなど）は使用しないでください。"
        )
        system_msgs = [m for m in normalized_messages if m.get("role") == "system"]
        if system_msgs:
            system_msgs[0]["content"] += "\n\n" + tool_instruction
        else:
            normalized_messages.insert(0, {"role": "system", "content": tool_instruction})
    
    # コンテキスト長を131072トークン(128k)以上に設定
    if "options" not in formatted_body:
        formatted_body["options"] = {}
    if "num_ctx" not in formatted_body["options"] or formatted_body["options"]["num_ctx"] < 131072:
        formatted_body["options"]["num_ctx"] = 131072

    # 成形された(またはパススルーの)リクエストボディを返却する
    return formatted_body

def parse_response_from_ollama(ollama_text: str) -> Dict[str, Any]:
    """
    概要: Ollamaからのテキスト応答をパースし、Tool Callが含まれているか判定・抽出する関数。
          XMLタグで出力されたTool呼び出しをOpenAI互換形式に変換します。
    引数:
        ollama_text (str): Ollamaから返却されたテキスト(ストリームの結合後の全テキスト)
    返却値: 
        Dict[str, Any]: Zoo codeへ返すためのOpenAI形式のmessageとfinish_reasonを含む辞書
    """
    # 正規表現を使用して <tool_call>...</tool_call> のパターンを検索・抽出するためのコンパイル
    tool_call_pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
    
    # マークダウンのJSONブロック (```json ... ```) を抽出するためのパターン (Qwen等へのフォールバック)
    json_block_pattern = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
    
    # マッチしたすべての箇所をリストとして取得
    matches = tool_call_pattern.findall(ollama_text)
    
    # <tool_call> が見つからなかった場合はJSONブロックを探す
    if not matches:
        json_matches = json_block_pattern.findall(ollama_text)
        for jm in json_matches:
            try:
                # パースしてみて、nameとargumentsが含まれていればTool Callとみなす
                data = json.loads(jm)
                if "name" in data and "arguments" in data:
                    matches.append(jm)
            except json.JSONDecodeError:
                continue
    
    # マッチした場合(Tool Callが存在する場合)の分岐
    if matches:
        # コンテンツからツール呼び出しの文字列を削除して、思考プロセスだけを残す
        clean_content = ollama_text
        for m in tool_call_pattern.finditer(ollama_text):
            clean_content = clean_content.replace(m.group(0), "")
        for m in json_block_pattern.finditer(ollama_text):
            try:
                data = json.loads(m.group(1))
                if "name" in data and "arguments" in data:
                    clean_content = clean_content.replace(m.group(0), "")
            except Exception:
                pass
        
        clean_content = clean_content.strip()
        if not clean_content:
            clean_content = None

        # 抽出したTool Callを格納するための空リストを変数として定義
        tool_calls = []
        
        # 複数ツールが呼ばれる可能性も考慮し、マッチした結果をループ処理
        for idx, match in enumerate(matches):
            try:
                # 中身はJSON文字列であることを期待しているためパースする
                call_data = json.loads(match)
                
                # OpenAI互換のTool Call形式に成形し、リストに追加する
                tool_calls.append({
                    "id": f"call_{idx}",
                    "type": "function",
                    "function": {
                        # ツール名を取得（存在しない場合は空文字）
                        "name": call_data.get("name", ""),
                        # argumentsはJSONのオブジェクトなので、文字列化(JSONエンコード)して格納する
                        "arguments": json.dumps(call_data.get("arguments", {}), ensure_ascii=False)
                    }
                })
            except json.JSONDecodeError:
                # JSONパースに失敗した場合の分岐(無視して次のマッチに進む)
                continue
                
        # もし1つでも正常にパースできてtool_callsリストに要素がある場合の分岐
        if tool_calls:
            # ツール呼び出し用のレスポンス構造を返却する
            return {
                "message": {
                    "role": "assistant",
                    # 思考プロセスを残すため、タグを除去したテキストをセットする
                    "content": clean_content, 
                    "tool_calls": tool_calls
                },
                # finish_reasonをtool_callsに設定し、クライアントにツール実行を促す
                "finish_reason": "tool_calls"
            }
            
    # Tool Callが存在しなかった場合(通常応答)の分岐
    # そのまま通常のテキストメッセージとして返却する
    return {
        "message": {
            "role": "assistant",
            "content": ollama_text
        },
        # finish_reasonをstopに設定し、応答完了を示す
        "finish_reason": "stop"
    }
