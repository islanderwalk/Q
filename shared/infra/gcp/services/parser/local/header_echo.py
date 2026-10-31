"""本機驗證用：記錄 Pub/Sub 模擬器 push 過來的 header 與 body，一律回 204。

只用標準函式庫，在 compose 的 header-echo 服務裡執行。
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        record = {
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "has_authorization": "authorization" in {k.lower() for k in self.headers.keys()},
            "body": json.loads(body) if body.startswith("{") else body,
        }
        print("ECHO " + json.dumps(record, ensure_ascii=False), flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # 精簡 access log
        print("ACCESS " + (fmt % args), flush=True)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
