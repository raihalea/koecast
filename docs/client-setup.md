# koecast クライアント — 手元実機 (Mac/Windows) セットアップ

`client/src-tauri/` 配下の Tauri アプリ本体は Mac/Windows の実機でビルド・実行する。
本ドキュメントはその手順書。詳しい背景・方針は `docs/stage6-implementation-plan.md`
§1.5 を参照。

> **状態**: 段階6-3-a で **Mac 分のみ** を整備中。Windows 分は手元で Windows
> 実機の検証に着手する段階で追記する（章立てだけ Placeholder として残してある）。

---

## 1. 全体像

- 実装本体（コード編集・git commit）は DGX 側の Claude Code セッションで進める。
- Mac/Windows 実機の役割は **(a) `git pull` で最新を取り込み**、**(b) Tauri アプリを
  ビルド・実行**して、**(c) 結果（ログ / 必要ならスクショ）をテキストコピペで Claude に運ぶ**。
- 1つの GitHub リポジトリ (`github.com/raihalea/koecast`) を DGX・Mac・Windows
  の3拠点で共有する。コミットは原則 DGX 側のセッション、手元は読み取り中心。
- Tauri 2.x の Mac/Windows 公式 prerequisites（Xcode CLT / VS Build Tools 等）に
  従う。本書は最小手順だけを示し、Tauri 公式の細部にはリンクで委ねる。

---

## 2. Mac 実機セットアップ

対象: Apple Silicon / Intel いずれも。macOS の最新 2 世代を想定（Sonoma / Sequoia 等）。

### 2.1 前提ツール

#### Xcode Command Line Tools

Tauri は Mac で `clang` / `ld` / システムフレームワークを使う。Xcode 本体ではなく
**Command Line Tools** で足りる。

```sh
xcode-select --install
```

ダイアログが出たら「インストール」を選ぶ。すでに入っていれば
`xcode-select: error: command line tools are already installed` と出るので無視してよい。

確認:

```sh
xcode-select -p   # /Library/Developer/CommandLineTools などが返る
clang --version
```

#### Rust toolchain (stable)

`rustup` で stable を入れる。リポジトリの `client/protocol/` と `client/src-tauri/`
は `rust-version = "1.77"` 以上を要求する。

```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# シェルを開き直すか:
source "$HOME/.cargo/env"

rustup toolchain install stable
rustup default stable
rustc --version   # 1.77.0 以上であること
```

#### Node.js

リポジトリの DGX 側 `devenv.nix` は Node 24 LTS を採用している。手元 Mac でも
**Node 24 LTS 系**に揃える。`nvm` / `fnm` / `volta` 等のバージョンマネージャ経由が
無難。

```sh
# 例: fnm を使う場合
brew install fnm
eval "$(fnm env --shell zsh)"
fnm install 24
fnm use 24
node --version   # v24.x
npm --version
```

`brew install node@24` でも可。

#### just（任意）

`justfile` 経由のタスク (`just test-shared` 等) は基本 DGX 側で叩くが、手元でも
ちょっと回したい場面のために入れておくと便利。

```sh
brew install just
just --version
```

#### (参考) Tauri 公式手順

詳細・最新差分は Tauri 公式 prerequisites を参照する:
<https://v2.tauri.app/start/prerequisites/>

### 2.2 リポジトリ取得・更新

clone 場所はどこでもよいが、DGX 側のセッションと運用を揃えるなら `ghq` 配下推奨。

```sh
# 初回 clone
git clone https://github.com/raihalea/koecast.git
cd koecast

# 以降の作業日: pull で最新を取り込む
git pull --ff-only
```

DGX 側で commit / push されたら、手元 Mac で `git pull --ff-only` してから次の作業に
入る。手元側で commit はしないのが原則（Claude Code を手元で動かして commit する
運用に切り替えた場合のみ例外）。

### 2.3 Tauri アプリの起動

段階6-3-a の動作確認はここまで（空ウィンドウが立てば成功）。

```sh
cd client
npm install            # 初回 + package.json 変更時
npm run tauri dev      # 開発用ビルド + 起動 (vite dev server + Tauri ウィンドウ)
```

初回ビルドは Rust 依存のコンパイルが走るため数分〜十数分かかる。2 回目以降は数秒〜
十数秒。`npm run tauri dev` は vite dev server (`http://localhost:5173`) と Tauri
ウィンドウを並行起動する。

ターミナルに `Compiling koecast-client v0.1.0` などが出たあと、空の Tauri ウィンドウ
（タイトル: `koecast`）が立てば段階6-3-a は OK。

終了: ターミナルで `Ctrl-C` を押すと dev サーバごと止まる。

### 2.4 macOS の権限設定（**後の小段階で必要になる**）

段階6-3-a（空ウィンドウ起動）では不要だが、後段で次の権限プロンプトが順次出る。
**プロンプトが出たら都度「許可」する**。事前に開いておくと早い:

`システム設定 → プライバシーとセキュリティ`

| 段階 | 機能 | 設定項目 |
|---|---|---|
| **6-3-c** | マイク入力 (`audio.rs`) | **マイク** に koecast を追加・許可 |
| **6-3-c** | グローバルホットキー (`hotkey.rs`) | **入力監視 (Input Monitoring)** に koecast を追加・許可 |
| **6-3-e** | 別アプリへの貼り付け (`inject.rs`) | **アクセシビリティ** に koecast を追加・許可 |

`npm run tauri dev` で立ち上がる開発ビルドは、署名なしのため初回起動時に
Gatekeeper の確認が出ることがある。出たら「開く」を選ぶ。

権限は **アプリのバイナリパスごと** に記録される。`npm run tauri dev` のビルド成果物
（`client/src-tauri/target/debug/koecast` など）と、後で `npm run tauri build` で
作る配布版バイナリは別物として扱われる点に注意（再付与が必要）。

### 2.5 結果の運搬

動作確認の結果は **テキストコピペを基本** として Claude (DGX 側セッション) に運ぶ:

- `npm run tauri dev` のターミナル出力（特にエラー / `Compiling ...` の最後の方）
- 起動後のアプリログ（実装段階で `tracing` 等を入れる予定。今は標準出力）
- 視覚確認が必要な場合 (空ウィンドウの存在 / overlay 表示 / 注入結果) のみ
  スクショを 1〜2 枚添える

---

## 3. Windows 実機セットアップ

> **未記述（後回し）。** 段階6-3-a 時点では Mac 分のみ整備し、Windows 分は
> **Mac 側の手順がひととおり安定してから、Windows 実機での検証に着手する
> タイミングで本セクションを埋める**運用。着手時期は Raiha が判断する
> （`docs/stage6-implementation-plan.md` §1.5「どちらの OS から先に着手するかは
> Raiha が決定して指示する」に従う）。
>
> 想定内容:
> - Visual Studio Build Tools (C++) + WebView2 Runtime のインストール
> - Rust stable / Node 24 LTS の導入
> - `git clone` / `git pull` の運用（Mac と同じ）
> - `cd client && npm install && npm run tauri dev`
> - 権限/UAC まわり (SmartScreen 初回バイパス、フォアグラウンド制御 等)

---

## 4. トラブルシューティング

### `xcrun: error: invalid active developer path`

Xcode CLT が消えている。`xcode-select --install` で再導入。

### `error: linker 'cc' not found`

CLT が入っていない / パスが通っていない。`xcode-select -p` で確認、必要なら
`sudo xcode-select --switch /Library/Developer/CommandLineTools`。

### `npm install` が `gyp` でこける

Node のメジャーバージョン不一致が多い。`node -v` で 24 系か確認、ずれていれば
`fnm use 24` などで揃える。

### Tauri ウィンドウが出ない / vite dev server に繋がらない

- `client/tauri.conf.json` の `build.devUrl` は `http://localhost:5173`。
- 別プロセスが 5173 を使っていないか確認 (`lsof -i :5173`)。
- `npm run dev` (vite のみ) を別ターミナルで先に起動して、`http://localhost:5173`
  がブラウザで開けるか単体確認。
