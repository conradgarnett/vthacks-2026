"""The recognition model RapidOCR reads with, fetched once.

RapidOCR ships PaddleOCR's PP-OCRv4 Chinese recognizer. It reads Latin
letters but was trained on text without spaces, and on English it drops
them ("TAKE1CAPSULEEVERY8HOURS", "CarParkLevel3") and garbles small print.
PP-OCRv5's recognizer reads Chinese, English and Japanese in one model and
keeps the spaces. Measured on this repo's corpora (RapidOCR, Linux CPU,
2026-09-29): allergen statements found 7 -> 20 of 42, prescription strength
7 -> 20 and 14 -> 22 of 40, invented text 0 everywhere, signage unchanged.

PaddlePaddle publishes it as ONNX with its character list in a YAML file
beside it; RapidOCR wants that list as plain text next to the model
(`ocr.RapidOCR` looks for `<model>.txt`). Both files are pinned to one
revision and the model is checked against its hash, so a fresh clone gets
exactly the model those numbers were measured on.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
REC_MODEL = Path("weights") / "ocr" / "PP-OCRv5_mobile_rec.onnx"

_REPO = "https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec_onnx/resolve"
_REVISION = "ed152b8b495f84de93cda5709d768548a9127622"
_MODEL_SHA256 = "da72dc72ca4dc220df0dfde68c1dedc31c58d3e76a25871122e5056227d50092"
_KEYS_SHA256 = "d1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b"


def rec_model_path() -> Path:
    return ROOT / REC_MODEL


def rec_model_ready() -> bool:
    model = rec_model_path()
    return model.exists() and model.with_suffix(".txt").exists()


def fetch_rec_model(timeout_s: float = 120.0) -> Path:
    """Download the model and write its character list. Idempotent.

    Raises on a failed download or a hash mismatch; nothing is left half
    written, so the reader keeps using the packaged model until this works.
    """
    model = rec_model_path()
    keys = model.with_suffix(".txt")
    if model.exists() and keys.exists():
        return model
    model.parent.mkdir(parents=True, exist_ok=True)

    onnx = _download(f"{_REPO}/{_REVISION}/inference.onnx", timeout_s)
    _check(onnx, _MODEL_SHA256, "model")
    listing = _characters(_download(f"{_REPO}/{_REVISION}/inference.yml", timeout_s))
    _check(listing, _KEYS_SHA256, "character list")

    _write_atomic(keys, listing)
    _write_atomic(model, onnx)
    log.info("OCR recognition model ready at %s", model)
    return model


def _download(url: str, timeout_s: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return response.read()


def _check(data: bytes, expected: str, what: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"OCR {what} hash mismatch: got {actual}, expected {expected}")


def _characters(inference_yml: bytes) -> bytes:
    """The recognizer's character list, one per line, from Paddle's YAML."""
    import yaml  # installed with ultralytics

    config = yaml.safe_load(inference_yml.decode("utf-8"))
    characters = config["PostProcess"]["character_dict"]
    return ("\n".join(str(c) for c in characters) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_rec_model())
