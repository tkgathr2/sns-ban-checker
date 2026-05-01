"""SNSガードマン — メインエントリーポイント"""

import os
import logging
from flask import Flask, request, jsonify
from slack_bolt.adapter.flask import SlackRequestHandler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:h
    pass  # Railway環境では環境変数が直接設定されるため不要

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# slack_handler をインポート（App初期化が含まれる）
from slack_handler import app as slack_app

flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)


@flask_app.route("/health", methods=["GET"])
def health():
    """ヘルスチェック"""
    return jsonify({"status": "ok", "service": "sns-ban-checker"})


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    """Slack Event API エンドポイント"""
    return handler.handle(request)
