"""textmeter のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。

各ケースは「公開関数の 1 回の呼び出し」の式。期待値は正しいコードで計算して凍結する。
テキストは文字列リテラルで書く。語彙の組合せで作るケースは seed 固定の乱数で決める。
"""

from __future__ import annotations

from typing import Any

import numpy as np

PACKAGE = "textmeter"
DESCRIPTION = "文字列の統計（正規化・分割・頻度・読みやすさ）"
HEADER = ["import pytest"]

TOPICS = {
    "normalize": "文字列の正規化",
    "collapse_ws": "空白の整理",
    "words": "語への分割",
    "sentences": "文への分割",
    "ngrams": "語の n-gram",
    "char_ngrams": "文字の n-gram",
    "word_freq": "語の頻度",
    "top_k": "頻度の上位語",
    "hapax": "1 回だけ現れる語",
    "avg_sentence_length": "平均文長",
    "type_token_ratio": "異なり語の割合",
    "long_word_ratio": "長い語の割合",
    "score": "読みにくさの得点",
    "profile": "文章の要約",
}

# 紛らわしい名前・似た処理の組（変異の「囮あり」の判定に使う。キーは module.qualname）
DECOYS = [
    ["tokenize._split_words", "tokenize.words"],
    ["freq._most_common", "freq.top_k"],
    ["readability.type_token_ratio", "readability.long_word_ratio"],
    ["freq._ratio", "readability.long_word_ratio"],
]

README_REFS = {
    "normalize": "`normalize(text, casefold=True, nfkc=True)`: `nfkc` が真なら NFKC 正規化",
    "collapse_ws": "連続する空白（改行・タブを含む）を半角スペース 1 つにし、前後の空白を取り除く",
    "words": "`keep_numbers=False` のときは数字だけの語を捨てる",
    "sentences": "記号が連続するとき（`?!`、`...`）は最後の記号で切る",
    "ngrams": "`tokens` が `n` 個に満たなければ空リスト",
    "char_ngrams": "`char_ngrams` は文字そのものを扱うので、大文字小文字を変えない（正規化しない）",
    "word_freq": "丁度 `min_len` 文字の語は数える",
    "top_k": "回数の降順、同数は語の昇順。`k` が 0 以下なら空リスト",
    "hapax": "回数が `max_count` 以下の語を昇順で返す",
    "avg_sentence_length": "1 文あたりの語数の平均。小数 2 桁に丸める。文が無ければ `0.0`",
    "type_token_ratio": "語数が `window` に満たなければ語全体を 1 つの窓とする",
    "long_word_ratio": "`min_len` 文字以上（丁度 `min_len` 文字を含む）の語の割合（小数 4 桁）",
    "score": "平均文長 × 2 ＋ 長い語の割合 × 40",
    "profile": "`hapax`（1 回だけ現れる語の数）",
}

EDGE = {
    "kind": "ambiguous",
    "tag": "edge_task",
    "readme_ref": "`char_ngrams` は文字そのものを扱うので、大文字小文字を変えない（正規化しない）",
    "prompt": (
        "文字列まわりの機能で「大文字小文字を区別しないでほしい」という要望が来ています。"
        "文字の n-gram が `Ab` と `aB` を別物として数えてしまうのが不便、という話のようですが、"
        "どの関数が対象なのか、依頼元は詳しく説明してくれませんでした。"
        "例えば `char_ngrams('AbAb', 2)` が `['Ab', 'bA', 'Ab']` を返すのを、大文字小文字を無視する形に直してください。"
    ),
}


def _c(
    target: str,
    module: str,
    call: str,
    *,
    view: str | None = None,
    setup: tuple[str, ...] = (),
    raises: str | None = None,
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "target": target,
        "module": module,
        "call": call,
        "view": view,
        "setup": list(setup),
        "raises": raises,
        "tags": list(tags),
    }


_NOUNS = [
    "garden", "window", "morning", "teacher", "village", "kitchen", "railway", "library",
    "mountain", "science", "picture", "evening", "weather", "problem", "blanket", "journey",
    "harvest", "lantern", "compass", "orchard", "bridge", "castle", "river", "forest",
]
_VERBS = [
    "watched", "carried", "painted", "followed", "opened", "forgot", "measured", "gathered",
    "crossed", "repaired", "noticed", "borrowed", "counted", "carved", "wrote", "found",
]
_SHORT = ["the", "a", "an", "of", "to", "in", "and", "was", "is", "it", "we", "he", "she"]
_ADJ = ["quiet", "old", "bright", "heavy", "narrow", "golden", "broken", "tiny", "long", "cold"]


def _sentence(rng: np.random.Generator, extra: str = "") -> str:
    parts = [
        str(rng.choice(_SHORT[:3])),
        str(rng.choice(_ADJ)),
        str(rng.choice(_NOUNS)),
        str(rng.choice(_VERBS)),
        str(rng.choice(_SHORT[:3])),
        str(rng.choice(_NOUNS)),
    ]
    if extra:
        parts.insert(3, extra)
    return " ".join(parts) + "."


def _text(rng: np.random.Generator, n: int, extra: str = "") -> str:
    return " ".join(_sentence(rng, extra if i == 0 else "") for i in range(n))


def _lit(s: str) -> str:
    return repr(s)


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    cs: list[dict[str, Any]] = []
    add = cs.append

    # --- normalize ---
    for s, kw, tag in [
        ("Hello   World", "", "default"),
        ("  Tabs\tand\nNewlines  ", "", "default"),
        ("ＦＵＬＬ　Ｗｉｄｔｈ １２３", "", "default"),
        ("ﾊﾝｶｸ ｶﾅ テスト", "", "default"),
        ("Straße ŞİMŞEK", "", "default"),
        ("MiXeD CaSe Words", ", casefold=False", ""),
        ("ＦＵＬＬ Ｗｉｄｔｈ", ", nfkc=False", ""),
        ("ＦＵＬＬ Ｗｉｄｔｈ", ", casefold=False", ""),
        ("Ｆｕｌｌ Ｗｉｄｔｈ Ａ", ", casefold=False, nfkc=False", ""),
        ("ﬁnal ﬂight ① ㈱", "", ""),
        ("already plain text", "", "default"),
        ("", "", "empty"),
    ]:
        add(_c("normalize", "normalize", f"normalize({_lit(s)}{kw})", tags=(tag,) if tag else ()))
    # --- collapse_ws ---
    for s, tag in [
        ("a  b   c", ""),
        ("\n\t lead and trail \r\n", ""),
        ("single", ""),
        ("line1\nline2\n\nline3", ""),
        ("　全角　空白　", "none"),
        ("   ", "empty"),
        ("", "empty"),
    ]:
        add(_c("collapse_ws", "normalize", f"collapse_ws({_lit(s)})", tags=(tag,) if tag else ()))
    # --- words ---
    for s, kw, tag in [
        ("Hello, World! It's a well-known fact.", "", "default"),
        ("-leading and trailing- 'quoted' words'", "", "boundary"),
        ("Order 66 ships on 12 May, item ab12 x9", "", "default"),
        ("Order 66 ships on 12 May, item ab12 x9", ", keep_numbers=False", ""),
        ("room 101 and 7 rooms, 2nd floor 3rd door", ", keep_numbers=False", ""),
        ("2024 2025 2026", ", keep_numbers=False", "empty"),
        ("The QUICK Brown FOX", ", casefold=False", ""),
        ("The QUICK Brown FOX", "", "default"),
        ("ＡＢＣ１２ ｄｅｆ", "", ""),
        ("吾輩は猫である。名前はまだ無い。", "", ""),
        ("東京タワーとスカイツリー、どちらも高い", "", ""),
        ("naïve café résumé", "", ""),
        ("one,two;three|four", "", ""),
        ("   ", "", "empty"),
        ("''' --- '''", "", "boundary"),
        ("don't stop-me now", "", ""),
        ("Zürich 8001 und Köln 50667", "", ""),
        ("MiXeD CaSe 42 Items", ", keep_numbers=False", ""),
    ]:
        add(_c("words", "tokenize", f"words({_lit(s)}{kw})", tags=(tag,) if tag else ()))
    # --- sentences ---
    for s, tag in [
        ("It rained all night. The river rose! Nobody slept?", ""),
        ("Dr. Watson met Mr. Holmes. They left.", ""),
        ("Pi is 3.14 and e is 2.718. Both are constants.", ""),
        ("Really?! Yes... I think so.", ""),
        ("no terminator at all", ""),
        ("Ends with space after dot. ", ""),
        ("Fruits, e.g. apples, are sold here. Prof. Lee buys some etc. every week.", ""),
        ("COMPARE it vs. THAT today. Done.", ""),
        ("吾輩は猫である。名前はまだ無い！どこで生れたか？", ""),
        ("Wait.. what? Hmm...", ""),
        ("   ", "empty"),
        ("a.b.c is a dotted name. Next one.", ""),
        ("Mrs. Brown said hi.Then she left. Ms. Green stayed.", ""),
        ("Hello world.", ""),
    ]:
        add(_c("sentences", "tokenize", f"sentences({_lit(s)})", tags=(tag,) if tag else ()))
    for _ in range(4):
        add(_c("sentences", "tokenize", f"sentences({_lit(_text(rng, 3))})"))
    add(_c("sentences", "tokenize", "sentences('Mr. Brown and Dr. Green went. Prof. Lee stayed behind.')"))
    add(_c("sentences", "tokenize", "sentences('We read etc. vs. i.e. lists today. Then slept.')"))
    # --- ngrams ---
    for toks, n, tag in [
        (["a", "b", "c", "d"], 2, ""),
        (["red", "green", "blue", "pink", "gray"], 3, ""),
        (["x", "y"], 3, "boundary"),
        (["x", "y", "z"], 3, "boundary"),
        (["solo"], 1, ""),
        ([], 2, "empty"),
        (["to", "be", "or", "not", "to", "be"], 2, ""),
        (["one", "two", "three"], 1, ""),
    ]:
        add(_c("ngrams", "ngrams", f"ngrams({toks!r}, {n})", tags=(tag,) if tag else ()))
    add(_c("ngrams", "ngrams", "ngrams(['a', 'b'], 0)", raises="ValueError", tags=("boundary",)))
    # --- char_ngrams ---
    for s, n, pad, tag in [
        ("abcd", 2, None, ""),
        ("AbAb", 2, None, "edge"),
        ("Hello World", 3, None, ""),
        ("ab", 3, None, "boundary"),
        ("abc", 3, None, "boundary"),
        ("", 2, None, "empty"),
        ("cat dog", 3, "'_'", ""),
        ("Cat DOG", 2, "'_'", ""),
        ("Ab12 x", 3, "'#'", ""),
        ("it's well-made", 2, "'^'", ""),
        ("日本語 テキスト", 2, "'＊'", ""),
        ("a", 1, "'_'", ""),
        ("Hi!", 3, "'_'", ""),
        ("x, y", 2, "'_'", ""),
        ("room 42 and 7", 4, "'.'", ""),
    ]:
        pad_s = "" if pad is None else f", {pad}"
        tags = tuple(t for t in (tag,) if t)
        add(_c("char_ngrams", "ngrams", f"char_ngrams({_lit(s)}, {n}{pad_s})", tags=tags))
    add(_c("char_ngrams", "ngrams", "char_ngrams('abc', 0)", raises="ValueError", tags=("boundary",)))
    # --- word_freq ---
    for s, kw, tag in [
        ("the cat and the hat and the bat", "", "default"),
        ("The the THE cat Cat", "", "default"),
        ("The the THE cat Cat", ", casefold=False", ""),
        ("a an the of to in it is", ", min_len=2", "boundary"),
        ("tiny words and giant mountains here", ", min_len=5", "boundary"),
        ("tiny words and giant mountains here", ", min_len=6", ""),
        ("to be or not to be that is the question", ", stopwords=['to', 'be', 'is']", ""),
        ("To BE or Not to Be", ", stopwords=['TO', 'Be']", ""),
        ("To BE or Not to Be", ", stopwords=['TO', 'Be'], casefold=False", ""),
        ("apple banana apple cherry banana apple", ", stopwords=[]", ""),
        ("", "", "empty"),
        ("room 12 room 12 room 7 ab12", "", ""),
        ("Naïve café NAÏVE CAFÉ naïve", "", ""),
        ("ＡＰＰＬＥ apple Apple", "", ""),
        ("犬 猫 犬 鳥 猫 犬", ", min_len=2", ""),
        ("犬 猫 犬 鳥 猫 犬", "", ""),
        ("it's it's its", ", min_len=4", ""),
    ]:
        add(_c("word_freq", "freq", f"word_freq({_lit(s)}{kw})", tags=(tag,) if tag else ()))
    for _ in range(5):
        add(_c("word_freq", "freq", f"word_freq({_lit(_text(rng, 2))})"))
    add(_c("word_freq", "freq", "word_freq('Alpha beta Gamma alpha BETA gamma delta', stopwords=['delta'], min_len=5)"))
    # --- top_k ---
    for freq, k, tag in [
        ({"apple": 4, "pear": 7, "plum": 7, "fig": 2}, 2, "tie"),
        ({"apple": 4, "pear": 7, "plum": 7, "fig": 2}, 10, "boundary"),
        ({"zeta": 3, "alpha": 3, "mu": 3, "beta": 3}, 3, "tie"),
        ({"zeta": 3, "alpha": 3, "mu": 3, "beta": 3}, 4, "tie"),
        ({"solo": 12}, 1, ""),
        ({}, 3, "empty"),
        ({"x1": 5, "x2": 6}, 0, "boundary"),
        ({"x1": 5, "x2": 6}, -2, "boundary"),
        ({"kiwi": 9, "lime": 8, "date": 8, "nut": 8, "oat": 5}, 3, "tie"),
        ({"melon": 15, "grape": 21, "lemon": 15}, 1, ""),
    ]:
        add(_c("top_k", "freq", f"top_k({freq!r}, {k})", tags=(tag,) if tag else ()))
    # --- hapax ---
    for freq, kw, tag in [
        ({"once": 1, "twice": 2, "thrice": 3, "also": 1}, "", "default"),
        ({"b1": 1, "a1": 1, "c1": 1}, "", ""),
        ({"p": 2, "q": 3}, "", "empty"),
        ({"once": 1, "twice": 2, "thrice": 3, "also": 1}, ", max_count=2", "boundary"),
        ({"once": 1, "twice": 2, "thrice": 3, "also": 1}, ", max_count=3", "boundary"),
        ({}, "", "empty"),
        ({"w7": 7, "w8": 8}, ", max_count=0", "boundary"),
    ]:
        add(_c("hapax", "freq", f"hapax({freq!r}{kw})", tags=(tag,) if tag else ()))
    # --- avg_sentence_length ---
    for s, tag in [
        ("One two three. Four five six seven eight.", ""),
        ("Short. A much longer sentence follows here today!", ""),
        ("no punctuation but eight words in this sentence ok", ""),
        ("Dr. Who met Mr. Smith today. They talked a lot.", ""),
        ("Wait... what? Really?! Yes.", ""),
        ("", "empty"),
        ("   ", "empty"),
        ("1 2 3 4. 5 6.", ""),
        ("吾輩は猫である。名前はまだ無い。", ""),
        ("Seven words here make this one. Three more words.", ""),
    ]:
        add(_c("avg_sentence_length", "readability", f"avg_sentence_length({_lit(s)})", tags=(tag,) if tag else ()))
    for _ in range(3):
        add(_c("avg_sentence_length", "readability", f"avg_sentence_length({_lit(_text(rng, 3))})"))
    # --- type_token_ratio ---
    for s, kw, tag in [
        ("a b a b c", "", "default"),
        ("the cat and the hat and the bat", "", "default"),
        ("one two three four", "", ""),
        ("same same same same", "", ""),
        ("The the THE cat Cat", "", ""),
        ("a b a b c", ", window=3", ""),
        ("a b a b c", ", window=5", "boundary"),
        ("a b a b c", ", window=9", "boundary"),
        ("red red blue red green green blue", ", window=4", ""),
        ("red red blue red green green blue", ", window=2", ""),
        ("x y z", ", window=1", "boundary"),
        ("", "", "empty"),
        ("", ", window=3", "empty"),
        ("Room 12 Room 12 Hall 7 Hall", "", ""),
    ]:
        add(_c("type_token_ratio", "readability", f"type_token_ratio({_lit(s)}{kw})", tags=(tag,) if tag else ()))
    add(_c("type_token_ratio", "readability", "type_token_ratio('a b c', window=0)", raises="ValueError", tags=("boundary",)))
    for _ in range(2):
        add(_c("type_token_ratio", "readability", f"type_token_ratio({_lit(_text(rng, 2))})"))
    # --- long_word_ratio ---
    for s, kw, tag in [
        ("reading machine is fine and small", "", "boundary"),
        ("seven7x chars", "", "boundary"),
        ("tiny cat sat", "", ""),
        ("Wonderful Mountains Everywhere", "", ""),
        ("reading machine is fine and small", ", min_len=5", ""),
        ("reading machine is fine and small", ", min_len=4", "boundary"),
        ("", "", "empty"),
        ("garden window morning teacher village kitchen", ", min_len=7", ""),
        ("a bb ccc dddd eeeee", ", min_len=3", "boundary"),
        ("東京タワーは三百三十三メートル", ", min_len=3", ""),
        ("Item 1234567 costs 12", "", ""),
    ]:
        add(_c("long_word_ratio", "readability", f"long_word_ratio({_lit(s)}{kw})", tags=(tag,) if tag else ()))
    for _ in range(3):
        add(_c("long_word_ratio", "readability", f"long_word_ratio({_lit(_text(rng, 2))})"))
    # --- score ---
    for s, tag in [
        ("The cat sat on the mat. The dog ran away.", ""),
        ("Wonderful mountains everywhere surround wandering travellers.", ""),
        ("Dr. Brown measured it. Mr. Green counted 12 books!", ""),
        ("", "empty"),
        ("   ", "empty"),
        ("a a a a a a a a a a a a", ""),
        ("Quick. Short. Tiny.", ""),
        ("Reading machines measure complicated paragraphs sometimes incorrectly.", ""),
        ("ＡＢＣ 12 Test 吾輩は猫である。", ""),
    ]:
        add(_c("score", "readability", f"score({_lit(s)})", tags=(tag,) if tag else ()))
    long_text = " ".join(str(w) for w in rng.choice(_NOUNS, size=58)) + "."
    add(_c("score", "readability", f"score({_lit(long_text)})", tags=("boundary",)))
    long_text2 = " ".join(str(w) for w in rng.choice(_ADJ, size=64)) + "."
    add(_c("score", "readability", f"score({_lit(long_text2)})", tags=("boundary",)))
    for _ in range(2):
        add(_c("score", "readability", f"score({_lit(_text(rng, 3))})"))
    # --- profile ---
    for s, kw, tag in [
        ("The cat sat. The dog ran far away. Mr. Brown laughed!", "", "default"),
        ("Tiny tiny tiny words, tiny things.", ", k=2", ""),
        ("Alpha beta Gamma. Alpha BETA gamma delta! Epsilon?", ", k=3", ""),
        ("", "", "empty"),
        ("Room 12 and room 7, room 12 again.", "", ""),
        ("ＡＢＣ ａｂｃ Ａｂｃ 猫 猫", ", k=1", ""),
        ("Dr. Who found 3.14 miles of etc. cables.", ", k=0", "boundary"),
        ("one two three four five six seven", ", k=10", "boundary"),
        ("Wonderful mountains everywhere surround wandering travellers.", "", ""),
        ("a b c a b a", ", k=2", "tie"),
    ]:
        add(_c("profile", "summary", f"profile({_lit(s)}{kw})", view="list($.values())", tags=(tag,) if tag else ()))
    for _ in range(4):
        add(_c("profile", "summary", f"profile({_lit(_text(rng, 2))})", view="list($.values())"))
    # --- ambiguous 用: README が char_ngrams の大文字小文字を保つと定めていることの確認 ---
    add(_c("char_ngrams", "ngrams", "char_ngrams('AbAb', 2)", tags=("edge_task",)))
    return cs
