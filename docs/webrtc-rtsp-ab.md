# WebRTC/WHEP と RTSP+FFmpeg の A/B 比較

## 目的

推論クライアントの映像受信方式について、同じ物理フレームを使って次の2経路を比較します。

```text
A. 現行相当
Browser --WHIP/WebRTC--> MediaMTX --RTSP--> FFmpeg --2fps--> 推論相当処理

B. 比較候補
Browser --WHIP/WebRTC--> MediaMTX --WHEP/WebRTC--> WebRTC Receiver --logical 2fps--> 推論相当処理
```

MediaMTXは残したまま、推論クライアント側の `RTSP + FFmpeg` を WebRTC/WHEP 受信にした場合、FFmpegのprobeに伴う接続待ちや初期フレーム滞留を減らせるかを確認します。

## 既存の性能検証実装を再利用する

今回のA/B比較では、commercialization validationで物理フレーム追跡に使用した方式をそのまま基準にします。

- 解像度: 1080x1920
- 入力: 15fps / H.264
- Browserはfake camera映像を専用HTMLからMediaMTXへWHIPでpublish
- 映像内に16個の白黒ブロックを配置
- 先頭4bitを同期パターン `1010`、後続12bitを `sourceFrameId` として使用
- RTSP+FFmpeg側とWebRTC/WHEP側の双方で、受信映像そのものから同じ `sourceFrameId` を復号する

送信側previewを画像解析してframe IDを推定する独自処理は使用しません。

## live edge の基準

A/Bの接続開始とは別に、MediaMTXへ事前接続したWebRTC/WHEP readerを1本維持します。

```text
                              ┌-- RTSP --> FFmpeg       ← A
Browser --WHIP--> MediaMTX ---┼-- WHEP --> 新規Reader   ← B
                              └-- WHEP --> 常時Reader   ← live edge基準
```

常時Readerで連続して復号した `sourceFrameId` を、その時点の最新映像に近い物理フレームの基準として使います。これはA/Bの起動時間には含めず、受信したフレームがどの程度古いかを判定するためだけに使用します。

この検証では、受信フレームが常時Readerの `sourceFrameId` から2フレーム以内まで追いついた時点を「接続済み相当」とします。

## 比較する指標

### `first_frame_ms`

Readerの接続開始から、最初に物理 `sourceFrameId` を復号できるまでの時間です。

### `first_frame_lag_ms_est`

最初に取得したフレームが、同時刻のlive edge基準から何フレーム遅れているかを15fpsで時間換算した推定値です。

### `ready_ms`

接続開始から、受信映像がlive edge基準の2フレーム以内まで追いつくまでの時間です。今回の方式比較で最も重視する値です。

### `frames_before_ready`

最初のフレーム取得後、live edgeへ追いつくまでに何フレーム処理したかを示します。古いフレームを順番に処理してから追いつく場合、この値が増えます。

### `post_ready_source_frame_steps`

「接続済み相当」以降に2fpsとして選択された物理 `sourceFrameId` の差分です。

15fpsから2fpsの場合、正常なサンプリングでは概ね `7, 8, 7, 8 ...` となります。

### `physical_sampling_ok`

接続済み以降の物理フレームIDに重複がなく、2fpsの選択間隔が7または8フレームで推移することを確認します。

## 判定方針

GitHub Actionsのpass/failは、A/B両経路について物理フレームを取得し、live edgeまで追いついた状態で比較データを生成できたことを条件にします。

WebRTC/WHEPが必ずRTSP+FFmpegより速いこと自体はCI成功条件にしません。方式選定は複数回の測定結果から判断します。

確認順序は以下です。

1. `ready_ms` が短いか
2. 最初のフレームがlive edgeに近いか
3. 接続済み以降の2fps物理フレームに不自然な欠落・重複がないか
4. 複数回で同じ傾向になるか

## 成果物

workflow artifactに以下を保存します。

```text
summary.json
publisher_state.json
repeat_1/
  metrics.json
  reference_events.json
  rtsp_ffmpeg_events.json
  webrtc_whep_events.json
  ffmpeg_command.json
  ffmpeg_stderr.log
  candidate_video_meta.json
repeat_2/
...
mediamtx.log
```

## 注意点

- GitHub-hosted runner上の絶対時間を、そのまま本番PCの性能値とは扱いません。
- 同一runner・同一MediaMTX・同一映像源での方式間の相対差を主に確認します。
- 常時WHEP Readerはlive edgeの物理フレーム基準であり、A/Bの性能比較対象ではありません。
- 今回は推論モデルやTritonを通さず、映像受信方式そのものを切り分けます。
- TURN経由や実ネットワークの揺らぎは検証範囲外です。
