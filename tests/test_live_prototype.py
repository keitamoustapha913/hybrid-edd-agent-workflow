"""Real Codex HTML preview safety and loopback server contract tests."""

from http.client import HTTPConnection
import threading
import unittest

from prototype import live_prototype


HTML = (
    "<!doctype html><html lang='en'><head><title>Review demo</title>"
    "<style>main{padding:1rem}button{font:inherit}</style></head>"
    "<body><main><h1>Task sample</h1><p id='state'>Empty state</p>"
    "<button id='try'>Try it</button><script>"
    "document.getElementById('try').addEventListener('click',()=>{"
    "document.getElementById('state').textContent='Synthetic success state';"
    "});</script></main></body></html>"
).encode("utf-8")


class LivePrototypeSafetyTests(unittest.TestCase):
    def test_self_contained_interactive_html_passes_structural_checks_only(self):
        checks = live_prototype.check_html(HTML)
        self.assertEqual(checks["html_parse"], "PASS")
        self.assertEqual(checks["self_contained_static_preview"], "PASS")
        self.assertEqual(checks["visual_usability"], "NOT_TESTED")
        self.assertEqual(checks["keyboard_accessibility"], "NOT_TESTED")
        self.assertEqual(checks["real_product_backend"], "NOT_CONNECTED")

    def test_remote_assets_and_api_calls_are_rejected(self):
        for fragment in (
            b"<script src='https://tracker.example/a.js'></script>",
            b"<img src='https://tracker.example/seen'>",
            b"<script>fetch('https://server.example/');</script>",
            b"<script>new WebSocket('ws:/server');</script>",
            b"<script>navigator.serviceWorker.register('worker.js');</script>",
            b"<iframe src='page.html'></iframe>",
            b"<form action='/collect'></form>",
            b"<a href='https://server.example/'>Leave</a>",
            b"<meta http-equiv='refresh' content='1'>",
            b"<svg></svg>",
            b"<script>window.open('elsewhere');</script>",
            b"<style>@import 'reset.css'</style>",
        ):
            with self.subTest(fragment=fragment):
                with self.assertRaises(live_prototype.PreviewRejected):
                    live_prototype.check_html(
                        HTML.replace(b"</main>", fragment + b"</main>")
                    )

    def test_invalid_markup_and_noninteractive_demos_rejected(self):
        variants = [
            b"<html><title>missing doctype</title></html>",
            HTML.replace(b"<main>", b"<div>").replace(b"</main>", b"</div>"),
            HTML.replace(b"<button", b"<span").replace(b"</button>", b"</span>"),
            HTML.replace(b"<script>", b"<!--").replace(b"</script>", b"-->"),
            HTML[:20] + b"\xff",
            b"x" * (live_prototype.MAX_HTML_BYTES + 1),
        ]
        for candidate in variants:
            with self.subTest(prefix=candidate[:28]):
                with self.assertRaises(live_prototype.PreviewRejected):
                    live_prototype.check_html(candidate)

    def test_serves_only_frozen_html_from_loopback_with_csp(self):
        sha = live_prototype._digest(HTML)
        with live_prototype.make_server(HTML, sha) as server:
            self.assertEqual(server.server_address[0], "127.0.0.1")
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
            )
            thread.start()
            try:
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                conn.request("GET", "/")
                response = conn.getresponse()
                data = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(data, HTML)
                csp = response.getheader("Content-Security-Policy")
                self.assertIn("connect-src 'none'", csp)
                self.assertIn("sandbox allow-scripts", csp)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                conn.close()

                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=4)
                conn.request("GET", "/not-allowed")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
                conn.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)

    def test_frozen_bytes_hash_is_required_for_server(self):
        with self.assertRaisesRegex(ValueError, "modified"):
            live_prototype.make_server(HTML, "0" * 64)


if __name__ == "__main__":
    unittest.main()
