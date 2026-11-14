# Omniverse 웹 스트리밍 뷰어 (Kit 106.0.3 + web-viewer-sample 1.4.1)

```bash
cd ~/mnt/research/cad-twin/omniverse
nohup ./run_stream.sh > kit_stream.log 2>&1 &        # Kit 앱 (화면 없음, GPU 0, draft/out/scene/factory_sim.usda 자동 열림)
nohup ./run_web_viewer.sh > web_viewer.log 2>&1 &    # 웹 뷰어 (포트 5173)
./stop_all.sh                                         # 둘 다 종료
```
- 다른 장면/GPU: `./run_stream.sh <장면.usda> <GPU번호>`
- 접속: 맥 브라우저(Chrome 권장)에서 `http://163.239.104.198:5173` → "UI for any streaming app" 선택 → Next
- 포트: 5173/tcp(웹 뷰어), 49100/tcp(신호), 47998/udp(영상)
- 서버 IP는 `web-viewer-sample/stream.config.json`의 `local.server`
- 캐시·데이터는 모두 이 폴더 안: `packman-repo/`, `kit-data/`, `npm-cache/`
- 앱 재생성/빌드: `cd kit-app-template && PM_PACKAGES_ROOT=../packman-repo ./repo.sh build`
