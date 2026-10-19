#!/usr/bin/env python3
"""카카오톡 '나에게 보내기' 클라이언트 (표준 라이브러리만 사용). 수신자별로 토큰을 따로 보관한다.

    python3 kakao.py login                      # 본인: 브라우저 로그인 → 주소 붙여넣기 → 토큰 저장 + 테스트 전송
    python3 kakao.py auth-url                   # 다른 수신자에게 보낼 로그인 주소 출력
    python3 kakao.py auth-code <주소> --name 이름  # 수신자가 돌려준 주소로 그 사람을 등록 + 테스트 전송
    python3 kakao.py recipients                 # 등록된 수신자 목록
    python3 kakao.py remove 이름                 # 수신자 삭제
    python3 kakao.py test "메시지" --name 이름    # 특정 수신자에게 테스트 전송 (기본: 본인)
"""
import http.server
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
TOKEN_FILE = Path(os.environ.get("KAKAO_TOKEN_FILE") or BASE / ".kakao_tokens.json")
ENV_FILE = BASE / ".env"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
OWNER = "me"  # 기본 수신자(본인)
TEXT_LIMIT = 200  # 기본 텍스트 템플릿의 본문 최대 글자 수


class KakaoError(RuntimeError):
    pass


def load_env():
    """.env 의 KEY=VALUE 를 읽는다 (이미 설정된 환경변수가 우선)."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _shared():
    """KAKAO_SHARED_ENV 가 가리키는 다른 프로젝트 .env 에서 앱 키만 읽는다 (읽기 전용, 절대 쓰지 않음)."""
    load_env()
    path = os.environ.get("KAKAO_SHARED_ENV", "")
    out = {}
    if path and Path(path).expanduser().exists():
        for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
            if line.startswith(("KAKAO_REST_API_KEY=", "KAKAO_CLIENT_SECRET=")):
                k, v = line.split("=", 1)
                out[k] = v.strip().strip("\"'")
    return out


def _client():
    shared = _shared()
    key = os.environ.get("KAKAO_REST_API_KEY") or shared.get("KAKAO_REST_API_KEY", "")
    if not key:
        raise KakaoError(".env 에 KAKAO_REST_API_KEY 또는 KAKAO_SHARED_ENV 를 설정하세요.")
    return key, os.environ.get("KAKAO_CLIENT_SECRET") or shared.get("KAKAO_CLIENT_SECRET", "")


def _redirect_uri():
    load_env()
    return os.environ.get("KAKAO_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def auth_url():
    key, _ = _client()
    return "https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": key, "redirect_uri": _redirect_uri(), "response_type": "code", "scope": "talk_message"}
    )


def _post(url, data, headers=None):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode(), headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise KakaoError(f"{url} → HTTP {e.code}: {e.read().decode(errors='replace')}")


def _token_request(params):
    key, secret = _client()
    data = {"client_id": key, **params}
    if secret:
        data["client_secret"] = secret
    return _post("https://kauth.kakao.com/oauth/token", data)


def _load_all():
    """수신자 이름 → 토큰. 예전 단일 토큰 파일은 본인(me) 것으로 읽는다."""
    if not TOKEN_FILE.exists():
        return {}
    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return {OWNER: data} if "access_token" in data else data


def _write_all(data):
    TOKEN_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)


def _save_tokens(name, tok):
    data = _load_all()
    data[name] = tok
    _write_all(data)


def recipients():
    return list(_load_all())


def remove(name):
    data = _load_all()
    if name not in data:
        raise KakaoError(f"등록되지 않은 수신자: {name}")
    del data[name]
    _write_all(data)


def exchange_code(code, name=OWNER):
    """인가 코드(또는 code= 가 포함된 리다이렉트 주소)를 토큰으로 바꿔 수신자 이름으로 저장."""
    if "code=" in code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
    res = _token_request({"grant_type": "authorization_code", "redirect_uri": _redirect_uri(), "code": code})
    now = time.time()
    _save_tokens(
        name,
        {
            "access_token": res["access_token"],
            "access_expires_at": now + res.get("expires_in", 0),
            "refresh_token": res["refresh_token"],
            "refresh_expires_at": now + res.get("refresh_token_expires_in", 0),
        },
    )
    print(f"토큰 저장 완료: {name}")


def auth():
    """브라우저로 카카오 로그인 → 인가 코드를 로컬 서버로 받아 토큰 발급."""
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("인증 완료. 이 창을 닫고 터미널로 돌아가세요.".encode())

        def log_message(self, *a):
            pass

    url = auth_url()
    srv = http.server.HTTPServer(("127.0.0.1", urllib.parse.urlparse(_redirect_uri()).port or 80), Handler)
    srv.timeout = 300
    print("브라우저에서 카카오 로그인/동의를 진행하세요. 열리지 않으면 아래 주소를 직접 여세요:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    srv.server_close()
    if "error" in got:
        raise KakaoError(f"인증 실패: {got}")
    exchange_code(got["code"])


def access_token(name=OWNER):
    """유효한 액세스 토큰을 반환한다. 만료가 가까우면 리프레시 토큰으로 갱신."""
    tok = _load_all().get(name)
    if not tok:
        raise KakaoError(f"'{name}' 의 토큰이 없습니다. 먼저 `python3 kakao.py login` 을 실행하세요.")
    now = time.time()
    if tok.get("access_expires_at", 0) - now > 300:
        return tok["access_token"]

    res = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    tok["access_token"] = res["access_token"]
    tok["access_expires_at"] = now + res.get("expires_in", 0)
    # 리프레시 토큰은 만료 1개월 이내일 때만 새로 내려온다.
    if res.get("refresh_token"):
        tok["refresh_token"] = res["refresh_token"]
        tok["refresh_expires_at"] = now + res.get("refresh_token_expires_in", 0)
    _save_tokens(name, tok)
    return tok["access_token"]


def send_text(text, url="https://www.espn.com/nfl/scoreboard", button="ESPN에서 보기", name=OWNER):
    """수신자 본인의 '나와의 채팅'으로 텍스트 메시지 1건 전송 (본문 200자 제한)."""
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 1] + "…"
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": button,
    }
    res = _post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        {"template_object": json.dumps(template, ensure_ascii=False)},
        {"Authorization": f"Bearer {access_token(name)}"},
    )
    if res.get("result_code") != 0:
        raise KakaoError(f"전송 실패: {res}")


def send_all(text, url, button):
    """등록된 모든 수신자에게 전송. (성공한 이름 목록, {실패한 이름: 사유}) 를 반환한다."""
    ok, failed = [], {}
    for name in recipients():
        try:
            send_text(text, url, button, name)
            ok.append(name)
        except KakaoError as e:  # 한 사람의 토큰 문제가 나머지 전송을 막지 않도록
            failed[name] = str(e)
    return ok, failed


def _opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv) else default


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    name = _opt("--name", OWNER)
    try:
        if cmd == "login":
            # 리다이렉트 포트(8080)를 다른 프로그램이 쓰고 있어도 되는 방식: 주소창의 주소를 붙여넣는다
            webbrowser.open(auth_url())
            print("브라우저에서 카카오 로그인/동의 후, 이동한 페이지(오류 화면이어도 정상)의 주소창 주소를 통째로 복사해 붙여넣으세요.")
            exchange_code(input("주소: ").strip(), name)
            send_text("🏈 NFL 리포트 카톡 연결 완료", name=name)
            print("테스트 메시지 전송 완료. 카카오톡 '나와의 채팅'을 확인하세요.")
        elif cmd == "auth":
            auth()
        elif cmd == "auth-url":
            print(auth_url())
        elif cmd == "auth-code" and len(sys.argv) > 2:
            exchange_code(sys.argv[2], name)
            send_text("🏈 NFL 리포트 카톡 연결 완료. 앞으로 이 채팅으로 리포트가 옵니다.", name=name)
            print(f"'{name}' 에게 테스트 메시지 전송 완료.")
        elif cmd == "recipients":
            for n, t in _load_all().items():
                days = (t.get("refresh_expires_at", 0) - time.time()) / 86400
                print(f"{n}\t로그인 유효 {days:.0f}일 남음 (리포트를 받을 때마다 자동 연장)")
        elif cmd == "remove" and len(sys.argv) > 2:
            remove(sys.argv[2])
            print(f"수신자 삭제: {sys.argv[2]}")
        elif cmd == "test":
            msg = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "🏈 NFL 소식 알림 테스트"
            send_text(msg, name=name)
            print(f"'{name}' 에게 전송 완료.")
        else:
            print(__doc__)
    except KakaoError as e:
        sys.exit(f"[kakao] {e}")
