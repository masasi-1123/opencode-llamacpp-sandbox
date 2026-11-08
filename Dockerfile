FROM ubuntu:latest

# ユーザーの対話プロンプトを無効化
ENV DEBIAN_FRONTEND=noninteractive

# コアユーティリティ、ビルドツール、Pythonのインストール
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Node.js v20 (LTS) のインストール
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ─── 【重要】グループ名も同時に変更して修正 ───
# 1. 既存の「ubuntu」グループの名称を「openvscode-server」に変更
# 2. 既存の「ubuntu」ユーザーの名称・ホームディレクトリを変更
# 3. パスワードなしのsudo権限を付与
RUN groupmod -n openvscode-server ubuntu && \
    usermod -l openvscode-server -g openvscode-server -d /home/openvscode-server -m ubuntu && \
    echo "openvscode-server ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# ワークスペース用のディレクトリを作成（グループが存在するのでchownが通ります）
RUN mkdir -p /home/workspace && chown -R openvscode-server:openvscode-server /home/workspace

# 【重要】Dockerボリュームのマウント先を事前作成して所有権を設定
# Named Volumeはデフォルトでrootが所有者になるため、先に正しい所有権でディレクトリを作っておく
# Dockerはボリュームの初期化時にイメージ内のディレクトリの所有者情報を引き継ぐ
RUN mkdir -p \
        /home/openvscode-server/.local/share/opencode \
        /home/openvscode-server/.cache/opencode \
        /home/openvscode-server/.config/opencode && \
    chown -R openvscode-server:openvscode-server \
        /home/openvscode-server/.local \
        /home/openvscode-server/.cache \
        /home/openvscode-server/.config

# 作業ディレクトリを設定
WORKDIR /home/workspace

# 一般ユーザーに切り替え
USER openvscode-server

# opencodeのインストール
RUN curl -fsSL https://opencode.ai/install | bash

# PATHの設定（opencode コマンドを直接実行可能にする）
ENV PATH="/home/openvscode-server/.opencode/bin:${PATH}"
RUN echo 'export PATH="/home/openvscode-server/.opencode/bin:$PATH"' >> /home/openvscode-server/.bashrc

# opencodeが動的に使用するnpmパッケージを事前インストール（ProviderInitError対策）
# これにより、起動時のダウンロードを不要にする
RUN mkdir -p /home/openvscode-server/.local/share/opencode && \
    cd /home/openvscode-server/.local/share/opencode && \
    npm init -y && \
    npm install @ai-sdk/openai-compatible @ai-sdk/ollama 2>/dev/null || true

# デフォルトのコマンド (Docker Compose等で上書き可能)
CMD ["/bin/bash"]