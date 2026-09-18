#!/usr/bin/env python3
"""
Generate a YouTube thumbnail image via the OpenAI Images API.

Usage:
    python3 tools/generate_thumbnail.py "Your prompt here" [--slug video-slug] [--model gpt-image-1]
    python3 tools/generate_thumbnail.py "Your prompt here" --images ref1.jpg ref2.png [--slug slug]

When --images are provided the edits endpoint is used and the reference photos
are passed as visual context alongside the prompt (useful for talking-head
thumbnails where the presenter photo should anchor the generated art).

Supported models:
    gpt-image-1    (default) — faster, cheaper, better text rendering
    gpt-image-1.5  — flagship quality
    gpt-image-2    — next-gen reasoning image model
    dall-e-3       — legacy, being deprecated May 2026

The image is saved to owners-inbox/thumbnails/<date>-<slug>.png
and the local file path is printed to stdout.
"""

import argparse
import base64
import io
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
THUMBNAILS_DIR = PKA_ROOT / "owners-inbox" / "thumbnails"

IMAGES_GENERATE_URL = "https://api.openai.com/v1/images/generations"
IMAGES_EDIT_URL = "https://api.openai.com/v1/images/edits"

# Size per model family:
#   gpt-image-1 / gpt-image-1.5: 1536x1024 is the best landscape (3:2) option
#   gpt-image-2: supports flexible sizes; 1792x1024 chosen for landscape 16:9
#   dall-e-3: 1792x1024 is the closest to 16:9
MODEL_SIZES = {
    "gpt-image-1": "1536x1024",
    "gpt-image-1.5": "1536x1024",
    "gpt-image-2": "1792x1024",
    "dall-e-3": "1792x1024",
}
DEFAULT_MODEL = "gpt-image-1"


def _get_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            api_key = subprocess.check_output(
                ["security", "find-generic-password", "-s", "OPENAI_API_KEY", "-w"],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set in the environment and could not be read from Keychain."
        )
    return api_key


def generate_image(prompt: str, model: str) -> bytes:
    """Call the OpenAI images/generations API and return raw PNG bytes."""
    api_key = _get_api_key()
    size = MODEL_SIZES.get(model, "1536x1024")

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }

    # DALL-E 3 returns a URL by default; request b64 explicitly for compatibility.
    # gpt-image-1 does not accept response_format — it returns b64_json by default.
    if model == "dall-e-3":
        payload["response_format"] = "b64_json"

    req = urllib.request.Request(
        IMAGES_GENERATE_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"OpenAI API error {e.code}: {body}") from e

    result = json.loads(resp.read())
    item = result["data"][0]

    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])

    # URL response (dall-e-3)
    image_url = item["url"]
    with urllib.request.urlopen(image_url) as r:
        return r.read()


def generate_image_with_references(prompt: str, model: str, images: list[Path]) -> bytes:
    """
    Call the OpenAI images/edits endpoint with one or more reference images.

    The reference images are passed as visual context; the model generates a
    new image that respects the prompt while drawing on the visual content of
    the references (faces, color palette, composition, etc.).
    """
    api_key = _get_api_key()
    size = MODEL_SIZES.get(model, "1536x1024")

    # Build multipart/form-data body manually (no third-party deps)
    boundary = "PKAThumbnailBoundary" + str(int(time.time() * 1000))

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    def _file_field(name: str, path: Path) -> bytes:
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        return header + path.read_bytes() + b"\r\n"

    buf = io.BytesIO()
    buf.write(_field("prompt", prompt))
    buf.write(_field("model", model))
    buf.write(_field("n", "1"))
    buf.write(_field("size", size))
    for img_path in images:
        buf.write(_file_field("image[]", img_path))
    buf.write(f"--{boundary}--\r\n".encode())
    body = buf.getvalue()

    req = urllib.request.Request(
        IMAGES_EDIT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    try:
        resp = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        raise RuntimeError(f"OpenAI API error {e.code}: {err_body}") from e

    result = json.loads(resp.read())
    item = result["data"][0]

    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])

    image_url = item["url"]
    with urllib.request.urlopen(image_url) as r:
        return r.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a YouTube thumbnail via the OpenAI Images API."
    )
    parser.add_argument("prompt", help="Text prompt describing the desired thumbnail")
    parser.add_argument(
        "--slug", default="thumbnail",
        help="Short slug used in the output filename (default: thumbnail)"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, choices=list(MODEL_SIZES.keys()),
        help=f"Image model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--images", nargs="*", metavar="PATH",
        help="One or more reference image file paths. When provided, the edits "
             "endpoint is used and the photos are passed as visual context."
    )
    args = parser.parse_args()

    slug = args.slug.strip().replace(" ", "-").lower()
    model = args.model
    prompt = args.prompt
    images: list[Path] = []

    if args.images:
        for p in args.images:
            img_path = Path(p)
            if not img_path.exists():
                print(f"Error: image not found: {img_path}", file=sys.stderr)
                sys.exit(1)
            images.append(img_path)

    today = date.today().isoformat()
    filename_png = f"{today}-{slug}.png"
    filename_jpg = f"{today}-{slug}.jpeg"
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    dest_png = THUMBNAILS_DIR / filename_png
    dest_jpg = THUMBNAILS_DIR / filename_jpg

    print(f"Model : {model}  Size: {MODEL_SIZES.get(model, '1536x1024')}", file=sys.stderr)

    if images:
        print(
            f"Reference images ({len(images)}): {', '.join(p.name for p in images)}",
            file=sys.stderr,
        )
        print("Generating thumbnail with reference images…", file=sys.stderr)
        image_bytes = generate_image_with_references(prompt, model, images)
    else:
        print(f"Generating thumbnail from prompt: {prompt!r}", file=sys.stderr)
        image_bytes = generate_image(prompt, model)

    print("Saving image…", file=sys.stderr)
    dest_png.write_bytes(image_bytes)

    # Convert PNG → JPEG using macOS sips (always available)
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(dest_png), "--out", str(dest_jpg)],
            check=True, capture_output=True,
        )
        dest = dest_jpg
    except (subprocess.CalledProcessError, FileNotFoundError):
        # sips unavailable or failed — fall back to PNG
        dest = dest_png

    # Path to stdout so callers (publish_to_youtube.py thumbnail command) can capture it
    print(str(dest))


if __name__ == "__main__":
    main()
