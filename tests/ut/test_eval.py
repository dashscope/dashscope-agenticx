# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.eval — EvalCase, EvalResult, EvalRunner scoring,
ProviderComparator, load_eval_suite."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dashscope_agenticx.eval import (
    EvalCase,
    EvalResult,
    EvalRunner,
    ProviderComparator,
    load_eval_suite,
)

# ---------------------------------------------------------------------------
# Mock agent — async generator for run_stream + messages list
# ---------------------------------------------------------------------------


class MockAgent:
    """Minimal agent stub compatible with EvalRunner.run_case.

    - ``run_stream(prompt)`` is an async generator yielding response chunks.
    - ``messages`` is a list of message dicts (matching the agent protocol).
    - ``exc`` if set is raised on the first yield.
    """

    def __init__(self, chunks=None, messages=None, exc=None):
        self._chunks = chunks if chunks is not None else ["hello world"]
        self.messages = messages if messages is not None else []
        self._exc = exc

    async def run_stream(self, prompt):
        if self._exc:
            raise self._exc
        for chunk in self._chunks:
            yield chunk


def _assistant_msg_with_tools(tool_names):
    """Build an assistant message dict with tool_calls, as the runner expects."""
    return {
        "role": "assistant",
        "tool_calls": [{"function": {"name": name}} for name in tool_names],
    }


# ---------------------------------------------------------------------------
# EvalCase
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_required_fields(self):
        case = EvalCase(name="t1", input="hello")
        assert case.name == "t1"
        assert case.input == "hello"

    def test_defaults(self):
        case = EvalCase(name="t1", input="hello")
        assert case.expected_keywords == []
        assert case.expected_tools == []
        assert case.must_not_contain == []
        assert case.max_turns == 10

    def test_with_all_fields(self):
        case = EvalCase(
            name="t2",
            input="search for cats",
            expected_keywords=["cat", "result"],
            expected_tools=["search"],
            must_not_contain=["error"],
            max_turns=5,
        )
        assert case.expected_keywords == ["cat", "result"]
        assert case.expected_tools == ["search"]
        assert case.must_not_contain == ["error"]
        assert case.max_turns == 5


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


class TestEvalResult:
    def test_to_dict_keys(self):
        result = EvalResult(case_name="t1", passed=True, score=0.9)
        d = result.to_dict()
        expected_keys = {
            "case_name",
            "passed",
            "score",
            "response",
            "tools_used",
            "errors",
            "duration",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self):
        result = EvalResult(
            case_name="t1",
            passed=True,
            score=0.85,
            response="The answer is 42",
            tools_used=["search", "read"],
            errors=[],
            duration=1.5,
        )
        d = result.to_dict()
        assert d["case_name"] == "t1"
        assert d["passed"] is True
        assert d["score"] == 0.85
        assert d["response"] == "The answer is 42"
        assert d["tools_used"] == ["search", "read"]
        assert d["errors"] == []
        assert d["duration"] == 1.5

    def test_to_dict_truncates_long_response(self):
        long_text = "x" * 1000
        result = EvalResult(
            case_name="t1",
            passed=False,
            score=0.0,
            response=long_text,
        )
        d = result.to_dict()
        assert len(d["response"]) == 500

    def test_to_dict_preserves_short_response(self):
        result = EvalResult(
            case_name="t1",
            passed=True,
            score=1.0,
            response="short",
        )
        d = result.to_dict()
        assert d["response"] == "short"

    def test_defaults(self):
        result = EvalResult(case_name="t1", passed=False, score=0.0)
        assert result.response == ""
        assert result.tools_used == []
        assert result.errors == []
        assert result.duration == 0.0


# ---------------------------------------------------------------------------
# EvalRunner.run_case — scoring components
# ---------------------------------------------------------------------------


class TestRunCaseKeywordScoring:
    """Keyword overlap component (0.4 weight)."""

    async def test_keyword_full_match(self):
        case = EvalCase(name="kw", input="q", expected_keywords=["hello"])
        agent = MockAgent(chunks=["hello world"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0.3 + no-forbidden 0.2 + no-errors 0.1 = 1.0
        assert result.score == pytest.approx(1.0)
        assert result.passed is True

    async def test_keyword_partial_match(self):
        case = EvalCase(
            name="kw",
            input="q",
            expected_keywords=["hello", "python"],
        )
        agent = MockAgent(chunks=["hello world"])  # only "hello" present
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword: 0.4 * (1/2) = 0.2; tools 0.3; no-forbidden 0.2; no-errors 0.1
        assert result.score == pytest.approx(0.2 + 0.3 + 0.2 + 0.1)
        assert result.passed is True  # 0.8 >= 0.7 and no errors

    async def test_keyword_no_match(self):
        case = EvalCase(name="kw", input="q", expected_keywords=["python"])
        agent = MockAgent(chunks=["hello world"])  # no "python"
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword: 0.4 * 0 = 0; tools 0.3; no-forbidden 0.2; no-errors 0.1 = 0.6
        assert result.score == pytest.approx(0.6)
        assert result.passed is False  # 0.6 < 0.7

    async def test_keyword_case_insensitive(self):
        case = EvalCase(name="kw", input="q", expected_keywords=["HELLO"])
        agent = MockAgent(chunks=["hello world"])  # lowercase in response
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score == pytest.approx(1.0)

    async def test_no_keywords_gives_full_marks(self):
        case = EvalCase(name="kw", input="q")  # no expected_keywords
        agent = MockAgent(chunks=["anything"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score == pytest.approx(1.0)


class TestRunCaseMaxTurns:
    """EvalCase.max_turns must be applied to the agent before the run."""

    async def test_applies_case_max_turns(self):
        case = EvalCase(name="mt", input="q", max_turns=3)
        agent = MockAgent(chunks=["hello"])
        runner = EvalRunner()
        await runner.run_case(case, agent)
        assert agent.max_turns == 3

    async def test_default_max_turns(self):
        case = EvalCase(name="mt", input="q")
        agent = MockAgent(chunks=["hello"])
        runner = EvalRunner()
        await runner.run_case(case, agent)
        assert agent.max_turns == 10


class TestRunCaseToolScoring:
    """Tool usage component (0.3 weight)."""

    async def test_tool_full_match(self):
        case = EvalCase(name="tool", input="q", expected_tools=["search"])
        agent = MockAgent(
            chunks=["done"],
            messages=[_assistant_msg_with_tools(["search"])],
        )
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0.3 + no-forbidden 0.2 + no-errors 0.1 = 1.0
        assert result.score == pytest.approx(1.0)
        assert "search" in result.tools_used

    async def test_tool_partial_match(self):
        case = EvalCase(
            name="tool",
            input="q",
            expected_tools=["search", "read"],
        )
        agent = MockAgent(
            chunks=["done"],
            messages=[_assistant_msg_with_tools(["search"])],
        )
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0.3*(1/2)=0.15 + no-forbidden 0.2 + no-errors 0.1 = 0.85
        assert result.score == pytest.approx(0.85)

    async def test_tool_no_match(self):
        case = EvalCase(name="tool", input="q", expected_tools=["search"])
        agent = MockAgent(chunks=["done"], messages=[])  # no tool calls
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0 + no-forbidden 0.2 + no-errors 0.1 = 0.7
        assert result.score == pytest.approx(0.7)
        assert result.passed is True  # 0.7 >= 0.7 and no errors

    async def test_no_expected_tools_gives_full_marks(self):
        case = EvalCase(name="tool", input="q")  # no expected_tools
        agent = MockAgent(chunks=["done"], messages=[])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score == pytest.approx(1.0)

    async def test_tools_collected_from_last_assistant_message(self):
        """Runner should collect tool names from the last assistant message
        that has tool_calls (not earlier ones)."""
        case = EvalCase(name="tool", input="q", expected_tools=["final_tool"])
        agent = MockAgent(
            chunks=["done"],
            messages=[
                _assistant_msg_with_tools(["earlier_tool"]),
                {"role": "user", "content": "..."},
                _assistant_msg_with_tools(["final_tool"]),
            ],
        )
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert "final_tool" in result.tools_used
        assert result.score == pytest.approx(1.0)


class TestRunCaseForbiddenContent:
    """No-forbidden-content component (0.2 weight)."""

    async def test_no_forbidden_present(self):
        case = EvalCase(name="forbid", input="q", must_not_contain=["error"])
        agent = MockAgent(chunks=["all good"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0.3 + no-forbidden 0.2 + no-errors 0.1 = 1.0
        assert result.score == pytest.approx(1.0)
        assert result.passed is True
        assert result.errors == []

    async def test_forbidden_present_fails(self):
        case = EvalCase(name="forbid", input="q", must_not_contain=["error"])
        agent = MockAgent(chunks=["an error occurred"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # keyword 0.4 + tools 0.3 + forbidden 0 + errors 0 = 0.7
        # BUT passed is False because errors is non-empty
        assert result.score == pytest.approx(0.7)
        assert result.passed is False
        assert any("forbidden" in e.lower() for e in result.errors)

    async def test_forbidden_case_insensitive(self):
        case = EvalCase(name="forbid", input="q", must_not_contain=["ERROR"])
        agent = MockAgent(chunks=["an error happened"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.passed is False

    async def test_no_must_not_contain_gives_full_marks(self):
        case = EvalCase(name="forbid", input="q")  # no must_not_contain
        agent = MockAgent(chunks=["anything goes"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score == pytest.approx(1.0)


class TestRunCaseErrors:
    """No-errors component (0.1 weight)."""

    async def test_no_errors_success(self):
        case = EvalCase(name="err", input="q")
        agent = MockAgent(chunks=["ok"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.errors == []
        assert result.score == pytest.approx(1.0)
        assert result.passed is True

    async def test_agent_raises_exception(self):
        case = EvalCase(name="err", input="q")
        agent = MockAgent(exc=RuntimeError("agent crashed"))
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert len(result.errors) == 1
        assert "agent crashed" in result.errors[0]
        # keyword 0.4 + tools 0.3 + no-forbidden 0.2 + errors 0 = 0.9
        assert result.score == pytest.approx(0.9)
        assert result.passed is False  # errors non-empty


# ---------------------------------------------------------------------------
# EvalRunner.run_case — pass threshold & edge cases
# ---------------------------------------------------------------------------


class TestRunCasePassThreshold:
    """pass threshold is score >= 0.7 AND no errors."""

    async def test_score_above_threshold_passes(self):
        # No keywords/tools/forbidden -> score 1.0
        case = EvalCase(name="pass", input="q")
        agent = MockAgent(chunks=["hello"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score >= 0.7
        assert result.passed is True

    async def test_score_below_threshold_fails(self):
        # Keyword miss -> score 0.6
        case = EvalCase(name="fail", input="q", expected_keywords=["missing"])
        agent = MockAgent(chunks=["hello world"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score < 0.7
        assert result.passed is False

    async def test_score_at_threshold_passes(self):
        # Tool miss with single expected tool -> score exactly 0.7
        case = EvalCase(name="edge", input="q", expected_tools=["search"])
        agent = MockAgent(chunks=["done"], messages=[])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        assert result.score == pytest.approx(0.7)
        assert result.passed is True  # 0.7 >= 0.7 and no errors

    async def test_high_score_with_errors_still_fails(self):
        """Even if score >= 0.7, non-empty errors means fail."""
        case = EvalCase(name="err", input="q", must_not_contain=["bad"])
        agent = MockAgent(chunks=["this is bad"])
        runner = EvalRunner()
        result = await runner.run_case(case, agent)
        # score = 0.4 + 0.3 + 0 + 0 = 0.7, but errors non-empty
        assert result.score == pytest.approx(0.7)
        assert result.passed is False


class TestRunCaseNoAgent:
    async def test_no_agent_no_factory(self):
        runner = EvalRunner()  # no factory
        case = EvalCase(name="noagent", input="q")
        result = await runner.run_case(case)
        assert result.passed is False
        assert result.score == 0.0
        assert "No agent available" in result.errors

    async def test_agent_factory_used_when_no_agent(self):
        factory_agent = MockAgent(chunks=["from factory"])
        runner = EvalRunner(agent_factory=lambda: factory_agent)
        case = EvalCase(name="factory", input="q")
        result = await runner.run_case(case)
        assert result.passed is True
        assert "from factory" in result.response

    async def test_explicit_agent_takes_precedence_over_factory(self):
        factory_agent = MockAgent(chunks=["from factory"])
        explicit_agent = MockAgent(chunks=["from explicit"])
        runner = EvalRunner(agent_factory=lambda: factory_agent)
        case = EvalCase(name="precedence", input="q")
        result = await runner.run_case(case, agent=explicit_agent)
        assert "from explicit" in result.response


class TestRunCaseResponse:
    async def test_response_is_concatenation_of_chunks(self):
        agent = MockAgent(chunks=["chunk1", "chunk2", "chunk3"])
        runner = EvalRunner()
        case = EvalCase(name="resp", input="q")
        result = await runner.run_case(case, agent)
        assert result.response == "chunk1chunk2chunk3"

    async def test_duration_is_non_negative(self):
        agent = MockAgent(chunks=["hello"])
        runner = EvalRunner()
        case = EvalCase(name="dur", input="q")
        result = await runner.run_case(case, agent)
        assert result.duration >= 0.0


# ---------------------------------------------------------------------------
# EvalRunner.run_suite
# ---------------------------------------------------------------------------


class TestRunSuite:
    async def test_multiple_cases(self):
        cases = [
            EvalCase(name="c1", input="q1"),
            EvalCase(name="c2", input="q2"),
            EvalCase(name="c3", input="q3"),
        ]
        agent = MockAgent(chunks=["hello"])
        runner = EvalRunner()
        results = await runner.run_suite(cases, agent)
        assert len(results) == 3
        assert all(r.passed for r in results)
        assert [r.case_name for r in results] == ["c1", "c2", "c3"]

    async def test_mixed_pass_fail(self):
        cases = [
            EvalCase(name="pass", input="q"),  # score 1.0
            EvalCase(name="fail", input="q", expected_keywords=["missing"]),
        ]
        agent = MockAgent(chunks=["hello world"])
        runner = EvalRunner()
        results = await runner.run_suite(cases, agent)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    async def test_empty_suite(self):
        runner = EvalRunner()
        results = await runner.run_suite([], agent=MockAgent())
        assert results == []

    async def test_same_agent_reused(self):
        """run_suite should pass the same agent to each case."""
        cases = [EvalCase(name=f"c{i}", input=f"q{i}") for i in range(3)]
        agent = MockAgent(chunks=["response"])
        runner = EvalRunner()
        results = await runner.run_suite(cases, agent)
        for r in results:
            assert r.response == "response"


# ---------------------------------------------------------------------------
# ProviderComparator
# ---------------------------------------------------------------------------


class TestProviderComparator:
    async def test_clear_winner(self):
        """Agent A matches keywords, agent B does not -> A wins."""
        cases = [EvalCase(name="c1", input="q", expected_keywords=["hello"])]
        agent_a = MockAgent(chunks=["hello world"])  # keyword match -> 1.0
        agent_b = MockAgent(chunks=["goodbye"])  # no match -> 0.6
        comparator = ProviderComparator()
        report = await comparator.compare(
            cases,
            agent_a,
            agent_b,
            "Alpha",
            "Beta",
        )
        assert report["winner"] == "Alpha"
        assert report["avg_score_a"] > report["avg_score_b"]
        assert report["pass_rate_a"] == 1.0
        assert report["pass_rate_b"] == 0.0

    async def test_tie(self):
        """Both agents produce identical results -> tie."""
        cases = [EvalCase(name="c1", input="q", expected_keywords=["hello"])]
        agent_a = MockAgent(chunks=["hello world"])
        agent_b = MockAgent(chunks=["hello world"])
        comparator = ProviderComparator()
        report = await comparator.compare(cases, agent_a, agent_b, "A", "B")
        assert report["winner"] == "tie"
        assert report["avg_score_a"] == pytest.approx(report["avg_score_b"])

    async def test_b_wins(self):
        """Agent B scores higher -> B wins."""
        cases = [EvalCase(name="c1", input="q", expected_keywords=["hello"])]
        agent_a = MockAgent(chunks=["goodbye"])  # 0.6
        agent_b = MockAgent(chunks=["hello world"])  # 1.0
        comparator = ProviderComparator()
        report = await comparator.compare(cases, agent_a, agent_b)
        assert report["winner"] == "B"

    async def test_report_structure(self):
        cases = [EvalCase(name="c1", input="q")]
        agent_a = MockAgent(chunks=["hello"])
        agent_b = MockAgent(chunks=["hello"])
        comparator = ProviderComparator()
        report = await comparator.compare(cases, agent_a, agent_b, "X", "Y")
        expected_keys = {
            "label_a",
            "label_b",
            "avg_score_a",
            "avg_score_b",
            "pass_rate_a",
            "pass_rate_b",
            "avg_duration_a",
            "avg_duration_b",
            "winner",
            "results_a",
            "results_b",
        }
        assert set(report.keys()) == expected_keys
        assert report["label_a"] == "X"
        assert report["label_b"] == "Y"
        assert isinstance(report["results_a"], list)
        assert isinstance(report["results_b"], list)
        assert len(report["results_a"]) == 1
        assert len(report["results_b"]) == 1

    async def test_multiple_cases_averaged(self):
        cases = [
            EvalCase(name="c1", input="q", expected_keywords=["hello"]),
            EvalCase(name="c2", input="q", expected_keywords=["world"]),
        ]
        agent_a = MockAgent(chunks=["hello world"])  # both match -> avg 1.0
        agent_b = MockAgent(chunks=["hello only"])  # c1 matches, c2 misses
        comparator = ProviderComparator()
        report = await comparator.compare(cases, agent_a, agent_b, "A", "B")
        assert report["winner"] == "A"
        assert report["avg_score_a"] == pytest.approx(1.0)
        assert report["avg_score_b"] < 1.0
        assert report["pass_rate_a"] == 1.0
        assert report["pass_rate_b"] == 0.5  # 1 of 2 passed

    async def test_custom_runner(self):
        """ProviderComparator should use the injected runner."""
        runner = EvalRunner()
        comparator = ProviderComparator(runner=runner)
        assert comparator._runner is runner


# ---------------------------------------------------------------------------
# load_eval_suite
# ---------------------------------------------------------------------------


class TestLoadEvalSuite:
    def test_load_full_suite(self, tmp_path):
        data = [
            {
                "name": "case1",
                "input": "hello",
                "expected_keywords": ["hi"],
                "expected_tools": ["search"],
                "must_not_contain": ["error"],
                "max_turns": 5,
            },
            {
                "name": "case2",
                "input": "bye",
            },
        ]
        path = tmp_path / "suite.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        cases = load_eval_suite(path)
        assert len(cases) == 2
        assert cases[0].name == "case1"
        assert cases[0].input == "hello"
        assert cases[0].expected_keywords == ["hi"]
        assert cases[0].expected_tools == ["search"]
        assert cases[0].must_not_contain == ["error"]
        assert cases[0].max_turns == 5
        assert cases[1].name == "case2"
        assert cases[1].expected_keywords == []
        assert cases[1].max_turns == 10  # default

    def test_load_empty_suite(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]", encoding="utf-8")
        cases = load_eval_suite(path)
        assert cases == []

    def test_load_accepts_string_path(self, tmp_path):
        data = [{"name": "c1", "input": "q"}]
        path = tmp_path / "suite.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        cases = load_eval_suite(str(path))
        assert len(cases) == 1

    async def test_loaded_cases_are_runnable(self, tmp_path):
        """Cases loaded from JSON should work with EvalRunner."""
        data = [
            {"name": "c1", "input": "q", "expected_keywords": ["hello"]},
        ]
        path = tmp_path / "suite.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        cases = load_eval_suite(path)
        runner = EvalRunner()
        agent = MockAgent(chunks=["hello world"])
        result = await runner.run_case(cases[0], agent)
        assert result.passed is True
        assert result.score == pytest.approx(1.0)

    def test_load_missing_name_raises(self, tmp_path):
        data = [{"input": "q"}]  # missing required "name"
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(KeyError):
            load_eval_suite(path)
