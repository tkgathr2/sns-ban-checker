"""Notion API操作 — ルールDB取得・判定ログ記録・BANログ記録"""

import os
import requests
from datetime import datetime, timezone, timedelta

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_RULES_DB_ID = os.environ.get("NOTION_RULES_DB_ID", "")
NOTION_LOG_DB_ID = os.environ.get("NOTION_LOG_DB_ID", "")
NOTION_BAN_DB_ID = os.environ.get("NOTION_BAN_DB_ID", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

JST = timezone(timedelta(hours=9))


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


# --- ヘルパー ---


def _get_title(prop: dict) -> str:
    items = prop.get("title", [])
    return items[0]["plain_text"] if items else ""


def _get_select(prop: dict) -> str:
    sel = prop.get("select")
    return sel["name"] if sel else ""


def _get_rich_text(prop: dict) -> str:
    items = prop.get("rich_text", [])
    return "".join(i["plain_text"] for i in items) if items else ""
