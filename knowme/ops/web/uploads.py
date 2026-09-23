"""Images attached to one turn of a conversation.

The browser hands over data URLs (the same shape the Reader uploads), and this
module turns each one into TWO things at once:

  * a file under ``<home>/uploads/``, so the conversation can draw the picture
    again next time you open it — the database stores the reference, never the
    pixels;
  * base64 + media type, which is what the model call actually carries.

That pair is why the module exists: saving and re-reading the same image have
to agree about the name, the type and the size, and splitting them across two
files is how they stop agreeing.

The picture lives for ONE turn. It rides on the user message of the request
that carried it — see core/runtime.run_loop_turn — and is deliberately never
put into session.history: that list holds plain strings (snip_compact and the
compaction stages all treat entries as text), and the model simply does not get
the pixels back on later turns. The chat log keeps a reference to the saved
file, so it is still in the conversation and you can scroll back to it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path

# Same ceiling as the Reader's uploads: 4 MB is comfortably more than a phone
# screenshot and comfortably less than a camera RAW, and one number for "the
# biggest file this app will take" is easier to hold in your head than two.
MAX_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 4

# What we accept, and how we recognise it. The suffix is chosen from the BYTES,
# never from the filename the browser sent: a file called logo.png that is
# really a JPEG would otherwise be stored as .png and served to the browser as
# image/png, which fails to render and looks like our bug.
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)
WEBP_MIME = "image/webp"


class UploadError(ValueError):
    """The attachment is not something we can send to a model."""


def _sniff(raw: bytes) -> tuple[str, str] | None:
    """(suffix, mime) from the leading bytes, or None if we don't know it."""
    for magic, suffix, mime in _MAGIC:
        if raw.startswith(magic):
            return suffix, mime
    # WebP is a RIFF container: "RIFF" + 4 size bytes + "WEBP".
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp", WEBP_MIME
    return None


def _decode(name: str, data_url: str) -> bytes:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise UploadError(f"「{name}」不是图片数据")
    if "," not in data_url:
        raise UploadError(f"「{name}」的数据格式不对")
    encoded = data_url.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise UploadError(f"「{name}」不是有效的图片（base64 解码失败）") from exc


def save_images(home: Path, images) -> list[dict]:
    """Validate, write and describe each attached image.

    Returns one dict per image: name (what you called it), url (where the
    browser can read it back), path, bytes, mime and data (base64 for the model
    call). Raises UploadError with something a person can act on.
    """
    if not images:
        return []
    if not isinstance(images, list):
        raise UploadError("图片列表格式不对")
    if len(images) > MAX_IMAGES:
        raise UploadError(f"一次最多发 {MAX_IMAGES} 张图片")
    out_dir = home / "uploads"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for item in images:
        if not isinstance(item, dict):
            raise UploadError("图片格式不对")
        name = str(item.get("name") or "图片")
        raw = _decode(name, item.get("data"))
        if not raw:
            raise UploadError(f"「{name}」是空文件")
        if len(raw) > MAX_BYTES:
            raise UploadError(f"「{name}」太大（最多 {MAX_BYTES // 1024 // 1024} MB）")
        sniffed = _sniff(raw)
        if sniffed is None:
            raise UploadError(f"「{name}」不是支持的图片格式（支持 PNG / JPEG / GIF / WebP）")
        suffix, mime = sniffed
        # Content-addressed: sending the same screenshot twice reuses the file
        # instead of piling up copies in a folder the user never cleans.
        digest = hashlib.sha256(raw).hexdigest()[:16]
        path = out_dir / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(raw)
        saved.append({
            "name": name,
            "url": f"/api/uploads/{path.name}",
            "path": str(path),
            "bytes": len(raw),
            "mime": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        })
    return saved


def resolve(home: Path, name: str) -> Path | None:
    """The file behind a /api/uploads/<name> request, or None if it isn't ours.

    ``Path(name).name != name`` rejects anything with a slash or a backslash in
    it, so "../state.db" and "..\\state.db" both fail before touching the disk.
    """
    if not name or Path(name).name != name:
        return None
    path = home / "uploads" / name
    return path if path.is_file() else None


# A model that cannot see pictures says so in its own words, but the words are
# always one of these shapes — the request was REFUSED, and the refusal names
# the image. Anything else (a timeout, a 401, a dropped connection) is left
# alone on purpose: blaming a model for not supporting images when the network
# was down sends the user off to change models for no reason.
_REFUSAL_MARKERS = ("400", "invalid_request", "bad request", "invalid request")
_IMAGE_MARKERS = ("image", "vision", "multimodal", "unsupported")


def image_hint(model: str, exc: Exception) -> str:
    """Rewrite a failure from a turn that carried pictures.

    The user cannot tell "this model doesn't do images" from "something broke",
    and the provider's own message is usually about a parameter they never
    typed. So say the likely cause in plain words — and keep the original
    error attached, because a guess that turns out to be wrong must not hide
    what actually happened.
    """
    text = str(exc)
    lowered = text.lower()
    looks_refused = any(m in lowered for m in _REFUSAL_MARKERS)
    names_image = any(m in lowered for m in _IMAGE_MARKERS)
    if not (looks_refused and names_image):
        return text
    return (f"图片没能发出去 —— 当前模型 {model} 可能不支持看图。"
            f"换一个支持视觉的模型再试，或者把图里的内容用文字说一遍。\n"
            f"（原始错误：{text}）")
