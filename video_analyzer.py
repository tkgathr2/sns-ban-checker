"""動画BANリスク判定エンジン — ffmpeg フレーム抽出 + Claude Vision分析"""

import os
import json
import subprocess
import tempfile
import base64
import logging
import requests

logger = logging.getLogger(__name__)

VIDEO_MAX_SIZE_MB = int(os.environ.get("VIDEO_MAX_SIZE_MB", "100"))
VIDEO_MAX_DURATION_SEC = int(os.environ.get("VIDEO_MAX_DURATION_SEC", "300"))
VIDEO_MAX_FRAMES = int(os.environ.get("VIDEO_MAX_FRAMES", "20"))

VIDEO_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
    "video/x-matroska", "video/mpeg", "video/3gpp", "video/3gpp2",
}

TIKTOK_VIDEO_SYSTEM_PROMPT = """あなたはTikTok動画のBANリスク判定専門家です。
人材紹介会社（特定技能・技能実習）のSNSアカウント運用担当として、TikTok投稿動画のリスクを視覚的に分析します。

## TikTok特有のリスクポイント
1. **著作権（最重要）**: BGM・音楽の著作権はTikTokのAIが自動検知します。フリー素材以外は即BAN対象です
2. **AI自動検知**: 煽り・感情操作・センセーショナルな表現をAIが自動検出します
3. **テンプレ乱用**: 同一テンプレート・エフェクトの繰り返し使用でスパム判定されます
4. **誤解を招く内容**: 就労条件・報酬・生活環境の誇張や虚偽は違反です

## 業界背景
- ステップアップ（人材紹介会社）のTikTokアカウント
- ベトナム人向け特定技能・技能実習の情報発信
- 派手な演出・効果音・テロップは著作権・センセーショナル判定リスクあり

## 出力フォーマット（JSONのみ出力・他テキスト不要）
{
  "risk_level": "🔴高" または "🟡中" または "🟢低",
  "summary": "映像リスク判定の1行サマリー（日本語）",
  "issues": [
    {
      "frame_description": "該当フレームの説明",
      "risk_type": "著作権|センセーショナル|テンプレ乱用|誇大表現|その他",
      "reason": "リスク理由（日本語）",
      "suggestion": "改善提案（日本語）"
    }
  ],
  "music_risk": "🔴高|🟡中|🟢低|不明",
  "visual_risk": "🔴高|🟡中|🟢低",
  "overall_notes": "その他の注意点（日本語、なければ空文字）"
}"""

FACEBOOK_VIDEO_SYSTEM_PROMPT = """あなたはFacebook動画のBANリスク判定専門家です。
人材紹介会社（特定技能・技能実習）のSNSアカウント運用担当として、Facebook投稿動画のリスクを視覚的に分析します。

## Facebook特有のリスクポイント
1. **人的搾取ポリシー**: 労働者の搾取・人身取引に関連する表現は即BAN。特に求人・斡旋内容に注意
2. **ベトナム語の誤判定**: Facebookのシステムはベトナム語の特定フレーズを誤検知することがある
3. **雇用・報酬の誇張**: 過大な報酬・生活保証の表示は違反
4. **医療・健康の誤情報**: 健康や医療を連想させる映像は注意が必要

## 業界背景
- ステップアップ（人材紹介会社）のFacebookアカウント
- 累計23回のBAN実績あり（過去に多数の違反を経験）
- ベトナム人向け特定技能・技能実習の情報発信

## 出力フォーマット（JSONのみ出力・他テキスト不要）
{
  "risk_level": "🔴高" または "🟡中" または "🟢低",
  "summary": "映像リスク判定の1行サマリー（日本語）",
  "issues": [
    {
      "frame_description": "該当フレームの説明",
      "risk_type": "人的搾取|誤情報|誇大表現|誤判定リスク|その他",
      "reason": "リスク理由（日本語）",
      "suggestion": "改善提案（日本語）"
    }
  ],
  "music_risk": "🔴高|🟡中|🟢低|不明",
  "visual_risk": "🔴高|🟡中|🟢低",
  "overall_notes": "その他の注意点（日本語、なければ空文字）"
}"""

PLATFORM_VIDEO_PROMPTS = {
    "TikTok": TIKTOK_VIDEO_SYSTEM_PROMPT,
    "Facebook": FACEBOOK_VIDEO_SYSTEM_PROMPT,
}


def is_video_file(mimetype: str) -> bool:
    return mimetype in VIDEO_MIME_TYPES


def download_slack_file(url: str, dest_path: str, bot_token: str) -> None:
    headers = {"Authorization": f"Bearer {bot_token}"}
    resp = requests.get(url, headers=headers, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def get_video_duration(video_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", video_path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(r.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                dur = float(stream.get("duration", 0))
                if dur > 0:
                    return dur
        r2 = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=30,
        )
        data2 = json.loads(r2.stdout)
        return float(data2.get("format", {}).get("duration", 0))
    except Exception as e:
        logger.warning(f"ffprobe エラー: {e}")
        return 0.0


def _calc_interval(duration: float) -> float:
    if duration <= 15:
        return 3
    elif duration <= 60:
        return 5
    elif duration <= 180:
        return 10
    else:
        return 15


def extract_frames(video_path: str, output_dir: str, duration: float) -> list:
    frames = []
    interval = _calc_interval(duration)

    def _snap(ss: float, name: str):
        path = os.path.join(output_dir, name)
        r = subprocess.run(
            ["ffmpeg", "-ss", str(ss), "-i", video_path,
             "-frames:v", "1", "-q:v", "2", path, "-y"],
            capture_output=True, timeout=30,
        )
        return path if r.returncode == 0 and os.path.exists(path) else None

    # 先頭フレーム
    p = _snap(0.5, "frame_first.jpg")
    if p:
        frames.append(p)

    # インターバルフレーム
    t = interval
    idx = 1
    while t < max(duration - 3, interval) and len(frames) < VIDEO_MAX_FRAMES - 1:
        p = _snap(t, f"frame_{idx:04d}.jpg")
        if p:
            frames.append(p)
        t += interval
        idx += 1

    # 末尾フレーム
    if duration > 3 and len(frames) < VIDEO_MAX_FRAMES:
        p = _snap(max(0, duration - 3), "frame_last.jpg")
        if p:
            frames.append(p)

    return frames


def analyze_video_frames(frame_paths: list, platform: str) -> dict:
    import anthropic
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    system_prompt = PLATFORM_VIDEO_PROMPTS.get(platform, PLATFORM_VIDEO_PROMPTS["Facebook"])

    content = []
    for fp in frame_paths:
        try:
            with open(fp, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
            })
        except Exception as e:
            logger.warning(f"フレーム読込エラー {fp}: {e}")

    if not content:
        return _error_result("フレームを読み込めませんでした")

    content.append({
        "type": "text",
        "text": (
            f"これらは{platform}投稿用動画から抽出した{len(frame_paths)}枚のフレームです。"
            "動画全体のBANリスクを判定してください。"
        ),
    })

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        json_lines, in_block = [], False
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
        return json.loads(response_text)
    except json.JSONDecodeError:
        return {
            "risk_level": "🟡中",
            "summary": "判定結果のパースに失敗しました",
            "issues": [],
            "music_risk": "不明",
            "visual_risk": "🟡中",
            "overall_notes": f"APIレスポンス: {response_text[:500]}",
        }


def judge_video(video_path: str, platform: str) -> dict:
    duration = get_video_duration(video_path)

    if duration > VIDEO_MAX_DURATION_SEC:
        return _error_result(
            f"動画が長すぎます（{duration:.0f}秒 > 上限{VIDEO_MAX_DURATION_SEC}秒）"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        frames = extract_frames(video_path, tmpdir, duration)
        if not frames:
            return _error_result("フレーム抽出に失敗しました")

        result = analyze_video_frames(frames, platform)
        result["frame_count"] = len(frames)
        result["duration"] = duration
        return result


def _error_result(message: str) -> dict:
    return {
        "risk_level": "🟡中",
        "summary": message,
        "issues": [],
        "music_risk": "不明",
        "visual_risk": "🟡中",
        "overall_notes": "",
        "frame_count": 0,
        "duration": 0.0,
    }


def format_video_slack_response(result: dict, platform: str) -> str:
    risk = result.get("risk_level", "🟡中")
    summary = result.get("summary", "")
    issues = result.get("issues", [])
    music_risk = result.get("music_risk", "不明")
    visual_risk = result.get("visual_risk", "")
    notes = result.get("overall_notes", "")
    frame_count = result.get("frame_count", 0)
    duration = result.get("duration", 0)

    if "🔴" in risk:
        header = f"*{risk} リスク（BAN可能性: 高）*"
    elif "🟡" in risk:
        header = f"*{risk} リスク（要注意）*"
    else:
        header = f"*{risk} リスク（問題なし）*"

    lines = [
        f"🎥 *{platform} 動画判定結果*",
        header,
        f"_{summary}_",
        f"分析: {frame_count}フレーム / {duration:.0f}秒",
        "",
        f"映像リスク: {visual_risk}  |  音楽リスク: {music_risk}",
        "",
    ]

    if issues:
        lines.append("*検出された問題:*")
        for i, issue in enumerate(issues, 1):
            lines.append(
                f"{i}. {issue.get('frame_description', '')}"
                f"\n   種別: {issue.get('risk_type', '')}"
                f"\n   理由: {issue.get('reason', '')}"
                f"\n   推奨: {issue.get('suggestion', '')}"
            )
        lines.append("")

    if notes:
        lines.append(f"_補足: {notes}_")

    lines.append(
        "\n_判定後、投稿して問題なければ ✅、BANされたら 🚫 をリアクションしてください_"
    )

    return "\n".join(lines)
