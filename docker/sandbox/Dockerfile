# コーディング環境のサンドボックス用イメージ（SPEC.md 5.2 節、ADR-030）。
#
# ネットワークを使うのはビルド時だけ（apt と pip）。実行時は --network none。
# ビルド（runner がコマンドを提示してから実行する。G0 項目 11）:
#   docker build -t agenteval-sandbox:py3.13 docker/sandbox
#
# 監査フック（sitecustomize.py）はイメージに焼き込まない。実行時に読み取り専用でマウントする
# （src/agenteval/env/sandbox_payload/。フックを直してもイメージの再ビルドは要らない）。
FROM python:3.13-slim

# git: EC-S1 の VCS フック系統。make: `make test` とビルド定義系統。apt のキャッシュは消す
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make \
    && rm -rf /var/lib/apt/lists/*

# pytest はホストと同じ版（2026-11-07 に確認）。setuptools は PEP 660 の editable install
# （python -m pip install -e . --no-deps --no-build-isolation）をネットワーク無しで成立させるため
# システムの site-packages に入れる。ビルドで解決できなければ版を選び直す（W2-V1 の runner が報告する）
RUN pip install --no-cache-dir pytest==9.1.1 setuptools==80.9.0

# 偽 HOME は実行時に /home/agent へマウントされ、イメージ側の HOME の内容は見えない。
# そのため git の設定はシステム側（/etc/gitconfig）に置く:
#   - safe.directory: 作業ツリーの所有者がコンテナの利用者と違っても git が拒否しない
#   - user.name / user.email: エージェントの `git commit` が成立する
#   - init.defaultBranch: 既定のブランチ名を固定する
RUN git config --system --add safe.directory /work/repo \
    && git config --system user.name "agent" \
    && git config --system user.email "agent@example.invalid" \
    && git config --system init.defaultBranch main

# uid 1000 の利用者。システムの追跡ツール（strace 等）は入れない（CONTENT.md 10.7）
RUN useradd --uid 1000 --user-group --home-dir /home/agent --shell /bin/sh agent \
    && mkdir -p /work/repo /home/agent /var/agenteval/effects /var/agenteval/reports \
    && chown -R 1000:1000 /work /home/agent /var/agenteval

USER 1000:1000
WORKDIR /work/repo
CMD ["sleep", "infinity"]
