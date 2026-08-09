# realtime-inference-benchmark

WebRTC / RTSP の映像配信と Triton 推論を組み合わせた、リアルタイム推論システム向けの性能測定ハーネスです。

このリポジトリ自身は MediaMTX や推論アプリの実装を抱えず、既存リポジトリを固定バージョンで参照し、起動・測定シナリオ・メタデータ収集・結果保存を担当します。

## 対象コンポーネント

- `mediamtx-playground`
  - MediaMTX / WebRTC / RTSP の配信・負荷試験
  - 参照コミット: `7f68a216c846eb8844c0a39bf9cb1545e88feddf`
- `realtime-pose-triton`
  - WebRTC 入力、RTMPose / Triton 推論、推論結果返却
  - `main` の参照コミット: `48be43377e459fb037797f59820a9cb05c47a266`
  - PR #12 で追加された CPU-only SmolVLM サンプルも同コミットに含まれますが、本ハーネスの主要測定対象は既存のリアルタイム姿勢推定経路です。

測定結果の再現性を保つため、submodule はブランチ名ではなくコミット SHA に固定します。元リポジトリを更新した場合は、測定条件を確認したうえで参照コミットを意図的に更新してください。

## 測定対象

基本的な処理経路は以下です。

```text
スマートフォン / 入力動画
        |
        v
     WebRTC
        |
        v
     MediaMTX
        |
        v
   推論パイプライン
        |
        v
      Triton
        |
        v
    推論結果返却
        |
        v
      Browser
```

比較測定では、同一動画を RTSP で入力した場合と相対比較します。

主な測定観点は以下です。

- WebRTC / RTSP の配信性能
- Triton 推論レイテンシ
- 推論 throughput / FPS
- CPU / GPU / メモリ等のリソース利用状況
- 映像送信から推論結果表示までの End-to-End レイテンシ
- WebRTC と RTSP で同一動画を使用した際の相対比較

推論精度については絶対的な正解率を求めず、同一入力に対する相対比較を前提とします。

## セットアップ

```bash
git clone --recurse-submodules https://github.com/ishiishikou/realtime-inference-benchmark.git
cd realtime-inference-benchmark
./scripts/component-versions.sh
```

通常の `git clone` を行った場合は以下を実行します。

```bash
git submodule update --init --recursive
```

## 構成

```text
.
├── components/
│   ├── mediamtx-playground/      # git submodule
│   └── realtime-pose-triton/     # git submodule
├── config/
│   └── benchmark.example.env
├── docs/
│   └── measurement-design.md
├── results/
│   └── .gitkeep
├── scripts/
│   ├── component-versions.sh
│   └── new-run.sh
├── .gitmodules
├── .gitignore
└── README.md
```

## 測定ランの作成

```bash
./scripts/new-run.sh
```

`results/<UTC timestamp>/` に以下を保存します。

- `metadata.env`: 測定時刻と参照コンポーネントの commit SHA
- `metrics.csv`: 測定値投入用の CSV ヘッダ
- `notes.md`: 実行条件・異常・観察事項のメモ

測定結果そのものは環境依存情報や映像を含む可能性があるため、`results/` 配下は `.gitkeep` を除き Git 管理対象外です。

## 公開リポジトリでの注意

このリポジトリには以下をコミットしないでください。

- 実認証情報、API token、secret
- 秘密鍵、証明書
- 実カメラ URL
- 実 IP アドレス、実ホスト名
- TURN / STUN の実認証情報
- 録画ファイル、キャプチャ画像
- 個人環境・社内環境のログ

設定値は `config/benchmark.example.env` をテンプレートとして、実値を `config/benchmark.local.env` に保存してください。`*.local.env` は Git 管理対象外です。

## 方針

- 各コンポーネントの実装責務は元リポジトリに残す
- このリポジトリには測定のオーケストレーションと結果管理だけを置く
- 測定時に使用した各コンポーネントの commit SHA を必ず残す
- 将来的な計測用フックは必要最小限を元リポジトリへ追加する
