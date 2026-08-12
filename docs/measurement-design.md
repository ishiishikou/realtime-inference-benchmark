# 測定設計

## 目的

スマートフォンまたは固定動画から映像を入力し、MediaMTX と Triton 推論を通して結果が返るまでの性能を再現可能な形で測定します。

## 比較軸

同一動画・同一推論モデル・同一推論設定を使い、入力プロトコルだけを切り替えて比較します。

- WebRTC 入力
- RTSP 入力

推論精度は絶対評価ではなく、同一入力に対する相対差を確認します。

## 時刻ポイント

将来的な End-to-End 計測では以下の時刻ポイントを使います。

```text
T0: ブラウザ / 送信側でフレーム送出
T1: MediaMTX 受信
T2: 推論リクエスト開始
T3: Triton 推論完了
T4: 推論結果送出
T5: ブラウザで推論結果反映
```

このとき主な指標は以下です。

- E2E latency: `T5 - T0`
- media delivery: `T1 - T0`
- inference pipeline: `T3 - T2`
- result delivery: `T5 - T3`

現時点で全ポイントを取得できない場合は、取得できるポイントだけ記録し、欠測値を推定値で埋めません。

## 最低限記録する条件

各測定ランで少なくとも以下を保存します。

- benchmark repository commit
- mediamtx-playground commit
- realtime-pose-triton commit
- protocol
- input identifier（公開可能な論理名のみ）
- target FPS
- duration
- repeat number
- model name
- 実行環境メモ

## CSV スキーマ

`new-run.sh` が作成する `metrics.csv` は次の列を持ちます。

```text
run_id,scenario,protocol,repeat,frame_id,t0_ms,t1_ms,t2_ms,t3_ms,t4_ms,t5_ms,inference_ms,e2e_ms,cpu_percent,memory_mb,gpu_util_percent,gpu_memory_mb,notes
```

不明な値は空欄にします。

## 再現性

性能値と同じくらい、使用したコードの固定が重要です。submodule は測定時点の commit に固定し、測定ランの metadata にも SHA を保存します。
