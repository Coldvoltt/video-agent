import pytest

from editor import edits


def test_merge_and_subtract():
    assert edits.merge_ranges([(5, 6), (1, 2), (1.5, 3)]) == [(1, 3), (5, 6)]
    assert edits.subtract_ranges((0, 10), [(2, 3), (2.5, 4), (9, 12)]) == [(0, 2), (4, 9)]
    assert edits.subtract_ranges((0, 10), []) == [(0, 10)]
    assert edits.subtract_ranges((0, 10), [(-1, 11)]) == []
    assert edits.subtract_ranges((5, 8), [(0, 1), (20, 21)]) == [(5, 8)]


def test_filler_ranges_normalises_words():
    words = [{"word": " Um,", "start": 1, "end": 1.3}, {"word": "hello", "start": 1.3, "end": 1.8},
             {"word": "UH...", "start": 2, "end": 2.2}, {"word": "umbrella", "start": 3, "end": 3.5}]
    assert edits.filler_ranges(words) == [(1, 1.3), (2, 2.2)]


def test_tighten_keeps_breathing_room_and_drops_slivers():
    clips = [{"id": "c1", "asset_id": "a", "in": 0, "out": 10, "volume": 0.8},
             {"id": "c2", "asset_id": "b", "in": 0, "out": 5}]
    silences = {"a": [(2.0, 4.0), (9.85, 10.0)]}      # second is too short once padded
    fillers = {"a": [(6.0, 6.4)]}
    new, removed = edits.tighten_clips(clips, silences, fillers, padding=0.15)
    a_pieces = [(c["in"], c["out"]) for c in new if c["asset_id"] == "a"]
    assert a_pieces == [(0, 2.15), (3.85, 6.0), (6.4, 10)]
    assert all(c["volume"] == 0.8 for c in new if c["asset_id"] == "a")   # clip props carried over
    assert [c["id"] for c in new] == ["c1-1", "c1-2", "c1-3", "c2"]
    assert removed == pytest.approx(1.7 + 0.4)


def test_snap_range_to_word_edges():
    words = [{"word": "hello", "start": 1.0, "end": 1.5}, {"word": "there", "start": 1.6, "end": 2.2},
             {"word": "friend", "start": 2.3, "end": 3.0}]
    assert edits.snap_range(1.2, 2.5, words) == (1.0, 3.0)     # both inside words
    assert edits.snap_range(1.55, 2.25, words) == (1.55, 2.25)  # in gaps: untouched
    assert edits.snap_range(1.2, 2.5, []) == (1.2, 2.5)
    long_word = [{"word": "x", "start": 0, "end": 10}]
    assert edits.snap_range(5, 6, long_word, max_shift=1.5) == (5, 6)  # implausible shift ignored


def test_implausibly_long_fillers_are_kept():
    words = [{"word": "um", "start": 10.72, "end": 12.42}, {"word": "trunks", "start": 12.42, "end": 12.44},
             {"word": "uh", "start": 2.78, "end": 3.08}]
    assert edits.filler_ranges(words) == [(2.78, 3.08)]


def test_spoken_text_uses_word_midpoints():
    t = {"segments": [{"start": 0, "end": 4, "text": "one two three four"}],
         "words": [{"word": " one", "start": 0, "end": 1}, {"word": " two", "start": 1, "end": 2},
                   {"word": " three", "start": 2, "end": 3}, {"word": " four", "start": 3, "end": 4}]}
    assert edits.spoken_text(t, 0.9, 3.0) == "two three"
    assert edits.spoken_text({"segments": t["segments"]}, 1, 2) == "one two three four"
    assert edits.spoken_text(None, 0, 1) == ""


def test_speech_gaps_from_word_timings():
    words = [{"word": "a", "start": 1.0, "end": 1.4}, {"word": "b", "start": 1.5, "end": 2.0},
             {"word": "c", "start": 3.4, "end": 4.0}]
    assert edits.speech_gaps(words, 0.6, 6.0) == [(0.0, 1.0), (2.0, 3.4), (4.0, 6.0)]
    assert edits.speech_gaps(words, 2.5, 6.0) == []
    assert edits.speech_gaps([], 0.5, 10) == []
