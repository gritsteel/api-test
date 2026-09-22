# 기상청 날씨 MCP 서버 (Python)

기상청 공공데이터를 두 개의 MCP 툴로 노출하는 최소 구성의 로컬 서버.
원래 이 디렉터리에 있던 미국 NWS 예제를 한국 데이터로 바꾼 것이다
(원본은 `weather.py.nws.bak`).

| 툴 | 기상청 오퍼레이션 | 하는 일 |
|---|---|---|
| `get_current_weather` | API Hub `typ01/cgi-bin/url/nph-aws2_min` | 지점의 현재 실황 — 기온·습도·기압·이슬점·강수감지·풍향·풍속 |
| `get_weather_alerts` | `WthrWrnInfoService/getWthrWrnList` + `getWthrWrnMsg` | 특보구역별 발표된 주의보·경보 목록과 통보문 본문 |

`get_current_weather` 는 서울·인천·부산·대구·광주·대전·울산·제주 8개 광역시만
지점번호를 자동으로 찾는다. 그 외 지역은 `stn` 인자로 기상청 관측지점번호를
직접 넘겨야 한다 (예: `get_current_weather(location="속초", stn=90)`).

리소스 `kma://locations` 는 내장 지명 91곳의 위·경도와 격자 좌표를 표로 돌려준다.

## 요구사항

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- 기상청 API Hub 인증키 — [apihub.kma.go.kr](https://apihub.kma.go.kr) 가입 후
  "지상관측 AWS 매분자료"(`nph-aws2_min`)를 활용신청하면 authKey 를 받는다.
  `get_current_weather` 가 쓴다.
- 공공데이터포털 인증키 — [기상청_기상특보 조회서비스](https://www.data.go.kr/data/15000415/openapi.do)를
  활용신청한다. `get_weather_alerts` 가 쓴다.

두 인증키는 발급처도 형식도 다른 별개의 키다. 서로 바꿔 넣으면 해당 툴만
조용히 인증 오류를 낸다.

## 인증키

키는 코드에 넣지 않고 환경변수에서 읽는다.

- `KMA_APIHUB_KEY` — API Hub authKey. `get_current_weather` 용.
- `KMA_API_KEY` — 공공데이터포털 일반 인증키. `get_weather_alerts` 용. 포털
  마이페이지가 보여주는 두 형태(Encoding / Decoding) 중 어느 쪽을 넣어도
  동작한다 — `%` 가 섞여 있으면 서버가 한 번 디코딩한다.

```bash
export KMA_APIHUB_KEY="발급받은-APIHub-authKey"
export KMA_API_KEY="발급받은-공공데이터포털-인증키"
# Windows PowerShell: $env:KMA_APIHUB_KEY="..."; $env:KMA_API_KEY="..."
uv run weather.py
```

`uv run` 이 첫 실행에서 가상환경과 의존성을 만든다. 서버는 stdio로 통신하므로
직접 실행하면 클라이언트를 기다리며 멈춰 있는 게 정상이다.

## Claude Desktop 등록

`claude_desktop_config.json` 에 아래를 넣는다
(Windows: `%APPDATA%\Claude\`, macOS: `~/Library/Application Support/Claude/`).
같은 내용이 이 디렉터리의 `claude_desktop_config.example.json` 에 들어 있다.

```json
{
  "mcpServers": {
    "weather": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\dev\\quickstart-resources\\weather-server-python",
        "run",
        "weather.py"
      ],
      "env": {
        "KMA_APIHUB_KEY": "발급받은-APIHub-authKey",
        "KMA_API_KEY": "발급받은-공공데이터포털-인증키"
      }
    }
  }
}
```

`uv` 가 PATH에 없다는 오류가 나면 `command` 를 `uv.exe` 의 절대 경로로 바꾼다
(`where uv` 로 확인). 설정을 고친 뒤에는 Claude Desktop을 완전히 종료하고 다시 켜야 한다.

## 사용 예

- "서울 지금 날씨 어때?" → `get_current_weather(location="서울")`
- "북위 37.48 동경 130.9 실황" → `get_current_weather(latitude=37.4843, longitude=130.9057)`
- "강원도에 특보 떴어?" → `get_weather_alerts(region="강원")`

지명은 광역시·도와 주요 시·군 91곳이 내장돼 있고, `경기도 수원시` 처럼
행정구역명이 붙어도 해석한다. 목록에 없는 곳은 위·경도로 조회한다.

특보구역(`region`)은 기상청 지점번호에 대응한다: 전국(108), 서울·인천·경기(109),
강원(105), 충북(131), 대전·세종·충남(133), 전북(146), 광주·전남(156),
대구·경북(143), 부산·울산·경남(159), 제주(184).

## 구현 노트

**격자 변환.** 기상청 동네예보는 위·경도가 아니라 5km 격자 `(nx, ny)` 를 받는다.
`latlon_to_grid()` 가 기상청 Lambert Conformal Conic 변환식
(`RE=6371.00877`, `GRID=5.0`, `SLAT1/2=30/60`, `OLON/OLAT=126/38`, `XO/YO=43/136`)을
그대로 구현한다. 서울(60,127)·부산(98,76)·인천(55,124)·대전(67,100)·광주(58,74)·
울산(102,84)·울릉도(127,127) 기준값으로 검증했다. `kma://locations` 리소스가
참고용으로 nx/ny/위경도를 모두 보여준다 — `get_current_weather` 자체는 관측지점번호
(`stn`)를 쓰므로 이 격자를 API 호출에 쓰지 않는다.

**관측지점번호(stn).** `nph-aws2_min` 은 위·경도가 아니라 기상청 관측지점번호를
받는다. 지점번호 전체 목록은 API Hub의 별도 메타데이터 API(`stn_inf.php`)로
조회할 수 있지만 그 자체가 추가 활용신청이 필요해서, 여기서는 실제 응답으로
직접 확인된 8개 광역시만 `STATIONS` 에 하드코딩했다. 그 외 지역은
`get_current_weather(stn=...)` 로 지점번호를 직접 넘겨야 한다.

**관측 지연 재시도.** 매분자료는 발표까지 몇 분의 지연이 있을 수 있다.
`get_current_weather` 가 요청 시각을 2분 전, 그다음 7분 전으로 두 번까지
시도해 빈 응답을 피한다.

**출력 스키마는 객체.** 특보 목록을 `RootModel[list[Alert]]` 로 두면 최상위 배열이
되어 더 간결하지만, 배열 루트 스키마는 프로토콜 개정판 `2026-07-28` 부터만
허용된다. 그 이전 버전을 협상한 클라이언트에서는 이 툴 하나가 아니라
`tools/list` 직렬화가 실패해 **서버 전체가 붙지 않는다**. 실제로 SDK 2.1.1
클라이언트가 `2025-11-25` 를 협상해 재현됐다. 그래서 `AlertList` 객체로 감쌌다.

**오류는 `ToolError`.** 평범한 `ValueError` 를 던지면 SDK가 크래시로 간주해
`"Error executing tool ..."` 만 남기고 원인을 감춘다. 지명 오탈자, 인증키 미설정,
`resultCode` 오류처럼 예상 가능한 실패는 `ToolError` 로 던져 메시지가
모델까지 전달되게 했다.

**결측값.** `nph-aws2_min` 은 결측·에러 값을 -50 이하의 코드(주로 `-99.9`)로 보낸다.
`_aws_float()` 이 이런 값을 `null` 로 정규화한다.

## 테스트

격자 변환 기준값, 지명 해석, base_time 규칙, 결측값 파싱, 인증키 처리,
목(mock) 응답 파싱, 툴·리소스 등록과 출력 스키마까지 검증하는 스크립트가 있다.

```bash
uv run tests/test_weather.py     # 단위 검증 (외부 호출 없음, 60여 항목)
uv run tests/smoke_stdio.py      # 실제 stdio 전송으로 initialize + tools/list + 리소스 읽기
```

`smoke_stdio.py` 는 서버를 자식 프로세스로 띄워 핸드셰이크와 스키마 직렬화까지
확인한다. 가짜 키로 실호출을 한 번 시도하므로 마지막 두 줄은 `is_error: True` 가 정상이다.

## 알려진 제약

- `get_current_weather` 는 8개 광역시 외 지역의 지점번호를 모른다. 확장하려면
  API Hub 지점정보 API(`stn_inf.php`, 별도 활용신청 필요)로 지점 목록을 받아
  `STATIONS` 를 채우거나, 위·경도로 최근접 지점을 찾는 로직을 추가하면 된다.
- 강수 여부는 AWS 의 강수감지(`RE`) 값만으로 판단해 "있음/없음/알 수 없음"만
  구분한다. 예전 초단기실황의 `PTY`(비/눈/진눈깨비 구분)에 대응하는 값은
  AWS 매분자료에 없다.
- `get_weather_alerts` 는 여전히 공공데이터포털(`apis.data.go.kr`)을 쓴다.
  특보 통보문(`getWthrWrnMsg`)의 응답 필드명(`t1`/`t2`/`t3`/`other`)은 포털 문서를
  근거로 했고 방어적으로 파싱한다. 조회에 실패하면 `body` 를 빈 문자열로 두고
  목록은 그대로 돌려준다.
- 단기예보(3일)와 초단기예보(6시간)는 구현하지 않았다.
