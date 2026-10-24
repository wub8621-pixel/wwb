"""一致性指标的测试。"""

import unittest

from eval.metrics import (
    adjacent_accuracy,
    confusion_matrix,
    exact_accuracy,
    format_confusion,
    mean_absolute_error,
    quadratic_weighted_kappa,
)


class ConfusionTest(unittest.TestCase):
    def test_counts_pairings(self):
        matrix = confusion_matrix(["优秀", "pass", "一般"], ["优秀", "及格", "一般"])
        self.assertEqual(matrix[3][3], 1)  # 优秀 -> 优秀
        self.assertEqual(matrix[0][1], 1)  # pass -> 及格
        self.assertEqual(matrix[2][2], 1)  # 一般 -> 一般

    def test_format_contains_all_labels(self):
        text = format_confusion(confusion_matrix(["优秀"], ["优秀"]))
        for label in ("优秀", "一般", "及格", "pass"):
            self.assertIn(label, text)


class KappaTest(unittest.TestCase):
    def test_perfect_agreement(self):
        values = ["优秀", "优秀", "一般", "pass"]
        self.assertAlmostEqual(quadratic_weighted_kappa(values, values), 1.0)

    def test_systematic_off_by_one_scores_lower(self):
        human = ["优秀", "优秀", "一般", "一般"]
        model = ["一般", "一般", "及格", "及格"]
        self.assertLess(quadratic_weighted_kappa(human, model), 0.5)

    def test_constant_prediction_is_zero(self):
        human = ["pass", "pass", "优秀", "优秀"]
        model = ["优秀", "优秀", "优秀", "优秀"]
        self.assertAlmostEqual(quadratic_weighted_kappa(human, model), 0.0)

    def test_empty_input_returns_nan(self):
        self.assertNotEqual(quadratic_weighted_kappa([], []), quadratic_weighted_kappa([], []))


class AccuracyTest(unittest.TestCase):
    def test_exact_accuracy(self):
        self.assertAlmostEqual(exact_accuracy(["优秀", "一般"], ["优秀", "及格"]), 0.5)

    def test_adjacent_accuracy_counts_off_by_one(self):
        human = ["优秀", "一般", "pass"]
        model = ["一般", "及格", "优秀"]
        # 前两个差一档算对，第三个差三档算错
        self.assertAlmostEqual(adjacent_accuracy(human, model), 2 / 3)

    def test_mae(self):
        self.assertAlmostEqual(mean_absolute_error([(80, 90), (70, 60)]), 10.0)

    def test_mae_empty(self):
        self.assertNotEqual(mean_absolute_error([]), mean_absolute_error([]))


if __name__ == "__main__":
    unittest.main()
