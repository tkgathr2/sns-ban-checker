"""SNSガードマン v2 ユニットテスト"""

import json
import unittest
from unittest.mock import patch, MagicMock


class TestPlatformDetection(unittest.TestCase):
    """SNSタグ検出テスト"""

    def test_detect_tiktok(self):
        """[tiktok]タグの検出"""
        from slack_handler import _detect_sns
        sns, text = _detect_sns("[tiktok] テスト投稿です")
        self.assertEqual(sns, "TikTok")
        self.assertEqual(text, "テスト投稿です")

    def test_detect_facebook(self):
        """[facebook]タグの検出"""
        from slack_handler import _detect_sns
        sns, text = _detect_sns("[facebook] テスト投稿")
        self.assertEqual(sns, "Facebook")
        self.assertEqual(text, "テスト投稿")

    def test_detect_instagram(self):
        """[instagram]タグの検出"""
        from slack_handler import _detect_sns
        sns, text = _detect_sns("[instagram] テスト")
        self.assertEqual(sns, "Instagram")
        self.assertEqual(text, "テスト")

    def test_no_tag(self):
        """タグなしは全共通"""
        from slack_handler import _detect_sns
        sns, text = _detect_sns("通常の投稿テキスト")
        self.assertEqual(sns, "全共通")
        self.assertEqual(text, "通常の投稿テキスト")

    def test_case_insensitive(self):
        """大文字小文字を区別しない"""
        from slack_handler import _detect_sns
        sns, _ = _detect_sns("[TIKTOK] テスト")
        self.assertEqual(sns, "TikTok")


class TestJudgePromptSelection(unittest.TestCase):
    """プラットフォーム別プロンプト選択テスト"""

    def test_tiktok_uses_tiktok_prompt(self):
        """TikTok判定はTikTok専用プロンプトを使用する"""
        import judge
        self.assertIn("TIKTOK_TEXT_SYSTEM_PROMPT_TEMPLATE", dir(judge))
        self.assertIn("TikTok", judge.TIKTOK_TEXT_SYSTEM_PROMPT_TEMPLATE)

    def test_facebook_uses_facebook_prompt(self):
        """Facebook判定はFacebook専用プロンプトを使用する"""
        import judge
        self.assertIn("FACEBOOK_TEXT_SYSTEM_PROMPT_TEMPLATE", dir(judge))
        self.assertIn("Facebook", judge.FACEBOOK_TEXT_SYSTEM_PROMPT_TEMPLATE)

    @patch("judge.get_rules", return_value=[])
    @patch("anthropic.Anthropic")
    def test_judge_post_tiktok(self, mock_anthropic, mock_rules):
        """TikTok判定でTikTok専用プロンプトが選択される"""
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text='{"risk_level": "🟢低", "summary": "問題なし", "issues": [], "improved_text": "テスト", "notes": ""}')]
        mock_client.messages.create.return_value = mock_message

        from judge import judge_post
        result = judge_post("テスト投稿", "TikTok")

        call_args = mock_client.messages.create.call_args
        system_prompt = call_args[1]["system"]
        self.assertIn("TikTok", system_prompt)

    @patch("judge.get_rules", return_value=[])
    @patch("anthropic.Anthropic")
    def test_judge_post_facebook(self, mock_anthropic, mock_rules):
        """Facebook判定でFacebook専用プロンプトが選択される"""
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text='{"risk_level": "🟢低", "summary": "問題なし", "issues": [], "improved_text": "テスト", "notes": ""}')]
        mock_client.messages.create.return_value = mock_message

        from judge import judge_post
        result = judge_post("テスト投稿", "Facebook")

        call_args = mock_client.messages.create.call_args
        system_prompt = call_args[1]["system"]
        self.assertIn("Facebook", system_prompt)


class TestVideoAnalyzer(unittest.TestCase):
    """動画判定テスト"""

    def test_is_video_file_mp4(self):
        """MP4はビデオファイルと判定"""
        from video_analyzer import is_video_file
        self.assertTrue(is_video_file("video/mp4"))

    def test_is_video_file_mov(self):
        """MOVはビデオファイルと判定"""
        from video_analyzer import is_video_file
        self.assertTrue(is_video_file("video/quicktime"))

    def test_is_video_file_text(self):
        """テキストはビデオファイルでない"""
        from video_analyzer import is_video_file
        self.assertFalse(is_video_file("text/plain"))

    def test_tiktok_prompt_exists(self):
        """TikTok動画プロンプトが存在する"""
        from video_analyzer import PLATFORM_VIDEO_PROMPTS
        self.assertIn("TikTok", PLATFORM_VIDEO_PROMPTS)
        self.assertIn("TikTok", PLATFORM_VIDEO_PROMPTS["TikTok"])

    def test_facebook_prompt_exists(self):
        """Facebook動画プロンプトが存在する"""
        from video_analyzer import PLATFORM_VIDEO_PROMPTS
        self.assertIn("Facebook", PLATFORM_VIDEO_PROMPTS)
        self.assertIn("Facebook", PLATFORM_VIDEO_PROMPTS["Facebook"])

    def test_error_result_structure(self):
        """エラー結果の構造確認"""
        from video_analyzer import _error_result
        result = _error_result("テストエラー")
        self.assertIn("risk_level", result)
        self.assertIn("summary", result)
        self.assertEqual(result["summary"], "テストエラー")

    def test_calc_interval_short(self):
        """15秒以下は3秒間隔"""
        from video_analyzer import _calc_interval
        self.assertEqual(_calc_interval(10), 3)

    def test_calc_interval_medium(self):
        """16〜60秒は5秒間隔"""
        from video_analyzer import _calc_interval
        self.assertEqual(_calc_interval(30), 5)

    def test_calc_interval_long(self):
        """61〜180秒は10秒間隔"""
        from video_analyzer import _calc_interval
        self.assertEqual(_calc_interval(120), 10)


class TestFormatSlackResponse(unittest.TestCase):
    """Slack返信フォーマットテスト"""

    def test_format_high_risk(self):
        """🔴高リスクの返信フォーマット"""
        from judge import format_slack_response
        result = {
            "risk_level": "🔴高",
            "summary": "テスト問題",
            "issues": [{"text": "問題テキスト", "rule": "ルール1", "reason": "理由", "suggestion": "改善案"}],
            "improved_text": "改善後テキスト",
            "notes": "",
        }
        response = format_slack_response(result)
        self.assertIn("🔴", response)
        self.assertIn("BAN可能性: 高", response)

    def test_format_low_risk(self):
        """🟢低リスクの返信フォーマット"""
        from judge import format_slack_response
        result = {
            "risk_level": "🟢低",
            "summary": "問題なし",
            "issues": [],
            "improved_text": "",
            "notes": "",
        }
        response = format_slack_response(result)
        self.assertIn("🟢", response)
        self.assertIn("問題なし", response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
