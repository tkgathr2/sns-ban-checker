# SNSガードマン

## 概要
Slackに投稿文を貼ると、バンリスクを判定して改善提案を返すBotです。

## 技術スタック
- Python Flask
- Claude API
- Slack API
- Notion API

## セットアップ
```bash
pip install -r requirements.txt
```

## 環境変数
- `SLACK_BOT_TOKEN` - Slack Botトークン
- `SLACK_SIGNING_SECRET` - Slack署名シークレット
- `ANTHROPIC_API_KEY` - Claude APIキー
- `NOTION_API_KEY` - Notion APIキー
