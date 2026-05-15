"""
Unit tests for app.services.transcription.is_valid_transcript.

No external dependencies — purely rule-based logic under test.
"""
import pytest

from app.services.transcription import is_valid_transcript


class TestIsValidTranscript:

    def test_valid_normal_sentence(self):
        assert is_valid_transcript("The mitochondria is the powerhouse of the cell") is True

    def test_too_short_zero_words(self):
        assert is_valid_transcript("") is False

    def test_too_short_one_word(self):
        assert is_valid_transcript("Hello") is False

    def test_too_short_three_words(self):
        assert is_valid_transcript("one two three") is False

    def test_exactly_four_words_passes(self):
        assert is_valid_transcript("one two three four") is True

    def test_hallucination_thanks_for_watching(self):
        assert is_valid_transcript("thanks for watching this video today") is False

    def test_hallucination_subscribe(self):
        assert is_valid_transcript("please subscribe to this channel now") is False

    def test_hallucination_like_and_subscribe(self):
        assert is_valid_transcript("like and subscribe for more content") is False

    def test_hallucination_subtitles_by(self):
        assert is_valid_transcript("subtitles by the team here") is False

    def test_hallucination_phrase_embedded_in_longer_text(self):
        # phrase appears as substring in the lower-cased text
        assert is_valid_transcript("Hello and thanks for watching everyone today") is False

    def test_repetition_loop_single_word_over_half(self):
        # "the" appears 6/9 times = 0.66 > 0.5
        assert is_valid_transcript("the the the the the the cat sat here") is False

    def test_repetition_loop_exactly_half_passes(self):
        # 2/4 = 0.5 — NOT strictly greater, so should pass
        assert is_valid_transcript("cat cat dog bird") is True

    def test_repetition_loop_just_over_half(self):
        # "cat" 3/5 = 0.6 > 0.5
        assert is_valid_transcript("cat cat cat dog bird") is False

    def test_case_insensitive_repetition(self):
        # "Cat" "cat" "CAT" counted as same token lowercase
        assert is_valid_transcript("Cat cat CAT dog bird") is False

    def test_valid_long_text(self):
        text = (
            "Today we are going to learn about the water cycle. "
            "Water evaporates from the surface of oceans and lakes, "
            "rises into the atmosphere, and eventually falls as rain."
        )
        assert is_valid_transcript(text) is True

    def test_hallucination_case_insensitive(self):
        assert is_valid_transcript("Thank You For Watching our lesson") is False
