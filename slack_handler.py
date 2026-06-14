"""Slackイベント処理 — メッセージ受信・判定・返信・投稿ログ記録"""

import os
import re
import json
import logging
import tempfile
import threading

from slack_bolt import App
from judge import judge_post, format_slack_response
from notion_client import (
    log_judgement,
    log_ban,
    log_post,
    update_post_ban_status,
)
from video_analyzer import (
    is_video_file,
    download_slack_file,
    judge_video,
    format_video_slack_response,
    VIDEO_MAX_SIZE_MB,
)

logger = logging.getLogger(__name__)

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

# SNSタグパターン: [facebook], [instagram], [tiktok]
SNS_TAG_PATTERN = re.compile(
    r"^\s*\[(facebook|instagram|tiktok)\]\s*", re.IGNORECASE | re.MULTILINE
)

# 投稿報告パターン: [スタッフ名] [プラットフォーム]
POST_REPORT_PATTERN = re.compile(
    r"^\[(?P<staff>[^\]]+)\]\s*\[(?P<platform>[^\]]+)\]",
    re.MULTILINE,
)

URL_PATTERN = re.compile(
    r"(?:URL|url|リンク|link)\s*[:：]\s*(https?://\S+)", re.IGNORECASE
)

ENGAGEMENT_PATTERN = re.compile(
    r"(?:いいね|like)\s*[:：]\s*(\d+)", re.IGNORECASE
)
COMMENT_PATTERN = re.compile(
    r"(?:コメント|comment)\s*[:：]\s*(\d+)", re.IGNORECASE
)
SHARE_PATTERN = re.compile(
    r"(?:シェア|share)\s*[:：]\s*(\d+)", re.IGNORECASE
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


def _parse_post_report(text: str) -> dict | None:
    """投稿報告メッセージをパースする

    フォーマット例:
        [ホア] [Facebook]
        投稿内容テキスト
        URL: https://www.facebook.com/...
        いいね: 9 コメント: 9 シェア: 0
    """
    match = POST_REPORT_PATTERN.search(text)
    if not match:
        return None

    staff = match.group("staff").strip()
    platform = match.group("platform").strip()
    rest = text[match.end() :].strip()

    url_match = URL_PATTERN.search(rest)
    post_url = url_match.group(1) if url_match else ""

    post_content = rest
    if url_match:
        post_content = rest[: url_match.start()].strip()

    likes_match = ENGAGEMENT_PATTERN.search(rest)
    comments_match = COMMENT_PATTERN.search(rest)
    shares_match = SHARE_PATTERN.search(rest)

    likes = int(likes_match.group(1)) if likes_match else None
    comments_count = int(comments_match.group(1)) if comments_match else None
    shares = int(shares_match.group(1)) if shares_match else None

    for pattern in [ENGAGEMENT_PATTERN, COMMENT_PATTERN, SHARE_PATTERN]:
        post_content = pattern.sub("", post_content).strip()

    post_content = "\n".join(
        line for line in post_content.split("\n") if line.strip()
    )

    return {
        "staff": staff,
        "platform": platform,
        "post_content": post_content,
        "post_url": post_url,
        "likes": likes,
        "comments": comments_count,
        "shares": shares,
    }


def _process_message(event: dict, say):
    """判定メッセージを処理する（バックグラウンドスレッド用）"""
    text = event.get("text", "")
    user = event.get("user", "unknown")
    channel = event.get("channel", "")
    ts = event.get("ts", "")

    if not text.strip():
        return

    target_sns, clean_text = _detect_sns(text)
    if not clean_text:
        return

    thinking_msg = say(text="判定中です... 🔍", thread_ts=ts)

    try:
        result = judge_post(clean_text, target_sns)
        response_text = format_slack_response(result)
        say(text=response_text, thread_ts=ts)

        try:
            app.client.chat_delete(channel=channel, ts=thinking_msg["ts"])
        except Exception:
            pass

        slack_link = _get_slack_message_link(channel, ts)
        risk_level = result.get("risk_level", "🟡中")

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


def _process_post_report(event: dict, say):
    """投稿報告を処理してNotionに記録する（バックグラウンドスレッド用）"""
    text = event.get("text", "")
    ts = event.get("ts", "")

    parsed = _parse_post_report(text)
    if not parsed:
        return

    try:
        page_id = log_post(
            staff_name=parsed["staff"],
            platform=parsed["platform"],
            post_text=parsed["post_content"],
            post_url=parsed["post_url"],
            likes=parsed["likes"],
            comments=parsed["comments"],
            shares=parsed["shares"],
            slack_ts=ts,
        )

        say(
            text=(
                f"📋 投稿ログに記録しました！\n"
                f"・スタッフ: {parsed['staff']}\n"
                f"・プラットフォーム: {parsed['platform']}\n"
                f"・投稿内容: {parsed['post_content'][:50]}...\n"
                f"---\n"
                f"✅ リアクションで「安全だった」\n"
                f"🚫 リアクションで「BANされた」\n"
                f"を記録できます。"
            ),
            thread_ts=ts,
        )
        logger.info(
            f"投稿ログ記録: staff={parsed['staff']}, "
            f"platform={parsed['platform']}, page_id={page_id}"
        )

    except Exception as e:
        logger.error(f"投稿ログ記録エラー: {e}", exc_info=True)
        say(
            text=f"❌ 投稿ログの記録に失敗しました: {str(e)[:200]}",
            thread_ts=ts,
        )


@app.event("message")
def handle_message(event, say):
    """チャンネルへのメッセージを処理する"""
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("thread_ts"):
        return

    channel = event.get("channel", "")

    ban_checker_channel = os.environ.get("BAN_CHECKER_CHANNEL_ID")
    if ban_checker_channel and channel == ban_checker_channel:
        thread = threading.Thread(
            target=_process_message, args=(event, say), daemon=True
        )
        thread.start()
        return

    post_log_channel = os.environ.get("POST_LOG_CHANNEL_ID")
    if post_log_channel and channel == post_log_channel:
        thread = threading.Thread(
            target=_process_post_report, args=(event, say), daemon=True
        )
        thread.start()
        return


@app.event("file_shared")
def handle_file_shared(event, client):
    """動画ファイルが共有されたときにプラットフォーム選択UIを表示する"""
    channel_id = event.get("channel_id")
    file_id = event.get("file_id")

    ban_checker_channel = os.environ.get("BAN_CHECKER_CHANNEL_ID")
    if not ban_checker_channel or channel_id != ban_checker_channel:
        return

    thread = threading.Thread(
        target=_handle_video_upload,
        args=(file_id, channel_id, client),
        daemon=True,
    )
    thread.start()


def _handle_video_upload(file_id: str, channel_id: str, client):
    try:
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        mimetype = file_data.get("mimetype", "")
        filename = file_data.get("name", "動画")
        file_size_bytes = file_data.get("size", 0)

        if not is_video_file(mimetype):
            return

        file_size_mb = file_size_bytes / (1024 * 1024)
        if file_size_mb > VIDEO_MAX_SIZE_MB:
            client.chat_postMessage(
                channel=channel_id,
                text=f"動画サイズが上限（{VIDEO_MAX_SIZE_MB}MB）を超えています（{file_size_mb:.1f}MB）。",
            )
            return

        client.chat_postMessage(
            channel=channel_id,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"動画「*{filename}*」（{file_size_mb:.1f}MB）が共有されました。\n"
                            "どのSNS用の動画か選択してください:"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "TikTok"},
                            "style": "primary",
                            "action_id": "video_judge",
                            "value": json.dumps(
                                {"file_id": file_id, "platform": "TikTok", "channel": channel_id}
                            ),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Facebook"},
                            "action_id": "video_judge",
                            "value": json.dumps(
                                {"file_id": file_id, "platform": "Facebook", "channel": channel_id}
                            ),
                        },
                    ],
                },
            ],
        )
    except Exception as e:
        logger.error(f"動画ファイル処理エラー: {e}", exc_info=True)


@app.action("video_judge")
def handle_video_judge_action(ack, body, client):
    """動画判定ボタンのアクション処理"""
    ack()

    value = json.loads(body["actions"][0]["value"])
    file_id = value["file_id"]
    platform = value["platform"]
    channel = value["channel"]
    message_ts = body.get("message", {}).get("ts")

    thread = threading.Thread(
        target=_process_video_judgment,
        args=(file_id, platform, channel, message_ts, client),
        daemon=True,
    )
    thread.start()


def _process_video_judgment(file_id: str, platform: str, channel: str, thread_ts: str, client):
    """動画判定処理（バックグラウンドスレッド用）"""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")

    thinking = client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"動画を分析中です... しばらくお待ちください（1〜2分）",
    )

    try:
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        download_url = (
            file_data.get("url_private_download") or file_data.get("url_private")
        )
        filename = file_data.get("name", "video")

        if not download_url:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="動画のダウンロードURLが取得できませんでした。",
            )
            return

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            download_slack_file(download_url, tmp_path, bot_token)
            result = judge_video(tmp_path, platform)
            response_text = format_video_slack_response(result, platform)

            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=response_text,
            )

            try:
                client.chat_delete(channel=channel, ts=thinking["ts"])
            except Exception:
                pass

            try:
                log_judgement(
                    post_text=f"[動画: {filename}]",
                    sns=platform,
                    risk_level=result.get("risk_level", "🟡中"),
                    result_text=response_text,
                    user_name="動画判定",
                    slack_link="",
                )
            except Exception as e:
                logger.warning(f"Notion動画ログ記録エラー: {e}")

            logger.info(
                f"動画判定完了: platform={platform}, risk={result.get('risk_level')}, "
                f"frames={result.get('frame_count')}, duration={result.get('duration'):.0f}s"
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"動画判定エラー: {e}", exc_info=True)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"動画の分析中にエラーが発生しました: {str(e)[:200]}",
        )


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

    post_log_channel = os.environ.get("POST_LOG_CHANNEL_ID")
    if post_log_channel and channel == post_log_channel:
        try:
            banned = reaction == "no_entry_sign"
            updated = update_post_ban_status(slack_ts=ts, banned=banned)

            if updated:
                status_text = "🚫 BANされた" if banned else "✅ 問題なし"
                say(
                    text=f"投稿ログを更新しました → {status_text}",
                    thread_ts=ts,
                )
                logger.info(
                    f"投稿ログBANステータス更新: ts={ts}, banned={banned}"
                )
            else:
                logger.warning(f"投稿ログが見つかりません: ts={ts}")
        except Exception as e:
            logger.error(f"投稿ログ更新エラー: {e}", exc_info=True)
        return

    ban_checker_channel = os.environ.get("BAN_CHECKER_CHANNEL_ID")
    if ban_checker_channel and channel != ban_checker_channel:
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
                reason="Slack 🚫 リアクションによるBAN報告",
                post_content=clean_text,
            )
            say(
                text="🚫 BAN報告を記録しました。NotionのBANログDBに追記されています。",
                thread_ts=ts,
            )
            logger.info(f"BAN報告: {clean_text[:50]}")
    except Exception as e:
        logger.error(f"リアクション処理エラー: {e}", exc_info=True)
