from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 4174


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>Speaker Client Bridge</title>
  </head>
  <body>
    <script>
      window.CLIENT_BRIDGE_PORT = 4174;
    </script>
  </body>
</html>
"""


class ClientBridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "port": PORT})
            return
        if parsed.path == "/read-file":
            self._handle_read_file(parsed.query)
            return
        if self.path in {"/", "/index.html"}:
            self._send_text(200, HTML_PAGE, "text/html; charset=utf-8")
            return
        self._send_text(404, "Not Found")

    def do_POST(self) -> None:
        if self.path == "/pick-media":
            self._handle_pick_media()
            return
        if self.path == "/pick-lower-thirds":
            self._handle_pick_lower_thirds()
            return
        self._send_json(404, {"detail": "not found"})

    def log_message(self, format: str, *args) -> None:
        return

    def _handle_pick_media(self) -> None:
        selection = _run_file_picker(multiple=False, file_types={"public.movie", "public.audio"})
        if not selection:
            self._send_json(200, {"files": []})
            return
        file_path = Path(selection[0])
        size_bytes = file_path.stat().st_size if file_path.exists() else None
        self._send_json(
            200,
            {
                "files": [
                    {
                        "name": file_path.name,
                        "path": str(file_path),
                        "sizeBytes": size_bytes,
                    }
                ]
            },
        )

    def _handle_pick_lower_thirds(self) -> None:
        selection = _run_file_picker(multiple=True, file_types={"public.png", "public.jpeg", "org.webmproject.webp"})
        files = [{"name": Path(path).name, "path": str(Path(path))} for path in selection]
        self._send_json(200, {"files": files})

    def _handle_read_file(self, query_string: str) -> None:
        params = parse_qs(query_string)
        raw_path = (params.get("path") or [""])[0]
        target_path = Path(raw_path).expanduser()
        if not raw_path or not target_path.exists() or not target_path.is_file():
            self._send_json(404, {"detail": "文件不存在"})
            return
        if target_path.stat().st_size > 2 * 1024 * 1024 * 1024:
            self._send_json(413, {"detail": "当前文件超过 2GB 上限，客户端已停止读取。请先压缩或拆分后再上传。"})
            return
        content = target_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-File-Name", target_path.name)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _run_file_picker(*, multiple: bool, file_types: set[str]) -> list[str]:
    chooser = "choose files" if multiple else "choose file"
    type_clause = " of type {" + ", ".join(f'"{item}"' for item in sorted(file_types)) + "}"
    prompt = " with prompt " + json.dumps("请选择要交给 Speaker 客户端的文件")
    script = f"""
set pickedItems to {chooser}{type_clause}{prompt}
set outputLines to {{}}
repeat with pickedItem in pickedItems
  set end of outputLines to POSIX path of pickedItem
end repeat
return outputLines as string
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    raw_output = result.stdout.strip()
    if not raw_output:
        return []
    if multiple:
        return [item.strip() for item in raw_output.split(", ") if item.strip()]
    return [raw_output]


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ClientBridgeHandler)
    print(f"Client bridge running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
