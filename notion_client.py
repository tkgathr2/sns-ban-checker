"""Notion API操作 — ルールDB取得・判定ログ記録・BANログ記録・投稿ログ記録"""

import os
import requests
from datetime import datetime, timezone, timedelta

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_RULES_DB_ID = os.environ.get("NOTION_RULES_DB_ID", "")
NOTION_LOG_DB_ID = os.environ.get("NOTION_LOG_DB_ID", "")
NOTION_BAN_DB_ID = os.environ.get("NOTION_BAN_DB_ID", "")
NOTION_POST_LOG_DB_ID = os.environ.get("NOTION_POST_LOG_DB_ID", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

JST = timezone(timedelta(hours=9))

# スタッフ名マッピング（Slack表示名 → Notion投稿ログDBのセレクト値）
STAFF_NAME_MAP = {
    "ホア": "ホア",
    "hoa": "ホア",
    "nguyen": "ホア",
    "ライ": "ライ",
    "rai": "ライ",
    "馬": "馬",
    "uma": "馬",
    "ガン": "ガン",
    "gan": "ガン",
    "ngan": "ガン",
}


def _normalize_staff_name(name: str) -> str:
    """Slackの表示名からNotionのスタッフ名セレクト値に変換"""
    lower = name.lower().strip()
    for key, value in STAFF_NAME_MAP.items():
        if key.lower() in lower:
            return value
    return ""


def get_rules(sns: str = None) -> list[dict]:
    """NotionルールDBから有効なルールを取得する"""
    filters = [{"property": "有効", "checkbox": {"equals": True}}]

    if sns and sns != "全共通":
        filters.append(
            {
                "or": [
                    {"property": "SNS", "select": {"equals": sns}},
                    {"property": "SNS", "select": {"equals": "共通"}},
                ]
            }
        )

    body = {
        "filter": {"and": filters} if len(filters) > 1 else filters[0],
        "page_size": 100,
    }

    url = f"https://api.notion.com/v1/databases/{NOTION_RULES_DB_ID}/query"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rules = []
    for page in data.get("results", []):
        props = page["properties"]
        rule = {
            "id": page["id"],
            "ルール名": _get_title(props.get("ルール名", {})),
            "SNS": _get_select(props.get("SNS", {})),
            "カテゴリ": _get_select(props.get("カテゴリ", {})),
            "NGワード/パターン": _get_rich_text(props.get("NGワード/パターン", {})),
            "リスクレベル": _get_select(props.get("リスクレベル", {})),
            "推奨表現": _get_rich_text(props.get("推奨表現", {})),
            "リスク理由": _get_rich_text(props.get("リスク理由", {})),
        }
        rules.append(rule)

    return rules


def log_judgement(
    post_text: str,
    sns: str,
    risk_level: str,
    result_text: str,
    user_name: str,
    slack_link: str = "",
) -> str:
    """判定結果をNotionの判定ログDBに記録する"""
    now = datetime.now(JST).isoformat()
    title = post_text[:30] if len(post_text) > 30 else post_text

    body = {
        "parent": {"database_id": NOTION_LOG_DB_ID},
        "properties": {
            "投稿文（先頭30文字）": {"title": [{"text": {"content": title}}]},
            "投稿文全文": {"rich_text": [{"text": {"content": post_text[:2000]}}]},
            "対象SNS": {"select": {"name": sns}},
            "リスクレベル": {"select": {"name": risk_level}},
            "判定結果": {"rich_text": [{"text": {"content": result_text[:2000]}}]},
            "投稿者": {"rich_text": [{"text": {"content": user_name}}]},
            "判定日時": {"date": {"start": now}},
            "実際にBANされたか": {"select": {"name": "未報告"}},
        },
    }

    if slack_link:
        body["properties"]["Slackリンク"] = {"url": slack_link}

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def log_ban(
    summary: str,
    sns: str,
    account: str,
    reason: str,
    post_content: str,
    ban_date: str = None,
) -> str:
    """BANログDBに記録する"""
    body = {
        "parent": {"database_id": NOTION_BAN_DB_ID},
        "properties": {
            "概要": {"title": [{"text": {"content": summary}}]},
            "SNS": {"select": {"name": sns}},
            "アカウント": {"rich_text": [{"text": {"content": account}}]},
            "原因（推定）": {"rich_text": [{"text": {"content": reason[:2000]}}]},
            "投稿内容": {"rich_text": [{"text": {"content": post_content[:2000]}}]},
            "対応状況": {"select": {"name": "未対応"}},
            "ルール反映済": {"checkbox": False},
        },
    }

    if ban_date:
        body["properties"]["BAN日時"] = {"date": {"start": ban_date}}

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def log_post(
    staff_name: str,
    platform: str,
    post_text: str,
    post_url: str = "",
    post_date: str = None,
    risk_level: str = "",
    likes: int = None,
    comments: int = None,
    shares: int = None,
    memo: str = "",
    slack_ts: str = "",
) -> str:
    """投稿ログDBに新規レコードを登録する"""
    if not post_date:
        post_date = datetime.now(JST).isoformat()

    title = post_text[:30] if len(post_text) > 30 else post_text

    properties = {
        "投稿タイトル": {"title": [{"text": {"content": title}}]},
        "投稿内容": {"rich_text": [{"text": {"content": post_text[:2000]}}]},
        "BANされたか": {"select": {"name": "未報告"}},
        "投稿日時": {"date": {"start": post_date}},
    }

    normalized_staff = _normalize_staff_name(staff_name)
    if normalized_staff:
        properties["スタッフ名"] = {"select": {"name": normalized_staff}}

    platform_map = {
        "fb": "Facebook", "facebook": "Facebook",
        "ig": "Instagram", "instagram": "Instagram",
        "tiktok": "TikTok", "tt": "TikTok",
    }
    normalized_platform = platform_map.get(platform.lower().strip(), platform)
    if normalized_platform in ("Facebook", "Instagram", "TikTok"):
        properties["プラットフォーム"] = {"select": {"name": normalized_platform}}

    if post_url:
        properties["投稿URL"] = {"url": post_url}
    if risk_level and risk_level in ("🟢低", "🟡中", "🔴高"):
        properties["リスク評価"] = {"select": {"name": risk_level}}
    if likes is not None:
        properties["いいね数"] = {"number": likes}
    if comments is not None:
        properties["コメント数"] = {"number": comments}
    if shares is not None:
        properties["シェア数"] = {"number": shares}
    if memo:
        properties["メモ"] = {"rich_text": [{"text": {"content": memo[:2000]}}]}
    if slack_ts:
        properties["Slackメッセージts"] = {"rich_text": [{"text": {"content": slack_ts}}]}

    body = {
        "parent": {"database_id": NOTION_POST_LOG_DB_ID},
        "properties": properties,
    }

    url = "https://api.notion.com/v1/pages"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def update_post_ban_status(slack_ts: str, banned: bool) -> bool:
    """SlackメッセージTSをキーに投稿ログのBANステータスを更新する"""
    body = {
        "filter": {
            "property": "Slackメッセージts",
            "rich_text": {"equals": slack_ts},
        },
        "page_size": 1,
    }
    url = f"https://api.notion.com/v1/databases/{NOTION_POST_LOG_DB_ID}/query"
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()

    results = resp.json().get("results", [])
    if not results:
        return False

    page_id = results[0]["id"]
    status = "BANされた" if banned else "問題なし"

    update_body = {
        "properties": {
            "BANされたか": {"select": {"name": status}},
        }
    }

    update_url = f"https://api.notion.com/v1/pages/{page_id}"
    resp = requests.patch(update_url, headers=HEADERS, json=update_body, timeout=30)
    resp.raise_for_status()
    return True


def _get_title(prop: dict) -> str:
    items = prop.get("title", [])
    return items[0]["plain_text"] if items else ""


def _get_select(prop: dict) -> str:
    sel = prop.get("select")
    return sel["name"] if sel else ""


def _get_rich_text(prop: dict) -> str:
    items = prop.get("rich_text", [])
    return "".join(i["plain_text"] for i in items) if items else ""
