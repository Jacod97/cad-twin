"""report/index.html → report/cad_twin_report.pdf

헤드리스 Firefox를 Marionette 프로토콜로 직접 조종해 A4로 인쇄한다 (추가 패키지 없음, 표준 라이브러리만 사용).
사용: python3 report/make_pdf.py
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "index.html"
OUT = HERE / "cad_twin_report.pdf"
PORT = 28281


class Marionette:
    def __init__(self, port):
        deadline = time.time() + 60
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=120)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.buf = b""
        self.msg_id = 0
        self._read()  # handshake

    def _read(self):
        while b":" not in self.buf:
            self.buf += self.sock.recv(65536)
        head, rest = self.buf.split(b":", 1)
        n = int(head)
        while len(rest) < n:
            rest += self.sock.recv(1 << 20)
        self.buf = rest[n:]
        return json.loads(rest[:n])

    def call(self, name, params=None):
        self.msg_id += 1
        data = json.dumps([0, self.msg_id, name, params or {}]).encode()
        self.sock.sendall(str(len(data)).encode() + b":" + data)
        while True:
            msg = self._read()
            if msg[0] == 1 and msg[1] == self.msg_id:
                if msg[2]:
                    raise RuntimeError(f"{name}: {msg[2]}")
                return msg[3]


def main():
    profile = tempfile.mkdtemp(prefix="ff_pdf_")
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {PORT});\n'
        'user_pref("ui.systemUsesDarkTheme", 0);\n'
        'user_pref("layout.css.prefers-color-scheme.content-override", 1);\n'
    )
    ff = subprocess.Popen(
        ["firefox", "--headless", "--marionette", "--no-remote", "--profile", profile],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        m = Marionette(PORT)
        m.call("WebDriver:NewSession", {"capabilities": {}})
        m.call("WebDriver:SetWindowRect", {"width": 900, "height": 1200})
        m.call("WebDriver:Navigate", {"url": SRC.as_uri()})
        # 지연 로딩 이미지를 모두 불러오고 웹폰트가 준비될 때까지 기다림
        m.call("WebDriver:ExecuteScript", {"script": "document.querySelectorAll('img').forEach(i => i.loading = 'eager'); return true;"})
        for _ in range(120):
            ok = m.call("WebDriver:ExecuteScript", {"script":
                "return document.fonts.status === 'loaded' && "
                "Array.from(document.images).filter(i => i.getAttribute('src')).every(i => i.complete && i.naturalWidth > 0);"})
            if ok.get("value"):
                break
            time.sleep(0.5)
        else:
            print("warning: some images or fonts did not finish loading")
        res = m.call("WebDriver:Print", {
            "orientation": "portrait",
            "background": True,
            "shrinkToFit": True,
            "page": {"width": 21.0, "height": 29.7},
            "margin": {"top": 1.4, "bottom": 1.4, "left": 1.5, "right": 1.5},
        })
        OUT.write_bytes(base64.b64decode(res["value"]))
        print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
        try:
            m.call("Marionette:Quit", {"flags": ["eForceQuit"]})
        except Exception:
            pass
    finally:
        try:
            ff.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ff.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
