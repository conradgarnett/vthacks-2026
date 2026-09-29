"""RapidOCR's recognition model is a setting, and a bad setting never costs OCR.

The packaged model is PaddleOCR's Chinese recognizer, which drops the spaces
in English text; RAPIDOCR_REC_MODEL swaps another in. These pin the plumbing
with a stand-in engine, so they run without RapidOCR installed.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.ai import ocr
from backend.config import get_settings


class _Engine:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        _Engine.calls.append(kwargs)


class _OldEngine:
    """A RapidOCR build whose constructor takes no model keywords."""

    calls: list[dict] = []

    def __init__(self, *args):
        _OldEngine.calls.append({})


@pytest.fixture
def engine(monkeypatch):
    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = _Engine
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)
    monkeypatch.setitem(sys.modules, "rapidocr", None)  # 2.x absent
    _Engine.calls = []
    get_settings.cache_clear()
    yield module
    get_settings.cache_clear()


def test_unset_uses_the_packaged_model(engine, monkeypatch):
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", "")
    reader = ocr.RapidOCR()
    assert reader.available
    assert reader.rec_model == "packaged"
    assert _Engine.calls == [{}]


def test_configured_model_and_its_character_list(engine, monkeypatch, tmp_path):
    model = tmp_path / "rec.onnx"
    model.write_bytes(b"")
    (tmp_path / "rec.txt").write_text("a\nb\n", encoding="utf-8")
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(model))

    reader = ocr.RapidOCR()
    assert reader.rec_model == str(model)
    assert _Engine.calls == [{"rec_model_path": str(model), "rec_keys_path": str(tmp_path / "rec.txt")}]


def test_model_without_a_character_list_file(engine, monkeypatch, tmp_path):
    model = tmp_path / "rec.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(model))

    ocr.RapidOCR()
    assert _Engine.calls == [{"rec_model_path": str(model)}]


def test_missing_model_falls_back_rather_than_losing_ocr(engine, monkeypatch, tmp_path):
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(tmp_path / "gone.onnx"))
    reader = ocr.RapidOCR()
    assert reader.available
    assert reader.rec_model == "packaged"


def test_engine_without_the_keyword_falls_back(engine, monkeypatch, tmp_path):
    engine.RapidOCR = _OldEngine
    _OldEngine.calls = []
    model = tmp_path / "rec.onnx"
    model.write_bytes(b"")
    monkeypatch.setenv("RAPIDOCR_REC_MODEL", str(model))

    reader = ocr.RapidOCR()
    assert reader.available
    assert reader.rec_model == "packaged"
    assert _OldEngine.calls == [{}]
