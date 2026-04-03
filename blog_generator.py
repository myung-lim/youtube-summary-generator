import json
import os
import re
import time
import warnings
from datetime import datetime
import logging
import tempfile
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.",
)

from openai import OpenAI
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled
from youtube_transcript_api import YouTubeTranscriptApi


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
TRANSCRIPT_LIMIT = 12000
_explicit_output_dir = os.getenv("BLOG_OUTPUT_DIR")
if _explicit_output_dir:
    OUTPUT_DIR = _explicit_output_dir
else:
    OUTPUT_DIR = "/tmp/outputs" if os.getenv("VERCEL") else "outputs"

logger = logging.getLogger(__name__)


def extract_video_id(url):
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/")
        if re.fullmatch(r"[0-9A-Za-z_-]{11}", candidate):
            return candidate

    if "youtube.com" in host:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
            if re.fullmatch(r"[0-9A-Za-z_-]{11}", candidate):
                return candidate

        match = re.search(r"/(shorts|embed|live)/([0-9A-Za-z_-]{11})", parsed.path)
        if match:
            return match.group(2)

    direct_match = re.search(r"([0-9A-Za-z_-]{11})", url)
    if direct_match:
        return direct_match.group(1)

    raise ValueError("? íš¨??YouTube URL???…ë ¥??ì£¼ì„¸??")


def get_youtube_transcript(url):
    video_id = extract_video_id(url)

    try:
        transcript = _fetch_transcript_with_retry(video_id)
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        # Try yt-dlp as a fallback for videos where the transcript API fails.
        try:
            transcript = _fetch_transcript_with_ytdlp(video_id)\r\n        except Exception:\r\n            raise ValueError(\r\n                "ÀÌ ¿µ»óÀº ÀÚ¸·À» °¡Á®¿Ã ¼ö ¾ø½À´Ï´Ù. "\r\n                "YouTube¿¡¼­ ÀÚ¸·ÀÌ ºñÈ°¼ºÈ­µÇ¾î ÀÖ°Å³ª ·Î±×ÀÎ ÄíÅ°°¡ ÇÊ¿äÇÒ ¼ö ÀÖ½À´Ï´Ù. "\r\n                "ÄíÅ° ÆÄÀÏÀ» ÃÖ½ÅÀ¸·Î °»½ÅÇÏ°Å³ª ÀÚ¸·ÀÌ ÀÖ´Â ´Ù¸¥ ¿µ»óÀ» ½ÃµµÇØ ÁÖ¼¼¿ä."\r\n            ) from exc
    except Exception as exc:
        logger.exception("Transcript fetch failed. video_id=%s url=%s", video_id, url)
        raise ValueError(
            "?ë§‰??ê°€?¸ì˜¤??ê³¼ì •?ì„œ ?¤ë¥˜ê°€ ë°œìƒ?ˆìŠµ?ˆë‹¤. ?ìƒ??ê³µê°œ ?íƒœ?¸ì?, "
            "ì§€???œí•œ?´ë‚˜ ?°ë ¹ ?œí•œ???†ëŠ”ì§€, ?ë§‰???œì„±?”ë˜???ˆëŠ”ì§€ ?•ì¸??ì£¼ì„¸?? "
            "?¼ì‹œ?ì¸ YouTube ?‘ë‹µ ?¤ë¥˜?????ˆìœ¼??? ì‹œ ???¤ì‹œ ?œë„??ë³´ì„¸??"
        ) from exc

    text = " ".join(item["text"].strip() for item in transcript if item.get("text"))
    cleaned = _normalize_transcript(text)

    if not cleaned:
        logger.warning("Transcript empty after cleaning. video_id=%s", video_id)
        raise ValueError("?ìƒ ?ë§‰??ë¹„ì–´ ?ˆìŠµ?ˆë‹¤.")

    logger.info("Transcript fetched. video_id=%s length=%s", video_id, len(cleaned))
    return cleaned[:TRANSCRIPT_LIMIT]


def _get_cookies_path():
    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()
    if cookies_path and os.path.exists(cookies_path):
        return cookies_path
    # Fallback: use repo cookie file if present (useful on Vercel)
    default_path = "www.youtube.com_cookies.txt"
    if os.path.exists(default_path):
        return default_path
    return None


def _validate_cookies_path():
    path = _get_cookies_path()
    if not path:
        return None, "missing"
    try:
        if os.path.getsize(path) <= 0:
            return None, "empty"
    except OSError:
        return None, "unreadable"
    return path, None


def _fetch_transcript_once(video_id):
    cookies_path, cookie_issue = _validate_cookies_path()
    if cookie_issue:
        logger.warning("YouTube cookies not usable. issue=%s", cookie_issue)
    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, cookies=cookies_path)
    languages = ["ko", "en"]

    try:
        transcript = transcript_list.find_transcript(languages)
    except Exception:
        try:
            transcript = transcript_list.find_generated_transcript(languages)
        except Exception:
            transcript = next(iter(transcript_list), None)

    if not transcript:
        raise NoTranscriptFound(video_id)

    return transcript.fetch()


def _fetch_transcript_with_retry(video_id, attempts=3):
    last_exc = None
    for attempt in range(attempts):
        try:
            return _fetch_transcript_once(video_id)
        except Exception as exc:
            last_exc = exc
            message = str(exc).lower()
            if "no element found" in message or "xml" in message:
                sleep_time = 0.8 + attempt * 0.6
                time.sleep(sleep_time)
                continue
            raise
    if last_exc:
        try:
            return _fetch_transcript_with_ytdlp(video_id)
        except Exception:
            raise last_exc


def _fetch_transcript_with_ytdlp(video_id):
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlpê°€ ?¤ì¹˜?˜ì–´ ?ˆì? ?Šì•„ ?€ì²??ë§‰ ì¶”ì¶œ??ì§„í–‰?????†ìŠµ?ˆë‹¤."
        ) from exc

    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_path, cookie_issue = _validate_cookies_path()

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "subs.%(ext)s")
        preferred_langs = ["ko", "ko-KR", "en", "en-US"]
        forced_lang = os.getenv("YT_DLP_FORCE_LANG", "").strip()
        if forced_lang:
            preferred_langs = [forced_lang]

        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": preferred_langs,
            "subtitlesformat": "vtt",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
        }
        if cookies_path:
            ydl_opts["cookiefile"] = cookies_path

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            message = str(exc)
            if "Sign in to confirm you?™re not a bot" in message or "Sign in to confirm you're not a bot" in message:
                raise RuntimeError(
                    "YouTubeê°€ ë´??•ì¸???”êµ¬?˜ê³  ?ˆìŠµ?ˆë‹¤. ë¡œê·¸??ì¿ í‚¤ê°€ ?„ìš”?©ë‹ˆ?? "
                    "ì¿ í‚¤ ?Œì¼??ìµœì‹ ?¼ë¡œ ê°±ì‹ ?˜ê³  YOUTUBE_COOKIES_PATHë¥??¤ì •??ì£¼ì„¸??"
                ) from exc
            if "cookie" in message.lower():
                raise RuntimeError(
                    "YouTube ì¿ í‚¤ê°€ ?„ìš”?˜ê±°??ë§Œë£Œ??ê²ƒìœ¼ë¡?ë³´ì…?ˆë‹¤. ì¿ í‚¤ ?Œì¼??ê°±ì‹ ??ì£¼ì„¸??"
                ) from exc
            raise
            requested = info.get("requested_subtitles") or {}
            subtitles = info.get("subtitles") or {}
            automatic = info.get("automatic_captions") or {}
            logger.info(
                "yt-dlp subtitles keys for video_id=%s: requested=%s subtitles=%s auto=%s",
                video_id,
                ", ".join(requested.keys()) if requested else "none",
                ", ".join(subtitles.keys()) if subtitles else "none",
                ", ".join(automatic.keys()) if automatic else "none",
            )

            for lang_code, meta in requested.items():
                filepath = meta.get("filepath")
                if filepath and os.path.exists(filepath):
                    logger.info(
                        "yt-dlp subtitle selected. video_id=%s lang=%s path=%s",
                        video_id,
                        lang_code,
                        filepath,
                    )
                    return _parse_vtt(filepath)

            # If nothing was written, try downloading subtitles explicitly with best available lang.
            available_langs = list(subtitles.keys() or automatic.keys())
            if available_langs:
                pick_lang = next(
                    (lang for lang in preferred_langs if lang in available_langs),
                    available_langs[0],
                )
                logger.info(
                    "yt-dlp explicit subtitle download. video_id=%s lang=%s",
                    video_id,
                    pick_lang,
                )
                ydl.params["subtitleslangs"] = [pick_lang]
                ydl.download([url])

            # fallback to any vtt in temp dir
            for name in os.listdir(tmpdir):
                if name.endswith(".vtt"):
                    fallback_path = os.path.join(tmpdir, name)
                    logger.info(
                        "yt-dlp subtitle fallback. video_id=%s path=%s",
                        video_id,
                        fallback_path,
                    )
                    return _parse_vtt(fallback_path)

    if cookie_issue:
        raise RuntimeError(
            "YouTube ?ë§‰??ê°€?¸ì˜¤ì§€ ëª»í–ˆ?µë‹ˆ?? ì¿ í‚¤ ?Œì¼???†ê±°???½ì„ ???†ìŠµ?ˆë‹¤. "
            "ì¿ í‚¤ ?Œì¼??ìµœì‹ ?¼ë¡œ ê°±ì‹ ????YOUTUBE_COOKIES_PATHë¥??¤ì •??ì£¼ì„¸??"
        )
    raise RuntimeError("yt-dlp ?ë§‰ ?Œì¼???½ì? ëª»í–ˆ?µë‹ˆ??")


def _parse_vtt(path):
    lines = []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("WEBVTT"):
                continue
            if line.lower().startswith("kind:"):
                continue
            if line.lower().startswith("language:"):
                continue
            if "-->" in line:
                continue
            if re.fullmatch(r"\d+", line):
                continue
            line = re.sub(r"<[^>]+>", "", line)
            line = _strip_meta_phrases(line)
            if line:
                lines.append(line)
    return [{"text": " ".join(lines)}]


def _strip_meta_phrases(text):
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^\)]+\)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_transcript(text):
    cleaned = _strip_meta_phrases(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"(.)\1{4,}", r"\1\1", cleaned)
    return cleaned


def _log_quality_metrics(blog_post):
    issues = []
    for key in ("intro", "body", "conclusion"):
        section = blog_post.get(key, {})
        if not section.get("summary"):
            issues.append(f"{section.get('title', key)}: summary missing")
        if not section.get("content"):
            issues.append(f"{section.get('title', key)}: content missing")
        if not section.get("recommendation"):
            issues.append(f"{section.get('title', key)}: recommendation missing")
        if not section.get("incorrect_points"):
            issues.append(f"{section.get('title', key)}: incorrect_points missing")

    length = sum(
        len(" ".join(section.get("content", [])))
        for section in blog_post.values()
        if isinstance(section, dict)
    )
    logger.info(
        "Blog quality: total_content_chars=%s issues=%s",
        length,
        "; ".join(issues) if issues else "none",
    )


def _empty_section(title):
    return {
        "title": title,
        "summary": "",
        "content": [],
        "recommendation": "",
        "incorrect_points": "",
    }


def _normalize_section(title, payload):
    section = _empty_section(title)
    if not isinstance(payload, dict):
        return section

    section["summary"] = str(payload.get("summary", "")).strip()

    content = payload.get("content", [])
    if isinstance(content, list):
        section["content"] = [str(item).strip() for item in content if str(item).strip()]
    elif isinstance(content, str) and content.strip():
        section["content"] = [content.strip()]

    section["recommendation"] = str(payload.get("recommendation", "")).strip()
    section["incorrect_points"] = str(payload.get("incorrect_points", "")).strip()
    return section


def _strip_code_fences(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def generate_blog_post(transcript):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY ?˜ê²½ ë³€?˜ê? ?¤ì •?˜ì–´ ?ˆì? ?ŠìŠµ?ˆë‹¤.")

    client = OpenAI(api_key=api_key)

    prompt = f"""
?¤ìŒ ? íŠœë¸??ë§‰??ë°”íƒ•?¼ë¡œ ?œêµ­??ë¸”ë¡œê·?ê¸€ ì´ˆì•ˆ???‘ì„±??ì£¼ì„¸??

ë°˜ë“œ??JSON ê°ì²´ë§?ë°˜í™˜?˜ì„¸?? ë§ˆí¬?¤ìš´, ì½”ë“œë¸”ë¡, ?¤ëª… ë¬¸ì¥?€ ?£ì? ë§ˆì„¸??

ì¶œë ¥ ?¤í‚¤ë§?
{{
  "title": "ë¸”ë¡œê·??œëª©",
  "intro": {{
    "summary": "1ë¬¸ì¥ ?”ì•½",
    "content": ["ë¬¸ì¥1", "ë¬¸ì¥2", "ë¬¸ì¥3"],
    "recommendation": "ì¶”ê?ë¡?ê³µë??˜ë©´ ì¢‹ì? ?´ìš©",
    "incorrect_points": "?˜ëª»??ë¶€ë¶„ì´ ?†ìœ¼ë©?'?†ìŒ'"
  }},
  "body": {{
    "summary": "1ë¬¸ì¥ ?”ì•½",
    "content": ["ë¬¸ì¥1", "ë¬¸ì¥2", "ë¬¸ì¥3"],
    "recommendation": "ì¶”ê?ë¡?ê³µë??˜ë©´ ì¢‹ì? ?´ìš©",
    "incorrect_points": "?˜ëª»??ë¶€ë¶„ì´ ?†ìœ¼ë©?'?†ìŒ'"
  }},
  "conclusion": {{
    "summary": "1ë¬¸ì¥ ?”ì•½",
    "content": ["ë¬¸ì¥1", "ë¬¸ì¥2", "ë¬¸ì¥3"],
    "recommendation": "ì¶”ê?ë¡?ê³µë??˜ë©´ ì¢‹ì? ?´ìš©",
    "incorrect_points": "?˜ëª»??ë¶€ë¶„ì´ ?†ìœ¼ë©?'?†ìŒ'"
  }}
}}

ê·œì¹™:
- êµ¬ì¡°???œë¡ , ë³¸ë¡ , ê²°ë¡ ?¼ë¡œë§??‘ì„±?©ë‹ˆ??
- ?œë¡ ?€ "?????ìƒ??ë§Œë“¤ê²??˜ì—ˆ?”ì?"?€ "?´ë–¤ ?´ë“¤?ê²Œ ?„ì????˜ëŠ”ì§€"???€???´ìš©???ë§‰???ˆìœ¼ë©??¬í•¨?˜ê³ , ?†ìœ¼ë©??´ë‹¹ ?´ìš©?€ ?œì™¸?©ë‹ˆ??
- ë³¸ë¡ ?€ ?ìƒ??ì£¼ìš” ì£¼ì œ?¤ì„ ë³‘ë ¬?ìœ¼ë¡??•ë¦¬?©ë‹ˆ??
- ê°??¹ì…˜?€ [?”ì•½(1ë¬¸ì¥) - ?´ìš©(2~3ë¬¸ì¥) - ì¶”ì²œ(ê³µë????´ìš©) - ë³¸ë‚´?©ì—???˜ëª»??ë¶€ë¶??ˆë‹¤ë©?] ?•ì‹??ì§€?µë‹ˆ??
- ?¬ì‹¤ê´€ê³„ê? ë¶ˆëª…?•í•˜ê±°ë‚˜ ?ìƒ ?´ìš©???¨ì •?ì´ë©?incorrect_points??ê·??ì„ ?ìŠµ?ˆë‹¤.
- ?«ì, ?¸ìš©, ë¹„êµ, ?ì¸-ê²°ê³¼ ì£¼ì¥ ì¤?ê·¼ê±°ê°€ ?¬ë°•?˜ê±°???ë§‰ë§Œìœ¼ë¡??•ì •?˜ê¸° ?´ë ¤??ê²ƒì? "ê²€ì¦??„ìš”"ë¡??œì‹œ?©ë‹ˆ??
- ë°œì–¸?ì˜ ì£¼ê????˜ê²¬?€ ?˜ê²¬?„ì„ ?œì‹œ?˜ê³ , ?¬ì‹¤ì²˜ëŸ¼ ?°ì? ?ŠìŠµ?ˆë‹¤.
- ?˜ëª»??ë¶€ë¶„ì´ ëª…í™•???†ìœ¼ë©?incorrect_points??"?†ìŒ"?¼ë¡œ ?‘ì„±?©ë‹ˆ??
- ë³¸ë¬¸?€ ?ì—°?¤ëŸ¬??ë¸”ë¡œê·?ë¬¸ì²´ë¡??‘ì„±?˜ë˜ ê³¼ì¥ ?œí˜„?€ ?¼í•©?ˆë‹¤.

? íŠœë¸??ë§‰:
{transcript}
""".strip()

    response = client.responses.create(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": prompt}],
    )

    raw_text = _strip_code_fences(response.output_text)

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "AI ?‘ë‹µ??êµ¬ì¡°?”ëœ ?°ì´?°ë¡œ ?´ì„?˜ì? ëª»í–ˆ?µë‹ˆ?? ? ì‹œ ???¤ì‹œ ?œë„??ì£¼ì„¸??"
        ) from exc

    return {
        "title": str(payload.get("title", "? íŠœë¸?ê¸°ë°˜ ë¸”ë¡œê·?ê¸€")).strip() or "? íŠœë¸?ê¸°ë°˜ ë¸”ë¡œê·?ê¸€",
        "intro": _normalize_section("?œë¡ ", payload.get("intro")),
        "body": _normalize_section("ë³¸ë¡ ", payload.get("body")),
        "conclusion": _normalize_section("ê²°ë¡ ", payload.get("conclusion")),
    }


def _slugify(value):
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    return cleaned or "blog-post"


def blog_post_to_markdown(blog_post, source_url, verification_notes):
    def section_block(section):
        lines = [
            f"## {section['title']}",
            "",
            f"**?”ì•½**: {section['summary'] or '?ì„±???”ì•½???†ìŠµ?ˆë‹¤.'}",
            "",
            "**?´ìš©**:",
        ]
        if section["content"]:
            lines.extend([f"- {item}" for item in section["content"]])
        else:
            lines.append("- ?ì„±??ë³¸ë¬¸???†ìŠµ?ˆë‹¤.")
        lines.extend(
            [
                "",
                f"**ì¶”ì²œ**: {section['recommendation'] or 'ì¶”ê? ì¶”ì²œ ?´ìš©???†ìŠµ?ˆë‹¤.'}",
                "",
                f"**ë³¸ë‚´?©ì—???˜ëª»??ë¶€ë¶?*: {section['incorrect_points'] or '?†ìŒ'}",
                "",
            ]
        )
        return "\n".join(lines)

    summary_box = (
        f"> **?”ì•½ ë°•ìŠ¤**\n"
        f"> - ?œë¡ : {blog_post['intro']['summary'] or '?”ì•½ ?†ìŒ'}\n"
        f"> - ë³¸ë¡ : {blog_post['body']['summary'] or '?”ì•½ ?†ìŒ'}\n"
        f"> - ê²°ë¡ : {blog_post['conclusion']['summary'] or '?”ì•½ ?†ìŒ'}\n"
    )

    toc = "\n".join(
        [
            "- [?”ì•½ ë°•ìŠ¤](#?”ì•½-ë°•ìŠ¤)",
            "- [ê²€ì¦??„ìš” ëª¨ìŒ](#ê²€ì¦??„ìš”-ëª¨ìŒ)",
            "- [?œë¡ ](#?œë¡ )",
            "- [ë³¸ë¡ ](#ë³¸ë¡ )",
            "- [ê²°ë¡ ](#ê²°ë¡ )",
        ]
    )

    verification_block = ["## ê²€ì¦??„ìš” ëª¨ìŒ", ""]
    if verification_notes:
        verification_block.extend([f"- {note}" for note in verification_notes])
    else:
        verification_block.append("- ?†ìŒ")

    parts = [
        f"# {blog_post['title']}",
        "",
        f"- ?ì„±?? {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- ì¶œì²˜: {source_url}",
        "",
        "## ?”ì•½ ë°•ìŠ¤",
        "",
        summary_box.strip(),
        "",
        "## ëª©ì°¨",
        "",
        toc,
        "",
        "\n".join(verification_block),
        "",
        section_block(blog_post["intro"]),
        section_block(blog_post["body"]),
        section_block(blog_post["conclusion"]),
    ]
    return "\n".join(parts).strip() + "\n"


def _collect_verification_notes(blog_post):
    notes = []
    for section_key in ("intro", "body", "conclusion"):
        section = blog_post.get(section_key, {})
        incorrect_points = str(section.get("incorrect_points", "")).strip()
        if not incorrect_points or incorrect_points == "?†ìŒ":
            continue

        candidates = re.split(r"[.\n;]+", incorrect_points)
        for candidate in candidates:
            cleaned = candidate.strip()
            if cleaned and "ê²€ì¦??„ìš”" in cleaned:
                notes.append(f"{section.get('title', section_key)}: {cleaned}")
    return notes


def save_blog_post_markdown(blog_post, source_url):
    date_folder = datetime.now().strftime("%Y-%m-%d")
    output_dir = os.path.join(OUTPUT_DIR, date_folder)
    os.makedirs(output_dir, exist_ok=True)
    slug = _slugify(blog_post["title"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{stamp}.md"
    path = os.path.join(output_dir, filename)
    verification_notes = _collect_verification_notes(blog_post)
    markdown = blog_post_to_markdown(blog_post, source_url, verification_notes)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    return path

