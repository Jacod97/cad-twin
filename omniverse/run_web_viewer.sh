#!/bin/bash
# 웹 뷰어(web-viewer-sample 1.4.1) 실행. 접속: http://<서버IP>:5173
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export npm_config_cache="$HERE/npm-cache"
cd "$HERE/web-viewer-sample" && exec npx vite --host 0.0.0.0 --port "${1:-5173}"
