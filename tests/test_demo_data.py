import json
import unittest
from pathlib import Path

from streamlit_app import free_question_answer, range_position, range_status


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


class DemoDataTests(unittest.TestCase):
    def load(self, case_id: str) -> dict:
        path = next(DATA_DIR.glob(f"{case_id}_*_V1.1.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_pipeline_versions(self):
        for case_id in ("P01", "P03", "P04", "P05"):
            with self.subTest(case_id=case_id):
                self.assertEqual(self.load(case_id)["pipeline"]["version"], "SLEEP-END-TO-END-V1.1")

    def test_question_selection(self):
        expected = {"P01": "DS01", "P03": "DS21", "P04": "DS15", "P05": None}
        for case_id, question_id in expected.items():
            with self.subTest(case_id=case_id):
                question = self.load(case_id)["metric_card"]["recommended_question"]
                actual = question["question_id"] if question else None
                self.assertEqual(actual, question_id)

    def test_answer_presence(self):
        for case_id in ("P01", "P03", "P04"):
            self.assertIsNotNone(self.load(case_id)["question_answer"])
        self.assertIsNone(self.load("P05")["question_answer"])

    def test_required_knowledge_refs(self):
        expected = {
            "P01": {"K-DS-AMOUNT-01", "K-SLEEP-QUALITY-01"},
            "P03": {"K-DS-NIGHT-01", "K-SLEEP-QUALITY-01"},
            "P04": {"K-DS-TREND-01", "K-SLEEP-QUALITY-01"},
        }
        for case_id, refs in expected.items():
            actual = set(self.load(case_id)["question_answer"]["knowledge_refs"])
            self.assertTrue(refs.issubset(actual))

    def test_range_visualization_distinguishes_states(self):
        inside, _, _ = range_position(74, 73, 76)
        below, _, _ = range_position(45, 73, 76)
        self.assertGreater(inside, 30)
        self.assertLess(inside, 70)
        self.assertLess(below, 10)
        self.assertEqual(range_status(74, 73, 76)[1], "within")
        self.assertEqual(range_status(45, 73, 76)[1], "below")

    def test_trend_question_respects_selected_scenario(self):
        answer, refs, scope = free_question_answer("最近几天深睡都偏少吗？", "P01")
        self.assertIn("没有提供足够的多晚趋势证据", answer)
        self.assertIn("K-DS-TREND-01", refs)
        self.assertEqual(scope, "当前场景数据 + 审核知识")

    def test_out_of_scope_question_is_explicit(self):
        answer, refs, scope = free_question_answer("REM 怎么样？", "P04")
        self.assertIn("当前 MVP", answer)
        self.assertEqual(refs, "MVP 产品边界")
        self.assertEqual(scope, "超出当前 MVP 范围")


if __name__ == "__main__":
    unittest.main()
