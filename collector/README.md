# BIMS 수집기

갤럭시 S21(Termux)에서 부산 BIMS 정류소 도착정보와 노선별 버스 위치를 수집합니다.
추가 패키지 없이 파이썬 기본 기능만 사용합니다.

## 준비

```bash
pkg install python -y
termux-setup-storage
cp keys.example.json keys.local.json   # 인증키 입력 (이 파일은 저장소에 올라가지 않음)
nano bims_collector.py                  # STOPS에 정류소 번호 입력
```

## 명령

| 명령 | 하는 일 |
|---|---|
| `python bims_collector.py find 성곡시장입구` | 정류소 번호(bstopid) 찾기 |
| `python bims_collector.py test` | 정류소마다 한 번씩 호출해 보기, 자동 간격 확인 |
| `python bims_collector.py plan` | 한도에 맞는 조회 간격 계산 |
| `termux-wake-lock && python bims_collector.py run` | 수집 시작 (06~23시) |
| `python bims_collector.py status` | 오늘 호출 수, 마지막 수집 시각, 파일 크기 |

## 동작

- 상세기능별 하루 한도(`DAILY_LIMIT`)와 남은 시간을 보고 조회 간격을 자동으로 조절합니다 (최소 15초).
- 응답은 가공하지 않고 조회 시각과 함께 `내장 저장공간/BIMS/날짜/`에 저장합니다.
- 지난 날짜의 파일은 자동으로 `.jsonl.gz`로 압축합니다.

## 저장 파일

| 파일 | 내용 |
|---|---|
| `stopArr.jsonl` | 정류소 도착정보 원본 |
| `busPos.jsonl` | 노선별 버스 위치 원본 |
| `_counts.json` | 키·기능별 오늘 호출 수, 마지막 수집 시각 |
| `_errors.jsonl`, `_log.txt` | 오류와 진행 기록 |
