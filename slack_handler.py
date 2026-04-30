"""Slackイベント処理 — メッセージ受信・判定・返信"""

import os
import re
import logging
import threading
from slack_bolt import App
from judge import judge_post, format_slack_response
from notion_client import log_judgement, log_ban

logger = logging.getLogger(__name__)

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

# SNSタグパターン: [facebook], [instagram], [tiktok]
SNS_TAG_PATTERN = re.compile(
    r"^\\s*\\[(facebook|instagram|tiktok)\\]\\s*", re.IGNORECASE | re.MULTILINE
)


def _detect_sns(text: str) -> tuple[str, str]:
    """投稿文からSNSタグを検出し、(SNS名, クリーンテキスト) を返す"""
    match = SNS_TAG_PATTERN.match(text)
    if match:
        sns_map = {
            "facebook": "Facebook",
            "instagram": "Instagram",
            "tiktok": "TikTok",
        }
        sns = sns_map.get(match.group(1).lower(), "全共通")
        clean_text = text[match.end() :].strip()
        return sns, clean_text
    return "全共通", text.strip()


def _get_slack_message_link(channel: str, ts: str) -> str:
    """SlackメッセージのパーマリンクURLを生成"""
    workspace = os.environ.get("SLACK_WORKSPACE", "")
    if workspace:
        ts_clean = ts.replace(".", "")
        return f"https://{workspace}.slack.com/archives/{channel}/p{ts_clean}"
    return ""


def _process_message(event: dict, say):
    """メッセージを処理する（バックグラウンドスレッド用）"""
    text = event.get("text", "")
    user = event.get("user", "unknown")
    channel = event.get("channel", "")
    ts = event.get("ts", "")

    if not text.strip():
        return

    target_sns, clean_text = _detect_sns(text)

    if not clean_text:
        return

    thinking_msg = say(
        text="判定中です... \ud83d\udd0d",
        thread_ts=ts,
    )

    try:
        result = judge_post(clean_text, target_sns)
        response_text = format_slack_response(result)
        say(text=response_text, thread_ts=ts)

        try:
            app.client.chat_delete(
                channel=channel, ts=thinking_msg["ts"]
            )
        except Exception:
            pass

        slack_link = _get_slack_message_link(channel, ts)
        risk_level = result.get("risk_level", "\ud83d\udfe1\u4e2d")

        try:
            user_info = app.client.users_info(user=user)
            user_name = (
                user_info["user"]["profile"].get("display_name")
                or user_info["user"]["profile"].get("real_name")
                or user
            )
        except Exception:
            user_name = user

        log_judgement(
            post_text=clean_text,
            sns=target_sns,
            risk_level=risk_level,
            result_text=response_text,
            user_name=user_name,
            slack_link=slack_link,
        )

        logger.info(
            f"判定完了: user={user_name}, sns={target_sns}, risk={risk_level}"
        )

    except Exception as e:
        logger.error(f"判定エラー: {e}", exc_info=True)
        say(
            text=f"判定中にエラーが発生しました: {str(e)[:200]}\nもう一度お試しください。",
            thread_ts=ts,
        )


@app.event("message")
def handle_message(event, say):
    """チャンネルへのメッセージを処理する"""
    if event.get("bot_id") or event.get("subtype"):
        return

    if event.get("thread_ts"):
        return

    allowed_channel = os.environ.get("BAN_CHECKER_CHANNEL_ID")
    if allowed_channel and event.get("channel") != allowed_channel:
        return

    thread = threading.Thread(
        target=_process_message, args=(event, say), daemon=True
    )
    thread.start()


@app.event("reaction_added")
def handle_reaction(event, say):
    """リアクションでフィードバックを収集"""
    reaction = event.get("reaction", "")
    item = event.get("item", {})

    if reaction not in ("white_check_mark", "no_entry_sign"):
        return

    channel = item.get("channel", "")
    ts = item.get("ts", "")

    if not channel or not ts:
        return

    try:
        result = app.client.conversations_history(
            channel=channel, latest=ts, limit=1, inclusive=True
        )
        messages = result.get("messages", [])
        if not messages:
            return

        original_text = messages[0].get("text", "")

        if reaction == "no_entry_sign":
            target_sns, clean_text = _detect_sns(original_text)
            log_ban(
                summary=f"BAN報告: {clean_text[:30]}",
                sns=target_sns if target_sns != "全共通" else "Facebook",
                account="Slack報告",
                reason="Slack \ud83d\udeab リアクションによるBAN報告",
                post_content=clean_text,
            )
            say(
                text="\ud83d\udeab BAN報告を記録しました。NotionのBANログDBに追記されています。",
                thread_ts=ts,
            )
            logger.info(f"BAN報告: {clean_text[:50]}")

    except Exception as e:
        logger.error(f"リアクション処理エラー: {e}", exc_info=True)
