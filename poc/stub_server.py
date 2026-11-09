"""本地假模型服务：一个 OpenAI 兼容的 /v1/chat/completions。

为什么需要它：CrewAI 和 AutoGen 都必须真的调一次模型才能跑。
有了它，三个 POC 都能离线跑、结果完全可复现，而且都比的是框架结构，不是模型强弱。

跑法：python stub_server.py 8111
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from stub_model import complete

STAGE_RE = re.compile(r"#STAGE:(\w+)#")
INPUT_RE = re.compile(r"#INPUT:(.*?)#", re.S)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "stub", "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        text = "\n".join(str(m.get("content", "")) for m in req.get("messages", []))

        stages = STAGE_RE.findall(text)
        if not stages:
            content = "缺少阶段标记，原样返回。" + text[-120:]
        else:
            # 用最后一个标记：它总是「当下这一步」的指令，前面的是历史
            stage = stages[-1]
            inputs = INPUT_RE.findall(text)
            payload = inputs[-1].strip() if inputs else text
            content = complete(stage, payload)

        self._send(200, {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": req.get("model", "stub"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": len(text), "completion_tokens": len(content),
                      "total_tokens": len(text) + len(content)},
        })

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8111
    print(f"假模型服务已就绪：http://127.0.0.1:{port}/v1")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
