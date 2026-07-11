"""Tests for iris_cli.output — JSON/table/status/result formatting."""

import json
from typing import Any

import pytest

import iris_cli.output as output_mod


@pytest.fixture(autouse=True)
def _reset_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset FORMAT to 'human' before each test."""
    monkeypatch.setattr(output_mod, "FORMAT", "human")


class TestPrintJson:
    """print_json writes valid JSON to stdout."""

    def test_outputs_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        output_mod.print_json({"key": "value"})
        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert parsed == {"key": "value"}

    def test_handles_nested_dicts(self, capsys: pytest.CaptureFixture[str]) -> None:
        data: dict[str, Any] = {"a": {"b": {"c": [1, 2, 3]}}}
        output_mod.print_json(data)
        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert parsed["a"]["b"]["c"] == [1, 2, 3]

    def test_handles_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        output_mod.print_json([1, 2, 3])
        captured = capsys.readouterr().out
        assert json.loads(captured) == [1, 2, 3]

    def test_trailing_newline(self, capsys: pytest.CaptureFixture[str]) -> None:
        output_mod.print_json({})
        captured = capsys.readouterr().out
        assert captured.endswith("\n")


class TestPrintTable:
    """print_table outputs JSON when FORMAT='json'."""

    def test_json_format_outputs_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(output_mod, "FORMAT", "json")
        data = [{"id": "1", "name": "foo"}, {"id": "2", "name": "bar"}]
        output_mod.print_table(data, columns=["id", "name"])
        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert len(parsed) == 2
        assert parsed[0]["id"] == "1"

    def test_human_format_does_not_crash(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Smoke test: human format should not raise."""
        data = [{"id": "1", "name": "foo"}]
        output_mod.print_table(data, columns=["id", "name"])
        # Just confirm it ran without error


class TestPrintStatus:
    """print_status outputs JSON when FORMAT='json'."""

    def test_json_format_outputs_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(output_mod, "FORMAT", "json")
        output_mod.print_status("status", "ok")
        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert parsed == {"status": "ok"}


class TestPrintResult:
    """print_result dispatches based on FORMAT."""

    def test_json_format_outputs_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(output_mod, "FORMAT", "json")
        output_mod.print_result({"result": "done"})
        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert parsed == {"result": "done"}

    def test_human_format_dict(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Human format with a dict should not raise."""
        output_mod.print_result({"key": "val"})

    def test_human_format_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Human format with a plain string should not raise."""
        output_mod.print_result("hello")
