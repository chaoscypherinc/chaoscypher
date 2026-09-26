# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Audio / video loaders keep Whisper's segment times.

The loaders used to join the transcript and throw the timestamps away, so a
citation into a recording could never say *when*. They now emit a
``location_index`` mapping each segment's char range to its media time span
(the same index shape the PDF loader uses for pages), which the chunker turns
into chunk ``start_time`` / ``end_time``. ffmpeg and faster-whisper are
stubbed; nothing here touches a real model or binary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.loaders.audio_loader import AudioLoader
from chaoscypher_core.services.sources.loaders.video_loader import VideoLoader
from chaoscypher_core.utils.chunk import build_transcript_location_index


_SEGMENTS = [
    SimpleNamespace(text=" Welcome to the show. ", start=0.0, end=2.5),
    SimpleNamespace(text="Today we talk about queues.", start=2.5, end=6.0),
    SimpleNamespace(text="Valkey replaced Redis last spring.", start=6.0, end=9.75),
]


class _FakeWhisperModel:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def transcribe(self, _path: str) -> tuple[list[SimpleNamespace], SimpleNamespace]:
        return list(_SEGMENTS), SimpleNamespace(language="en", duration=9.75)


@pytest.fixture(autouse=True)
def _stub_whisper_and_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeWhisperModel)
    )
    AudioLoader._model = None
    VideoLoader._model = None
    ok = MagicMock(returncode=0, stderr="")
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.audio_loader.subprocess.run", lambda *a, **k: ok
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.video_loader.subprocess.run", lambda *a, **k: ok
    )


@pytest.mark.parametrize("loader_cls", [AudioLoader, VideoLoader])
def test_loader_emits_a_transcript_location_index(loader_cls: type, tmp_path: Path) -> None:
    media = tmp_path / ("episode.mp3" if loader_cls is AudioLoader else "episode.mp4")
    media.write_bytes(b"\x00")

    docs = loader_cls().load_document(str(media))

    assert len(docs) == 1
    text = docs[0]["content"]
    assert (
        text
        == "Welcome to the show. Today we talk about queues. Valkey replaced Redis last spring."
    )
    index = docs[0]["metadata"]["location_index"]
    assert [(b["start_time"], b["end_time"]) for b in index] == [
        (0.0, 2.5),
        (2.5, 6.0),
        (6.0, 9.75),
    ]
    # Each boundary's char range slices the joined text back to its segment.
    assert [text[b["start_char"] : b["end_char"]] for b in index] == [
        "Welcome to the show.",
        "Today we talk about queues.",
        "Valkey replaced Redis last spring.",
    ]
    assert docs[0]["metadata"]["duration"] == 9.8
    assert docs[0]["metadata"]["segment_count"] == 3


def test_build_transcript_location_index_shape() -> None:
    index = build_transcript_location_index([("ab", 0.0, 1.0), ("cde", 1.0, 3.5)])
    assert index == [
        {
            "start_char": 0,
            "end_char": 2,
            "page_number": None,
            "section": None,
            "start_time": 0.0,
            "end_time": 1.0,
        },
        {
            "start_char": 3,
            "end_char": 6,
            "page_number": None,
            "section": None,
            "start_time": 1.0,
            "end_time": 3.5,
        },
    ]


def test_build_transcript_location_index_empty() -> None:
    assert build_transcript_location_index([]) == []


def test_whisper_failure_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "broken.wav"
    media.write_bytes(b"\x00")
    with (
        patch.object(_FakeWhisperModel, "transcribe", side_effect=RuntimeError("no audio")),
        pytest.raises(RuntimeError),
    ):
        AudioLoader().load_document(str(media))
