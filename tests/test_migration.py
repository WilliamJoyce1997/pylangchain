"""Offline Python 3.12 and LangChain v1 regression tests."""
import base64
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch

import httpx
import requests
from PIL import Image

import main
from deepseek_extensions import DeepSeekChat

KEY = "mock-deepseek-key"
FILE = {"id": "file-mock", "filename": "sample.png", "bytes": 123, "created_at": 1720000000}
MEDIA_URL = "https://media.example.test/generated"


def png_bytes():
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    return image.getvalue()


def response(payload=None, content=None, status=200, mime="application/json"):
    reply = requests.Response()
    reply.status_code = status
    reply.headers["Content-Type"] = mime
    reply._content = json.dumps(payload).encode() if content is None else content
    return reply


def answer_from(page):
    return unescape(page.split('<textarea id="diagnostics_raw" readonly rows="5">', 1)[1].split("</textarea>", 1)[0])


class ModelScript:
    """Returns OpenAI-compatible responses while exercising the real Agent and SDK."""

    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def handle(self, request):
        self.requests.append(json.loads(request.content))
        assert request.url.host == "api.deepseek.com"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        output = next(self.outputs)
        message = {"role": "assistant", "reasoning_content": "mock reasoning"}
        if isinstance(output, str):
            message["content"] = output
        else:
            message["content"] = None
            message["tool_calls"] = [
                {"id": f"call_{len(self.requests)}_{i}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}
                for i, (name, args) in enumerate(output)
            ]
        data = {"id": "mock", "object": "chat.completion", "model": "deepseek-flash",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "stop" if isinstance(output, str) else "tool_calls"}]}
        return httpx.Response(200, json=data)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.client = main.app.test_client()
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.session_transaction() as session:
            self.csrf = {"X-DeepSeek-CSRF": session["deepseek_csrf"]}

        # Fail loudly if any mock misses an outbound HTTP request.
        entered = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected real HTTP request"))
        self.addCleanup(entered.stop)
        entered.start()
        media_dirs = patch.object(main, "ALLOWED_MEDIA_DIRS", set())
        self.addCleanup(media_dirs.stop)
        media_dirs.start()
        socket_guard = patch("socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        self.addCleanup(socket_guard.stop)
        socket_guard.start()

    def post_chat(self, script, **fields):
        with httpx.Client(transport=httpx.MockTransport(script.handle)) as http_client:
            def model(**kwargs):
                return DeepSeekChat(**kwargs, http_client=http_client, max_retries=0)
            with patch.object(main, "ChatDeepSeek", side_effect=model):
                return self.client.post("/", data={
                    "base_dir": str(self.root / "files"), "ui_session_id": "test-session",
                    "deepseek_key": KEY, "query": "测试请求", **fields,
                })

    def post_deepseek(self, endpoint, **fields):
        return self.client.post("/deepseek/" + endpoint, json={
            "deepseek_key": KEY, "session_id": "test-session", **fields,
        }, headers=self.csrf)

    def test_home_settings_and_price_estimate(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Nano Banana 2.1", page)
        with self.client.get("/static/js/conversations.js") as history_script:
            self.assertIn("lingsi", history_script.get_data(as_text=True))
        self.assertTrue(self.client.post("/", data={"save_settings": "1", "deepseek_key": KEY}).json["ok"])
        self.assertEqual(self.client.post("/video/estimate", data={"video_model": "pollo-v2-5"}).status_code, 200)
        self.assertEqual(self.client.post("/video/estimate", data={"video_model": "unknown"}).status_code, 400)

    def test_balance_and_attachment_round_trip(self):
        with patch("requests.request", return_value=response({
            "balance_infos": [{"currency": "CNY", "total_balance": "23.455"}],
        })) as outbound:
            self.assertEqual(self.post_deepseek("balance").json["balance"], "23.46")
            self.assertTrue(outbound.call_args.args[1].endswith("/user/balance"))
        with patch("requests.request", return_value=response(FILE)) as outbound:
            args = {"deepseek_key": KEY, "session_id": "test-session"}
            bad = self.client.post("/deepseek/files/upload", data={
                **args, "file": (io.BytesIO(b"%PDF"), "fake.png")}, headers=self.csrf)
            self.assertEqual(bad.status_code, 400)
            outbound.assert_not_called()
            upload = self.client.post("/deepseek/files/upload", data={
                **args, "file": (io.BytesIO(png_bytes()), "sample.png")}, headers=self.csrf)
            self.assertEqual(upload.status_code, 200, upload.get_data(as_text=True))
            self.assertEqual(outbound.call_args.kwargs["data"], {"purpose": "user_data"})
        script = ModelScript([("retrieve_cloud_file", {"file_id": FILE["id"]})], "已读取附件")
        with patch("requests.request", return_value=response(FILE)):
            reply = self.post_chat(script, chat_file_ids=json.dumps([FILE["id"]]))
        self.assertIn("已读取附件", answer_from(reply.get_data(as_text=True)))
        self.assertEqual(len(script.requests), 2)
        for turn in script.requests:
            user = next(m for m in turn["messages"] if m["role"] == "user")
            self.assertEqual(user["content"][-1], {"type": "file", "file_id": FILE["id"]})
        follow_up = script.requests[1]["messages"]
        self.assertEqual(follow_up[-2]["reasoning_content"], "mock reasoning")
        self.assertEqual(follow_up[-1]["tool_call_id"], "call_1_0")

    def test_local_tools_in_order_and_memory_format(self):
        folder = self.root / "files"
        folder.mkdir()
        memory = self.root / "memory"
        memory.mkdir()
        path = memory / "memory.txt"
        path.write_text("USER: 历史问题\nAI: 历史回答\n\n", encoding="utf-8")
        script = ModelScript([("write_file", {"file_path": "data.txt", "text": "测试内容"}),
                              ("run_powershell", {"path": "forbidden.ps1"})],
                             [("read_file", {"file_path": "data.txt"})], "已读取本地文件")
        with patch.object(main.subprocess, "run") as run:
            reply = self.post_chat(script, use_memory="on", memory_path=str(memory),
                                   system_prompt="保留 {JSON}",
                                   persona_file=(io.BytesIO("资料员".encode()), "role.txt"), use_persona="on")
            run.assert_not_called()
        self.assertIn("已读取本地文件", answer_from(reply.get_data(as_text=True)))
        self.assertEqual((folder / "data.txt").read_text(encoding="utf-8"), "测试内容")
        self.assertIn("AI: 已读取本地文件", path.read_text(encoding="utf-8"))
        first = script.requests[0]["messages"]
        self.assertEqual(first[0]["role"], "system")
        self.assertIn("{JSON}", first[0]["content"])
        self.assertIn("历史回答", first[1]["content"])
        self.assertIn("资料员", first[1]["content"])
        self.assertNotIn("web_search", [t["function"]["name"] for t in script.requests[0]["tools"]])
        self.assertEqual(len(script.requests[1]["messages"][-2]["tool_calls"]), 1)

    def test_file_tool_is_directory_scoped_and_agent_loop_is_bounded(self):
        (self.root / "outside.txt").write_text("outside-fixture", encoding="utf-8")
        read_outside = [("read_file", {"file_path": "../outside.txt"})]
        script = ModelScript(*([read_outside] * 30))
        page = self.post_chat(script).get_data(as_text=True)
        self.assertIn("达到工具调用上限", answer_from(page))
        tool_result = script.requests[1]["messages"][-1]
        self.assertEqual(tool_result["role"], "tool")
        self.assertIn("denied", tool_result["content"].lower())
        self.assertNotIn("outside-fixture", tool_result["content"])
        self.assertLess(len(script.requests), 30)

    def test_script_tools_keep_permissions_and_timeout(self):
        local = self.root / "files"
        local.mkdir()
        (local / "safe.ps1").write_text("Write-Output 'fixture'", encoding="utf-8")
        (local / "safe.exe").touch()
        script = ModelScript([("run_powershell", {"path": str(self.root / "outside.ps1")})],
                             [("run_powershell", {"path": "safe.ps1"})],
                             [("run_executable", {"path": "safe.exe"})], "执行测试完成")
        finished = subprocess.CompletedProcess([], 0, stdout="mock stdout", stderr="")
        with patch.object(main.subprocess, "run", return_value=finished) as run:
            reply = self.post_chat(script)
        self.assertIn("执行测试完成", answer_from(reply.get_data(as_text=True)))
        self.assertIn("权限错误", script.requests[1]["messages"][-1]["content"])
        self.assertEqual(len(run.call_args_list), 2)
        ps, exe = run.call_args_list
        self.assertEqual(ps.args[0][-1], str(local / "safe.ps1"))
        self.assertEqual(ps.kwargs["timeout"], 600)
        self.assertFalse(ps.kwargs["shell"])
        self.assertEqual(exe.args[0], [str(local / "safe.exe")])
        self.assertEqual(exe.kwargs["timeout"], 300)

    def test_windows_junction_cannot_escape_script_root(self):
        if os.name != "nt":
            self.skipTest("Windows junction")
        root = self.root / "root"
        outside = self.root / "outside"
        root.mkdir()
        outside.mkdir()
        junction = root / "junction"
        # Creating a junction uses cmd's mklink builtin and requires no admin privileges.
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                       check=True, capture_output=True)
        self.addCleanup(junction.rmdir)
        with self.assertRaises(PermissionError):
            main.make_safe_path(str(root), "junction/sample.ps1")

    def test_cloud_delete_is_confirmed_and_single_use(self):
        with patch("requests.request", side_effect=[
            response(FILE), response(FILE), response({"id": FILE["id"], "deleted": True}),
        ]) as outbound:
            def propose():
                script = ModelScript([("delete_cloud_file", {"file_id": FILE["id"]}),
                                      ("run_executable", {"path": "untrusted.exe"})])
                page = self.post_chat(script).get_data(as_text=True)
                section = page.split('id="cloud_pending_data">', 1)[1].split("</script>", 1)[0]
                records = json.loads(section)
                self.assertEqual(len(records), 1)
                self.assertEqual(len(script.requests), 1)
                return {"file_id": FILE["id"], "token": records[0]["token"]}

            with patch.object(main.subprocess, "run") as run:
                canceled = propose()
                self.assertEqual(self.post_deepseek("files/delete", decision="cancel", **canceled).status_code, 200)
                self.assertEqual(self.post_deepseek("files/delete", decision="confirm", **canceled).status_code, 409)
                pending = propose()
                forged = self.post_deepseek("files/delete", decision="confirm",
                                            **{**pending, "session_id": "different"})
                self.assertGreaterEqual(forged.status_code, 400)
                self.assertTrue(self.post_deepseek("files/delete", decision="confirm", **pending).json["deleted"])
                self.assertEqual(self.post_deepseek("files/delete", decision="confirm", **pending).status_code, 409)
                run.assert_not_called()
        self.assertEqual([call.args[0] for call in outbound.call_args_list], ["GET", "GET", "DELETE"])

    def test_search_fetch_pagination_and_sources(self):
        url = "https://example.com/fixture"
        script = ModelScript([("web_search", {"query": "fixture", "count": 1})],
                             [("web_fetch", {"url": url})],
                             [("list_cloud_files", {"after": "file-first", "limit": 1})], "已查证")
        with patch("requests.request", side_effect=[
            response({"results": [{"title": "Fixture", "url": url, "summary": "摘要"}]}),
            response({"title": "Fixture", "content": "网页正文"}), response({"data": [FILE], "has_more": True, "last_id": FILE["id"]}),
        ]) as outbound:
            page = self.post_chat(script, enable_network="on").get_data(as_text=True)
        self.assertIn("已查证", answer_from(page))
        self.assertIn(url, page)
        self.assertIn("游标", page)
        self.assertIn("untrusted_web_data", script.requests[2]["messages"][-1]["content"])
        self.assertNotIn("网页正文", script.requests[3]["messages"][0]["content"])
        self.assertEqual(outbound.call_args.kwargs["params"]["after"], "file-first")

    def test_api_failures_and_credential_redaction(self):
        with patch("requests.request", return_value=response({"error": KEY}, status=429)):
            reply = self.post_deepseek("balance")
        self.assertEqual(reply.status_code, 429)
        self.assertNotIn(KEY, reply.get_data(as_text=True))
        self.assertEqual(self.client.post("/deepseek/balance", json={"deepseek_key": KEY}).status_code, 403)
        with patch.object(ModelScript, "handle", side_effect=RuntimeError("PRIVATE-MOCK-EXCEPTION")):
            reply = self.post_chat(ModelScript())
        self.assertIn("DeepSeek 对话请求失败", answer_from(reply.get_data(as_text=True)))
        self.assertNotIn("PRIVATE-MOCK-EXCEPTION", reply.get_data(as_text=True))

    def test_image_provider_routes_and_media_downloads(self):
        for model in ("doubao-seedream-5-0-260128", "nano-banana-2-1", "grok-imagine-image-2.0"):
            with self.subTest(model=model):
                self.check_image_route(model)

    def check_image_route(self, model):
        dest = self.root / model
        png = png_bytes()
        encoded = base64.b64encode(png).decode()
        if model == "nano-banana-2-1":
            accepted = response({"taskId": "nano-task"})
            polls = [response({"generations": [{"mediaType": "image", "status": "succeed", "url": MEDIA_URL}]}),
                     response(content=png, mime="image/png")]
        else:
            accepted = response({"data": [{"b64_json": encoded}]})
            polls = []
        with (patch("requests.post", return_value=accepted),
              patch("requests.request", return_value=accepted),
              patch("requests.get", side_effect=polls)):
            page = self.client.post("/", data={"send_image": "1", "ark_model": model, "img_ratio": "1:1",
                "image_prompt": "mock", "image_media_dir": str(dest), "ark_key": "mock-ark",
                "pollo_key": "mock-pollo", "xai_key": "mock-xai"}).get_data(as_text=True)
        self.assertIn("已保存", answer_from(page))
        output, = dest.glob("*.png")
        self.assertEqual(output.read_bytes(), png)
        with self.client.get("/media", query_string={"path": str(output)}) as saved:
            self.assertEqual(saved.data, png)

    def test_video_provider_routes_and_media_downloads(self):
        for model in ("pollo-v2-5", "grok-imagine-video-1.5"):
            with self.subTest(model=model):
                dest = self.root / model
                video = b"offline-video-fixture"
                if model == "pollo-v2-5":
                    accepted = response({"taskId": "pollo-task"})
                    polls = [response({"generations": [{"status": "succeed", "mediaType": "video", "url": MEDIA_URL}]}),
                             response(content=video, mime="video/mp4")]
                    xai_calls = []
                else:
                    accepted = response({})
                    polls = [response(content=video, mime="video/mp4")]
                    xai_calls = [response({"request_id": "xai-task"}),
                                 response({"status": "done", "video": {"url": MEDIA_URL}})]
                with (patch("requests.post", return_value=accepted),
                      patch("requests.request", side_effect=xai_calls),
                      patch("requests.get", side_effect=polls)):
                    page = self.client.post("/", data={"send_video": "1", "video_model": model,
                        "video_prompt": "mock", "media_dir": str(dest), "pollo_key": "mock-pollo",
                        "xai_key": "mock-xai"}).get_data(as_text=True)
                self.assertIn("视频生成完成", answer_from(page))
                output, = dest.glob("*.mp4")
                self.assertEqual(output.read_bytes(), video)

    def test_voice_training_and_audio_route(self):
        args = {"doubao_token": "mock-token", "doubao_appid": "mock-app", "voice_speaker_id": "mock"}
        with patch("requests.post", return_value=response({"status": "mock-ok"})) as outbound:
            upload = self.client.post("/", data={**args, "voice_upload": "1",
                "voice_upload_file": (io.BytesIO(b"audio-fixture"), "voice.wav")})
            self.assertIn("上传成功", answer_from(upload.get_data(as_text=True)))
            self.assertTrue(outbound.call_args.args[0].endswith("/audio/upload"))
            status = self.client.post("/", data={**args, "check_voice_status": "1"})
            self.assertIn("训练状态查询结果", answer_from(status.get_data(as_text=True)))
        audio = b"mock-mp3"
        with (patch.object(main, "TTS_AUDIO_DIR", str(self.root)),
              patch("requests.post", return_value=response(content=audio, mime="audio/mpeg"))):
            reply = self.post_chat(ModelScript("需要合成"), enable_tts="on", asr_ark_key="mock-tts",
                                   tts_speaker_id="mock")
            self.assertIn("语音已保存", answer_from(reply.get_data(as_text=True)))
            output, = self.root.glob("tts_*.mp3")
            with self.client.get(f"/tts_audio/{output.name}") as saved:
                self.assertEqual(saved.data, audio)


if __name__ == "__main__":
    unittest.main()
