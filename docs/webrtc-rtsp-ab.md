# WebRTC/WHEP と RTSP+FFmpeg の A/B 比較

## 目的

推論クライアントの映像受信方式について、同じ映像を使って次の 2 経路を比較します。

```text
A. 現行相当
Browser --WebRTC--> MediaMTX --RTSP--> FFmpeg --2fps--> 推論相当処理

B. 比較候補
Browser --WebRTC--> MediaMTX --WebRTC/WHEP--> WebRTC Receiver --2fps--> 推論相当処理
```

この検証では MediaMTX 自体は外しません。推論クライアント側の `RTSP + FFmpeg` を WebRTC 受信へ置き換えた場合に、FFmpeg の probe に伴う接続待ちや古いフレームの滞留を減らせるかを切り分けます。

## 入力条件

- 解像度: 1920x1080
- 入力 FPS: 15fps
- Codec: H.264
- 入力元: Headless Chrome の fake camera
- 配信: Browser から MediaMTX へ WebRTC
- 同一フレームを識別できる `sourceFrameId` 相当のマーカーを映像内へ埋め込む
- 1 回の workflow で 3 回比較する

## 比較する指標

### `first_frame_ms`

Reader の接続開始から、最初に識別可能な映像フレームを取得するまでの時間です。

単純な「最初に映像が来た速さ」を示しますが、そのフレームが現在時刻に近いとは限りません。

### `first_frame_lag_ms_est`

最初に取得したフレームが、同時刻の送信側フレームから何フレーム遅れているかを 15fps 基準で時間へ換算した推定値です。

FFmpeg の probe 中に古いフレームが滞留している場合、この値が大きくなる可能性があります。

### `ready_ms`

接続開始から、受信フレームが送信側の最新フレームから 2 フレーム以内まで追いつくまでの時間です。

この検証では単に decoder が開いた時点ではなく、**live edge に十分近づいた時点を「接続済み相当」**として扱います。

### `frames_before_ready`

最初のフレーム取得後、live edge に追いつくまでに何フレーム処理したかを示します。

古いフレームを順に消化してから追いつく方式では値が増えます。

### `post_ready_source_frame_steps`

「接続済み相当」以降に 2fps として選択された物理フレーム ID の差分です。

15fps から 2fps へ変換する場合、期待される差分は概ね `7, 8, 7, 8 ...` です。

### `physical_sampling_ok`

`post_ready_source_frame_steps` が 7 または 8 で推移し、接続済み以降の 2fps サンプリングに不自然な欠落・重複がないことを確認します。

## 判定方針

GitHub Actions の成功条件は、「A/B 両経路で測定可能なフレームを取得できたこと」です。

WebRTC/WHEP が必ず RTSP+FFmpeg より速い、という条件は CI の pass/fail にはしません。方式選定は複数回の測定値を比較して判断します。

特に重視するのは次の順です。

1. `ready_ms` が短いか
2. 最初のフレームが live edge に近いか
3. 接続済み以降の 2fps 物理フレームに欠落・重複がないか
4. 結果が複数回で再現するか

## 成果物

workflow artifact に以下を保存します。

```text
summary.json
repeat_1/
  metrics.json
  publisher_events.json
  rtsp_ffmpeg_events.json
  webrtc_whep_events.json
  ffmpeg_stderr.log
repeat_2/
...
mediamtx.log
```

`summary.json` には方式ごとの median と各 repeat の結果を保存します。

## 注意点

- GitHub-hosted runner 上の絶対レイテンシを、そのまま本番 PC の性能値として扱いません。
- 同じ runner、同じ MediaMTX、同じ映像源での **方式間の相対差** を主に確認します。
- 今回は推論モデルや Triton を通さず、映像受信方式そのものを切り分けます。
- TURN 経由や実ネットワークでの揺らぎはこの検証範囲外です。
- 本番方式を決める場合は、選定後に実環境で再度 E2E 性能を測定します。
