# YouTube Blog Generator

![License](https://img.shields.io/github/license/myung-lim/youtube-summary-generator?style=flat-square)
![Stars](https://img.shields.io/github/stars/myung-lim/youtube-summary-generator?style=flat-square)
![Issues](https://img.shields.io/github/issues/myung-lim/youtube-summary-generator?style=flat-square)

## 스크린샷

![입력 화면](docs/images/home.png)
![결과 화면](docs/images/result.png)

YouTube URL을 입력하면 영상 자막을 기반으로 블로그 초안을 생성하는 Flask 애플리케이션입니다.

## 생성 형식

- 서론
- 본론
- 결론

각 섹션은 아래 구조를 따릅니다.

- 요약: 1문장
- 내용: 2~3문장
- 추천: 추가로 공부할 내용
- 본내용에서 잘못된 부분: 있으면 지적, 없으면 `없음`

## 로컬 실행

1. 의존성 설치

```powershell
py -3 -m pip install -r requirements.txt
```

2. OpenAI API 키 설정

```powershell
$env:OPENAI_API_KEY="your-api-key"
```

3. 앱 실행

```powershell
py -3 app.py
```

4. 브라우저 접속

```text
http://127.0.0.1:5000
```

## 외부 공개

프로젝트 루트에 있는 `ngrok.exe`를 이용해 외부에서 접속할 수 있습니다.

```powershell
.\start_public.ps1
```

옵션:

- `NGROK_AUTHTOKEN` 환경 변수가 있으면 스크립트가 먼저 토큰을 등록합니다.
- `PORT` 환경 변수를 설정하면 다른 포트로 실행할 수 있습니다.

ngrok 실행 후 표시되는 `https://...ngrok-free.app` 주소를 외부 공유 URL로 사용하면 됩니다.

## 자막 오류 대응

일부 영상은 브라우저에 자막이 보여도 API로는 차단되는 경우가 있습니다. 이럴 땐 브라우저에서 내보낸 쿠키를 사용해 우회할 수 있습니다.

```powershell
$env:YOUTUBE_COOKIES_PATH="C:\path\to\cookies.txt"
```

쿠키 파일은 `Netscape` 포맷 텍스트 파일이어야 합니다.

API 자막 추출이 계속 실패한다면 `yt-dlp`로 자막을 다시 시도합니다.

```powershell
py -3 -m pip install yt-dlp
```

`yt-dlp`는 자동 자막을 포함해 다른 방식으로 자막을 가져올 수 있어, API가 막힌 영상에서도 통과되는 경우가 있습니다.

## API 엔드포인트

브라우저 UI 외에도 JSON API를 제공합니다.

```text
POST /api/generate
Content-Type: application/json
{
  "url": "https://www.youtube.com/watch?v=..."
}
```

`save`를 `false`로 보내면 Markdown 저장을 건너뜁니다.

```text
POST /api/generate
Content-Type: application/json
{
  "url": "https://www.youtube.com/watch?v=...",
  "save": false
}
```

## 주요 파일

- `app.py`: Flask 웹 앱과 API 엔드포인트
- `blog_generator.py`: YouTube 자막 추출과 OpenAI 블로그 생성 로직
- `templates/home.html`: 입력 화면
- `templates/blog_result.html`: 결과 화면
- `start_public.ps1`: ngrok 기반 외부 공개 실행 스크립트
- `outputs/`: 자동 저장된 Markdown 파일이 쌓이는 폴더
