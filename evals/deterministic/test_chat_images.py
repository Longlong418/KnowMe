"""Pictures the user attaches to one turn.

Three separate promises, each with its own failure mode:

  * an attachment is validated and stored as a real image file (upload.py);
  * it reaches the model on THIS turn's user message, in the shape the provider
    actually speaks — including the OpenAI-shaped providers, which need the
    block BRIDGED, and must not have their tool-result turns changed by it;
  * it never reaches session.history (that list holds strings — see
    test_snip_compact.test_history_holds_no_content_blocks, which this files
    under the same invariant), and what the chat log keeps is the reference,
    not the pixels.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from knowme.core.models import OpenAICompatClient
from knowme.ops.web.uploads import UploadError, image_hint, resolve, save_images

# A real 1x1 PNG — the smallest thing that sniffs as an image. Its bytes are
# what the magic-number check reads, so a fake header would not test anything.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20   # enough for the sniffer, not a real picture
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def _data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


# --- upload.py: what gets stored, and what gets refused ---------------------

def test_an_image_is_stored_under_its_content_hash(tmp_path):
    saved = save_images(tmp_path, [{"name": "shot.png", "data": PNG_URL}])
    assert len(saved) == 1
    img = saved[0]
    assert img["name"] == "shot.png"
    assert img["mime"] == "image/png"
    assert img["bytes"] == len(PNG)
    # Stored, and reachable by the url the conversation will use.
    assert Path(img["path"]).read_bytes() == PNG
    assert resolve(tmp_path, img["url"].rsplit("/", 1)[1]) == Path(img["path"])
    # base64 for the model call, without the data-URL prefix.
    assert base64.b64decode(img["data"]) == PNG


def test_the_same_image_twice_reuses_one_file(tmp_path):
    """Content-addressed: re-sending a screenshot must not pile up copies in a
    folder the user never looks at."""
    first = save_images(tmp_path, [{"name": "a.png", "data": PNG_URL}])
    second = save_images(tmp_path, [{"name": "b.png", "data": PNG_URL}])
    assert first[0]["path"] == second[0]["path"]
    assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_the_suffix_comes_from_the_bytes_not_the_filename(tmp_path):
    """A JPEG called logo.png must not be stored as .png and then served as
    image/png — the browser would fail to draw it and it would look like our bug."""
    saved = save_images(tmp_path, [{"name": "logo.png", "data": _data_url(JPEG)}])
    assert saved[0]["mime"] == "image/jpeg"
    assert saved[0]["path"].endswith(".jpg")


def test_what_is_not_an_image_is_refused_with_a_readable_reason(tmp_path):
    with pytest.raises(UploadError, match="不是图片数据"):
        save_images(tmp_path, [{"name": "x.txt", "data": "data:text/plain;base64,QQ=="}])
    with pytest.raises(UploadError, match="不是支持的图片格式"):
        save_images(tmp_path, [{"name": "x.png", "data": _data_url(b"not an image")}])
    with pytest.raises(UploadError, match="一次最多发 4 张"):
        save_images(tmp_path, [{"name": f"{i}.png", "data": PNG_URL} for i in range(5)])
    too_big = _data_url(PNG + b"\x00" * (4 * 1024 * 1024))
    with pytest.raises(UploadError, match="太大"):
        save_images(tmp_path, [{"name": "big.png", "data": too_big}])


def test_a_lookup_cannot_escape_the_uploads_folder(tmp_path):
    """The GET route hands the name straight to this function, so a name with a
    path in it is the whole attack."""
    (tmp_path / "state.db").write_bytes(b"secret")
    assert resolve(tmp_path, "state.db") is None          # not in uploads/
    assert resolve(tmp_path, "../state.db") is None
    assert resolve(tmp_path, "..\\state.db") is None
    assert resolve(tmp_path, "missing.png") is None


# --- the OpenAI bridge ------------------------------------------------------

def test_an_image_is_bridged_to_the_openai_shape():
    client = OpenAICompatClient.__new__(OpenAICompatClient)   # no network, no init
    kwargs = client._to_openai(
        model="m", max_tokens=10,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "这是什么"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "QUJD"}},
        ]}])
    assert kwargs["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "这是什么"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}]


def test_a_tool_result_turn_is_unchanged_by_the_image_support():
    """The regression that matters: adding a second kind of user block must not
    touch the turns that have no pictures. This is the exact output shape the
    loop relied on before images existed."""
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    kwargs = client._to_openai(
        model="m", max_tokens=10,
        messages=[{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "42"},
        ]}])
    assert kwargs["messages"] == [
        {"role": "tool", "tool_call_id": "tu_1", "content": "42"}]


# --- the hint that a model cannot see --------------------------------------

def test_a_refusal_that_names_images_is_explained():
    hint = image_hint("glm-5.2", Exception(
        "Error code: 400 - {'error': {'message': 'model does not support image input'}}"))
    assert "glm-5.2" in hint
    assert "不支持" in hint
    assert "model does not support image input" in hint   # the original survives


def test_a_timeout_is_not_blamed_on_the_model():
    """A guess that turns out to be wrong must not hide what happened — and
    sending the user off to change models for a network fault is worse than
    saying nothing."""
    for exc in (TimeoutError("read timed out"),
                RuntimeError("401 invalid api key")):
        assert image_hint("glm-5.2", exc) == str(exc)


# --- the hint that the reader of the message needs --------------------------
# image_hint above covers the model that REFUSES a picture. The measured case is
# worse: a gateway that accepts the request, drops the image and answers 200 —
# the same turn billed 19 prompt tokens on mimo-v2.6-flash and 4231 on glm-5.2.
# Nothing the server can see, so the one defence is a sentence next to the
# picture. Same node harness as test_reader_frontend (no build step, no runner).

RENDER_JS = (Path(__file__).resolve().parents[2]
             / "knowme" / "ops" / "static" / "js" / "render.js")

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// ---- paintAttach(): the strip, and the one sentence that goes with it -------
(function testPaintAttach() {
  const slice = src.slice(src.indexOf("function attThumbs("),
                          src.indexOf("function wireComposer("));
  if (!slice) { assert(false, "render.js no longer has the attachment section"); return; }
  const box = { hidden: null, innerHTML: "" };
  const document = { getElementById: id => (id === "datt" ? box : null) };
  const esc = s => String(s === undefined || s === null ? "" : s);
  const alert = () => {};
  // dropAttach() looks the target up by key, so these are the real targets here.
  const MAIN_CHAT = { key: "d", attach: [] };
  const ASK_CHAT = { key: "a", attach: [] };
  eval(slice);

  paintAttach(MAIN_CHAT);
  assert(box.hidden === true, "with nothing attached the strip is collapsed");
  assert(!/支持视觉/.test(box.innerHTML), "with nothing attached nothing is said about pictures");

  MAIN_CHAT.attach.push({ name: "shot.png", dataUrl: "data:image/png;base64,AAAA", bytes: 3 });
  paintAttach(MAIN_CHAT);
  assert(box.hidden === false, "the strip appears with a picture");
  assert(/<img src="data:image\/png;base64,AAAA"/.test(box.innerHTML),
         "the thumbnail is the local data URL");
  assert(/没收到图片/.test(box.innerHTML),
         "the hint names the symptom the user will actually see");
  assert(/支持视觉/.test(box.innerHTML), "the hint says what to do about it");
  assert(/这一轮/.test(box.innerHTML), "the hint says the picture is only for this turn");

  dropAttach("d", 0);
  assert(MAIN_CHAT.attach.length === 0 && box.hidden === true,
         "removing the last picture collapses the strip again");
  assert(!/支持视觉/.test(box.innerHTML), "and takes the hint with it");

  MAIN_CHAT.attach = [{ name: "a.png", dataUrl: "d1" }, { name: "b.png", dataUrl: "d2" }];
  paintAttach(MAIN_CHAT);
  assert(/2 张/.test(box.innerHTML), "two pictures are counted");
})();

// Without this the FAIL lines above print and node still exits 0 — the first
// version of this harness did exactly that, and a reverted render.js "passed".
process.exit(failures ? 1 : 0);
"""


def test_the_composer_says_what_a_picture_will_and_will_not_do(tmp_path):
    """Runs the real paintAttach() in node against a DOM stub: the hint is shown
    exactly while something is attached, and nowhere else."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    harness = tmp_path / "attach_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(RENDER_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)

    print(proc.stdout)
    assert proc.returncode == 0, f"frontend checks failed:\n{proc.stdout}\n{proc.stderr}"


# --- the turn itself --------------------------------------------------------

def _gate(retrieve=False):
    """The first model call of a turn is the retrieval gate — see
    test_knowme_facade, which scripts the same way."""
    from evals.helpers import response, text_block

    return response([text_block(json.dumps({"retrieve": retrieve, "query": "", "reason": "t"}))])


class _RecordingClient:
    """A scripted client that also keeps what it was asked — the only way to
    assert a claim about what the MODEL received rather than what we stored."""

    def __init__(self, script):
        from evals.helpers import ScriptedClient

        self._inner = ScriptedClient(script)
        self.messages = self
        self.calls: list[dict] = []

    def create(self, **kwargs):
        # Shallow-copy the message list: the loop keeps appending to the list it
        # passed, so a stored reference would show the turn AFTER the fact and
        # the test would assert about a request that was never sent.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._inner._create(**kwargs)


def test_the_picture_rides_on_the_user_message_and_never_enters_history(tmp_path):
    from evals.helpers import make_knowme, response, text_block

    client = _RecordingClient([_gate(), response([text_block("红色的")])])
    knowme = make_knowme(tmp_path, client=client)
    saved = save_images(tmp_path, [{"name": "red.png", "data": PNG_URL}])
    knowme.respond("这是什么颜色", images=saved)

    # The model got it, on the last user message, in Anthropic's block shape.
    sent = client.calls[-1]["messages"][-1]
    assert sent["role"] == "user"
    assert sent["content"][-1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png",
                   "data": saved[0]["data"]},
    }
    assert "这是什么颜色" in sent["content"][0]["text"]

    # History is text only — the invariant snip/compaction depend on.
    assert all(isinstance(m["content"], str) for m in knowme.session.history)
    assert [m["content"] for m in knowme.session.history if m["role"] == "user"] == [
        "这是什么颜色"]

    # ...and the chat log keeps where the picture is, not the picture.
    row = knowme.conn.execute(
        "SELECT meta FROM chat_log WHERE role='user' ORDER BY id DESC LIMIT 1").fetchone()
    meta = json.loads(row["meta"])
    assert meta["images"] == [{"name": "red.png", "url": saved[0]["url"],
                               "bytes": len(PNG)}]
    assert "data" not in row["meta"] and "base64" not in row["meta"]


def test_a_picture_with_no_words_still_records_a_title(tmp_path):
    """Only a screenshot, no text: the session list takes its title from the
    first user message, so it must not end up blank."""
    from evals.helpers import ScriptedClient, make_knowme, response, text_block

    knowme = make_knowme(tmp_path, client=ScriptedClient([_gate(), response([text_block("看到了")])]))
    saved = save_images(tmp_path, [{"name": "red.png", "data": PNG_URL}])
    knowme.respond("", images=saved)

    contents = [m["content"] for m in knowme.session.history if m["role"] == "user"]
    assert contents == ["[图片]"]
    assert knowme.memory.session_history(knowme.session.session_id)[0][0] == "[图片]"
