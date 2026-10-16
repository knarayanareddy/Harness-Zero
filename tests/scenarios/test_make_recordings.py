"""`make_recordings.py` and the committed synthetic corpus are one thing (WS-01).

Three properties, all cheap to hold and expensive to lose:

* regenerating writes the exact bytes that are in git — so a fixture can be reviewed by reading the generator,
  and a "can't reproduce it locally" report is a bug in the generator, not in the reviewer;
* every scenario marked `recorded_from: synthetic` has an entry, so a hand-written `provider.jsonl` cannot
  become the only copy of itself;
* nothing in the generator belongs to a scenario recorded from a real provider, because `python
  make_recordings.py` overwrites what it lists.
"""
from __future__ import annotations

from pathlib import Path

import make_recordings
import yaml
from _lib.codec import load_streams, write_streams

HERE = Path(make_recordings.__file__).resolve().parent


def _recorded_from(directory: Path) -> str:
    data = yaml.safe_load((directory / "scenario.yaml").read_text(encoding="utf-8")) or {}
    return str(data.get("recorded_from") or "")


def _scenario_dirs() -> list[Path]:
    return sorted(d for d in HERE.iterdir() if d.is_dir() and (d / "scenario.yaml").exists())


def test_regenerating_the_corpus_writes_the_committed_bytes(tmp_path: Path):
    written = 0
    for name, streams in make_recordings.RECORDINGS.items():
        committed = HERE / name / "provider.jsonl"
        if not committed.exists():
            continue  # a scenario mid-authoring has no fixture yet; the next check is the one that cares
        out = tmp_path / name / "provider.jsonl"
        write_streams(out, streams)
        assert out.read_bytes() == committed.read_bytes(), f"{name}: generator output differs from the fixture"
        written += 1
    assert written == len(make_recordings.RECORDINGS), "RECORDINGS names scenario dirs that no longer exist"


def test_every_synthetic_scenario_can_be_regenerated():
    missing = [
        d.name
        for d in _scenario_dirs()
        if _recorded_from(d) == "synthetic" and d.name not in make_recordings.RECORDINGS
    ]
    assert not missing, f"no generator entry for synthetic scenario(s): {missing}"


def test_the_generator_never_touches_a_real_recording():
    """A stream recorded from `anthropic` is evidence; `make_recordings.py` would replace it with a guess."""
    stale = [
        name
        for name in make_recordings.RECORDINGS
        if (HERE / name / "scenario.yaml").exists() and _recorded_from(HERE / name) not in ("", "synthetic")
    ]
    assert not stale, f"generator entries for recorded scenario(s): {stale} — delete the entry, keep the fixture"


def test_the_codec_is_a_fixed_point_for_every_fixture(tmp_path: Path):
    """`load_streams` → `write_streams` returns each committed file unchanged, recorded or synthetic.

    That is what lets a real recording and a hand-written one sit in the same directory with the same rules:
    there is no normalising step between "what the kernel consumed" and "what is in git".
    """
    for d in _scenario_dirs():
        fixture = d / "provider.jsonl"
        if not fixture.exists():
            continue
        out = tmp_path / f"{d.name}.jsonl"
        write_streams(out, load_streams(fixture))
        assert out.read_bytes() == fixture.read_bytes(), f"{d.name}: codec is not a fixed point"
