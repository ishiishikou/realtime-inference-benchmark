#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="${repo_root}/results/${timestamp}"
mkdir -p "${run_dir}"

benchmark_sha="$(git -C "${repo_root}" rev-parse HEAD)"
mediamtx_sha="$(git -C "${repo_root}/components/mediamtx-playground" rev-parse HEAD 2>/dev/null || printf 'NOT_INITIALIZED')"
pose_sha="$(git -C "${repo_root}/components/realtime-pose-triton" rev-parse HEAD 2>/dev/null || printf 'NOT_INITIALIZED')"

cat > "${run_dir}/metadata.env" <<EOF
RUN_ID=${timestamp}
CREATED_AT_UTC=${timestamp}
BENCHMARK_SHA=${benchmark_sha}
MEDIAMTX_PLAYGROUND_SHA=${mediamtx_sha}
REALTIME_POSE_TRITON_SHA=${pose_sha}
EOF

cat > "${run_dir}/metrics.csv" <<'EOF'
run_id,scenario,protocol,repeat,frame_id,t0_ms,t1_ms,t2_ms,t3_ms,t4_ms,t5_ms,inference_ms,e2e_ms,cpu_percent,memory_mb,gpu_util_percent,gpu_memory_mb,notes
EOF

cat > "${run_dir}/notes.md" <<EOF
# 測定メモ ${timestamp}

## 条件

- scenario:
- protocol:
- input:
- target FPS:
- duration:
- repeat:
- model:

## 観察事項

- 

## 異常・欠測

- 
EOF

printf 'created %s\n' "${run_dir}"
