"""NFL 리포트 한 장짜리 HTML 페이지 렌더러 (모바일 우선, 외부 의존성 없음)."""
import datetime as dt
import re
from html import escape as h

KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = "월화수목금토일"

CSS = """
:root{--bg:#f4f2ee;--card:#fff;--ink:#16181d;--sub:#646a75;--line:#e4e1da;--fav:#e31837;--gold:#ffb612;--win:#16181d;--lose:#9aa0aa}
@media(prefers-color-scheme:dark){:root{--bg:#0f1114;--card:#191c21;--ink:#f1f2f4;--sub:#9299a5;--line:#2a2e36;--win:#fff;--lose:#6d7480}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif;-webkit-text-size-adjust:100%}
a{color:inherit;text-decoration:none}
img{display:block;max-width:100%}
.wrap{max-width:720px;margin:0 auto;padding:0 16px 48px}
.mast{padding:28px 0 18px;border-bottom:3px solid var(--ink);margin-bottom:20px}
.kicker{font-size:12px;font-weight:800;letter-spacing:.18em;color:var(--fav)}
.mast h1{font-size:34px;font-weight:900;letter-spacing:-.03em;line-height:1.15;margin:4px 0 6px}
.mast p{font-size:13px;color:var(--sub)}
h2{font-size:13px;font-weight:800;letter-spacing:.12em;color:var(--sub);margin:34px 0 12px;display:flex;align-items:center;gap:10px}
h2:after{content:"";flex:1;height:1px;background:var(--line)}
.fav{background:linear-gradient(135deg,#e31837 0%,#a80f26 100%);color:#fff;border-radius:18px;padding:20px;position:relative;overflow:hidden}
.fav:before{content:"";position:absolute;right:-40px;top:-40px;width:200px;height:200px;border-radius:50%;background:rgba(255,182,18,.16)}
.fav-head{display:flex;align-items:center;gap:14px;position:relative}
.fav-head img{width:68px;height:68px}
.fav-name{font-size:13px;font-weight:700;opacity:.85}
.fav-rec{font-size:34px;font-weight:900;line-height:1.1;letter-spacing:-.02em}
.fav-rec small{font-size:14px;font-weight:700;color:var(--gold);margin-left:8px;letter-spacing:0}
.badge{display:inline-block;background:var(--gold);color:#3a2a00;font-size:12px;font-weight:800;border-radius:99px;padding:3px 10px;margin-top:12px}
.fav-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;position:relative}
.fav-box{background:rgba(0,0,0,.2);border-radius:12px;padding:12px}
.fav-box b{display:block;font-size:11px;letter-spacing:.1em;color:var(--gold);margin-bottom:4px}
.fav-box span{display:block;font-size:17px;font-weight:800}
.fav-box em{display:block;font-style:normal;font-size:12px;opacity:.85;margin-top:2px}
.fav-news{margin-top:14px;position:relative}
.fav-news a{display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px solid rgba(255,255,255,.18)}
.fav-news img{width:84px;height:56px;object-fit:cover;border-radius:8px;flex:none}
.fav-news strong{font-size:15px;line-height:1.35;display:block}
.fav-news p{font-size:13px;opacity:.85;line-height:1.45;margin-top:2px}
.game{background:var(--card);border-radius:16px;padding:16px;margin-bottom:12px;border:1px solid var(--line)}
.game.is-fav{border:2px solid var(--gold)}
.team{display:flex;align-items:center;gap:10px;padding:5px 0;color:var(--lose)}
.team.w{color:var(--win)}
.team img{width:34px;height:34px}
.team .nm{font-size:17px;font-weight:800;flex:1}
.team .nm small{font-size:12px;font-weight:500;color:var(--sub);margin-left:6px}
.team .sc{font-size:26px;font-weight:900;font-variant-numeric:tabular-nums}
.leaders{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.ld{text-align:center;min-width:0}
.ld img{width:56px;height:56px;border-radius:50%;object-fit:cover;object-position:top;background:var(--bg);margin:0 auto 6px}
.ld b{display:block;font-size:10px;letter-spacing:.1em;color:var(--fav)}
.ld span{display:block;font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ld em{display:block;font-style:normal;font-size:11px;color:var(--sub);line-height:1.35}
.recap{font-size:14px;color:var(--sub);margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.up{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:4px 16px}
.up-row{display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}
.up-row:last-child{border:0}
.up-row time{width:104px;flex:none;color:var(--sub);font-size:12px;font-variant-numeric:tabular-nums}
.up-row img{width:22px;height:22px}
.up-row b{font-weight:800}
.up-row i{font-style:normal;color:var(--sub);font-size:12px}
.lead{display:block;background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;margin-bottom:12px}
.lead img{width:100%;aspect-ratio:16/9;object-fit:cover}
.lead div{padding:16px}
.lead strong{font-size:22px;font-weight:900;line-height:1.3;letter-spacing:-.02em;display:block}
.item{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.item img{width:112px;height:76px;object-fit:cover;border-radius:10px;flex:none}
.item strong{font-size:16px;font-weight:800;line-height:1.35;display:block}
.sum{font-size:14px;color:var(--sub);margin-top:6px}
.en{font-size:11px;color:var(--sub);opacity:.75;margin-top:6px;line-height:1.35}
.item>div,.fav-news a>div,.lead>div{min-width:0;flex:1}
body{overflow-wrap:anywhere;word-break:keep-all}
.nav{display:flex;gap:6px;position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0;margin-bottom:6px}
.nav a{flex:1;text-align:center;font-size:14px;font-weight:800;padding:9px 0;border-radius:99px;background:var(--card);border:1px solid var(--line);color:var(--sub)}
.nav a.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.conf{font-size:22px;font-weight:900;margin:28px 0 4px;letter-spacing:-.02em}
.tbl{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:13px;font-variant-numeric:tabular-nums}
.tbl caption{text-align:left;font-size:13px;font-weight:800;letter-spacing:.08em;color:var(--sub);padding:14px 2px 8px}
.tbl th{font-size:11px;font-weight:700;color:var(--sub);padding:8px 4px;text-align:right;border-bottom:1px solid var(--line)}
.tbl td{padding:9px 4px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl tr:last-child td{border-bottom:0}
.tbl th:first-child,.tbl td:first-child{text-align:left;padding-left:12px}
.tbl th:last-child,.tbl td:last-child{padding-right:12px}
.tbl .tm{display:flex;align-items:center;gap:8px;font-weight:800;font-size:14px}
.tbl .tm img{width:24px;height:24px}
.tbl .tm i{font-style:normal;font-size:10px;font-weight:800;color:var(--sub);width:14px}
.tbl tr.me td{background:rgba(255,182,18,.16)}
.tbl .pos{color:#1a9f53}.tbl .neg{color:#d6453d}
.note{font-size:12px;color:var(--sub);margin-top:8px}
.more{display:flex;gap:8px;margin-top:12px}
.more a{flex:1;text-align:center;font-size:14px;font-weight:800;padding:12px 0;border-radius:12px;background:var(--card);border:1px solid var(--line)}
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.pc{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;display:flex;gap:10px;align-items:center;min-width:0}
.pc img{width:54px;height:54px;border-radius:50%;object-fit:cover;object-position:top;background:var(--bg);flex:none}
.pc div{min-width:0}
.pc b{display:block;font-size:10px;letter-spacing:.1em;color:var(--fav)}
.pc span{display:block;font-size:14px;font-weight:800;line-height:1.25}
.pc em{display:block;font-style:normal;font-size:11px;color:var(--sub);line-height:1.35;margin-top:2px}
.cat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:6px 14px;margin-bottom:12px}
.cat h3{font-size:13px;font-weight:800;color:var(--fav);padding:8px 0 2px;letter-spacing:.04em}
.row{display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.row:first-of-type{border-top:0}
.row .rk{width:14px;font-size:12px;font-weight:800;color:var(--sub);text-align:center}
.row img{width:36px;height:36px;border-radius:50%;object-fit:cover;object-position:top;background:var(--bg);flex:none}
.row .who{flex:1;min-width:0;font-size:14px;font-weight:800;line-height:1.25}
.row .who small{display:block;font-size:11px;font-weight:500;color:var(--sub)}
.row .val{font-size:15px;font-weight:900;font-variant-numeric:tabular-nums;text-align:right}
.row.top .val{font-size:20px}
.row .val{white-space:nowrap}
.row.top img{width:48px;height:48px}
.row.me .who{color:var(--fav)}
.gd{position:relative;background:var(--gold);color:#2b1f00;border-radius:14px;padding:14px;margin-top:14px;text-align:center;box-shadow:0 0 0 3px rgba(255,255,255,.35);animation:gd 1.6s ease-in-out infinite}
@keyframes gd{50%{box-shadow:0 0 0 8px rgba(255,255,255,.12)}}
.gd-tag{font-size:13px;font-weight:900;letter-spacing:.22em}
.gd-vs{display:flex;align-items:center;justify-content:center;gap:12px;font-size:22px;font-weight:900;margin:8px 0 4px}
.gd-vs img{width:48px;height:48px}
.gd-vs small{display:block;font-size:11px;font-weight:700;opacity:.7}
.gd-time{font-size:17px;font-weight:900}
.gd-sub{font-size:12px;font-weight:700;opacity:.75;margin-top:2px}
.up-row.me{background:rgba(255,182,18,.18);margin:0 -16px;padding:12px 16px;border-left:4px solid var(--gold)}
.up-row.me b{color:var(--fav)}
.empty{color:var(--sub);font-size:14px;padding:8px 0}
footer{margin-top:36px;font-size:11px;color:var(--sub);line-height:1.6}
"""

IMG = '<img src="{}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">'


def img(url):
    return IMG.format(h(url)) if url else ""


def kst(d, with_date=True):
    d = d.astimezone(KST)
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]}) {d:%H:%M}" if with_date else f"{d:%H:%M}"


def standing_ko(s):
    m = re.match(r"(\d+)\w+ in (.+)", s or "")
    return f"{m.group(2)} {m.group(1)}위" if m else (s or "")


def team_row(t):
    cls = "team w" if t.get("winner") else "team"
    rec = f"<small>{h(t['record'])}</small>" if t.get("record") else ""
    return (
        f'<div class="{cls}">{img(t["logo"])}<span class="nm">{h(t["name"])}{rec}</span>'
        f'<span class="sc">{h(str(t["score"]))}</span></div>'
    )


def game_card(g, fav):
    is_fav = fav in (g["away"]["abbr"], g["home"]["abbr"])
    out = [f'<article class="game{" is-fav" if is_fav else ""}">', team_row(g["away"]), team_row(g["home"])]
    if g["leaders"]:
        out.append('<div class="leaders">')
        for l in g["leaders"]:
            out.append(
                f'<div class="ld">{img(l["headshot"])}<b>{h(l["label"])}</b>'
                f'<span>{h(l["name"])}</span><em>{h(l["team"])} · {h(l["stat"])}</em></div>'
            )
        out.append("</div>")
    if g.get("ko"):
        link = f' <a href="{h(g["url"])}">자세히 ›</a>' if g.get("url") else ""
        out.append(f'<p class="recap">{h(g["ko"])}{link}</p>')
    out.append("</article>")
    return "".join(out)


def short_score(g, fav):
    """관심 팀 기준 한 줄 결과: ('승 30-27', '@ Raiders')."""
    me, opp = (g["home"], g["away"]) if g["home"]["abbr"] == fav else (g["away"], g["home"])
    where = "vs" if me is g["home"] else "@"
    res = "승" if me.get("winner") else "패"
    return f"{res} {me['score']}-{opp['score']}", f"{where} {opp['name']}"


def dday(n):
    return "" if n is None else " · 오늘" if n == 0 else " · 내일" if n == 1 else f" · D-{n}"


def fav_section(f):
    out = ['<section class="fav">', '<div class="fav-head">', img(f["logo"]), "<div>"]
    out.append(f'<div class="fav-name">⭐ MY TEAM · {h(f["name"])}</div>')
    out.append(f'<div class="fav-rec">{h(f["record"])}<small>{h(standing_ko(f["standing"]))}</small></div>')
    out.append("</div></div>")
    if f.get("bye"):
        out.append('<span class="badge">이번 주는 휴식 주간 (BYE)</span>')
    if f.get("gameday") and f.get("next"):
        g = f["next"]
        live = g["state"] == "in"

        def side(t):
            rec = f'<small>{h(t["record"])}</small>' if t.get("record") else ""
            return f'{img(t["logo"])}<span>{h(t["abbr"])}{rec}</span>'

        out.append(
            f'<div class="gd"><div class="gd-tag">{"🔴 LIVE · 경기 진행 중" if live else "🏈 GAME DAY"}</div>'
            f'<div class="gd-vs">{side(g["away"])}<span>@</span>{side(g["home"])}</div>'
            f'<div class="gd-time">{kst(g["date"])} 킥오프</div>'
            f'<div class="gd-sub">한국시간{" · " + h(g["tv"]) if g.get("tv") else ""}</div></div>'
        )
    out.append('<div class="fav-grid">')
    if f.get("last"):
        a, b = short_score(f["last"], f["abbr"])
        day = kst(f["last"]["date"]).split(" ")[0]
        out.append(f'<div class="fav-box"><b>지난 경기</b><span>{h(a)}</span><em>{h(b)} · {day}</em></div>')
    if f.get("next"):
        g = f["next"]
        home = g["home"]["abbr"] == f["abbr"]
        opp = g["away"] if home else g["home"]
        out.append(
            f'<div class="fav-box"><b>다음 경기</b><span>{"vs" if home else "@"} {h(opp["name"])}</span>'
            f'<em>{kst(g["date"])} KST{dday(f.get("days_left"))}</em></div>'
        )
    out.append("</div>")
    if f.get("news"):
        out.append('<div class="fav-news">')
        for n in f["news"]:
            out.append(
                f'<a href="{h(n["url"])}">{img(n["image"])}<div><strong>{h(n.get("title") or n["headline"])}</strong>'
                f'<p>{kst(n["published"]).split(" ")[0]} · {h(n.get("ko") or "")}</p></div></a>'
            )
        out.append("</div>")
    out.append("</section>")
    return "".join(out)


def news_section(news):
    if not news:
        return '<p class="empty">새 소식이 없습니다.</p>'
    out = []
    for i, n in enumerate(news):
        title = h(n.get("title") or n["headline"])
        summary = f'<p class="sum">{h(n["ko"])}</p>' if n.get("ko") else ""
        en = f'<p class="en">{h(n["headline"])}</p>' if n.get("title") else ""
        cls = "lead" if i == 0 else "item"
        out.append(f'<a class="{cls}" href="{h(n["url"])}">{img(n["image"])}<div><strong>{title}</strong>{summary}{en}</div></a>')
    return "".join(out)


def upcoming_section(games, fav=""):
    out = ['<div class="up">']
    for g in games:
        tv = f'<i>{h(g["tv"])}</i>' if g.get("tv") else ""
        live = " · 진행 중" if g["state"] == "in" else ""
        out.append(
            f'<div class="up-row{" me" if fav in (g["away"]["abbr"], g["home"]["abbr"]) else ""}"><time>{kst(g["date"])}{live}</time>{img(g["away"]["logo"])}<b>{h(g["away"]["abbr"])}</b>'
            f'<i>@</i>{img(g["home"]["logo"])}<b>{h(g["home"]["abbr"])}</b><span style="flex:1"></span>{tv}</div>'
        )
    out.append("</div>")
    return "".join(out)


ON, ME = ' class="on"', ' class="me"'
STREAK = {"W": "연승", "L": "연패", "T": "무"}
NAV = (("index.html", "리포트"), ("standings.html", "순위"), ("players.html", "선수"))


def shell(d, active, title, body):
    """공통 머리말·탭·바닥글을 씌운 완성 HTML."""
    up = d["updated"].astimezone(KST)
    stamp = f"{up.year}.{up.month}.{up.day}({WEEKDAYS[up.weekday()]}) {up:%H:%M} 업데이트"
    nav = "".join(
        f'<a href="{href}?v={d["build"]}"{ON if href == active else ""}>{label}</a>' for href, label in NAV
    )
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<meta name="build" content="{d["build"]}">'
        f'<title>NFL {d["week"]}주차 {title}</title><style>{CSS}</style></head>'
        '<body><div class="wrap">'
        '<header class="mast"><div class="kicker">NFL DAILY REPORT</div>'
        f'<h1>{d["week"]}주차 {title}</h1><p>{stamp} · 시각은 모두 한국시간</p></header>'
        f'<nav class="nav">{nav}</nav>{body}'
        "<footer>데이터·사진: ESPN · 한국어 요약은 AI가 자동 생성해 오역이 있을 수 있습니다. 기록과 점수는 원본 수치 그대로입니다.</footer>"
        "</div></body></html>"
    )


def streak_ko(s):
    m = re.match(r"([WLT])(\d+)", s or "")
    return f"{m.group(2)}{STREAK[m.group(1)]}" if m else (s or "")


def division_table(div, fav, seeds=True):
    rows = []
    for t in div["teams"]:
        diff_cls = "pos" if t["diff"].startswith("+") else "neg" if t["diff"].startswith("-") else ""
        seed = f'<i>{t["seed"]}</i>' if seeds and t["seed"] <= 7 else "<i></i>" if seeds else ""
        rows.append(
            f'<tr{ME if t["abbr"] == fav else ""}><td><span class="tm">{seed}{img(t["logo"])}{h(t["name"])}</span></td>'
            f'<td><b>{h(t["record"])}</b></td><td>{h(t["pct"])}</td><td>{h(t["pf"])}-{h(t["pa"])}</td>'
            f'<td class="{diff_cls}">{h(t["diff"])}</td><td>{h(streak_ko(t["streak"]))}</td></tr>'
        )
    return (
        f'<table class="tbl"><caption>{h(div["name"])}</caption>'
        "<tr><th>팀</th><th>전적</th><th>승률</th><th>득-실</th><th>득실차</th><th>흐름</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def fav_division(d):
    fav = d["fav"]["abbr"] if d.get("fav") else ""
    for conf in d.get("standings") or []:
        for div in conf["divisions"]:
            if any(t["abbr"] == fav for t in div["teams"]):
                return div
    return None


def render(d):
    fav = d["fav"]["abbr"] if d.get("fav") else ""
    body = []
    if d.get("fav"):
        body.append(fav_section(d["fav"]))
        div = fav_division(d)
        if div:
            body.append(division_table(div, fav, seeds=False))
        v = d["build"]
        body.append(f'<div class="more"><a href="standings.html?v={v}">전체 순위 ›</a><a href="players.html?v={v}">선수 활약 ›</a></div>')
    body.append("<h2>경기 결과</h2>")
    body.append("".join(game_card(g, fav) for g in d["results"]) or '<p class="empty">최근 종료된 경기가 없습니다.</p>')
    if d["upcoming"]:
        body.append("<h2>예정 경기</h2>" + upcoming_section(d["upcoming"], fav))
    body.append("<h2>주요 소식</h2>" + news_section(d["news"]))
    return shell(d, "index.html", "리포트", "".join(body))


def render_standings(d):
    fav = d["fav"]["abbr"] if d.get("fav") else ""
    body = []
    for conf in d.get("standings") or []:
        body.append(f'<div class="conf">{h(conf["name"])}</div>')
        body += [division_table(div, fav) for div in conf["divisions"]]
    if not body:
        body.append('<p class="empty">순위 정보를 불러오지 못했습니다.</p>')
    else:
        body.append('<p class="note">팀 이름 앞 숫자는 컨퍼런스 플레이오프 시드(1~7위 진출권)입니다. 득-실은 총 득점-총 실점.</p>')
    return shell(d, "standings.html", "순위", "".join(body))


def player_cards(rows):
    return '<div class="pgrid">' + "".join(
        f'<div class="pc">{img(r["headshot"])}<div><b>{h(r["label"])}</b><span>{h(r["name"])}</span>'
        f'<em>{h(r["pos"])}{" · " if r["pos"] else ""}{h(r["value"])}</em></div></div>'
        for r in rows
    ) + "</div>"


def category_list(cat, fav):
    rows = []
    for i, p in enumerate(cat["players"]):
        cls = "row" + (" top" if i == 0 and len(p["value"]) <= 8 else "") + (" me" if p["team"] == fav else "")
        sub = " · ".join(x for x in (p["team"], p.get("pos", "")) if x)
        rows.append(
            f'<div class="{cls}"><span class="rk">{i + 1}</span>{img(p["headshot"])}'
            f'<span class="who">{h(p["name"])}<small>{h(sub)}</small></span><span class="val">{h(p["value"])}</span></div>'
        )
    return f'<div class="cat"><h3>{h(cat["label"])}</h3>{"".join(rows)}</div>'


def render_players(d):
    fav = d.get("fav") or {}
    body = []
    fp = d.get("fav_players")
    if fp and (fp["season"] or fp["last"]):
        if fp["season"]:
            body.append(f'<h2>⭐ {h(fav["name"])} · 시즌 누적</h2>' + player_cards(fp["season"]))
        if fp["last"]:
            a, b = short_score(fav["last"], fav["abbr"])
            body.append(f'<h2>⭐ 지난 경기 활약 · {h(b)} {h(a)}</h2>' + player_cards(fp["last"]))
    if d.get("week_top"):
        body.append(f'<h2>{d["week"]}주차 톱 퍼포머 (종료 경기 기준)</h2>')
        body += [category_list(c, fav.get("abbr", "")) for c in d["week_top"]]
    if d.get("leaders"):
        body.append("<h2>시즌 부문별 리더</h2>")
        body += [category_list(c, fav.get("abbr", "")) for c in d["leaders"]]
    if not body:
        body.append('<p class="empty">선수 기록을 불러오지 못했습니다.</p>')
    return shell(d, "players.html", "선수 활약", "".join(body))
