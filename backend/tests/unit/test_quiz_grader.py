"""
Unit tests for app.services.quiz_grader._parse_grades.

_parse_grades is a pure sync function (no I/O), so no mocking needed.
"""
from __future__ import annotations

import json

import pytest

from app.models.domain import QuizGrade
from app.services.quiz_grader import _parse_grades


class TestParseGrades:

    def test_valid_correct_grade(self):
        data = [{"attempt_id": 1, "grade": "correct", "rationale": "Spot on."}]
        raw = json.dumps(data)
        result = _parse_grades(raw)
        assert 1 in result
        grade, rationale = result[1]
        assert grade == QuizGrade.correct
        assert rationale == "Spot on."

    def test_valid_partial_grade(self):
        data = [{"attempt_id": 2, "grade": "partial", "rationale": "Close but misses key point."}]
        raw = json.dumps(data)
        result = _parse_grades(raw)
        assert result[2][0] == QuizGrade.partial

    def test_valid_incorrect_grade(self):
        data = [{"attempt_id": 3, "grade": "incorrect", "rationale": "Completely wrong."}]
        raw = json.dumps(data)
        result = _parse_grades(raw)
        assert result[3][0] == QuizGrade.incorrect

    def test_multiple_attempts(self):
        data = [
            {"attempt_id": 1, "grade": "correct", "rationale": "Good."},
            {"attempt_id": 2, "grade": "incorrect", "rationale": "Wrong."},
            {"attempt_id": 3, "grade": "partial", "rationale": "Partial."},
        ]
        result = _parse_grades(json.dumps(data))
        assert len(result) == 3

    def test_no_json_array_raises(self):
        with pytest.raises(ValueError, match="No JSON array"):
            _parse_grades('{"attempt_id": 1, "grade": "correct"}')

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_grades("")

    def test_invalid_grade_value_skips_item(self):
        data = [
            {"attempt_id": 5, "grade": "excellent", "rationale": "Unknown grade."},
            {"attempt_id": 6, "grade": "correct", "rationale": "OK."},
        ]
        result = _parse_grades(json.dumps(data))
        # attempt 5 has an invalid grade so it's dropped
        assert 5 not in result
        assert 6 in result

    def test_missing_attempt_id_skips_item(self):
        data = [
            {"grade": "correct", "rationale": "No id."},
            {"attempt_id": 7, "grade": "correct", "rationale": "Has id."},
        ]
        result = _parse_grades(json.dumps(data))
        assert 7 in result
        assert len(result) == 1

    def test_non_dict_item_skips_gracefully(self):
        raw = '[1, 2, {"attempt_id": 8, "grade": "correct", "rationale": "ok"}]'
        result = _parse_grades(raw)
        assert 8 in result

    def test_rationale_defaults_to_empty_string(self):
        data = [{"attempt_id": 9, "grade": "correct"}]
        result = _parse_grades(json.dumps(data))
        _, rationale = result[9]
        assert rationale == ""

    def test_grade_leading_trailing_whitespace_stripped(self):
        data = [{"attempt_id": 10, "grade": "  correct  ", "rationale": "Trimmed."}]
        result = _parse_grades(json.dumps(data))
        assert result[10][0] == QuizGrade.correct

    def test_prose_before_array_is_tolerated(self):
        inner = json.dumps([{"attempt_id": 11, "grade": "partial", "rationale": "Almost."}])
        raw = f"Here are my results: {inner} Hope that helps."
        result = _parse_grades(raw)
        assert 11 in result
