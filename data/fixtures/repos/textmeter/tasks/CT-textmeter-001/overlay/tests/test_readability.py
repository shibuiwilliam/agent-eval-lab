import pytest
from textmeter import *  # noqa: F403


def test_avg_sentence_length_009():
    assert avg_sentence_length('吾輩は猫である。名前はまだ無い。') == 1.0


def test_avg_sentence_length_012():
    assert avg_sentence_length('a golden problem crossed an bridge. the old castle watched the village. the tiny journey followed the railway.') == 6.0


def test_avg_sentence_length_013():
    assert avg_sentence_length('the narrow library counted a forest. the bright compass counted an garden. a tiny picture carried a picture.') == 6.0


def test_type_token_ratio_003():
    assert type_token_ratio('one two three four') == 1.0


def test_type_token_ratio_011():
    assert type_token_ratio('x y z', window=1) == 1.0


def test_long_word_ratio_002():
    assert long_word_ratio('seven7x chars') == 0.5


def test_long_word_ratio_003():
    assert long_word_ratio('tiny cat sat') == 0.0


def test_long_word_ratio_007():
    assert long_word_ratio('') == 0.0


def test_long_word_ratio_008():
    assert long_word_ratio('garden window morning teacher village kitchen', min_len=7) == 0.6667


def test_score_003():
    assert score('Dr. Brown measured it. Mr. Green counted 12 books!') == 17.89


def test_score_005():
    assert score('   ') == 0.0


def test_score_006():
    assert score('a a a a a a a a a a a a') == 33.17
