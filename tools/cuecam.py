#!/usr/bin/env python3
"""
CueCam Presenter CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/cuecam.py <command> [options]

Commands:
  create      Create a .cuecam bundle from a Google Doc
  compose     Create a .cuecam bundle from a mobile-style card spec
  from-file   Create a .cuecam bundle from a local markdown file
  list        List .cuecam bundles in owners-inbox/presentations/

Output: JSON to stdout. Errors to stderr.
"""

import argparse
import copy
import json
import os
import re
import sys
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import requests as http_requests

# Ensure tools/ is on sys.path so local modules (pka_index, llm, etc.) resolve from any cwd.
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from pka_index import index_cuecam_bundle

PKA_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "gdocs"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
PKA_FOLDER_FILE = CREDENTIALS_DIR / "pka_folder_id.txt"
CLIENT_SECRET_FILE = PKA_ROOT / "data" / "gcal" / "client_secret.json"
OUTPUT_DIR = PKA_ROOT / "owners-inbox" / "presentations"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

# John's default config from PKA.cuecam
DEFAULT_CONFIG = {
    "previewing": False,
    "showCardPreviews": True,
    "showingAudioControls": False,
    "showingWebcam": True,
    "slideConfig": {
        "backgroundColor": {
            "alpha": 1,
            "blue": 0.29411762952804565,
            "green": 0.4941176772117615,
            "red": 0.7960784435272217,
        },
        "backgroundStyle": "fullContentArea",
        "darkText": False,
        "fontName": "AppleGothic",
        "fontSize": 96,
        "hierarchyStepSize": 0.25,
        "hierarchyStyles": [{"scale": {}}],
        "name": "Clean",
        "padding": {"bottom": 20, "leading": 20, "top": 20, "trailing": 20},
        "spacing": 10,
        "transitionDuration": 0.5,
        "transitionStyle": {"slideEffect": {}},
    },
    "startingHour": None,
    "startingMinute": None,
}

THEME_ALIASES = {
    "clean": "Clean",
    "interleaved": "Interleaved",
    "cyber glass": "Cyber Glass Max",
    "cyber-glass": "Cyber Glass Max",
    "cyber glass max": "Cyber Glass Max",
    "cyber-glass-max": "Cyber Glass Max",
    "swiss": "Swiss Modernist Max",
    "swiss modernist": "Swiss Modernist Max",
    "swiss-modernist": "Swiss Modernist Max",
    "swiss max": "Swiss Modernist Max",
    "swiss-max": "Swiss Modernist Max",
    "warm editorial": "Warm Editorial",
    "warm-editorial": "Warm Editorial",
    "broadcast alpha": "Broadcast Alpha",
    "broadcast-alpha": "Broadcast Alpha",
    "sage workshop": "Sage Workshop",
    "sage-workshop": "Sage Workshop",
}

THEME_PRESETS = {
    "Cyber Glass Max": {
        "backgroundColor": {"alpha": 1, "red": 0.051, "green": 0.067, "blue": 0.090},
        "backgroundStyle": "fullContentArea",
        "darkText": False,
        "fontName": "HelveticaNeue-Bold",
        "fontSize": 112,
        "padding": {"bottom": 10, "leading": 10, "top": 10, "trailing": 10},
        "hierarchyStepSize": 0.20,
    },
    "Swiss Modernist Max": {
        "backgroundColor": {"alpha": 1, "red": 0.094, "green": 0.094, "blue": 0.106},
        "backgroundStyle": "fullContentArea",
        "darkText": False,
        "fontName": "AvenirNext-Heavy",
        "fontSize": 112,
        "padding": {"bottom": 10, "leading": 10, "top": 10, "trailing": 10},
        "hierarchyStepSize": 0.20,
    },
    "Warm Editorial": {
        "backgroundColor": {"alpha": 1, "red": 0.110, "green": 0.098, "blue": 0.090},
        "backgroundStyle": "fullContentArea",
        "darkText": False,
        "fontName": "Georgia-Bold",
        "fontSize": 112,
        "padding": {"bottom": 10, "leading": 10, "top": 10, "trailing": 10},
        "hierarchyStepSize": 0.20,
    },
    "Broadcast Alpha": {
        "backgroundColor": None,
        "backgroundStyle": "overlay",
        "darkText": False,
        "fontName": "SFProText-Bold",
        "fontSize": 108,
        "padding": {"bottom": 20, "leading": 20, "top": 20, "trailing": 20},
        "hierarchyStepSize": 0.25,
    },
    "Sage Workshop": {
        "backgroundColor": {"alpha": 1, "red": 0.102, "green": 0.180, "blue": 0.149},
        "backgroundStyle": "fullContentArea",
        "darkText": False,
        "fontName": "Futura-Medium",
        "fontSize": 112,
        "padding": {"bottom": 10, "leading": 10, "top": 10, "trailing": 10},
        "hierarchyStepSize": 0.20,
    },
}

COLOR_ALIASES = {
    "warm orange": {"alpha": 1, "red": 0.7960784435272217, "green": 0.4941176772117615, "blue": 0.29411762952804565},
    "orange": {"alpha": 1, "red": 0.7960784435272217, "green": 0.4941176772117615, "blue": 0.29411762952804565},
    "light yellow": {"alpha": 1, "red": 1.0, "green": 1.0, "blue": 0.8784313725490196},
    "yellow": {"alpha": 1, "red": 1.0, "green": 0.9215686274509803, "blue": 0.23137254901960785},
    "green": {"alpha": 1, "red": 0.3215686274509804, "green": 0.5294117647058824, "blue": 0.3215686274509804},
    "forest green": {"alpha": 1, "red": 0.19607843137254902, "green": 0.40784313725490196, "blue": 0.19607843137254902},
    "teal": {"alpha": 1, "red": 0.18823529411764706, "green": 0.5607843137254902, "blue": 0.5607843137254902},
    "blue": {"alpha": 1, "red": 0.2235294117647059, "green": 0.42745098039215684, "blue": 0.788235294117647},
    "purple": {"alpha": 1, "red": 0.4470588235294118, "green": 0.3176470588235294, "blue": 0.7019607843137254},
    "gray": {"alpha": 1, "red": 0.24313725490196078, "green": 0.2627450980392157, "blue": 0.3058823529411765},
    "charcoal": {"alpha": 1, "red": 0.1411764705882353, "green": 0.1568627450980392, "blue": 0.17647058823529413},
    "black": {"alpha": 1, "red": 0.0, "green": 0.0, "blue": 0.0},
    "white": {"alpha": 1, "red": 1.0, "green": 1.0, "blue": 1.0},
    "transparent": {"alpha": 0, "red": 0.0, "green": 0.0, "blue": 0.0},
    "clear": {"alpha": 0, "red": 0.0, "green": 0.0, "blue": 0.0},
    "none": {"alpha": 0, "red": 0.0, "green": 0.0, "blue": 0.0},
}

FONT_ALIASES = {
    "applegothic": "AppleGothic",
    "apple gothic": "AppleGothic",
    "futura": "Futura",
    "light futura": "Futura-Light",
    "futura light": "Futura-Light",
    "bold futura": "Futura-Bold",
    "futura bold": "Futura-Bold",
    "helvetica": "Helvetica",
    "helvetica neue": "HelveticaNeue",
    "helvetica neue bold": "HelveticaNeue-Bold",
    "helveticaneue bold": "HelveticaNeue-Bold",
    "helveticaneue-bold": "HelveticaNeue-Bold",
    "helvetica neue light": "HelveticaNeue-Light",
    "helveticaneue light": "HelveticaNeue-Light",
    "helveticaneue-light": "HelveticaNeue-Light",
    "gill sans": "Gill Sans",
    "sf mono": "SF Mono",
    "comfortaa bold": "Comfortaa-Bold",
    "american typewriter": "American Typewriter",
}

CARD_INDEX_PATTERN = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|i|ii|iii|iv|v|vi|vii|viii|ix|x)"

SCRIPT_TEMPLATE = {
    "info": {"description": "Generated by PKA"},
    "keyCommandOptions": [f"\u2318{i}" for i in range(1, 10)],
    "knownSourceNames": [],
    "productionSoftware": "ecammLive",
}

LAYOUT_ALIASES = {
    "left": "left",
    "right": "right",
    "center": "center",
    "centered": "center",
    "centred": "center",
    "middle": "center",
    "lower third": "lower-third",
    "lower-third": "lower-third",
    "lowerthird": "lower-third",
}

IMAGE_MODE_ALIASES = {
    "inline": "inline",
    "background": "background",
    "bg": "background",
}

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".mpg", ".mpeg"}


def _new_card_id():
    return str(uuid.uuid4()).upper()


def _truncate_words(text, n=6):
    """Truncate text to n words, appending ellipsis if truncated."""
    words = text.split()
    if len(words) <= n:
        return text
    return " ".join(words[:n]) + "…"


def _make_card(
    text,
    layout="lower-third",
    bg_image=None,
    bg_mode="fit",
    inline_image=None,
    media_item=None,
    picture_in_picture=True,
    slide_design=None,
):
    """Create a CueCam card dict."""
    card = {
        "demoMode": False,
        "ecammSceneTitle": None,
        "ecammSceneUUID": None,
        "graphics": [],
        "id": _new_card_id(),
        "keyCommand": None,
        "obsSceneName": None,
        "pictureInPicture": picture_in_picture,
        "slide": {
            "animatesBullets": True,
            "backgroundContentMode": None,
            "backgroundImageFilename": bg_image,
            "backgroundMode": bg_mode if bg_image else None,
            "design": slide_design,
            "flexBasis": {"auto": {}},
            "imageFilename": inline_image,
            "layout": layout,
            "mediaURL": None,
            "text": None,
            "version": 3,
        },
        "text": text,
        "version": 2,
    }
    if media_item:
        card["slide"]["mediaItem"] = media_item
    return card


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def _normalize_layout(value: Optional[str], default: str = "lower-third") -> str:
    if not value:
        return default
    normalized = value.strip().lower().replace("_", " ")
    normalized = normalized.replace("-", " ")
    normalized = _normalize_whitespace(normalized)
    return LAYOUT_ALIASES.get(normalized, default)


def _normalize_image_mode(value: Optional[str], default: str = "background") -> str:
    if not value:
        return default
    normalized = value.strip().lower().replace("_", " ")
    normalized = normalized.replace("-", " ")
    normalized = _normalize_whitespace(normalized)
    return IMAGE_MODE_ALIASES.get(normalized, default)


def _normalize_font_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().replace("_", " ")
    normalized = normalized.replace("-", " ")
    normalized = _normalize_whitespace(normalized.lower())
    alias = FONT_ALIASES.get(normalized)
    if alias:
        return alias
    raw = value.strip()
    return raw or None


def _normalize_theme_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = _normalize_whitespace(value.strip().lower().replace("_", " ").replace("-", " "))
    return THEME_ALIASES.get(normalized, value.strip())


def _normalize_color_value(value: Optional[str]) -> Optional[dict]:
    if not value:
        return None
    candidate = value.strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{8}", candidate):
        hex_value = candidate.lstrip("#")
        return {
            "alpha": int(hex_value[6:8], 16) / 255,
            "red": int(hex_value[0:2], 16) / 255,
            "green": int(hex_value[2:4], 16) / 255,
            "blue": int(hex_value[4:6], 16) / 255,
        }
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", candidate):
        hex_value = candidate.lstrip("#")
        return {
            "alpha": 1,
            "red": int(hex_value[0:2], 16) / 255,
            "green": int(hex_value[2:4], 16) / 255,
            "blue": int(hex_value[4:6], 16) / 255,
        }
    normalized = _normalize_whitespace(candidate.lower().replace("_", " ").replace("-", " "))
    color = COLOR_ALIASES.get(normalized)
    if color:
        return dict(color)
    return None


def _extract_pip_setting(text: str) -> Optional[bool]:
    lowered = f" {_normalize_whitespace(text.lower().replace('_', ' ').replace('-', ' '))} "

    off_keywords = (
        " pip off ",
        " picture in picture off ",
        " without picture in picture ",
        " no picture in picture ",
        " picture in picture disabled ",
    )
    if any(keyword in lowered for keyword in off_keywords):
        return False

    on_keywords = (
        " pip on ",
        " picture in picture on ",
        " with picture in picture ",
        " picture in picture enabled ",
    )
    if any(keyword in lowered for keyword in on_keywords):
        return True

    return None


def _extract_font_size_setting(text: str) -> Optional[float]:
    patterns = (
        r"\bfont\s+size\s+(\d+(?:\.\d+)?)\b",
        r"\bfont\s+(\d+(?:\.\d+)?)\b",
        r"\b(\d+(?:\.\d+)?)\s*pt\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _extract_card_settings(text: str) -> tuple[Optional[str], Optional[str], Optional[bool], Optional[float]]:
    lowered = f" {_normalize_whitespace(text.lower().replace('_', ' ').replace('-', ' '))} "
    layout = None
    image_mode = None
    picture_in_picture = _extract_pip_setting(text)
    font_size = _extract_font_size_setting(text)

    for keyword in ("lower third", "centered", "centred", "center", "middle", "right", "left"):
        if f" {keyword} " in lowered:
            layout = _normalize_layout(keyword)
            break

    for keyword in ("background", "inline"):
        if f" {keyword} " in lowered:
            image_mode = _normalize_image_mode(keyword)
            break

    return layout, image_mode, picture_in_picture, font_size


def _parse_design_line(plain: str) -> dict:
    directives: dict = {}
    lowered = plain.lower()

    theme_match = re.search(r"\btheme\s*:\s*([a-z0-9 _-]+)\b", plain, flags=re.IGNORECASE)
    if theme_match:
        directives["theme_name"] = _normalize_theme_name(theme_match.group(1))
    else:
        for alias in THEME_ALIASES:
            if re.search(rf"\b{re.escape(alias)}\s+theme\b", lowered):
                directives["theme_name"] = THEME_ALIASES[alias]
                break

    background_patterns = (
        r"\bbackground\s*:\s*([#a-z0-9 _-]+)\b",
        r"\bwith\s+(?:a|an)\s+([#a-z0-9 _-]+?)\s+background\b",
        r"\bbackground\s+should\s+be\s+([#a-z0-9 _-]+)\b",
    )
    for pattern in background_patterns:
        match = re.search(pattern, plain, flags=re.IGNORECASE)
        if not match:
            continue
        color = _normalize_color_value(match.group(1))
        if color:
            directives["background_color"] = color
            break

    font_patterns = (
        r"\bfont\s*:\s*([A-Za-z0-9 _-]+)\b",
        r"\b(?:text\s+should\s+be\s+in|use)\s+([A-Za-z0-9 _-]+?)\s+font\b",
    )
    for pattern in font_patterns:
        match = re.search(pattern, plain, flags=re.IGNORECASE)
        if not match:
            continue
        font_name = _normalize_font_name(match.group(1))
        if font_name:
            directives["font_name"] = font_name
            break

    if re.search(r"\blight text\b", lowered):
        directives["dark_text"] = False
    elif re.search(r"\bdark text\b", lowered):
        directives["dark_text"] = True

    pip = _extract_pip_setting(plain)
    if pip is not None and re.search(r"\b(?:pip|picture(?:\s+|-)?in(?:\s+|-)?picture)\b", lowered):
        directives["default_pip"] = pip

    return directives


def _compose_config(overrides: dict) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    slide_config = config["slideConfig"]
    if overrides.get("theme_name"):
        theme_name = overrides["theme_name"]
        slide_config["name"] = theme_name
        if theme_name in THEME_PRESETS:
            preset = THEME_PRESETS[theme_name]
            for key, val in preset.items():
                slide_config[key] = val
    if overrides.get("background_color"):
        slide_config["backgroundColor"] = overrides["background_color"]
    if overrides.get("font_name"):
        slide_config["fontName"] = overrides["font_name"]
    if overrides.get("dark_text") is not None:
        slide_config["darkText"] = overrides["dark_text"]
    return config


def _card_design_override(config: dict, *, font_size: Optional[float] = None) -> Optional[dict]:
    if font_size is None:
        return None
    design = copy.deepcopy(config.get("slideConfig", {}))
    design["fontSize"] = font_size
    return design


def _summarize_config(config: dict) -> dict:
    slide_config = config.get("slideConfig", {})
    return {
        "theme": slide_config.get("name"),
        "font": slide_config.get("fontName"),
        "text": "dark" if slide_config.get("darkText") else "light",
        "backgroundColor": slide_config.get("backgroundColor"),
        "transitionDuration": slide_config.get("transitionDuration"),
    }


def _cuecam_text(
    headline: str,
    notes: str,
    image_mode: Optional[str] = None,
    image_filename: Optional[str] = None,
    media_kind: str = "image",
    card_kind: str = "standard",
) -> str:
    parts = []
    if media_kind == "video" and image_filename:
        parts.append(f"// {image_filename}")
    if card_kind == "quote":
        quote_text = headline.strip()
        quote_line = f"> {quote_text}".rstrip()
        if notes.strip():
            quote_line = f"{quote_line} ~ {notes.strip()}"
        parts.append(quote_line)
    elif headline.strip():
        if media_kind == "video":
            parts.append(f"#{headline.strip()}")
        else:
            parts.append(f"# {headline.strip()}")
    elif notes.strip():
        # Blank headline keeps this as a teleprompter-only cue card.
        parts.append("# ")
    if notes.strip() and card_kind != "quote":
        parts.append(notes.strip())
    if image_filename and media_kind == "image":
        mode_label = "Inline-" if image_mode == "inline" else "Background-"
        parts.append(mode_label)
        parts.append(f"! {image_filename}")
    return "\n".join(parts).strip()


def _copy_compose_asset(path: Path, used_names: set[str]) -> tuple[str, bytes]:
    safe_name = re.sub(r"\s+", "_", path.name)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix or path.suffix or ".jpg"
    filename = safe_name
    counter = 2
    while filename in used_names:
        filename = f"{stem}-{counter}{suffix}"
        counter += 1
    used_names.add(filename)
    return filename, path.read_bytes()


def _asset_kind(path: Path) -> str:
    return "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image"


def _movie_media_item(filename: str, original_url: Optional[str] = None) -> dict:
    item = {
        "currentPlaybackTime": None,
        "filename": filename,
        "followAction": "pause",
        "id": _new_card_id(),
        "loop": False,
        "mute": False,
        "opacity": 1,
        "originalURL": original_url,
        "playbackRate": 1,
        "shouldMuteMicrophone": False,
        "startTime": {
            "epoch": 0,
            "flags": 1,
            "timescale": 1,
            "value": 0,
        },
        "startsPaused": False,
        "type": {
            "constantIndex": 84,
            "identifier": "com.apple.quicktime-movie",
        },
        "volume": 0.7,
    }
    return item


def _parse_card_line(
    raw_line: str,
    default_layout: str,
    default_image_mode: str,
    default_picture_in_picture: bool,
) -> Optional[dict]:
    line = raw_line.strip()
    if not line:
        return None

    line = line.replace("—", "-").replace("–", "-")
    line = re.sub(
        rf"^(?:for\s+)?(?:[-*]\s*)?(?:card\s*)?{CARD_INDEX_PATTERN}\s*[\.\),:-]?\s*",
        "",
        line,
        flags=re.IGNORECASE,
    )

    teleprompter_match = re.match(
        r"^(?P<prefix>.*?)(?:teleprompter|notes-only|teleprompter-only|read)\s*:\s*(?P<notes>.+)$",
        line,
        flags=re.IGNORECASE,
    )
    if teleprompter_match:
        prefix = teleprompter_match.group("prefix").strip(" -:")
        layout, image_mode, picture_in_picture, font_size = _extract_card_settings(prefix)
        notes = teleprompter_match.group("notes").strip()
        if not notes:
            return None
        return {
            "headline": "",
            "notes": notes,
            "layout": layout or default_layout,
            "image_mode": image_mode or default_image_mode,
            "picture_in_picture": default_picture_in_picture if picture_in_picture is None else picture_in_picture,
            "font_size": font_size,
            "text_only": True,
        }

    # movie: form — like teleprompter: but consumes the next asset as a video (movie card, no headline)
    movie_match = re.match(
        r"^(?P<prefix>.*?)movie\s*:\s*(?P<notes>.+)$",
        line,
        flags=re.IGNORECASE,
    )
    if movie_match:
        prefix = movie_match.group("prefix").strip(" -:")
        layout, image_mode, picture_in_picture, font_size = _extract_card_settings(prefix)
        notes = movie_match.group("notes").strip()
        if not notes:
            return None
        return {
            "headline": "",
            "notes": notes,
            "layout": layout or default_layout,
            "image_mode": image_mode or default_image_mode,
            "picture_in_picture": default_picture_in_picture if picture_in_picture is None else picture_in_picture,
            "font_size": font_size,
            "text_only": False,
        }

    # image: form — like movie: but consumes the next asset as a still (image card, no headline)
    image_match = re.match(
        r"^(?P<prefix>.*?)image\s*:\s*(?P<notes>.+)$",
        line,
        flags=re.IGNORECASE,
    )
    if image_match:
        prefix = image_match.group("prefix").strip(" -:")
        layout, image_mode, picture_in_picture, font_size = _extract_card_settings(prefix)
        notes = image_match.group("notes").strip()
        if not notes:
            return None
        return {
            "headline": "",
            "notes": notes,
            "layout": layout or default_layout,
            "image_mode": image_mode or default_image_mode,
            "picture_in_picture": default_picture_in_picture if picture_in_picture is None else picture_in_picture,
            "font_size": font_size,
            "text_only": False,
        }

    quote_match = re.search(r'[""](.+?)[""]', line)
    if not quote_match:
        return None

    headline = quote_match.group(1).strip()
    if not headline:
        return None

    before_quote = line[:quote_match.start()].strip(" -:")
    after_quote = line[quote_match.end():].strip()
    lowered_before_quote = before_quote.lower()
    card_kind = "quote" if re.search(r"\bquote\b", lowered_before_quote) else "standard"

    layout, image_mode, picture_in_picture, font_size = _extract_card_settings(before_quote)
    notes = ""

    if after_quote.startswith(","):
        notes = after_quote[1:].strip()
    elif after_quote:
        directive_tail, sep, remainder = after_quote.partition(",")
        tail_layout, tail_image_mode, tail_picture_in_picture, tail_font_size = _extract_card_settings(directive_tail)
        layout = layout or tail_layout
        image_mode = image_mode or tail_image_mode
        if picture_in_picture is None:
            picture_in_picture = tail_picture_in_picture
        if font_size is None:
            font_size = tail_font_size
        if sep:
            notes = remainder.strip()
        elif not directive_tail.strip():
            notes = ""

    return {
        "headline": headline,
        "notes": notes,
        "layout": layout or default_layout,
        "image_mode": image_mode or default_image_mode,
        "picture_in_picture": default_picture_in_picture if picture_in_picture is None else picture_in_picture,
        "font_size": font_size,
        "card_kind": card_kind,
        "text_only": False,
    }


def _looks_like_card_line(text: str) -> bool:
    stripped = text.strip().replace("—", "-").replace("–", "-")
    if not stripped:
        return False
    return bool(
        re.match(
            rf"^(?:for\s+)?(?:[-*]\s*)?(?:card\s*)?{CARD_INDEX_PATTERN}\s*[\.\),:-]?\s*",
            stripped,
            flags=re.IGNORECASE,
        )
    )


def _parse_default_line(plain: str) -> tuple[Optional[str], Optional[str], Optional[bool]]:
    """Parse natural-language default settings like 'Left layout'."""
    layout_patterns = (
        r"^(?:default\s+)?layout\s*:\s*(.+)$",
        r"^(?:default\s+)?(.+?)\s+layout$",
    )
    for pattern in layout_patterns:
        match = re.match(pattern, plain, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _normalize_layout(match.group(1).strip(), default="")
        if candidate:
            return candidate, None, None

    image_patterns = (
        r"^(?:default\s+)?(?:images?|image mode)\s*:\s*(.+)$",
        r"^(?:default\s+)?(.+?)\s+images?$",
    )
    for pattern in image_patterns:
        match = re.match(pattern, plain, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _normalize_image_mode(match.group(1).strip(), default="")
        if candidate:
            return None, candidate, None

    pip_patterns = (
        r"^(?:default\s+)?(?:pip|picture(?:\s+|-)?in(?:\s+|-)?picture)\s*:\s*(.+)$",
        r"^(?:default\s+)?(?:pip|picture(?:\s+|-)?in(?:\s+|-)?picture)\s+(.+)$",
    )
    for pattern in pip_patterns:
        match = re.match(pattern, plain, flags=re.IGNORECASE)
        if not match:
            continue
        # The keyword is consumed by the pattern, so re-attach it: _extract_pip_setting
        # only recognizes on/off next to "pip" or "picture in picture".
        pip = _extract_pip_setting(f"pip {match.group(1).strip()}")
        if pip is not None:
            return None, None, pip

    return None, None, None


_PROSE_NORMALIZER_SYSTEM = """You convert loose presentation dictation into PKA's CueCam mobile-spec format.

Output ONLY the normalized spec. No commentary. No markdown fences. No leading or trailing prose.

Format reference:
- Optional design block first (one directive per line): theme: <name>, background: <color>, layout: left|center|right, font: <name>, light text, pip on, pip off.
- Blank line, then numbered cards: `N. "Headline", teleprompter notes`.
- Quote cards: `N. quote center pip off "Quote text", Attribution`.
- Movie/image cards consume attachments in order; do not invent attachment paths.

Example dictation:
Use a clean theme with a green background and left layout. The text should be in light Futura font. For Card One, the headline should be "My Day at the Farmers Market." The teleprompter for this card should say "There's lots of asparagus." Include the first image attached.

Example normalized spec:
theme: clean
background: green
layout: left
font: Futura-Light

1. "My Day at the Farmers Market.", There's lots of asparagus.

Rules:
- Preserve owner's exact headline wording in quotes.
- Keep teleprompter notes concise; do not embellish.
- If the owner is ambiguous about layout/theme/font, omit the directive rather than guess.
- If a card line is malformed in the dictation, leave it as the closest reasonable card and let the deterministic parser surface the error."""


def normalize_prose_to_spec(prose: str) -> str:
    """Use Grok (via tools/llm.py) to rewrite loose dictation into CueCam mobile spec.

    Validation happens downstream in parse_compose_spec(); a bad pass fails fast there.
    """
    import sys as _sys
    _tools_dir = str(Path(__file__).resolve().parent)
    if _tools_dir not in _sys.path:
        _sys.path.insert(0, _tools_dir)
    import llm  # type: ignore

    text = (prose or "").strip()
    if not text:
        raise ValueError("normalize_prose_to_spec: empty prose input")

    return llm.chat_text(
        text,
        system=_PROSE_NORMALIZER_SYSTEM,
        provider="xai",
        max_tokens=1024,
        label="cuecam_normalize",
    ).strip()


def parse_compose_spec(spec_text: str, asset_paths: list[Path]):
    """Parse a mobile-friendly CueCam spec plus attached media.

    Supported grammar:
      Defaults:
      - layout: left
      - images: inline
      - use attached media in order

      Cards:
      1. "Headline", teleprompter notes
      2. right background "Another Headline", more notes
      3. teleprompter: Something to read on camera
      4. image: Cue line for a titleless still card
    """
    lines = spec_text.splitlines()
    default_layout = "lower-third"
    default_image_mode = "background"
    default_picture_in_picture = True
    title = None
    card_specs = []
    warnings = []
    errors = []
    config_overrides: dict = {}

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        plain = re.sub(r"^[-*]\s*", "", stripped)
        lowered = plain.lower()

        title_match = re.match(r"^title\s*:\s*(.+)$", plain, flags=re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            continue

        parsed_layout, parsed_image_mode, parsed_pip = _parse_default_line(plain)
        if parsed_layout:
            default_layout = parsed_layout
            continue
        if parsed_image_mode:
            default_image_mode = parsed_image_mode
            continue
        if parsed_pip is not None:
            default_picture_in_picture = parsed_pip
            continue

        if lowered in {"cards:", "defaults:"}:
            continue

        looks_like_card = _looks_like_card_line(stripped)

        if not looks_like_card:
            design_directives = _parse_design_line(plain)
            hinted_layout, hinted_image_mode, hinted_pip, _hinted_font_size = _extract_card_settings(plain)
            if hinted_layout:
                default_layout = hinted_layout
            if hinted_image_mode:
                default_image_mode = hinted_image_mode
            if hinted_pip is not None:
                default_picture_in_picture = hinted_pip
            if design_directives:
                config_overrides.update(design_directives)
                if design_directives.get("default_pip") is not None:
                    default_picture_in_picture = design_directives["default_pip"]
                continue

        card = _parse_card_line(
            stripped,
            default_layout=default_layout,
            default_image_mode=default_image_mode,
            default_picture_in_picture=default_picture_in_picture,
        )
        if card:
            card_specs.append(card)
            continue

        if looks_like_card:
            errors.append(
                f"Could not parse card line: {stripped}. "
                'Use the form: 2. "Headline", teleprompter notes, or 2. teleprompter: something to read, '
                'or 2. image: cue line for a titleless still card'
            )

    if not card_specs:
        raise ValueError(
            'No card lines found. Use lines like: 1. "Headline", teleprompter notes, '
            'or 2. teleprompter: something to read, or 3. image: cue line for a titleless still card'
        )

    if not title:
        title = card_specs[0]["headline"]

    config = _compose_config(config_overrides)
    assets = []
    cards = []
    used_names = set()

    for index, card_spec in enumerate(card_specs, start=1):
        asset_filename = None
        asset_kind = None
        media_item = None
        if not card_spec.get("text_only") and index <= len(asset_paths):
            asset_path = asset_paths[index - 1]
            asset_kind = _asset_kind(asset_path)
            asset_filename, asset_data = _copy_compose_asset(asset_path, used_names)
            assets.append((asset_filename, asset_data))
        elif not card_spec.get("text_only"):
            warnings.append(f"Card {index} has no matching attachment.")

        layout = _normalize_layout(card_spec["layout"], default_layout)
        image_mode = _normalize_image_mode(card_spec["image_mode"], default_image_mode)
        card_text = _cuecam_text(
            card_spec["headline"],
            card_spec["notes"],
            image_mode=image_mode if asset_filename and asset_kind == "image" else None,
            image_filename=asset_filename,
            media_kind=asset_kind,
            card_kind=card_spec.get("card_kind", "standard"),
        )
        slide_design = _card_design_override(config, font_size=card_spec.get("font_size"))

        if asset_filename and asset_kind == "video":
            media_item = _movie_media_item(asset_filename)

        bg_img = asset_filename if (asset_filename and asset_kind == "image" and image_mode == "background") else None
        inline_img = asset_filename if (asset_filename and asset_kind == "image" and image_mode != "background") else None

        cards.append(
            _make_card(
                text=card_text,
                layout=layout,
                bg_image=bg_img,
                inline_image=inline_img,
                media_item=media_item,
                picture_in_picture=card_spec["picture_in_picture"],
                slide_design=slide_design,
            )
        )

        card_spec["index"] = index
        card_spec["image"] = asset_filename
        card_spec["asset_kind"] = asset_kind
        card_spec["layout"] = layout
        card_spec["image_mode"] = image_mode
        card_spec["font_size"] = card_spec.get("font_size")

    if len(asset_paths) > len(card_specs):
        warnings.append(
            f"{len(asset_paths) - len(card_specs)} extra attachment(s) were not used."
        )

    return title, card_specs, cards, assets, warnings, errors, config


# ── Auth / Services ─────────────────────────────────────────────────

def _get_creds():
    """Load or refresh credentials."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                print("Token expired. Run: tools/gdocs.py auth", file=sys.stderr)
                sys.exit(1)
            TOKEN_FILE.write_text(creds.to_json())
        else:
            print("Not authenticated. Run: tools/gdocs.py auth", file=sys.stderr)
            sys.exit(1)
    return creds


def get_docs_service():
    return build("docs", "v1", credentials=_get_creds())


def get_drive_service():
    return build("drive", "v3", credentials=_get_creds())


def get_pka_folder_id():
    if PKA_FOLDER_FILE.exists():
        return PKA_FOLDER_FILE.read_text().strip() or None
    return None


# ── Google Doc Parsing ──────────────────────────────────────────────

def _download_image(content_uri, creds):
    """Download an image from a Google Docs content URI."""
    resp = http_requests.get(
        content_uri,
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    resp.raise_for_status()
    # Determine extension from content type
    ct = resp.headers.get("Content-Type", "image/png")
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/heic": ".heic",
    }
    ext = ext_map.get(ct.split(";")[0].strip(), ".png")
    return resp.content, ext


def parse_google_doc(doc_id):
    """Parse a Google Doc into CueCam cards and images.

    Returns (cards, images) where images is [(filename, bytes), ...].
    """
    creds = _get_creds()
    docs = build("docs", "v1", credentials=creds)

    doc = docs.documents().get(documentId=doc_id).execute()
    title = doc.get("title", "Untitled")
    body_content = doc.get("body", {}).get("content", [])
    inline_objects = doc.get("inlineObjects", {})

    cards = []
    images = []
    image_counter = [0]

    # Current card state
    current_lines = []
    current_layout = "lower-third"
    current_bg_image = None
    current_has_text = False
    current_bullet_count = 0

    def _flush_card():
        nonlocal current_lines, current_layout, current_bg_image, current_has_text, current_bullet_count
        text = "\n".join(current_lines).strip()
        if text or current_bg_image:
            cards.append(_make_card(
                text=text,
                layout=current_layout,
                bg_image=current_bg_image,
            ))
        current_lines = []
        current_layout = "lower-third"
        current_bg_image = None
        current_has_text = False
        current_bullet_count = 0

    def _get_image(obj_id):
        """Download an inline object image and return its filename."""
        obj = inline_objects.get(obj_id, {})
        embedded = obj.get("inlineObjectProperties", {}).get("embeddedObject", {})
        content_uri = embedded.get("imageProperties", {}).get("contentUri")
        if not content_uri:
            return None
        image_counter[0] += 1
        img_data, ext = _download_image(content_uri, creds)
        filename = f"image-{image_counter[0]:02d}{ext}"
        images.append((filename, img_data))
        return filename

    for element in body_content:
        if "sectionBreak" in element:
            continue

        if "paragraph" not in element:
            continue

        para = element["paragraph"]
        style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        bullet = para.get("bullet")

        # Collect text and images from this paragraph
        para_text_parts = []
        para_images = []

        for elem in para.get("elements", []):
            if "inlineObjectElement" in elem:
                obj_id = elem["inlineObjectElement"]["inlineObjectId"]
                filename = _get_image(obj_id)
                if filename:
                    para_images.append(filename)
            elif "textRun" in elem:
                text = elem["textRun"]["content"].rstrip("\n")
                if text.strip():
                    para_text_parts.append(text)

        para_text = " ".join(para_text_parts).strip()

        # HEADING_1, HEADING_2, HEADING_3 each start a new card
        if style in ("HEADING_1", "HEADING_2", "HEADING_3"):
            _flush_card()
            current_lines.append(f"# {_truncate_words(para_text)}")
            if para_text:
                current_lines.append(_truncate_words(para_text, 15))  # teleprompter line
            current_layout = "left"
            current_has_text = True
            for img in para_images:
                current_lines.append(f"! {img}")

        elif bullet:
            if para_text and current_bullet_count < 5:
                current_lines.append(f"* {_truncate_words(para_text)}")
                current_lines.append(_truncate_words(para_text, 15))  # teleprompter line
                current_has_text = True
                current_bullet_count += 1
            for img in para_images:
                current_lines.append(f"! {img}")

        else:
            # Normal text
            if para_text:
                current_lines.append(para_text)
                current_has_text = True

            # Handle images: if this paragraph is image-only (no text in card yet),
            # treat as background; otherwise inline
            for img in para_images:
                if not current_has_text and not current_bg_image:
                    current_bg_image = img
                else:
                    current_lines.append(f"! {img}")

    _flush_card()
    return title, cards, images


# ── Markdown File Parsing ───────────────────────────────────────────

def parse_markdown_file(filepath):
    """Parse a local markdown file into CueCam cards and images.

    Returns (title, cards, images).
    """
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")
    images = []

    # Extract title from first H1 or filename
    title = path.stem

    cards = []
    current_lines = []
    current_layout = "lower-third"
    current_bg_image = None
    current_bullet_count = 0

    def _flush():
        nonlocal current_lines, current_layout, current_bg_image, current_bullet_count
        text = "\n".join(current_lines).strip()
        if text or current_bg_image:
            cards.append(_make_card(
                text=text,
                layout=current_layout,
                bg_image=current_bg_image,
            ))
        current_lines = []
        current_layout = "lower-third"
        current_bg_image = None
        current_bullet_count = 0

    def _resolve_image(img_path):
        """Resolve a markdown image reference and add to images list."""
        img_file = Path(img_path)
        if not img_file.is_absolute():
            img_file = path.parent / img_file
        if img_file.exists():
            filename = img_file.name
            images.append((filename, img_file.read_bytes()))
            return filename
        return None

    for line in lines:
        stripped = line.strip()

        # H1 starts a new card
        h1_match = re.match(r"^#\s+(.+)", stripped)
        if h1_match and not stripped.startswith("##"):
            _flush()
            hl_text = h1_match.group(1)
            current_lines.append(f"# {_truncate_words(hl_text)}")
            current_lines.append(_truncate_words(hl_text, 15))  # teleprompter line
            current_layout = "left"
            continue

        # H2 also starts a new card
        h2_match = re.match(r"^##\s+(.+)", stripped)
        if h2_match and not stripped.startswith("###"):
            _flush()
            hl_text = h2_match.group(1)
            current_lines.append(f"# {_truncate_words(hl_text)}")
            current_lines.append(_truncate_words(hl_text, 15))  # teleprompter line
            current_layout = "left"
            continue

        # H3 also starts a new card
        h3_match = re.match(r"^###\s+(.+)", stripped)
        if h3_match:
            _flush()
            hl_text = h3_match.group(1)
            current_lines.append(f"# {_truncate_words(hl_text)}")
            current_lines.append(_truncate_words(hl_text, 15))  # teleprompter line
            current_layout = "left"
            continue

        # Bullet (max 5 per card)
        bullet_match = re.match(r"^[-*]\s+(.+)", stripped)
        if bullet_match:
            if current_bullet_count < 5:
                bullet_text = bullet_match.group(1)
                current_lines.append(f"* {_truncate_words(bullet_text)}")
                current_lines.append(_truncate_words(bullet_text, 15))  # teleprompter line
                current_bullet_count += 1
            continue

        # Markdown image ![alt](path)
        img_match = re.match(r"^!\[.*?\]\((.+?)\)", stripped)
        if img_match:
            filename = _resolve_image(img_match.group(1))
            if filename:
                if not current_lines and not current_bg_image:
                    current_bg_image = filename
                else:
                    current_lines.append(f"! {filename}")
            continue

        # Blockquote
        quote_match = re.match(r"^>\s+(.+)", stripped)
        if quote_match:
            current_lines.append(f"> {quote_match.group(1)}")
            continue

        # Regular text (skip empty lines between sections)
        if stripped:
            current_lines.append(stripped)

    _flush()

    # Use first H1 as title if found
    for card in cards:
        h1 = re.match(r"^#\s+(.+)", card["text"])
        if h1:
            title = h1.group(1)
            break

    return title, cards, images


# ── Bundle Building ─────────────────────────────────────────────────

def build_cuecam_bundle(title, cards, images, config=None):
    """Create a .cuecam directory bundle.

    Returns the path to the created bundle.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:50]
    base_name = f"{date.today().isoformat()}-{slug}"
    bundle_path = OUTPUT_DIR / f"{base_name}.cuecam"
    version = 2
    while bundle_path.exists():
        bundle_path = OUTPUT_DIR / f"{base_name}-v{version}.cuecam"
        version += 1

    bundle_path.mkdir(parents=True)
    media_dir = bundle_path / "Media"
    media_dir.mkdir()

    # Point any movie cards at the bundled media file for better portability.
    for card in cards:
        media_item = card.get("slide", {}).get("mediaItem")
        if not media_item:
            continue
        filename = media_item.get("filename")
        if not filename:
            continue
        media_item["originalURL"] = (media_dir / filename).resolve().as_uri()

    # Write Config.json
    cfg = config or DEFAULT_CONFIG
    (bundle_path / "Config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False)
    )

    # Write images to Media/
    for filename, data in images:
        (media_dir / filename).write_bytes(data)

    # Write Script.json
    script = dict(SCRIPT_TEMPLATE)
    script["cards"] = cards
    (bundle_path / "Script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False)
    )

    return bundle_path


def _index_bundle(bundle_path, *, title, cards, images=0, videos=0):
    try:
        return index_cuecam_bundle(
            bundle_path,
            title=title,
            cards=cards,
            images=images,
            videos=videos,
        )
    except Exception as exc:
        print(
            f"Warning: bundle created but could not be indexed ({exc})",
            file=sys.stderr,
        )
        return None


# ── Drive Upload ────────────────────────────────────────────────────

def upload_to_drive(bundle_path):
    """Zip the .cuecam bundle and upload to Google Drive PKA folder."""
    zip_path = bundle_path.with_suffix(".cuecam.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(bundle_path):
            for f in files:
                abs_path = Path(root) / f
                arc_name = str(abs_path.relative_to(bundle_path.parent))
                zf.write(abs_path, arc_name)

    drive = get_drive_service()
    folder_id = get_pka_folder_id()

    metadata = {"name": zip_path.name}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(zip_path), mimetype="application/zip")
    result = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    # Clean up zip
    zip_path.unlink()

    return {
        "file_id": result["id"],
        "name": result["name"],
        "url": result.get("webViewLink", f"https://drive.google.com/file/d/{result['id']}/view"),
    }


# ── Commands ────────────────────────────────────────────────────────

def cmd_create(args):
    """Create a .cuecam bundle from a Google Doc."""
    doc_id = args.doc_id

    # Extract doc ID from URL if needed
    url_match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", doc_id)
    if url_match:
        doc_id = url_match.group(1)

    print(f"Fetching Google Doc {doc_id}...", file=sys.stderr)
    title, cards, images = parse_google_doc(doc_id)

    if args.title:
        title = args.title

    bundle_path = build_cuecam_bundle(title, cards, images)
    index_result = _index_bundle(
        bundle_path,
        title=title,
        cards=len(cards),
        images=len(images),
        videos=0,
    )

    result = {
        "status": "created",
        "title": title,
        "path": str(bundle_path),
        "cards": len(cards),
        "images": len(images),
    }
    if index_result:
        result["knowledge_base_id"] = index_result["knowledge_base_id"]
        result["file_id"] = index_result["file_id"]

    if args.upload:
        print("Uploading to Google Drive...", file=sys.stderr)
        drive_result = upload_to_drive(bundle_path)
        result["drive"] = drive_result

    print(json.dumps(result, indent=2))


def cmd_from_file(args):
    """Create a .cuecam bundle from a local markdown file."""
    filepath = Path(args.file)
    if not filepath.is_absolute():
        filepath = PKA_ROOT / filepath

    if not filepath.exists():
        print(json.dumps({"error": f"File not found: {filepath}"}), file=sys.stderr)
        sys.exit(1)

    title, cards, images = parse_markdown_file(filepath)

    if args.title:
        title = args.title

    bundle_path = build_cuecam_bundle(title, cards, images)
    index_result = _index_bundle(
        bundle_path,
        title=title,
        cards=len(cards),
        images=len(images),
        videos=0,
    )

    result = {
        "status": "created",
        "title": title,
        "path": str(bundle_path),
        "cards": len(cards),
        "images": len(images),
    }
    if index_result:
        result["knowledge_base_id"] = index_result["knowledge_base_id"]
        result["file_id"] = index_result["file_id"]

    if args.upload:
        print("Uploading to Google Drive...", file=sys.stderr)
        drive_result = upload_to_drive(bundle_path)
        result["drive"] = drive_result

    print(json.dumps(result, indent=2))


def cmd_compose(args):
    """Create a .cuecam bundle from a mobile-style text spec and media."""
    raw_prose_path = getattr(args, "raw_prose", None)
    spec_file = getattr(args, "spec_file", None)

    if raw_prose_path and spec_file:
        print(json.dumps({"error": "Use either --raw-prose or --spec-file, not both."}), file=sys.stderr)
        sys.exit(2)
    if not raw_prose_path and not spec_file:
        print(json.dumps({"error": "Provide --raw-prose <file|-> or --spec-file <file>."}), file=sys.stderr)
        sys.exit(2)

    if raw_prose_path:
        # Read prose (stdin if "-"), normalize via Grok, persist sibling .normalized.spec for traceability.
        if raw_prose_path == "-":
            prose_text = sys.stdin.read()
            normalized_dir = PKA_ROOT / "data" / "tmp"
            normalized_dir.mkdir(parents=True, exist_ok=True)
            spec_path = normalized_dir / f"cuecam-prose-{date.today().isoformat()}-{uuid.uuid4().hex[:8]}.normalized.spec"
        else:
            prose_path = Path(raw_prose_path)
            if not prose_path.is_absolute():
                prose_path = PKA_ROOT / prose_path
            if not prose_path.exists():
                print(json.dumps({"error": f"Prose file not found: {prose_path}"}), file=sys.stderr)
                sys.exit(1)
            prose_text = prose_path.read_text(encoding="utf-8")
            spec_path = prose_path.with_suffix(prose_path.suffix + ".normalized.spec")

        try:
            normalized = normalize_prose_to_spec(prose_text)
        except Exception as exc:
            print(json.dumps({"error": f"Prose normalization failed: {exc}"}), file=sys.stderr)
            sys.exit(1)

        try:
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(normalized + ("\n" if not normalized.endswith("\n") else ""), encoding="utf-8")
        except Exception as exc:
            print(json.dumps({"error": f"Could not save normalized spec: {exc}"}), file=sys.stderr)
            sys.exit(1)
    else:
        spec_path = Path(spec_file)
        if not spec_path.is_absolute():
            spec_path = PKA_ROOT / spec_path

        if not spec_path.exists():
            print(json.dumps({"error": f"Spec file not found: {spec_path}"}), file=sys.stderr)
            sys.exit(1)

    asset_paths = []
    raw_assets = list(args.asset) + list(args.image)
    for raw_path in raw_assets:
        path = Path(raw_path)
        if not path.is_absolute():
            path = PKA_ROOT / path
        path = path.resolve()
        if not path.exists():
            print(json.dumps({"error": f"Attachment not found: {path}"}), file=sys.stderr)
            sys.exit(1)
        asset_paths.append(path)

    spec_text = spec_path.read_text(encoding="utf-8")
    title, card_specs, cards, assets, warnings, errors, config = parse_compose_spec(spec_text, asset_paths)

    if args.title:
        title = args.title

    if args.preview:
        print(
            json.dumps(
                {
                    "status": "preview",
                    "title": title,
                    "path": str(spec_path),
                    "cards": card_specs,
                    "design": _summarize_config(config),
                    "warnings": warnings,
                    "errors": errors,
                },
                indent=2,
            )
        )
        return

    if errors:
        payload = {
            "error": "CueCam compose spec contains malformed card lines.",
            "details": errors,
            "warnings": warnings,
            "path": str(spec_path),
        }
        if raw_prose_path:
            payload["normalized_from_prose"] = True
            payload["hint"] = "Inspect the normalized spec at the path above; edit it directly or refine the dictation."
        print(json.dumps(payload, indent=2), file=sys.stderr)
        sys.exit(1)

    bundle_path = build_cuecam_bundle(title, cards, assets, config=config)
    image_count = len([card for card in card_specs if card.get("asset_kind") == "image"])
    video_count = len([card for card in card_specs if card.get("asset_kind") == "video"])
    index_result = _index_bundle(
        bundle_path,
        title=title,
        cards=len(cards),
        images=image_count,
        videos=video_count,
    )
    result = {
        "status": "created",
        "title": title,
        "path": str(bundle_path),
        "cards": len(cards),
        "images": image_count,
        "videos": video_count,
        "assets": len(assets),
        "spec": str(spec_path),
        "design": _summarize_config(config),
        "warnings": warnings,
        "errors": errors,
    }
    if index_result:
        result["knowledge_base_id"] = index_result["knowledge_base_id"]
        result["file_id"] = index_result["file_id"]

    if args.upload:
        print("Uploading to Google Drive...", file=sys.stderr)
        drive_result = upload_to_drive(bundle_path)
        result["drive"] = drive_result

    print(json.dumps(result, indent=2))


def cmd_polish(args):
    """Create a single-movie-card polish-pass bundle from a rendered overlay video."""
    import shutil as _shutil

    source_deck = Path(args.source_deck)
    if not source_deck.is_absolute():
        source_deck = PKA_ROOT / source_deck
    source_deck = source_deck.resolve()
    if not source_deck.exists():
        print(json.dumps({"error": f"Source deck not found: {source_deck}"}), file=sys.stderr)
        sys.exit(1)

    rendered_video = Path(args.rendered_video)
    if not rendered_video.is_absolute():
        rendered_video = PKA_ROOT / rendered_video
    rendered_video = rendered_video.resolve()
    if not rendered_video.exists():
        print(json.dumps({"error": f"Rendered video not found: {rendered_video}"}), file=sys.stderr)
        sys.exit(1)

    source_config_path = source_deck / "Config.json"
    if source_config_path.exists():
        config = json.loads(source_config_path.read_text(encoding="utf-8"))
    else:
        config = copy.deepcopy(DEFAULT_CONFIG)

    source_stem = source_deck.stem  # e.g. "2026-04-26-bird-walk"
    bundle_path = OUTPUT_DIR / f"{source_stem}-polished.cuecam"
    version = 2
    while bundle_path.exists():
        bundle_path = OUTPUT_DIR / f"{source_stem}-polished-v{version}.cuecam"
        version += 1

    bundle_path.mkdir(parents=True)
    media_dir = bundle_path / "Media"
    media_dir.mkdir()

    video_filename = rendered_video.name
    dest_video = media_dir / video_filename
    _shutil.copy2(rendered_video, dest_video)

    media_item = _movie_media_item(video_filename, original_url=dest_video.resolve().as_uri())
    note_text = args.note or ""
    card = _make_card(
        text=note_text,
        layout="lower-third",
        media_item=media_item,
        picture_in_picture=True,
    )

    (bundle_path / "Config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False))

    script = dict(SCRIPT_TEMPLATE)
    script["cards"] = [card]
    (bundle_path / "Script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False))

    title = args.title or f"{source_stem} polished"
    index_result = _index_bundle(bundle_path, title=title, cards=1, images=0, videos=1)

    result = {
        "status": "created",
        "title": title,
        "path": str(bundle_path),
        "source_deck": str(source_deck),
        "rendered_video": str(rendered_video),
        "cards": 1,
        "videos": 1,
    }
    if index_result:
        result["knowledge_base_id"] = index_result["knowledge_base_id"]

    print(json.dumps(result, indent=2))


def cmd_list(args):
    """List .cuecam bundles in owners-inbox/presentations/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    bundles = []
    for item in sorted(OUTPUT_DIR.iterdir()):
        if item.suffix == ".cuecam" and item.is_dir():
            script_file = item / "Script.json"
            card_count = 0
            if script_file.exists():
                try:
                    data = json.loads(script_file.read_text())
                    card_count = len(data.get("cards", []))
                except Exception:
                    pass

            media_dir = item / "Media"
            image_count = len(list(media_dir.iterdir())) if media_dir.exists() else 0

            bundles.append({
                "name": item.name,
                "path": str(item),
                "cards": card_count,
                "images": image_count,
            })

    print(json.dumps({"presentations": bundles, "count": len(bundles)}, indent=2))


# ── CLI Setup ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CueCam Presenter CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # create
    p_create = sub.add_parser("create", help="Create from a Google Doc")
    p_create.add_argument("--doc-id", required=True, help="Google Doc ID or URL")
    p_create.add_argument("--title", help="Override presentation title")
    p_create.add_argument("--upload", action="store_true", help="Upload zip to Google Drive PKA folder")

    # from-file
    p_file = sub.add_parser("from-file", help="Create from a local markdown file")
    p_file.add_argument("--file", required=True, help="Path to .md file")
    p_file.add_argument("--title", help="Override presentation title")
    p_file.add_argument("--upload", action="store_true", help="Upload zip to Google Drive PKA folder")

    # compose
    p_compose = sub.add_parser("compose", help="Create from a mobile-style card spec")
    p_compose.add_argument("--spec-file", help="Path to a text file describing cards (mutually exclusive with --raw-prose)")
    p_compose.add_argument(
        "--raw-prose",
        help="Path to a loose-dictation prose file (or '-' for stdin); Grok normalizes to spec, then runs the deterministic parser.",
    )
    p_compose.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Card media path in attachment order; may be repeated",
    )
    p_compose.add_argument(
        "--image",
        action="append",
        default=[],
        help="Backwards-compatible alias for --asset",
    )
    p_compose.add_argument("--title", help="Override presentation title")
    p_compose.add_argument("--preview", action="store_true", help="Parse and preview cards without building")
    p_compose.add_argument("--upload", action="store_true", help="Upload zip to Google Drive PKA folder")

    # list
    sub.add_parser("list", help="List .cuecam bundles")

    # polish
    p_polish = sub.add_parser(
        "polish",
        help="Create a polish-pass single-movie-card bundle from a rendered overlay video",
    )
    p_polish.add_argument(
        "--source-deck", required=True,
        help="Path to the original .cuecam bundle (copies Config.json for deck settings)",
    )
    p_polish.add_argument(
        "--rendered-video", required=True,
        help="Path to the rendered overlay video to embed",
    )
    p_polish.add_argument("--title", help="Title for the polished bundle")
    p_polish.add_argument("--note", default="", help="Teleprompter note for the movie card")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": cmd_create,
        "compose": cmd_compose,
        "from-file": cmd_from_file,
        "list": cmd_list,
        "polish": cmd_polish,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
