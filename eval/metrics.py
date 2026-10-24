"""评测指标：等级是有序的，所以用二次加权 kappa 而不是普通准确率。

差一档（优秀→一般）和差三档（优秀→pass）的严重程度不同，
加权 kappa 会把这件事算进去。
"""

from __future__ import annotations

from collections import Counter

# 从低到高；索引即等级序号
GRADE_ORDER: tuple[str, ...] = ("pass", "及格", "一般", "优秀")
GRADE_RANK: dict[str, int] = {grade: index for index, grade in enumerate(GRADE_ORDER)}
# 相邻一致：差一档以内算“方向没错”
ADJACENT_TOLERANCE = 1


def confusion_matrix(
    expected: list[str], predicted: list[str], labels: tuple[str, ...] = GRADE_ORDER
) -> list[list[int]]:
    """行=人工等级，列=模型等级。"""
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for truth, guess in zip(expected, predicted):
        if truth in index and guess in index:
            matrix[index[truth]][index[guess]] += 1
    return matrix


def quadratic_weighted_kappa(
    expected: list[str], predicted: list[str], labels: tuple[str, ...] = GRADE_ORDER
) -> float:
    """二次加权 kappa。完全一致为 1，随机水平为 0，负值表示比随机还差。"""
    pairs = [(labels.index(a), labels.index(b)) for a, b in zip(expected, predicted)
             if a in labels and b in labels]
    n = len(pairs)
    if n == 0:
        return float("nan")

    k = len(labels)
    observed = [[0] * k for _ in range(k)]
    for truth, guess in pairs:
        observed[truth][guess] += 1

    row_sums = [sum(row) for row in observed]
    col_sums = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    expected_mass = [[row_sums[i] * col_sums[j] / n for j in range(k)] for i in range(k)]

    def weight(i: int, j: int) -> float:
        return ((i - j) ** 2) / ((k - 1) ** 2)

    numerator = sum(weight(i, j) * observed[i][j] for i in range(k) for j in range(k))
    denominator = sum(weight(i, j) * expected_mass[i][j] for i in range(k) for j in range(k))

    if denominator == 0:
        return 1.0 if numerator == 0 else 0.0
    return 1 - numerator / denominator


def exact_accuracy(expected: list[str], predicted: list[str]) -> float:
    if not expected:
        return float("nan")
    return sum(a == b for a, b in zip(expected, predicted)) / len(expected)


def adjacent_accuracy(
    expected: list[str], predicted: list[str], tolerance: int = ADJACENT_TOLERANCE
) -> float:
    """差一档以内算对，衡量“大方向有没有错”。"""
    pairs = [(GRADE_RANK[a], GRADE_RANK[b]) for a, b in zip(expected, predicted)
             if a in GRADE_RANK and b in GRADE_RANK]
    if not pairs:
        return float("nan")
    return sum(abs(a - b) <= tolerance for a, b in pairs) / len(pairs)


def mean_absolute_error(pairs: list[tuple[float, float]]) -> float:
    if not pairs:
        return float("nan")
    return sum(abs(a - b) for a, b in pairs) / len(pairs)


def format_confusion(matrix: list[list[int]], labels: tuple[str, ...] = GRADE_ORDER) -> str:
    width = max(len(label) for label in labels) + 2
    header = " " * (width + 4) + "".join(f"{label:>{width}}" for label in labels)
    lines = ["行=人工  列=模型", header]
    for label, row in zip(labels, matrix):
        lines.append(f"{label:>{width}}    " + "".join(f"{value:>{width}}" for value in row))
    return "\n".join(lines)


def most_common(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None
