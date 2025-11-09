# AWS Lambda xAI

Python-based AWS Lambda integration that fetches posts from xAI and sends them to Discord via Webhook.  
Python 製 AWS Lambda 整合範例：從 xAI 取得貼文並透過 Discord Webhook 發送。  
Pythonで作成したAWS Lambda連携：xAIの投稿を取得し、Discord Webhookを通じて送信します。

---

## Files / 檔案說明 / ファイル一覧

- `lambda_xai_discord.py`  
  Main function for xAI → Discord.  
  主要功能：xAI → Discord 傳送邏輯。  
  メイン機能：xAI → Discord への送信処理。

---

## Environment Variables / 環境變數設定 / 環境変数設定

| Name | Description | 中文說明 | 日本語説明 |
|------|--------------|----------|------------|
| `XAI_API_URL` | Base URL for xAI API endpoint | xAI API 的基本端點網址 | xAI APIエンドポイントの基本URL |
| `DISCORD_WEBHOOK_URL` | Webhook URL for Discord channel | Discord 頻道的 Webhook 連結 | DiscordチャンネルのWebhook URL |
| `MAX_POSTS` | Number of recent posts to fetch (default: 5) | 取得貼文數量（預設：5） | 取得する投稿数（デフォルト：5） |
| `TIMEZONE` | Optional timezone for timestamp formatting | 時區設定（可選） | タイムゾーン設定（任意） |

**Example (AWS Lambda console):**
```env
XAI_API_URL=https://api.x.ai/posts
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxxxxxxxxxxx
MAX_POSTS=5
TIMEZONE=Asia/Tokyo
```

---

## Notes / 備註 / 注意事項
Deployment notes and runtime environment.

- **AWS Lambda (Python 3.12)** runtime.  
- Dependencies can be packed via `requirements.txt` and deployed as a ZIP.  
- 調用週期可由 **EventBridge Scheduler** 控制。  
- デプロイ時は `requirements.txt` に基づき ZIP 化し、EventBridge Scheduler で定期実行を設定。

---

Created by **Hikari Chan** · GitHub: [kiiroitori7](https://github.com/kiiroitori7)