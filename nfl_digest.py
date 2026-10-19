#!/usr/bin/env python3
"""NFL 리포트 페이지를 만들고, 새 소식이 있으면 카카오톡(나에게 보내기)으로 링크를 보낸다.

    python3 nfl_digest.py --dry-run   # 페이지만 로컬 생성(site/index.html), 게시·전송 없음
    python3 nfl_digest.py             # 페이지 생성 → 게시 → 새 소식 있으면 카톡 전송
    옵션: --no-llm (번역/요약 생략), --force (새 소식이 없어도 전송)

점수·선수 기록은 ESPN 데이터를 코드로 그대로 포맷하고(숫자 왜곡 방지),
기사/경기 요약 문장만 로컬 Ollama 모델로 한국어 요약한다.
"""
import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import kakao
import report

BASE = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("STATE_FILE") or BASE / "state.json")
SITE_DIR = Path(os.environ.get("SITE_DIR") or BASE / "site")
PENDING_FILE = STATE_FILE.with_name("pending.json")  # 페이지 배포 후 보낼 카톡 (GitHub Actions용)
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
KST = dt.timezone(dt.timedelta(hours=9))

kakao.load_env()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b")
SUMMARIZER = os.environ.get("SUMMARIZER", "claude")  # claude(로컬 CLI) | api(Anthropic API, GitHub Actions) | ollama(무료·로컬)
API_MODEL = os.environ.get("API_MODEL", "claude-haiku-5-5")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")
CLAUDE_BATCH = 10
FAVORITE_TEAM = os.environ.get("FAVORITE_TEAM", "kc").lower()
PAGE_URL = os.environ.get("PAGE_URL", "").rstrip("/")
MAX_NEWS = int(os.environ.get("MAX_NEWS", "8"))
NEWS_LOOKBACK_HOURS = int(os.environ.get("NEWS_LOOKBACK_HOURS", "30"))
FAV_NEWS_LOOKBACK_HOURS = 168  # 휴식 주간에도 소식이 비지 않도록 일주일
GAME_LOOKBACK_DAYS = 4
GAMEDAY_HOURS = float(os.environ.get("GAMEDAY_HOURS", "24"))  # 관심 팀 킥오프가 이 시간 이내면 경기일로 강조
CACHE_VER = "v3:"  # 프롬프트를 바꾸면 올려서 기존 요약을 다시 만든다

SKIP_WORDS = ("fantasy", "betting", "odds", "picks")  # 판타지·베팅 기사는 제외
LEADER_LABEL = {"passingYards": "패스", "rushingYards": "러싱", "receivingYards": "리시빙"}


def log(msg):
    print(f"[{dt.datetime.now(KST):%m-%d %H:%M}] {msg}", file=sys.stderr)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


# ---------- 수집 ----------

def fmt_stat(s):
    for a, b in ((" YDS", "yd"), (" CAR", "회"), (" REC", "회"), (" TD", "TD"), (" INT", "INT"), (",", "")):
        s = s.replace(a, b)
    return s


def parse_team(t):
    team = t["team"]
    score = t.get("score")
    if isinstance(score, dict):  # 팀 일정 API는 {"displayValue": "31"} 형태
        score = score.get("displayValue")
    logo = team.get("logo") or (team.get("logos") or [{}])[0].get("href", "")
    rec = (t.get("records") or t.get("record") or [{}])[0]
    return {
        "abbr": team["abbreviation"],
        "name": team.get("shortDisplayName") or team.get("displayName", ""),
        "full": team.get("displayName", team["abbreviation"]),
        "logo": logo,
        "score": score if score is not None else "",
        "winner": bool(t.get("winner")),
        "record": rec.get("summary") or rec.get("displayValue", ""),
    }


def parse_event(e):
    c = e["competitions"][0]
    teams = {t["homeAway"]: t for t in c["competitors"]}
    abbr = {t["team"]["id"]: t["team"]["abbreviation"] for t in c["competitors"]}
    leaders = []
    for l in c.get("leaders", []):
        if l["name"] not in LEADER_LABEL or not l.get("leaders"):
            continue
        x = l["leaders"][0]
        a = x["athlete"]
        leaders.append(
            {
                "label": LEADER_LABEL[l["name"]],
                "name": a.get("shortName", a.get("displayName", "")),
                "team": abbr.get(str(a.get("team", {}).get("id", "")), ""),
                "stat": fmt_stat(x["displayValue"]),
                "headshot": a.get("headshot", ""),
            }
        )
    recap = next((h for h in c.get("headlines", []) if h.get("description")), None)
    st = c["status"]["type"]
    return {
        "id": e["id"],
        "date": parse_time(e["date"]),
        "week": e.get("week", {}).get("number"),
        "state": st.get("state", "pre"),
        "completed": bool(st.get("completed")),
        "away": parse_team(teams["away"]),
        "home": parse_team(teams["home"]),
        "leaders": leaders,
        "recap": f"{recap.get('shortLinkText', '')} {recap['description']}".strip() if recap else "",
        "url": next((l["href"] for l in e.get("links", []) if "summary" in l.get("rel", [])), ""),
        "tv": ", ".join((c.get("broadcasts") or [{}])[0].get("names", [])),
    }


def fetch_games():
    """(현재 주차, 최근 종료 경기[최신순], 이번 주 남은 경기[시간순])."""
    data = get_json(f"{ESPN}/scoreboard")
    events = data.get("events", [])
    week, stype = data.get("week", {}).get("number", 1), data.get("season", {}).get("type", 2)
    # ESPN이 다음 주차로 넘어간 직후에도 직전 주 월요일 경기를 놓치지 않도록 전 주차도 조회
    prev = get_json(f"{ESPN}/scoreboard?seasontype={stype}&week={week - 1}").get("events", []) if week > 1 else []
    cutoff = now_utc() - dt.timedelta(days=GAME_LOOKBACK_DAYS)
    games = [parse_event(e) for e in prev + events]
    results = sorted((g for g in games if g["completed"] and g["date"] >= cutoff), key=lambda g: g["date"], reverse=True)
    upcoming = sorted((g for g in games if not g["completed"] and g["week"] == week), key=lambda g: g["date"])
    return week, data.get("season", {}).get("year"), results, upcoming


def parse_articles(data, hours):
    cutoff = now_utc() - dt.timedelta(hours=hours)
    out = []
    for a in data.get("articles", []):
        if a.get("type") not in ("Story", "HeadlineNews") or not a.get("published"):
            continue
        if parse_time(a["published"]) < cutoff:
            continue
        if any(w in a["headline"].lower() for w in SKIP_WORDS):
            continue
        out.append(
            {
                "id": str(a.get("id") or a.get("dataSourceIdentifier")),
                "headline": a["headline"],
                "published": parse_time(a["published"]),
                "description": a.get("description", ""),
                "image": (a.get("images") or [{}])[0].get("url", ""),
                "url": a.get("links", {}).get("web", {}).get("href", ""),
            }
        )
    return out


def fetch_news():
    """최근 기사 (ESPN 노출 순서 = 중요도 순)."""
    return parse_articles(get_json(f"{ESPN}/news?limit=40"), NEWS_LOOKBACK_HOURS)


def fetch_favorite(week):
    """관심 팀의 성적·지난/다음 경기·관련 기사."""
    t = get_json(f"{ESPN}/teams/{FAVORITE_TEAM}")["team"]
    sched = get_json(f"{ESPN}/teams/{FAVORITE_TEAM}/schedule")
    games = [parse_event(e) for e in sched.get("events", [])]
    done = [g for g in games if g["completed"]]
    todo = [g for g in games if not g["completed"]]
    nick = t["name"].lower()
    # 팀 피드에는 리그 전체 기사도 섞여 오므로 팀 이름이 언급된 기사만 남긴다
    news = [
        n
        for n in parse_articles(get_json(f"{ESPN}/news?team={t['id']}&limit=50"), FAV_NEWS_LOOKBACK_HOURS)
        if nick in f"{n['headline']} {n['description']}".lower()
    ]
    return {
        "id": t["id"],
        "abbr": t["abbreviation"],
        "name": t["displayName"],
        "short": t.get("shortDisplayName") or t["name"],
        "logo": (t.get("logos") or [{}])[0].get("href", ""),
        "record": (t.get("record", {}).get("items") or [{}])[0].get("summary", ""),
        "standing": t.get("standingSummary", ""),
        "bye": sched.get("byeWeek") == week,
        "last": done[-1] if done else None,
        "next": todo[0] if todo else None,
        "news": sorted(news, key=lambda n: n["published"], reverse=True)[:3],
    }


# ---------- 한국어 요약 (로컬 LLM) ----------

# 원문에 없어도 요약에 등장할 수 있는 영문 약어
ALLOWED_WORDS = {
    "nfl", "afc", "nfc", "qb", "rb", "wr", "te", "ol", "dl", "de", "dt", "lb", "cb", "td", "int",
    "vs", "mvp", "ir", "acl", "mcl", "mnf", "snf", "tnf", "week", "yd", "bye",
}


def verify(source, out, check_names=True):
    """요약에 원문에 없는 숫자(2자리 이상)나 영문 이름이 있으면 그 토큰을 반환한다 (환각 방지)."""
    src = source.lower()
    for num in re.findall(r"\d[\d,.]*\d", out):
        if num not in src and num.replace(",", "") not in src.replace(",", ""):
            return num
    if not check_names:
        return None
    for word in re.findall(r"[A-Za-z][A-Za-z'’\-]*", out):
        w = word.lower().strip("'’-")
        if len(w) > 1 and w not in ALLOWED_WORDS and w not in src and w.rstrip("s") not in src:
            return word
    return None


def llm_one(text, facts=""):
    """영문 기사 1건 → {'title','ko'} | None. 한 건씩 처리해 항목 간 내용 혼동을 막는다."""
    prompt = (
        "너는 NFL(미식축구) 전문 번역 기자다. 아래 영문 기사를 한국어로 정리하라.\n"
        "- title: 한국어 제목, 30자 이내. 질문형이면 핵심 내용의 평서문으로 바꾼다.\n"
        "- ko: 한국어 요약 1~2문장, 120자 이내, '~했다' 체.\n"
        "규칙:\n"
        "1. 기사에 있는 사실만 쓴다. 추측하거나 내용을 보태지 않는다.\n"
        "2. 누가 이기고 졌는지, 누가 무엇을 했는지 주어와 목적어를 정확히 옮긴다.\n"
        "3. 숫자·점수는 그대로 옮긴다.\n"
        "4. 선수·감독 이름과 팀 이름(예: Buccaneers, Texans, 49ers)은 번역하지 말고 영어 철자 그대로 둔다.\n"
        "5. 용어: upset=이변, rookie=신인, injury=부상, trade=트레이드, bye=휴식 주간.\n"
        + (f"\n[확정 사실 — 반드시 이와 일치해야 한다]\n{facts}\n" if facts else "")
        + f"\n[기사]\n{text}\n\n"
        'JSON으로만 답하라: {"title": "...", "ko": "..."}'
    )
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }
    source = f"{facts}\n{text}"
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                res = json.loads(json.loads(r.read().decode())["response"])
            title, ko = str(res.get("title", "")).strip(), str(res.get("ko", "")).strip()
            bad = verify(source, f"{title} {ko}") if ko else "빈 응답"
            if not bad:
                return {"title": title, "ko": ko}
            log(f"요약 검증 실패({bad}) 재시도 {attempt + 1}: {text[:50]}")
            body["options"]["temperature"] = 0.4
        except Exception as e:
            log(f"LLM 요약 실패: {e}")
    return None  # 실패 시 영문 원문으로 대체 표시


def game_facts(g):
    w, l = (g["away"], g["home"]) if g["away"]["winner"] else (g["home"], g["away"])
    return f"최종 점수: {w['full']} {w['score']} - {l['score']} {l['full']}. 승리 팀: {w['full']}, 패배 팀: {l['full']}."


CLAUDE_SYSTEM = (
    "너는 NFL(미식축구) 전문 번역 기자다. 주어진 영문 기사들을 각각 한국어로 정리한다.\n"
    "- title: 한국어 제목, 30자 이내. 질문형 제목은 핵심 내용의 평서문으로 바꾼다.\n"
    "- ko: 한국어 요약 1~2문장, 120자 이내, '~했다' 체의 뉴스 문체.\n"
    "규칙: 기사에 있는 사실만 쓰고 추측하지 않는다. 승패와 주어·목적어를 정확히 옮긴다. "
    "[확정 사실]이 있으면 반드시 그와 일치시킨다. 숫자·점수는 그대로 옮긴다. "
    "선수·감독·팀 이름만 영어 철자 그대로 두고, 요일·포지션 설명 등 나머지는 자연스러운 한국어로 쓴다.\n"
    '출력은 JSON 배열만(코드블록·설명 없이): [{"n": 1, "title": "...", "ko": "..."}]'
)


def _apply_batch(result, items, out):
    """모델이 돌려준 JSON 배열을 검증해 out 에 채운다."""
    for it in json.loads(result[result.index("[") : result.rindex("]") + 1]):
        i = int(it.get("n", 0)) - 1
        title, ko = str(it.get("title", "")).strip(), str(it.get("ko", "")).strip()
        if not (0 <= i < len(out)) or not ko:
            continue
        bad = verify("\n".join(items[i]), f"{title} {ko}", check_names=False)
        if bad:
            log(f"요약 검증 실패({bad}): {items[i][0][:50]}")
        else:
            out[i] = {"title": title, "ko": ko}


def _batch_prompt(items):
    return "\n\n".join(
        f"### {i + 1}\n" + (f"[확정 사실] {facts}\n" if facts else "") + text for i, (text, facts) in enumerate(items)
    )


def api_batch(items):
    """[(text, facts)] → [{'title','ko'} | None]. Anthropic API 직접 호출 (GitHub Actions 등 CLI가 없는 환경)."""
    import anthropic

    out = [None] * len(items)
    try:
        resp = anthropic.Anthropic().messages.create(
            model=API_MODEL,
            max_tokens=8000,
            system=CLAUDE_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": _batch_prompt(items)}],
        )
        if resp.stop_reason == "refusal":
            log("Claude API 요약 거절됨 → 영문 원문 사용")
            return out
        _apply_batch("".join(b.text for b in resp.content if b.type == "text"), items, out)
    except Exception as e:  # 요약 실패가 리포트 전체를 막지 않도록 (영문 원문으로 대체)
        log(f"Claude API 요약 실패: {e}")
    return out


def claude_batch(items):
    """[(text, facts)] → [{'title','ko'} | None]. Claude CLI를 도구 없이 1회 호출한다."""
    out = [None] * len(items)
    blocks = [
        f"### {i + 1}\n" + (f"[확정 사실] {facts}\n" if facts else "") + text for i, (text, facts) in enumerate(items)
    ]
    cmd = [CLAUDE_BIN, "-p", "--model", CLAUDE_MODEL, "--output-format", "json", "--system-prompt", CLAUDE_SYSTEM,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    try:
        r = subprocess.run(cmd, input="\n\n".join(blocks), capture_output=True, text=True, timeout=300, cwd="/tmp")
        _apply_batch(json.loads(r.stdout)["result"], items, out)
    except Exception as e:
        log(f"Claude 요약 실패 → 로컬 모델로 대체: {e}")
    return out


def add_summaries(results, articles, cache, use_llm):
    """경기 요약·기사에 한국어 title/ko 를 채운다. 결과는 cache 에 저장해 재요약을 피한다."""
    todo = [(f"{CACHE_VER}g:{g['id']}", g, g["recap"], game_facts(g)) for g in results if g["recap"]]
    todo += [(f"{CACHE_VER}a:{n['id']}", n, f"{n['headline']}\n{n['description']}", "") for n in articles]
    missing = [(k, text, facts) for k, _, text, facts in todo if k not in cache] if use_llm else []
    batch_fn = {"claude": claude_batch, "api": api_batch}.get(SUMMARIZER)
    if batch_fn:
        for i in range(0, len(missing), CLAUDE_BATCH):
            chunk = missing[i : i + CLAUDE_BATCH]
            for (k, _, _), res in zip(chunk, batch_fn([(t, f) for _, t, f in chunk])):
                if res:
                    cache[k] = res
    for k, text, facts in missing:  # Claude가 실패했거나 ollama 모드인 항목 (api 모드엔 로컬 모델이 없음)
        if k not in cache and SUMMARIZER != "api":
            res = llm_one(text, facts)
            if res:
                cache[k] = res
    for k, item, _, _ in todo:
        if k in cache:
            item["ko"] = cache[k]["ko"]
            if "a:" in k:
                item["title"] = cache[k]["title"]


# ---------- 순위 · 선수 기록 ----------

SEASON_CATEGORIES = [
    ("passingYards", "패싱 야드"), ("passingTouchdowns", "패싱 TD"),
    ("rushingYards", "러싱 야드"), ("rushingTouchdowns", "러싱 TD"),
    ("receivingYards", "리시빙 야드"), ("receivingTouchdowns", "리시빙 TD"),
    ("sacks", "색"), ("interceptions", "인터셉션"), ("totalTackles", "태클"),
]
PLAYER_LABEL = {
    "passingYards": "패싱", "rushingYards": "러싱", "receivingYards": "리시빙",
    "totalTackles": "태클", "sacks": "색", "interceptions": "인터셉션",
}


PLAYER_UNIT = {"passingYards": "yd", "rushingYards": "yd", "receivingYards": "yd", "totalTackles": "회", "sacks": "개", "interceptions": "개"}


def fetch_standings():
    """컨퍼런스 → 지구 → 팀 순위."""
    data = get_json("https://site.api.espn.com/apis/v2/sports/football/nfl/standings?level=3")
    confs = []
    for conf in data.get("children", []):
        divisions = []
        for div in conf.get("children", []):
            teams = []
            for e in div["standings"]["entries"]:
                st = {x["name"]: x.get("displayValue", "") for x in e["stats"]}
                t = e["team"]
                teams.append(
                    {
                        "abbr": t["abbreviation"],
                        "name": t.get("shortDisplayName") or t["displayName"],
                        "logo": (t.get("logos") or [{}])[0].get("href", ""),
                        "record": st.get("overall", ""),
                        "pct": st.get("winPercent", ""),
                        "pf": st.get("pointsFor", ""),
                        "pa": st.get("pointsAgainst", ""),
                        "diff": st.get("pointDifferential", ""),
                        "streak": st.get("streak", ""),
                        "seed": int(st.get("playoffSeed") or 99),
                        "div": st.get("vs. Div.", ""),
                    }
                )
            divisions.append({"name": div["name"], "teams": sorted(teams, key=lambda x: x["seed"])})
        confs.append({"name": conf.get("abbreviation") or conf["name"], "divisions": divisions})
    return confs


def fetch_season_leaders():
    """리그 시즌 부문별 상위 5명."""
    cats = {c["name"]: c for c in get_json("https://site.api.espn.com/apis/site/v3/sports/football/nfl/leaders")["leaders"]["categories"]}
    out = []
    for key, label in SEASON_CATEGORIES:
        players = []
        for l in cats.get(key, {}).get("leaders", [])[:5]:
            a = l["athlete"]
            players.append(
                {
                    "name": a.get("displayName", ""),
                    "pos": a.get("position", {}).get("abbreviation", ""),
                    "team": l.get("team", {}).get("abbreviation", ""),
                    "value": l.get("displayValue", ""),
                    "headshot": a.get("headshot", {}).get("href", ""),
                }
            )
        if players:
            out.append({"label": label, "players": players})
    return out


def week_performers(results, week):
    """이번 주 종료 경기의 부문별 최고 기록 (야드 순)."""
    out = []
    for label in LEADER_LABEL.values():
        rows = [l for g in results if g["week"] == week for l in g["leaders"] if l["label"] == label]
        yards = lambda l: int((re.search(r"(\d+)yd", l["stat"]) or [0, 0])[1])
        rows = sorted(rows, key=yards, reverse=True)[:5]
        if rows:
            out.append({"label": label, "players": [{**l, "value": l["stat"], "pos": ""} for l in rows]})
    return out


def fetch_fav_players(fav, season):
    """관심 팀 선수: 시즌 누적 부문 1위 + 지난 경기 활약."""
    core = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
    team_id = fav["id"]
    cats = {c["name"]: c for c in get_json(f"{core}/seasons/{season}/types/2/teams/{team_id}/leaders").get("categories", [])}
    season_rows = []
    for key, label in PLAYER_LABEL.items():
        lead = (cats.get(key, {}).get("leaders") or [None])[0]
        if not lead:
            continue
        a = get_json(lead["athlete"]["$ref"].replace("http://", "https://"))
        season_rows.append(
            {
                "label": label,
                "name": a.get("displayName", ""),
                "pos": a.get("position", {}).get("abbreviation", ""),
                "value": fmt_stat(lead.get("displayValue", "")) + PLAYER_UNIT.get(key, ""),
                "headshot": a.get("headshot", {}).get("href", ""),
            }
        )
    last_rows = []
    if fav.get("last"):
        summary = get_json(f"{ESPN}/summary?event={fav['last']['id']}")
        for t in summary.get("leaders", []):
            if t["team"]["abbreviation"] != fav["abbr"]:
                continue
            for c in t["leaders"]:
                if c["name"] not in PLAYER_LABEL or not c.get("leaders"):
                    continue
                l = c["leaders"][0]
                a = l["athlete"]
                last_rows.append(
                    {
                        "label": PLAYER_LABEL[c["name"]],
                        "name": a.get("displayName", ""),
                        "pos": a.get("position", {}).get("abbreviation", ""),
                        "value": fmt_stat(l.get("displayValue", "")),
                        "headshot": a.get("headshot", {}).get("href", ""),
                    }
                )
    return {"season": season_rows, "last": last_rows}


# ---------- 게시 · 전송 ----------

def wait_for_network(hosts=("github.com", "site.api.espn.com"), tries=12, delay=15):
    """잠자기에서 깬 직후엔 인터넷이 아직 안 붙어 있을 수 있어 최대 약 3분 기다린다."""
    for i in range(tries):
        try:
            for host in hosts:
                socket.getaddrinfo(host, 443)
            return True
        except OSError:
            if i == 0:
                log("인터넷 연결 대기 중…")
            time.sleep(delay)
    log("인터넷 연결 안 됨 → 계속 진행 (실패할 수 있음)")
    return False


def publish(build):
    """site/ 가 git 저장소면 커밋·푸시하고, 새 버전이 실제로 서비스될 때까지 기다린다."""
    if not (SITE_DIR / ".git").exists() or not PAGE_URL:
        log("게시 설정 없음(site/.git 또는 PAGE_URL) → 게시 생략")
        return False
    git = ["git", "-C", str(SITE_DIR)]
    subprocess.run(git + ["add", "-A"], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(git + ["commit", "-q", "-m", f"report {build}"], check=True)
    for attempt in range(3):  # 네트워크가 잠깐 끊겨도 재시도
        if subprocess.run(git + ["push", "-q"]).returncode == 0:
            break
        log(f"게시(push) 실패, 재시도 {attempt + 1}/3")
        time.sleep(30)
    else:
        return False
    for _ in range(24):  # 최대 약 4분
        try:
            req = urllib.request.Request(f"{PAGE_URL}/?v={build}", headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=15) as r:
                if build in r.read().decode(errors="replace"):
                    return True
        except Exception:
            pass
        time.sleep(10)
    log("게시 확인 시간 초과 (링크는 잠시 뒤 갱신될 수 있음)")
    return True


def build_message(week, new_games, new_news, fav, updated, alert=False, max_scores=4):
    """카카오 본문(200자 제한)에 맞춘 한 건짜리 안내 메시지."""
    lines = []
    if alert:  # 관심 팀 경기일: 맨 위에 크게
        g = fav["next"]
        home = g["home"]["abbr"] == fav["abbr"]
        opp = g["away"] if home else g["home"]
        lines += [
            f"🔴🏈 GAME DAY! {fav['short']} 경기 🔴",
            f"{fav['short']} {'vs' if home else '@'} {opp['name']}" + (f" ({opp['record']})" if opp["record"] else ""),
            f"⏰ {report.kst(g['date'])} 킥오프" + (f" · {g['tv']}" if g["tv"] else ""),
            "━━━━━━━━━━",
        ]
    for g in new_games:  # 관심 팀 경기 결과도 맨 위에
        if fav and fav["abbr"] in (g["away"]["abbr"], g["home"]["abbr"]):
            res, where = report.short_score(g, fav["abbr"])
            lines += [f"⭐ {fav['short']} {res} ({where})", "━━━━━━━━━━"]
    lines.append(f"🏈 NFL {week}주차 리포트 ({report.kst(updated)})")
    if new_games:
        lines.append(f"새 경기 결과 {len(new_games)}건")
        for g in new_games[:max_scores]:
            lines.append(f"· {g['away']['abbr']} {g['away']['score']}-{g['home']['score']} {g['home']['abbr']}")
        if len(new_games) > max_scores:
            lines.append(f"· 외 {len(new_games) - max_scores}경기" if max_scores else "· 전체 결과는 아래 링크에서")
    if new_news:
        lines.append(f"새 소식 {len(new_news)}건")
    if fav and not alert:
        s = f"⭐ {fav['abbr']} {fav['record']}"
        if fav.get("next"):
            g = fav["next"]
            opp = g["away"] if g["home"]["abbr"] == fav["abbr"] else g["home"]
            s += f" · 다음 {opp['abbr']}전 {report.kst(g['date'])}"
        lines.append(s)
    return "\n".join(lines)


def load_state():
    state = {"games": [], "articles": [], "alerts": [], "ko": {}}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--build-only", action="store_true", help="페이지만 만들고 보낼 카톡은 pending.json 에 남김")
    ap.add_argument("--send-pending", action="store_true", help="pending.json 의 카톡을 전송")
    args = ap.parse_args()

    if args.send_pending:
        if PENDING_FILE.exists():
            deliver(json.loads(PENDING_FILE.read_text(encoding="utf-8")), load_state())
            PENDING_FILE.unlink()
        else:
            log("보낼 카톡 없음")
        return

    wait_for_network()
    state = load_state()
    updated = now_utc()
    build = f"{updated.astimezone(KST):%Y%m%d%H%M}"

    week, season, results, upcoming = fetch_games()
    news = fetch_news()[:MAX_NEWS]
    try:
        fav = fetch_favorite(week)
    except Exception as e:  # 관심 팀 조회 실패가 전체 리포트를 막지 않도록
        log(f"관심 팀 조회 실패: {e}")
        fav = None
    fav_news = fav["news"] if fav else []
    if fav and fav.get("next"):
        hours = (fav["next"]["date"] - updated).total_seconds() / 3600
        fav["gameday"] = fav["next"]["state"] == "in" or -5 <= hours <= GAMEDAY_HOURS
        fav["days_left"] = (fav["next"]["date"].astimezone(KST).date() - updated.astimezone(KST).date()).days
    news = [n for n in news if n["id"] not in {f["id"] for f in fav_news}]

    add_summaries(results, news + fav_news, state["ko"], not args.no_llm)

    def safe(label, fn, default):
        try:
            return fn()
        except Exception as e:  # 부가 페이지 실패가 전체 리포트를 막지 않도록
            log(f"{label} 조회 실패: {e}")
            return default

    standings = safe("순위", fetch_standings, [])
    leaders = safe("시즌 리더", fetch_season_leaders, [])
    fav_players = safe("관심 팀 선수", lambda: fetch_fav_players(fav, season), {"season": [], "last": []}) if fav else None

    data = {
        "week": week, "updated": updated, "build": build, "fav": fav, "results": results, "upcoming": upcoming,
        "news": news, "standings": standings, "leaders": leaders, "fav_players": fav_players,
        "week_top": week_performers(results, week),
    }
    SITE_DIR.mkdir(exist_ok=True)
    for name, render in (("index", report.render), ("standings", report.render_standings), ("players", report.render_players)):
        (SITE_DIR / f"{name}.html").write_text(render(data), encoding="utf-8")

    new_games = [g for g in results if g["id"] not in state["games"]]
    new_news = [n for n in news + fav_news if n["id"] not in state["articles"]]
    log(f"페이지 생성: 결과 {len(results)}, 예정 {len(upcoming)}, 기사 {len(news)}+{len(fav_news)} / 새 경기 {len(new_games)}, 새 기사 {len(new_news)}")
    # 경기일 알림은 경기당 한 번만 (새 소식이 없어도 보낸다)
    alert = bool(fav and fav.get("gameday") and fav["next"]["id"] not in state["alerts"])
    msg = build_message(week, new_games, new_news, fav, updated, alert)

    if args.dry_run:
        print(f"--- 카톡 메시지 ({len(msg)}자)\n{msg}\n--- 페이지: {SITE_DIR / 'index.html'}")
        return

    # 요약 캐시는 전송 여부와 무관하게 저장 (다음 실행에서 재요약 방지)
    state["ko"] = dict(list(state["ko"].items())[-400:])
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    published = True if args.build_only else publish(build)  # --build-only 면 배포는 워크플로가 맡는다
    if not (new_games or new_news or alert or args.force):
        PENDING_FILE.unlink(missing_ok=True)
        return
    if not published:
        sys.exit("페이지가 게시되지 않아 카톡 링크를 보내지 않았습니다. site/ 저장소와 .env 의 PAGE_URL 을 확인하세요.")

    # 버튼이 안 열리는 경우를 대비해 본문에도 주소를 반드시 넣는다. 자리가 모자라면 경기 결과 줄을 줄인다.
    text_link = f"\n👉 {PAGE_URL}/?{build[-8:]}"
    for n in (4, 3, 2, 1, 0):
        msg = build_message(week, new_games, new_news, fav, updated, alert, max_scores=n)
        if len(msg) + len(text_link) <= kakao.TEXT_LIMIT:
            break
    pending = {
        "msg": msg[: kakao.TEXT_LIMIT - len(text_link)] + text_link,
        "link": f"{PAGE_URL}/?v={build}",
        "button": "경기 정보 보기" if alert else "리포트 보기",
        "alert": fav["next"]["id"] if alert else None,
        "games": [g["id"] for g in new_games],
        "articles": [n["id"] for n in new_news],
    }
    if args.build_only:
        PENDING_FILE.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
        log("카톡은 페이지 배포 후 전송 (--send-pending)")
        return
    deliver(pending, state)


def deliver(p, state):
    """카톡 전송 후, 보낸 경기·기사를 상태에 기록한다."""
    sent, failed = kakao.send_all(p["msg"], p["link"], p["button"])
    for name, why in failed.items():
        log(f"전송 실패({name}): {why}")
    if not sent:
        sys.exit("[kakao] 모든 수신자 전송 실패")
    if p["alert"]:
        state["alerts"] = (state["alerts"] + [p["alert"]])[-50:]
    state["games"] = (state["games"] + p["games"])[-500:]
    state["articles"] = (state["articles"] + p["articles"])[-500:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    log(f"카카오톡 전송 완료: {', '.join(sent)}")

if __name__ == "__main__":
    try:
        main()
    except kakao.KakaoError as e:
        sys.exit(f"[kakao] {e}")
