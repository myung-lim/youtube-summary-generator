# YouTube Summary Generator

![License](https://img.shields.io/github/license/myung-lim/youtube-summary-generator?style=flat-square)
![Stars](https://img.shields.io/github/stars/myung-lim/youtube-summary-generator?style=flat-square)
![Issues](https://img.shields.io/github/issues/myung-lim/youtube-summary-generator?style=flat-square)

Generate a blog-style summary from a YouTube URL.

## Screenshots

![Home](docs/images/home.png)
![Result](docs/images/result.png)

To update screenshots:
1. Run the app locally.
2. Capture the home page and result page.
3. Save them as `docs/images/home.png` and `docs/images/result.png`.

## Quick Start

```powershell
py -3 -m pip install -r requirements.txt
$env:OPENAI_API_KEY="your-api-key"
py -3 app.py
```

Open `http://127.0.0.1:5000` in your browser.

For the full Korean guide, see `README_KO.md`.
