import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HttpFileStreamTimeoutTest(unittest.TestCase):
    def test_file_uses_http_client_timeout_before_streaming(self):
        source = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
        match = re.search(
            r"bool handleFileRead\(String path\)\s*\{(?P<body>.*?)^\}",
            source,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "handleFileRead was not found")
        handler = match.group("body")

        timeout = handler.find("file.setTimeout(server->client().getTimeout());")
        stream = handler.find("server->streamFile(file, contentType)")

        self.assertNotEqual(stream, -1, "handleFileRead no longer streams the file")
        self.assertNotEqual(
            timeout,
            -1,
            "the LittleFS timeout must match the HTTP client's send timeout",
        )
        self.assertLess(timeout, stream, "set the timeout before streaming the file")


if __name__ == "__main__":
    unittest.main()
