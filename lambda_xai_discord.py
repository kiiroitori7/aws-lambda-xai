import os, json, urllib.request, urllib.error, time
from datetime import datetime, timedelta

# ========== 環境變數 ==========
API_KEY      = os.environ["GROK_API_KEY"]
WEBHOOK      = os.environ["DISCORD_WEBHOOK_URL"]
X_HANDLES    = [h.strip() for h in os.environ.get("X_HANDLES", "").split(",") if h.strip()]
DAYS         = int(os.environ.get("DAYS", "1"))
MAX_RESULTS  = int(os.environ.get("MAX_RESULTS", "2"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "55"))  # 記得讓 Lambda Timeout > 這個值
DEBUG        = os.environ.get("DEBUG", "0") == "1"

# ========== Header ==========
DEFAULT_HEADERS = {
    "User-Agent": "curl/8.5.0",         # 避免被 Cloudflare/WAF 擋
    "Accept": "application/json",
}


HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "User-Agent": "aws-lambda-grok/1.0"
}

def _post_json(url: str, payload: dict, headers: dict | None = None,
               method: str = "POST", timeout: int | None = None):
    """
    單次請求：預設 110 秒，關閉重試（避免超過 Lambda 180 秒總時限）
    """
    HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "110"))  # 建議環境變數也設 110
    timeout = timeout or HTTP_TIMEOUT

    base_headers = {
        "User-Agent": "curl/8.5.0",
        "Accept": "application/json",
    }
    if headers:
        base_headers.update(headers)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=base_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body_text = resp.read().decode("utf-8", "ignore")
            ctype = resp.headers.get("Content-Type", "")
            return json.loads(body_text) if "application/json" in ctype else body_text
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTPError {e.code}: {body}")
    except urllib.error.URLError as e:
        # 不重試，直接拋出，讓上層決定要不要發 Discord 告警
        raise RuntimeError(str(e))




def _get_json(url: str, headers: dict):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=min(HTTP_TIMEOUT, 20)) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _discord_send_chunks(prefix: str, text: str):
    """
    Discord webhook 每則訊息上限 ~2000 chars。
    這裡用 1900 的安全邊界分段送出，並加上 prefix。
    """
    MAX_LEN = 1900
    if not text:
        return
    chunks = []
    while text:
        chunk = text[:MAX_LEN]
        text = text[MAX_LEN:]
        chunks.append(chunk)

    for i, c in enumerate(chunks, 1):
        content = f"{prefix} (part {i}/{len(chunks)})\n{c}" if len(chunks) > 1 else f"{prefix}\n{c}"
        _post_json(WEBHOOK, {"content": content}, headers={"Content-Type": "application/json"}, timeout=20)

def post_discord_both(summary_text: str, raw_response: dict):
    """
    先送摘要，再送 xAI 原始 JSON（code block + 分段）。
    """
    # 1) 摘要
    _post_json(WEBHOOK,
               {"content": f"📢 **X 自動摘要**\n{summary_text if summary_text else '（無更新）'}"},
               headers={"Content-Type": "application/json"},
               timeout=20)

    # 2) 原始 JSON（截斷到 100k 字以免過大）
    try:
        raw_str = json.dumps(raw_response, ensure_ascii=False, indent=2)
    except Exception:
        raw_str = str(raw_response)

    if len(raw_str) > 100_000:
        raw_str = raw_str[:100_000] + "\n... (truncated)"

    # 包成 code block，保持可讀性
    _discord_send_chunks("xAI raw JSON", "```json\n" + raw_str + "\n```")


def post_discord(text: str):
    # Discord Webhook 不需要 Authorization，只要 Content-Type
    payload = {"content": text}
    _post_json(
        WEBHOOK,
        payload,
        headers={"Content-Type": "application/json"}, timeout=20  # UA/Accept 由 _post_json 自動補
    )

from datetime import datetime, timedelta, timezone

def _parse_iso_utc(s: str) -> datetime:
    # 支援帶 Z 或 +00:00 的 ISO 字串
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)

def search_and_summarize(handles, since_days, max_results):
    now_utc = datetime.now(timezone.utc)
    # 你想以 JST 視角抓「近 N 天」也可以：jst = timezone(timedelta(hours=9))
    # 然後以 JST 0:00 對齊再轉回 UTC；為簡潔這裡先用 UTC 連續時段
    start_utc = now_utc - timedelta(days=since_days)

    system = (
        "你是檢索助手。只回傳 JSON，嚴格只包含時間在 [start_utc, end_utc) 之間的貼文；"
        "每個項目為 {account, title, url, posted_at_utc}，posted_at_utc 用 UTC ISO8601，例 2025-11-03T04:15:00Z。"
        "不要多餘文字。"
    )
    user = (
        f"accounts={handles}; "
        f"start_utc={start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}; "
        f"end_utc={now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}; "
        f"per_account_max={max_results}"
    )

    body = {
        "model": "grok-4-latest",
        "response_format": {"type": "json_object"},  # 要求 JSON（xAI 相容 OpenAI 風格）
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "search_parameters": {
            "mode": "on",
            "sources": [{"type": "x", "included_x_handles": handles}],
            # 仍然帶上區間，做第一層篩；最終還是以本地過濾為準
            "from_date": start_utc.strftime("%Y-%m-%d"),
            "to_date":   now_utc.strftime("%Y-%m-%d"),
            "max_results": max_results
        }
    }

    resp = _post_json("https://api.x.ai/v1/chat/completions", body, HEADERS, timeout=95)

    data = json.loads(resp["choices"][0]["message"]["content"])

    items = data.get("items", [])
    # 第二層：本地硬性過濾（保險）
    filtered = []
    for it in items:
        try:
            ts = _parse_iso_utc(it.get("posted_at_utc", ""))
            if start_utc <= ts < now_utc:
                filtered.append(it)
        except Exception:
            # 沒有合法時間就略過
            continue

    # 組成你熟悉的 Discord 文字
    lines = []
    for it in filtered:
        acc = it.get("account","")
        title = it.get("title","").strip()
        url = it.get("url","")
        # 如果你想顯示 JST 時間：
        jst = timezone(timedelta(hours=9))
        jst_str = _parse_iso_utc(it["posted_at_utc"]).astimezone(jst).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{acc}、{title}、{jst_str}、{url}")
    
def extract_summary(resp_json):
    """從 xAI chat/completions JSON 取出文字內容；失敗時回傳空字串"""
    try:
        return (resp_json["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""

def post_discord_json(title: str, data: dict, max_chars: int = 1800):
    """把 JSON 轉成漂亮 code block 丟 Discord，避免超過訊息長度"""
    pretty = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    if len(pretty) > max_chars:
        pretty = pretty[:max_chars] + "\n…(truncated)"
    text = f"**{title}**\n```json\n{pretty}\n```"
    _post_json(WEBHOOK, {"content": text},
               headers={"Content-Type": "application/json"}, timeout=20)

def summarize_from_xai_json(resp_json: dict) -> str:
    """
    從 xAI chat/completions 的原始 JSON 取出 content(JSON字串) -> 轉成 dict -> 組成你要貼到 Discord 的文字。
    允許兩種欄位命名：
      1) account, title, start_date_jst, url   （你在 search_live_raw 的 system prompt 要求的）
      2) account, title, posted_at_utc, url   （備援：若模型回的是 UTC 時間）
    """
    try:
        content = (resp_json["choices"][0]["message"]["content"] or "").strip()
        data = json.loads(content)
    except Exception:
        return ""  # 拿不到就回空字串，讓上層貼「（無更新）」即可

    items = data.get("items") or data.get("results") or []
    if not isinstance(items, list):
        return ""

    lines = []
    for it in items:
        acc   = (it.get("account") or "").strip()
        title = (it.get("title")   or "").strip()
        url   = (it.get("url")     or "").strip()

        # 盡量以 JST 顯示；若只有 UTC 也接受
        when = (it.get("start_date_jst") or it.get("posted_at_utc") or "").strip()
        if not (acc and title and url):
            continue

        # 簡單防呆：若是 UTC 格式，轉為 JST 顯示
        try:
            if when and "T" in when and "Z" in when:
                from datetime import timezone, timedelta, datetime as _dt
                ts  = _dt.fromisoformat(when.replace("Z", "+00:00"))
                jst = timezone(timedelta(hours=9))
                when = ts.astimezone(jst).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        lines.append(f"{acc}、{title}、{when}、{url}")

    return "\n".join(lines)


def post_discord_debug_json(title: str, data: dict, max_chars: int = 1800):
    # 直接重用 post_discord_json 的邏輯
    return post_discord_json(title, data, max_chars=max_chars)

    
def search_live_raw(handles, since_days, max_results):
    """
    呼叫 xAI Live Search，回傳「完整原始 JSON」。
    策略：
      1) 先用原參數請求，逾時上限 = HTTP_TIMEOUT (建議 150)
      2) 若逾時/網路錯誤 -> 自動降載重試一次（max_results=1、max_output_tokens=400、timeout=30）
    """
    today = datetime.utcnow().date()
    start_date = (today - timedelta(days=since_days)).isoformat()
    end_date   = today.isoformat()

    def build_body(max_results_local: int, max_tokens: int = 800):
        return {
            "model": "grok-4-latest",
            "temperature": 0,
            "max_output_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content":
                 "你是資訊整理助手。請『只』以 JSON 回覆，且不包含任何說明文字。"
                 "欄位: account, title, start_date_jst, url。找不到就回 {\"items\":[]}。"},
                {"role": "user", "content":
                 f"帳號: {', '.join(handles)}；期間: {start_date} ~ {end_date}；每帳號最多 {max_results_local} 則。"}
            ],
            "search_parameters": {
                "mode": "on",
                "sources": [{"type": "x", "included_x_handles": handles}],
                "from_date": start_date,
                "to_date": end_date,
                "max_results": max_results_local
            },
        }

    # 第一次：原參數
    req_timeout_1 = min(int(os.environ.get("HTTP_TIMEOUT", "110")), 170)  # 建議設成 150
    body_1 = build_body(max_results_local=max_results, max_tokens=800)
    try:
        return _post_json("https://api.x.ai/v1/chat/completions", body_1, HEADERS, timeout=req_timeout_1)
    except Exception as e1:
        # 第二次：降載快速重試（更短、更小）
        body_2 = build_body(max_results_local=1, max_tokens=400)
        try:
            return _post_json("https://api.x.ai/v1/chat/completions", body_2, HEADERS, timeout=30)
        except Exception as e2:
            # 把兩次錯誤串起來丟回去，上層會貼到 Discord
            raise RuntimeError(f"primary failed: {e1}; fallback failed: {e2}")



def lambda_handler(event, context):
    # probe：健康檢查
    if isinstance(event, dict) and event.get("mode") == "probe":
        models = _get_json("https://api.x.ai/v1/models", {"Authorization": f"Bearer {API_KEY}"})
        return {"ok": True, "models_count": len(models.get("data", []))}

    if not X_HANDLES:
        raise RuntimeError("X_HANDLES is empty.")

    debug_on = str(os.environ.get("DEBUG", "0")).strip() == "1"

    try:
        # 只呼叫一次
        resp_json = search_live_raw(X_HANDLES, DAYS, MAX_RESULTS)

        # 需要時，轉送原始 JSON 方便排錯
        if debug_on:
            post_discord_json("xAI raw response", resp_json)

        # 直接產生摘要（允許重複貼沒關係）
        summary = summarize_from_xai_json(resp_json)
        post_discord(summary or "（無更新）")
        return {"ok": True}

    except Exception as e:
        # 任何錯都回報一下，方便知道錯在上游或逾時
        post_discord(f"⚠️ 呼叫 xAI 失敗：{e}")
        # 不中斷排程：回 200
        return {"ok": False, "error": str(e)}



