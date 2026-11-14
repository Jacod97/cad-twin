#!/bin/bash
# Kit 스트리밍 앱 실행 (화면 없음, GPU 1장, 결과 색 장면을 바로 연 상태로 시작)
# 사용: ./run_stream.sh [장면.usda] [GPU번호]
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENE="${1:-$HERE/../draft/out/scene/factory_sim.usda}"
GPU="${2:-0}"
export PM_PACKAGES_ROOT="$HERE/packman-repo"
exec "$HERE/kit-app-template/_build/linux-x86_64/release/cadtwin.viewer_streaming.kit.sh" \
  --no-window \
  --portable-root "$HERE/kit-data" \
  --/app/auto_load_usd="$(realpath "$SCENE")" \
  --/rtx/verifyDriverVersion/enabled=false \
  --/renderer/multiGpu/enabled=false \
  --/renderer/activeGpu="$GPU" \
  "${@:3}"
