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
OUTPUT_DIR = os.getenv("BLOG_OUTPUT_DIR", "outputs")

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

    raise ValueError("유효한 YouTube URL을 입력해 주세요.")


def get_youtube_transcript(url):
    video_id = extract_video_id(url)

    try:
        transcript = _fetch_transcript_with_retry(video_id)
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        raise ValueError(
            "이 영상은 자막을 가져올 수 없습니다. 자막이 있는 다른 영상을 시도해 주세요."
        ) from exc
    except Exception as exc:
        logger.exception("Transcript fetch failed. video_id=%s url=%s", video_id, url)
        raise ValueError(
            "자막을 가져오는 과정에서 오류가 발생했습니다. 영상이 공개 상태인지, "
            "지역 제한이나 연령 제한이 없는지, 자막이 활성화되어 있는지 확인해 주세요. "
            "일시적인 YouTube 응답 오류일 수 있으니 잠시 후 다시 시도해 보세요."
        ) from exc

    text = " ".join(item["text"].strip() for item in transcript if item.get("text"))
    cleaned = _normalize_transcript(text)

    if not cleaned:
        logger.warning("Transcript empty after cleaning. video_id=%s", video_id)
        raise ValueError("영상 자막이 비어 있습니다.")

    logger.info("Transcript fetched. video_id=%s length=%s", video_id, len(cleaned))
    return cleaned[:TRANSCRIPT_LIMIT]


def _get_cookies_path():
    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()
    if cookies_path and os.path.exists(cookies_path):
        return cookies_path
    return None


def _fetch_transcript_once(video_id):
    cookies_path = _get_cookies_path()
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
            "yt-dlp가 설치되어 있지 않아 대체 자막 추출을 진행할 수 없습니다."
        ) from exc

    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_path = _get_cookies_path()

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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
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

    raise RuntimeError("yt-dlp 자막 파일을 읽지 못했습니다.")


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
        raise ValueError("OPENAI_API_KEY 환경 변수가 설정되어 있지 않습니다.")

    client = OpenAI(api_key=api_key)

    prompt = f"""
다음 유튜브 자막을 바탕으로 한국어 블로그 글 초안을 작성해 주세요.

반드시 JSON 객체만 반환하세요. 마크다운, 코드블록, 설명 문장은 넣지 마세요.

출력 스키마:
{{
  "title": "블로그 제목",
  "intro": {{
    "summary": "1문장 요약",
    "content": ["문장1", "문장2", "문장3"],
    "recommendation": "추가로 공부하면 좋은 내용",
    "incorrect_points": "잘못된 부분이 없으면 '없음'"
  }},
  "body": {{
    "summary": "1문장 요약",
    "content": ["문장1", "문장2", "문장3"],
    "recommendation": "추가로 공부하면 좋은 내용",
    "incorrect_points": "잘못된 부분이 없으면 '없음'"
  }},
  "conclusion": {{
    "summary": "1문장 요약",
    "content": ["문장1", "문장2", "문장3"],
    "recommendation": "추가로 공부하면 좋은 내용",
    "incorrect_points": "잘못된 부분이 없으면 '없음'"
  }}
}}

규칙:
- 구조는 서론, 본론, 결론으로만 작성합니다.
- 서론은 "왜 이 영상을 만들게 되었는지"와 "어떤 이들에게 도움이 되는지"에 대한 내용이 자막에 있으면 포함하고, 없으면 해당 내용은 제외합니다.
- 본론은 영상의 주요 주제들을 병렬식으로 정리합니다.
- 각 섹션은 [요약(1문장) - 내용(2~3문장) - 추천(공부할 내용) - 본내용에서 잘못된 부분(있다면)] 형식을 지킵니다.
- 사실관계가 불명확하거나 영상 내용이 단정적이면 incorrect_points에 그 점을 적습니다.
- 숫자, 인용, 비교, 원인-결과 주장 중 근거가 희박하거나 자막만으로 확정하기 어려운 것은 "검증 필요"로 표시합니다.
- 발언자의 주관적 의견은 의견임을 표시하고, 사실처럼 쓰지 않습니다.
- 잘못된 부분이 명확히 없으면 incorrect_points는 "없음"으로 작성합니다.
- 본문은 자연스러운 블로그 문체로 작성하되 과장 표현은 피합니다.

유튜브 자막:
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
            "AI 응답을 구조화된 데이터로 해석하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from exc

    return {
        "title": str(payload.get("title", "유튜브 기반 블로그 글")).strip() or "유튜브 기반 블로그 글",
        "intro": _normalize_section("서론", payload.get("intro")),
        "body": _normalize_section("본론", payload.get("body")),
        "conclusion": _normalize_section("결론", payload.get("conclusion")),
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
            f"**요약**: {section['summary'] or '생성된 요약이 없습니다.'}",
            "",
            "**내용**:",
        ]
        if section["content"]:
            lines.extend([f"- {item}" for item in section["content"]])
        else:
            lines.append("- 생성된 본문이 없습니다.")
        lines.extend(
            [
                "",
                f"**추천**: {section['recommendation'] or '추가 추천 내용이 없습니다.'}",
                "",
                f"**본내용에서 잘못된 부분**: {section['incorrect_points'] or '없음'}",
                "",
            ]
        )
        return "\n".join(lines)

    summary_box = (
        f"> **요약 박스**\n"
        f"> - 서론: {blog_post['intro']['summary'] or '요약 없음'}\n"
        f"> - 본론: {blog_post['body']['summary'] or '요약 없음'}\n"
        f"> - 결론: {blog_post['conclusion']['summary'] or '요약 없음'}\n"
    )

    toc = "\n".join(
        [
            "- [요약 박스](#요약-박스)",
            "- [검증 필요 모음](#검증-필요-모음)",
            "- [서론](#서론)",
            "- [본론](#본론)",
            "- [결론](#결론)",
        ]
    )

    verification_block = ["## 검증 필요 모음", ""]
    if verification_notes:
        verification_block.extend([f"- {note}" for note in verification_notes])
    else:
        verification_block.append("- 없음")

    parts = [
        f"# {blog_post['title']}",
        "",
        f"- 생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 출처: {source_url}",
        "",
        "## 요약 박스",
        "",
        summary_box.strip(),
        "",
        "## 목차",
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
        if not incorrect_points or incorrect_points == "없음":
            continue

        candidates = re.split(r"[.\n;]+", incorrect_points)
        for candidate in candidates:
            cleaned = candidate.strip()
            if cleaned and "검증 필요" in cleaned:
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
