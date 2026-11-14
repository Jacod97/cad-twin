#!/bin/bash
# 스트리밍 앱과 웹 뷰어 종료
pkill -f "kit-app-template/_build/linux-x86_64/release/kit/kit" ; pkill -f "web-viewer-sample/node_modules/.bin/vite"; echo stopped
