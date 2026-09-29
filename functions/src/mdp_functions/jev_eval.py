"""Small, offline evaluation helpers shared by eval and diff."""

import csv
import math
from pathlib import Path

from mdp_functions.jev_types import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)


def labeled_rows(path: Path, questions):
    rows = list(csv.DictReader(path.open(newline="")))
    seen = set()
    for row in rows:
        identity = row.get("state_id")
        if not identity or identity in seen:
            raise ValueError("Labels need unique, nonempty state_id values")
        seen.add(identity)
        questions.state(row)
        for key, question in questions.questions.items():
            value = row.get("expected_" + key)
            if value is None or value == "":
                raise ValueError(f"Labels need expected_{key} on every row")
            if isinstance(question, Choice):
                if value not in question.criteria:
                    raise ValueError(f"expected_{key} must be a declared option")
            else:
                number = float(value)
                maximum = (
                    len(question.criteria) - 1 if isinstance(question, Score) else 1
                )
                if not math.isfinite(number) or not 0 <= number <= maximum:
                    raise ValueError(f"expected_{key} is outside the question range")
                if isinstance(question, Noul) and number not in (0, 1):
                    raise ValueError("Noul calibration needs binary labels: 0 or 1")
    if not rows:
        raise ValueError("Labels must contain at least one row")
    return rows


def mean(values):
    return sum(values) / len(values) if values else None


def value(answer):
    if isinstance(answer, ChoiceAnswer):
        return answer.choice
    return answer.score if isinstance(answer, ScoreAnswer) else answer.noul


def reliability(pairs):
    bins = []
    for index in range(10):
        selected = [(p, y) for p, y in pairs if min(9, int(p * 10)) == index]
        bins.append(
            {
                "low": index / 10,
                "high": (index + 1) / 10,
                "count": len(selected),
                "probability": mean([p for p, _ in selected]),
                "observed": mean([y for _, y in selected]),
            }
        )
    return bins


def metrics(questions, rows, responses):
    result = {}
    for key, question in questions.questions.items():
        answers = [r.answers[key] for r in responses]
        labels = [row["expected_" + key] for row in rows]
        if not isinstance(question, Choice):
            labels = list(map(float, labels))
        correct, pairs, briers = [], [], []
        for label, answer in zip(labels, answers, strict=True):
            if isinstance(answer, NoulAnswer):
                correct.append((answer.noul >= 0.5) == bool(label))
                pairs.append((answer.noul, label))
                briers.append((answer.noul - label) ** 2)
            else:
                # Score accuracy is nearest-level agreement; MAE retains the continuous score.
                prediction = (
                    answer.choice
                    if isinstance(answer, ChoiceAnswer)
                    else math.floor(answer.score + 0.5)
                )
                expected = (
                    math.floor(label + 0.5) if isinstance(question, Score) else label
                )
                correct.append(prediction == expected)
                if isinstance(question, Score) and not float(label).is_integer():
                    continue  # Continuous labels have no one-hot class for Brier/calibration.
                label_key = str(int(label)) if isinstance(question, Score) else label
                probabilities = [
                    (p, float(option == label_key))
                    for option, p in answer.probabilities.items()
                ]
                pairs.extend(probabilities)
                briers.append(sum((p - y) ** 2 for p, y in probabilities))
        item = {
            "count": len(rows),
            "accuracy": mean(correct),
            "brier": mean(briers),
            "calibration_rows": len(briers),
            "reliability": reliability(pairs),
        }
        if isinstance(question, Choice):
            classes = {}
            for option in question.criteria:
                tp = sum(
                    a.choice == option and y == option
                    for a, y in zip(answers, labels, strict=True)
                )
                predicted = sum(a.choice == option for a in answers)
                actual = labels.count(option)
                classes[option] = {
                    "precision": tp / predicted if predicted else None,
                    "recall": tp / actual if actual else None,
                    "support": actual,
                }
            item["classes"] = classes
        if isinstance(question, Score):
            item["mae"] = mean(
                [abs(a.score - y) for a, y in zip(answers, labels, strict=True)]
            )
        if not isinstance(question, Noul):
            thresholds = sorted(
                {0, 0.5, 0.7, 0.8, 0.9, 1, questions.thresholds.get(key, 0)}
            )
            item["coverage"] = [
                {
                    "threshold": t,
                    "coverage": sum(a.confidence >= t for a in answers) / len(answers),
                    "accuracy": mean(
                        [
                            c
                            for c, a in zip(correct, answers, strict=True)
                            if a.confidence >= t
                        ]
                    ),
                }
                for t in thresholds
            ]
        result[key] = item
    return result


def missed_floors(questions, results):
    missed = []
    for key, limit in questions.floors.items():
        question, metric = key.split(".", 1)
        if (
            question not in results
            or metric not in {"accuracy", "mae", "brier"}
            or metric not in results[question]
        ):
            raise ValueError(f"Unknown evaluation floor: {key}")
        actual = results[question][metric]
        if actual is None or (
            actual > limit if metric in {"mae", "brier"} else actual < limit
        ):
            missed.append(key)
    return missed


def report_text(report):
    lines = [
        f"# Jev evaluation: {report['question_set']}",
        "",
        f"Mode: {report['mode']}.",
        f"Question version: `{report['version']}`.",
        f"Model: `{report['model']}`.",
        "",
        "Synthetic responses test the workflow. They do not measure vendor quality."
        if report["mode"] == "synthetic"
        else "Responses replay the saved vendor cassette.",
        "",
    ]
    for key, item in report["metrics"].items():
        lines += [
            f"## {key}",
            "",
            f"Rows: {item['count']}. Accuracy: {item['accuracy']:.3f}. Brier: {item['brier']}.",
        ]
        if "mae" in item:
            lines += [
                f"Mean absolute error: {item['mae']:.3f}. Accuracy uses the nearest level.",
                "Brier and reliability use only integer-level labels.",
            ]
        if "classes" in item:
            lines += [
                "",
                "| Class | Precision | Recall | Support |",
                "|---|---:|---:|---:|",
            ]
            lines += [
                f"| {k} | {v['precision']} | {v['recall']} | {v['support']} |"
                for k, v in item["classes"].items()
            ]
        lines += [
            "",
            "Reliability pools each option's probability and one-hot label. Brier sums squared errors per row.",
            "",
            "| Probability bin | Count | Mean probability | Observed fraction |",
            "|---|---:|---:|---:|",
        ]
        lines += [
            f"| {b['low']:.1f}–{b['high']:.1f} | {b['count']} | {b['probability']} | {b['observed']} |"
            for b in item["reliability"]
        ]
        if "coverage" in item:
            lines += [
                "",
                "| Confidence threshold | Coverage | Accuracy |",
                "|---|---:|---:|",
            ]
            lines += [
                f"| {b['threshold']} | {b['coverage']} | {b['accuracy']} |"
                for b in item["coverage"]
            ]
    lines += [
        "",
        "Missed floors: " + (", ".join(report["missed_floors"]) or "none") + ".",
        "",
    ]
    return "\n".join(lines)
