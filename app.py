import os

from flask import Flask, jsonify, render_template, request
import logging

from blog_generator import (
    generate_blog_post,
    get_youtube_transcript,
    save_blog_post_markdown,
    _log_quality_metrics,
)


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form["url"].strip()
        try:
            transcript = get_youtube_transcript(url)
            blog_post = generate_blog_post(transcript)
            _log_quality_metrics(blog_post)
            saved_path = save_blog_post_markdown(blog_post, url)
            return render_template(
                "blog_result.html", blog_post=blog_post, url=url, saved_path=saved_path
            )
        except Exception as exc:
            app.logger.exception("Failed to generate blog post. url=%s", url)
            return render_template("home.html", error=str(exc), url=url)

    return render_template("home.html")


@app.route("/api/generate", methods=["POST"])
def generate():
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url", "")).strip()

    if not url:
        return jsonify({"error": "url 필드는 필수입니다."}), 400

    try:
        transcript = get_youtube_transcript(url)
        blog_post = generate_blog_post(transcript)
        _log_quality_metrics(blog_post)
        save_flag = str(payload.get("save", "true")).lower() != "false"
        saved_path = save_blog_post_markdown(blog_post, url) if save_flag else None
        response = dict(blog_post)
        if saved_path:
            response["saved_path"] = saved_path
        return jsonify(response)
    except Exception as exc:
        app.logger.exception("API generate failed. url=%s", url)
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
