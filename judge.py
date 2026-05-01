"""Claude APIによるSNSガードマン判定エンジン"""

import os
import json
import anthropic
from notion_client import get_rules

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT_TEMPLATE = """あなたはSNS投稿のバンリスク判定の専門家です。
人材紹介業界（特定技能・技能実習）に特化した知識を持ち、各SNSプラットフォームのポリシーに精通しています。

## あなたの役割
ユーザーが投稿しようとしているSNS投稿文を受け取り、バンリスクを判定してください。

## 対象SNS: {target_sns}

## 判定ルール一覧
以下はNotionのルールDBから取得した現在有効なルールです:
{rules_json}

## 業界背景
- ステップアップは人材紹介会社で、ベトナム人の特定技能・技能実習生の日本での就労を支援しています
- Facebookアカウントは累計23回のBANを受けており、投稿内容には細心の注意が必要です
- 投稿はベトナム語（Tiếng Việt）の場合もあります。ベトナム語の投稿も正確に判定してください

## 出力フォーマット
以下のJSON形式で出力してください。JSON以外は出力しないでください。
{{
  "risk_level": "🔴高" または "🟡中" または "🟢低",
  "summary": "リスク判定の1行サマリー（日本語）",
  "issues": [
    {{
      "text": "問題のあるテキスト部分",
      "rule": "該当ルール名",
      "reason": "リスク理由（日本語）",
      "suggestion": "改善提案（日本語）"
    }}
  ],
  "improved_text": "リスクを下げた改善版の投稿文全体（元の言語で）",
  "notes": "その他の注意点（日本語、なければ空文字）"
}}

## 判定基準
- 🔴高: BANされる可能性が高い。即座に修正が必要
- 🟡中: BANリスクあり。修正を推奨
- 🟢低: 問題は少ない。そのまま投稿可能

## 重要な注意点
- 複数の問題がある場合、最も高いリスクレベルを全体のリスクとして採用してください
- ベトナム語の投稿の場合、improved_textもベトナム語で出力してください
- 問題が見つからない場合もJSON形式で出力してください（issuesを空配列に）
- ルールDBにないリスクでも、SNSプラットフォームの一般的なポリシーに基づいて判定してください
"""


def judge_post(post_text: str, target_sns: str = "全共通") -> dict:
    """投稿文のバンリスクを判定する"""
    rules = get_rules(target_sns if target_sns != "全共通" else None)

    rules_for_prompt = []
    for r in rules:
        rules_for_prompt.append(
            {
                "ルール名": r["ルール名"],
                "SNS": r["SNS"],
                "カテゴリ": r["カテゴリ"],
                "NGワード/パターン": r["NGワード/パターン"],
                "リスクレベル": r["リスクレベル"],
                "推奨表現": r["推奨表現"],
                "リスク理由": r["リスク理由"],
            }
        )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target_sns=target_sns,
        rules_json=json.dumps(rules_for_prompt, ensure_ascii=False, indent=2),
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"以下の投稿文のバンリスクを判定してください:\n\n{post_text}",
            }
        ],
    )

    response_text = message.content[0].text.strip()

    if response_text.startswith("```"):
        lines = response_text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        response_text = "\n".join(json_lines)

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        result = {
            "risk_level": "🟡中",
            "summary": "判定結果のパースに失敗しました",
            "issues": [],
            "improved_text": post_text,
            "notes": f"Claude APIのレスポンス: {response_text[:500]}",
        }

    return result


def format_slack_response(result: dict) -> str:
    """判定結果をSlackメッセージ形式にフォーマットする"""
    risk = result.get("risk_level", "🟡中")
    summary = result.get("summary", "")
    issues = result.get("issues", [])
    improved = result.get("improved_text", "")
    notes = result.get("notes", "")

    if "🔴" in risk:
        header = f"*{risk} リスク（BAN可能性: 高）*"
    elif "🟡" in risk:
        header = f"*{risk} リスク（要注意）*"
    else:
        header = f"*{risk} リスク（問題なし）*"

    lines = [header, f"_{summary}_", ""]

    if issues:
        lines.append("*検出された問題:*")
        for i, issue in enumerate(issues, 1):
            lines.append(
                f"{i}. 「{issue.get('text', '')}」"
                f"\n   理由: {issue.get('reason', '')}"
                f"\n   推奨: {issue.get('suggestion', '')}"
            )
        lines.append("")

    if improved and issues:
        lines.append("*改善案:*")
        lines.append(f"```{improved}```")
        lines.append("")

    if notes:
        lines.append(f"_補足: {notes}_")

    lines.append(
        "\n_判定後、投稿して問題なければ ✅、BANされたら 🚫 をリアクションしてください_"
    )

    return "\n".join(lines)
