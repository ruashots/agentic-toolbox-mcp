"""Whisper models come from one pinned commit and are kept only if their sha256 matches."""
import hashlib
import importlib.util
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "whisper_models", Path(__file__).resolve().parent.parent / "whisper" / "models.py")
models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(models)

GOOD = b"the real model bytes"


def test_every_model_is_pinned_to_a_commit_and_a_checksum():
    assert re.fullmatch(r"[0-9a-f]{40}", models.REVISION)
    for name, (filename, sha256) in models.MODELS.items():
        assert filename == f"ggml-{name}.bin"
        assert re.fullmatch(r"[0-9a-f]{64}", sha256)
        assert f"/{models.REVISION}/" in models.url(name)


@pytest.fixture
def served(monkeypatch):
    """A local stand-in for Hugging Face that serves `body` for any path."""
    state = {"body": GOOD}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(models, "REPO", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setitem(models.MODELS, "test", ("ggml-test.bin", hashlib.sha256(GOOD).hexdigest()))
    yield state
    httpd.shutdown()


def test_a_download_that_matches_is_kept(served, tmp_path):
    dest = tmp_path / "ggml-test.bin"
    models.download("test", dest)
    assert dest.read_bytes() == GOOD
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ggml-test.bin"]


def test_a_download_that_does_not_match_is_deleted(served, tmp_path):
    served["body"] = b"something else"
    dest = tmp_path / "ggml-test.bin"
    with pytest.raises(models.ChecksumMismatch, match="not the pinned"):
        models.download("test", dest)
    assert list(tmp_path.iterdir()) == []
