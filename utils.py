from youtube_transcript_api import YouTubeTranscriptApi
import openai
import re
import os

# OpenAI API 키 설정 (환경 변수에서 가져옴)
openai.api_key = os.getenv('OPENAI_API_KEY')

def get_youtube_transcript(url):
    # URL에서 비디오 ID 추출
    video_id = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url).group(1)
    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    text = ' '.join([item['text'] for item in transcript])
    return text

def generate_blog_post(transcript):
    # AI를 사용해서 블로그 글 생성
    prompt = f"""
    다음 유튜브 트랜스크립트를 바탕으로 블로그 글을 생성하세요. 구조는 서론, 본론, 결론으로 나누고, 각 부분은 다음과 같이 구성하세요:
    [요약(1문장) - 내용(2-3문장) - 추천(공부할 내용) - 본내용에서 잘못된 부분(있다면)]

    트랜스크립트: {transcript[:2000]}  # 제한해서 요약
    """
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500
    )
    return response.choices[0].message['content'].strip()