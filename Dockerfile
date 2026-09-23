FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# korail-mobile-api 는 패키징 메타데이터가 없어 소스를 그대로 가져와 PYTHONPATH 에 둡니다.
# 항상 upstream main 의 최신 커밋을 따라갑니다. 특정 커밋에 묶으려면
# --build-arg KORAIL_API_REF=<sha> 로 빌드하세요.
ARG KORAIL_API_REPO=https://github.com/yakisoba0728/korail-mobile-api.git
ARG KORAIL_API_REF=main

# main 이 움직이면 이 커밋 피드 내용이 바뀌어 아래 단계의 빌드 캐시가 무효화됩니다.
ADD https://github.com/yakisoba0728/korail-mobile-api/commits/${KORAIL_API_REF}.atom /tmp/korail-mobile-api.ref.json

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && git init /opt/korail-mobile-api \
 && git -C /opt/korail-mobile-api fetch --depth 1 "$KORAIL_API_REPO" "$KORAIL_API_REF" \
 && git -C /opt/korail-mobile-api checkout FETCH_HEAD \
 && git -C /opt/korail-mobile-api log -1 --format='korail-mobile-api %H %cd' > /opt/korail-mobile-api/VERSION \
 && rm -rf /opt/korail-mobile-api/.git /tmp/korail-mobile-api.ref.json \
 && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY korail_bot ./korail_bot
# 호스트의 umask(예: 077)로 체크아웃된 파일도 비루트 사용자가 읽을 수 있게 합니다.
RUN chmod -R a+rX /app /opt/korail-mobile-api

ENV PYTHONPATH=/opt/korail-mobile-api/src:/app

RUN useradd --create-home --uid 10001 bot
USER bot

EXPOSE 8000
CMD ["python", "-m", "korail_bot"]
