"""SNSガードマン — メインエントリーポイント"""

import os
import logging
from flask import Flask, request, jsonify
from slack_bolt.adapter.flask import SlackRequestHandler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from slack_handler import app as slack_app

flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "sns-ban-checker"})


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


@flask_app.route("/slack/interactive", methods=["POST"])
def slack_interactive():
    return handler.handle(request)


@flask_app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "service": "SNSガードマン",
            "version": "2.0.0",
            "endpoints": {
                "/health": "ヘルスチェック",
                "/slack/events": "Slack Event API",
                "/slack/interactive": "Slack Interactive Components",
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port, debug=True)
