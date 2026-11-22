# -*- coding: utf-8 -*-
"""
뛸까 말까 - 부산 BIMS 수집기 (갤럭시 S21 + Termux용, 추가 설치 패키지 없음)

사용법
  python bims_collector.py find 성곡시장입구   # 정류소 번호(bstopid) 찾기
  python bims_collector.py test               # 설정한 정류소마다 한 번씩 호출해 보기
  python bims_collector.py plan               # 한도에 맞는 조회 간격 계산만 해 보기
  python bims_collector.py run                # 수집 시작 (06~23시에만 호출, 그 외에는 대기)
  python bims_collector.py status             # 오늘 호출 수, 마지막 성공 시각, 파일 크기
"""
import sys, os, time, json, gzip, re, math, urllib.request, urllib.parse, datetime

# ==================== 설정 (여기만 고치면 됨) ====================
# 인증키: keys.local.json 파일에 {"A": "내 키", "B": "팀원 키"} 형식으로 적기 (저장소에 올리지 않음)
KEYS = {
    "A": os.environ.get("BIMS_KEY_A", ""),
    "B": os.environ.get("BIMS_KEY_B", ""),
}

# 키마다 상세기능별 하루 호출 한도 (마이페이지에서 확인한 값으로 고치기)
DAILY_LIMIT = {
    "A": {"stopArrByBstopid": 100000, "busInfoByRouteId": 100000},
    "B": {"stopArrByBstopid": 10000, "busInfoByRouteId": 10000},
}

# 정류소 이름: (bstopid, 사용할 키)  ← find 명령으로 찾은 번호를 적고, 줄 앞 # 지우기
STOPS = {
    # "성곡시장입구_1": ("000000000", "A"),
    # "성곡시장입구_2": ("000000000", "A"),
    # "운촌_1":        ("000000000", "A"),
    # "운촌_2":        ("000000000", "A"),
    # "B정류장_진구":   ("000000000", "B"),
    # "C정류장_진구":   ("000000000", "B"),
    # "B정류장_해운대": ("000000000", "B"),
    # "C정류장_해운대": ("000000000", "B"),
}

START_HOUR, END_HOUR = 6, 23     # 06:00 ~ 22:59 수집
TARGET_INTERVAL = 15             # 원하는 조회 간격(초). 한도가 부족하면 자동으로 늘어남
COLLECT_POSITION = True          # 노선별 버스 위치 수집 (간격은 남은 한도에 맞춰 자동 조절)
POSITION_MIN_INTERVAL = 15       # 버스 위치 최소 간격(초)
POSITION_KEY = "A"               # 버스 위치에 쓸 키
SAFETY_MARGIN = 100              # 한도에서 이만큼 남기고 멈춤
BASE = "http://apis.data.go.kr/6260000/BusanBIMS"
OUT_DIR = os.path.expanduser("~/storage/shared/BIMS")   # 내장 저장공간/BIMS
# ================================================================

# 인증키는 저장소에 올리지 않는다: 같은 폴더의 keys.local.json 또는 환경변수에서 읽음
_kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.local.json")
if os.path.exists(_kf):
    with open(_kf, encoding="utf-8") as _f:
        KEYS.update({k: v for k, v in json.load(_f).items() if v})

# 정류소 목록: 같은 폴더의 stops.json이 있으면 그것을 씀 {"이름": ["bstopid", "키"]}
_sf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stops.json")
if os.path.exists(_sf):
    with open(_sf, encoding="utf-8") as _f:
        STOPS = {k: tuple(v) for k, v in json.load(_f).items() if not k.startswith("_")}

HOURS_SEC = (END_HOUR - START_HOUR) * 3600


def now():
    return datetime.datetime.now()


def call(key_name, op, **params):
    q = urllib.parse.urlencode(dict(serviceKey=KEYS[key_name], **params))
    req = urllib.request.Request(f"{BASE}/{op}?{q}", headers={"User-Agent": "bims-collector"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def plan_interval():
    """키마다 정류소 수와 한도를 보고, 하루 안에 한도를 넘지 않는 최소 간격(15초 단위)을 구함"""
    worst = TARGET_INTERVAL
    lines = []
    for k in KEYS:
        n = sum(1 for _, (_, kk) in STOPS.items() if kk == k)
        if n == 0:
            continue
        lim = DAILY_LIMIT[k]["stopArrByBstopid"] - SAFETY_MARGIN
        need = math.ceil(n * HOURS_SEC / lim)
        need = max(TARGET_INTERVAL, math.ceil(need / 15) * 15)
        worst = max(worst, need)
        lines.append(f"키 {k}: 정류소 {n}곳, 한도 {lim + SAFETY_MARGIN} → 최소 간격 {need}초")
    for k in KEYS:
        n = sum(1 for _, (_, kk) in STOPS.items() if kk == k)
        if n:
            lines.append(f"  간격 {worst}초로 하면 키 {k} 하루 호출 약 {n * HOURS_SEC // worst}번")
    return worst, lines


def day_dir(d=None):
    p = os.path.join(OUT_DIR, d or now().strftime("%Y-%m-%d"))
    os.makedirs(p, exist_ok=True)
    return p


def load_counts():
    try:
        with open(os.path.join(day_dir(), "_counts.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def save_counts(c):
    with open(os.path.join(day_dir(), "_counts.json"), "w") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def append(name, rec):
    with open(os.path.join(day_dir(), name + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log(msg):
    line = f"{now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(os.path.join(day_dir(), "_log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def compress_old_days():
    today = now().strftime("%Y-%m-%d")
    if not os.path.isdir(OUT_DIR):
        return
    for d in sorted(os.listdir(OUT_DIR)):
        p = os.path.join(OUT_DIR, d)
        if d >= today or not os.path.isdir(p):
            continue
        for fn in os.listdir(p):
            if fn.endswith(".jsonl"):
                src = os.path.join(p, fn)
                with open(src, "rb") as a, gzip.open(src + ".gz", "wb") as b:
                    b.write(a.read())
                os.remove(src)
                log(f"압축 완료 {d}/{fn}.gz")


def guarded_call(key_name, op, counts, **params):
    ck = f"{key_name}:{op}"
    if counts.get(ck, 0) >= DAILY_LIMIT[key_name][op] - SAFETY_MARGIN:
        return None
    t0 = now()
    counts[ck] = counts.get(ck, 0) + 1
    try:
        status, body = call(key_name, op, **params)
    except Exception as e:
        append("_errors", {"t": t0.isoformat(timespec="seconds"), "key": key_name, "op": op,
                           "params": params, "err": repr(e)})
        return None
    return {"t": t0.isoformat(timespec="seconds"), "op": op, "params": params,
            "status": status, "body": body}


def check_config():
    ok = True
    if not STOPS:
        print("STOPS가 비어 있습니다. find 명령으로 정류소 번호를 찾아 적어 주세요."); ok = False
    for name, (sid, k) in STOPS.items():
        if k not in KEYS or not KEYS[k] or KEYS[k].startswith("여기에"):
            print(f"{name}: 키 {k}가 비어 있습니다."); ok = False
    return ok


def seconds_left_today():
    n = now()
    end = n.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
    return max(1.0, (end - n).total_seconds())


def adaptive_interval(calls_per_round, key_name, op, counts, min_iv):
    """남은 한도와 남은 시간을 보고, 오늘 끝까지 한도를 넘지 않는 간격을 다시 계산"""
    left = DAILY_LIMIT[key_name][op] - SAFETY_MARGIN - counts.get(f"{key_name}:{op}", 0)
    if left <= 0 or calls_per_round == 0:
        return None
    return max(min_iv, calls_per_round * seconds_left_today() / left)


def run():
    if not check_config():
        return
    interval, lines = plan_interval()
    log(f"수집기 시작: 정류소 {len(STOPS)}곳, 조회 간격 {interval}초")
    for l in lines:
        log(l)
    routes = set()
    last_pos = 0.0
    next_stop = {}
    last_hour_log = [-1]
    warned = set()
    compressed_for = None
    while True:
        n = now()
        if compressed_for != n.date():
            compress_old_days()
            compressed_for = n.date()
        if not (START_HOUR <= n.hour < END_HOUR):
            time.sleep(30)
            continue
        cycle = time.time()
        counts = load_counts()
        # 키별로 이번 차례에 부를지 결정 (남은 한도에 맞춰 간격 자동 조절)
        for k in KEYS:
            mine = [(nm, sid) for nm, (sid, kk) in STOPS.items() if kk == k]
            if not mine:
                continue
            iv = adaptive_interval(len(mine), k, "stopArrByBstopid", counts, interval)
            if iv is None:
                if (k, n.date()) not in warned:
                    log(f"키 {k} 정류소 도착정보 하루 한도 도달 - 오늘은 이 키의 정류소 수집 중단")
                    warned.add((k, n.date()))
                continue
            if time.time() - next_stop.get(k, 0) < iv - 0.5:
                continue
            next_stop[k] = time.time()
            for name, sid in mine:
                rec = guarded_call(k, "stopArrByBstopid", counts, bstopid=sid)
                if rec is None:
                    continue
                rec["stop"] = name
                append("stopArr", rec)
                routes.update(re.findall(r"<lineid>(\d+)</lineid>", rec["body"]))
        if COLLECT_POSITION and routes:
            piv = adaptive_interval(len(routes), POSITION_KEY, "busInfoByRouteId", counts, POSITION_MIN_INTERVAL)
            if piv is not None and time.time() - last_pos >= piv - 0.5:
                last_pos = time.time()
                for lid in sorted(routes):
                    rec = guarded_call(POSITION_KEY, "busInfoByRouteId", counts, lineid=lid)
                    if rec is not None:
                        append("busPos", slim_position(rec))
                if int(time.time()) // 3600 != last_hour_log[0]:
                    last_hour_log[0] = int(time.time()) // 3600
                    log(f"노선 {len(routes)}개, 버스 위치 간격 약 {piv:.0f}초")
        counts["last_cycle"] = now().isoformat(timespec="seconds")
        save_counts(counts)
        time.sleep(max(1.0, 15 - (time.time() - cycle)))


def slim_position(rec):
    """노선 위치 응답에는 버스가 없는 정류장 줄까지 모두 들어 있어 용량이 큼.
    차량번호(carno)가 있는 줄, 즉 버스가 있는 위치만 남김. 형식을 못 알아보면 원본 유지."""
    body = rec["body"]
    items = re.findall(r"<item>.*?</item>", body, re.S)
    if not items:
        return rec
    keep = [it for it in items if re.search(r"<carno>\s*[^<\s]+\s*</carno>", it)]
    if not keep and "<carno>" not in body:
        return rec
    rec = dict(rec)
    rec["body"] = "".join(keep)
    rec["n_items"] = len(items)
    return rec


def find(name):
    k = next(k for k, v in KEYS.items() if v and not v.startswith("여기에"))
    s, body = call(k, "busStopList", bstopnm=name)
    items = re.findall(r"<item>(.*?)</item>", body, re.S)
    if not items:
        print(body[:800]); return
    for it in items:
        g = lambda t: (re.search(fr"<{t}>(.*?)</{t}>", it) or [None, ""])[1]
        print(f"{g('bstopnm')}  bstopid={g('bstopid')}  arsno={g('arsno')}  위치={g('gpsy')},{g('gpsx')}")


def test():
    if not check_config():
        return
    for name, (sid, k) in STOPS.items():
        s, body = call(k, "stopArrByBstopid", bstopid=sid)
        cnt = len(re.findall(r"<item>", body))
        lids = sorted(set(re.findall(r"<lineid>(\d+)</lineid>", body)))
        print(f"{name} (키 {k}): HTTP {s}, 도착 정보 {cnt}건, 노선 {len(lids)}개")
        if cnt == 0:
            print("  응답 앞부분:", body[:400])
    if COLLECT_POSITION and lids:
        s2, b2 = call(POSITION_KEY, "busInfoByRouteId", lineid=lids[0])
        print(f"버스 위치 테스트 (노선 {lids[0]}): HTTP {s2}, {len(re.findall(r'<item>', b2))}건")
    interval, lines = plan_interval()
    print(f"\n실행하면 조회 간격은 {interval}초가 됩니다.")
    for l in lines:
        print(l)


def status():
    print(json.dumps(load_counts(), ensure_ascii=False, indent=1))
    p = day_dir()
    for fn in sorted(os.listdir(p)):
        print(f"{fn}: {os.path.getsize(os.path.join(p, fn)) / 1e6:.2f} MB")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "find":
        find(" ".join(sys.argv[2:]))
    elif cmd == "test":
        test()
    elif cmd == "plan":
        for l in plan_interval()[1]:
            print(l)
    elif cmd == "status":
        status()
    else:
        run()
