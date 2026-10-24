"""确定性规则的测试：年限档位、学历评分、聚合与硬门槛。"""

import unittest

from resume_agent.rules import (
    aggregate,
    education_dim,
    school_tier,
    tenure_dim,
)
from resume_agent.schema import DimResult, EducationEntry, ResumeDoc


def dims(tech=80.0, project=80.0, tenure=100.0, education=75.0) -> dict[str, DimResult]:
    return {
        "tech": DimResult("tech", tech, evidence=["主导架构设计"]),
        "project": DimResult("project", project, evidence=["0 到 1 搭建"]),
        "tenure": DimResult("tenure", tenure, evidence=["6 年"]),
        "education": DimResult("education", education, evidence=["本科"]),
    }


class TenureDimTest(unittest.TestCase):
    def test_meets_requirement(self):
        self.assertEqual(tenure_dim(1.0, required=1.0).score, 100.0)

    def test_slightly_below_requirement(self):
        self.assertEqual(tenure_dim(0.9, required=1.0).score, 80.0)

    def test_far_below_requirement(self):
        self.assertEqual(tenure_dim(0.7, required=1.0).score, 60.0)

    def test_below_veto_line_scores_zero(self):
        self.assertEqual(tenure_dim(0.5, required=1.0).score, 0.0)

    def test_unknown_years_is_neutral_and_low_confidence(self):
        dim = tenure_dim(None)
        self.assertEqual(dim.score, 60.0)
        self.assertLess(dim.confidence, 0.5)

    def test_overqualified_is_flagged_in_reason(self):
        self.assertIn("overqualified", tenure_dim(3.0, required=1.0).reason)


class SchoolTierTest(unittest.TestCase):
    def test_matches_configured_school(self):
        tiers = {"燕京理工学院": "普通", "清华大学": "985"}
        self.assertEqual(school_tier("清华大学", tiers), "985")

    def test_unknown_school(self):
        self.assertIsNone(school_tier("某不知名学院", {"清华大学": "985"}))

    def test_missing_config(self):
        self.assertIsNone(school_tier("清华大学", None))


class EducationDimTest(unittest.TestCase):
    def test_bachelor(self):
        doc = ResumeDoc(education=[EducationEntry(school="燕京理工学院", degree="本科")])
        self.assertEqual(education_dim(doc).score, 75.0)

    def test_master(self):
        doc = ResumeDoc(education=[EducationEntry(school="某大学", degree="硕士")])
        self.assertEqual(education_dim(doc).score, 85.0)

    def test_985_bonus_is_capped_at_100(self):
        doc = ResumeDoc(education=[EducationEntry(school="清华大学", degree="博士")])
        self.assertEqual(education_dim(doc, {"清华大学": "985"}).score, 100.0)

    def test_without_tier_config_confidence_is_lower(self):
        doc = ResumeDoc(education=[EducationEntry(school="清华大学", degree="本科")])
        dim = education_dim(doc, None)
        self.assertLess(dim.confidence, 1.0)

    def test_missing_education(self):
        dim = education_dim(ResumeDoc())
        self.assertEqual(dim.score, 0.0)
        self.assertLess(dim.confidence, 0.5)

    def test_picks_best_entry(self):
        doc = ResumeDoc(
            education=[
                EducationEntry(school="某学院", degree="专科"),
                EducationEntry(school="某大学", degree="硕士"),
            ]
        )
        self.assertEqual(education_dim(doc).score, 85.0)


class AggregateTest(unittest.TestCase):
    def test_excellent(self):
        verdict = aggregate(dims(90, 90, 100, 75), years=6.0)
        self.assertEqual(verdict.grade, "优秀")
        self.assertAlmostEqual(verdict.score, 90.75, places=2)

    def test_normal(self):
        self.assertEqual(aggregate(dims(70, 70, 100, 75), years=6.0).grade, "一般")

    def test_pass_by_score(self):
        self.assertEqual(aggregate(dims(50, 50, 60, 60), years=6.0).grade, "pass")

    def test_veto_overrides_high_score(self):
        verdict = aggregate(dims(95, 95, 0, 90), years=0.5)
        self.assertEqual(verdict.grade, "pass")
        self.assertIsNotNone(verdict.veto)
        self.assertIn("低于门槛", verdict.veto)

    def test_unknown_years_does_not_trigger_veto(self):
        verdict = aggregate(dims(90, 90, 60, 75), years=None)
        self.assertIsNone(verdict.veto)

    def test_low_confidence_is_surfaced(self):
        dimensions = dims()
        dimensions["project"] = DimResult("project", 80, evidence=[], confidence=0.3)
        self.assertTrue(aggregate(dimensions, years=6.0).low_confidence)

    def test_missing_dimension_raises(self):
        with self.assertRaises(ValueError):
            aggregate({"tech": DimResult("tech", 80)}, years=6.0)

    def test_summary_mentions_grade(self):
        self.assertIn("优秀", aggregate(dims(90, 90, 100, 75), years=6.0).summary)


if __name__ == "__main__":
    unittest.main()
