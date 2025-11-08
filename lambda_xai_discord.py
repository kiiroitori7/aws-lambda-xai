import os, json, urllib.request, urllib.error, time
from datetime import datetime, timedelta

# ========== 環境變數 ==========
API_KEY      = os.environ["GROK_API_KEY"]
WEBHOOK      = os.environ["DISCORD_WEBHOOK_URL"]
X_HANDLES    = [h.strip() for h in os.environ.get("X_HANDLES", "").split(",") if h.strip()]
DAYS         = int(os.environ.get("DAYS", "1"))
MAX_RESULTS  = int(os.environ.get("MAX_RESULTS", "2"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "55"))  # 記得讓 Lambda Timeout > 這個值

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
               method: str = "POST", timeout: int | None = None, retries: int = 2):
    t = timeout if timeout is not None else HTTP_TIMEOUT  # 例如環境變數設 100
    headers = {**DEFAULT_HEADERS, **(headers or {})}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=t) as resp:
                body = resp.read().decode("utf-8", "ignore")
                return json.loads(body) if "application/json" in (resp.headers.get("Content-Type","")) else body
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTPError {e.code}: {e.read().decode('utf-8','ignore')}")
        except Exception as e:
            last_err = str(e)
            if attempt < retries-1:
                time.sleep(2*(attempt+1))
            else:
                raise RuntimeError(last_err)


def _get_json(url: str, headers: dict):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=min(HTTP_TIMEOUT, 20)) as resp:
        return json.loads(resp.read().decode("utf-8"))

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
    return "\n".join(lines).strip()


def lambda_handler(event, context):
    # --- 1) 健康檢查：不走 Live Search，只 GET /v1/models 驗證金鑰與網路 ---
    if isinstance(event, dict) and event.get("mode") == "probe":
        models = _get_json("https://api.x.ai/v1/models", {"Authorization": f"Bearer {API_KEY}"})
        return {"ok": True, "models_count": len(models.get("data", []))}

    # --- 2) 正常流程 ---
    if not X_HANDLES:
        raise RuntimeError("X_HANDLES is empty.")

    summary = search_and_summarize(X_HANDLES, DAYS, MAX_RESULTS)
    post_discord(summary if summary else "（無更新）")
    return {"ok": True}
