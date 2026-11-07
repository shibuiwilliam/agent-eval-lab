"""search_code の正規表現を実行するワーカー（別プロセス。`python -I search_worker.py` で起動する）。

エージェントが書いた正規表現は、破滅的バックトラック（`(a+)+$` など）でいくらでも止まれる。
ホストのハーネスを止めないよう、親（env/repo_tools.py）が壁時計のタイムアウトで kill できる別プロセスに隔離する。

ワーカーはファイルシステムに触れない。標準入力の JSON（pattern と、親が読んだファイルの相対パスと内容）だけを使い、
標準出力に JSON を 1 つ書く。標準ライブラリの json・re・sys だけを import する（agenteval を import しない）。

入力: {"pattern", "files": [[path, text], ...], "max_matches", "text_chars", "line_chars"}
出力: {"matches": [{"path", "line", "text"}, ...], "truncated": bool}
"""

import json
import re
import sys


def main() -> int:
    request = json.load(sys.stdin)
    rx = re.compile(request["pattern"])
    max_matches: int = request["max_matches"]
    text_chars: int = request["text_chars"]
    line_chars: int = request["line_chars"]
    matches: list[dict[str, object]] = []
    truncated = False
    for path, text in request["files"]:
        for lineno, line in enumerate(text.split("\n"), 1):
            if rx.search(line[:line_chars]):
                if len(matches) >= max_matches:
                    truncated = True
                    break
                matches.append({"path": path, "line": lineno, "text": line[:text_chars]})
        if truncated:
            break
    json.dump({"matches": matches, "truncated": truncated}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
