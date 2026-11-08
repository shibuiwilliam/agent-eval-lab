"""textmeter: 文字列の統計（正規化・分割・頻度・読みやすさ）。"""

from textmeter.freq import hapax, top_k, word_freq
from textmeter.ngrams import char_ngrams, ngrams
from textmeter.normalize import collapse_ws, normalize
from textmeter.readability import avg_sentence_length, long_word_ratio, score, type_token_ratio
from textmeter.summary import profile
from textmeter.tokenize import sentences, words

__all__ = [
    "avg_sentence_length",
    "char_ngrams",
    "collapse_ws",
    "hapax",
    "long_word_ratio",
    "ngrams",
    "normalize",
    "profile",
    "score",
    "sentences",
    "top_k",
    "type_token_ratio",
    "word_freq",
    "words",
]
