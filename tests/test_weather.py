import asyncio
import json
import os
import pathlib
import sys

os.environ["KMA_API_KEY"] = "TEST%2Bkey%3D"
os.environ["KMA_APIHUB_KEY"] = "TEST-APIHUB-KEY"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

import weather as w  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)

print("== 1. 격자 변환 (기상청 기준값) ==")
for name, lat, lon, exp in [
    ("서울", 37.5665, 126.9780, (60, 127)),
    ("부산", 35.1796, 129.0756, (98, 76)),
    ("인천", 37.4563, 126.7052, (55, 124)),
    ("대전", 36.3504, 127.3845, (67, 100)),
    ("광주", 35.1595, 126.8526, (58, 74)),
    ("울산", 35.5384, 129.3114, (102, 84)),
    ("울릉도", 37.4843, 130.9057, (127, 127)),
]:
    got = w.latlon_to_grid(lat, lon)
    check(f"{name} -> {exp}", got == exp, f"got={got}")

print("== 2. 지명 해석 ==")
check("'서울' 정확 일치", w.resolve_location("서울")[3:] == (60, 127))
check("'서울특별시' 별칭", w.resolve_location("서울특별시")[0] == "서울")
check("'경기도 수원시' 부분 일치", w.resolve_location("경기도 수원시")[0] == "수원")
check("'제주특별자치도' 별칭", w.resolve_location("제주특별자치도")[0] == "제주")
check("위경도 직접 지정", w.resolve_location("", 37.5665, 126.9780)[3:] == (60, 127))
for bad, why in [("", "빈 입력"), ("아틀란티스", "미지의 지명")]:
    try:
        w.resolve_location(bad)
        check(f"{why} 거부", False)
    except ToolError:
        check(f"{why} 거부", True)
check("전남 별칭이 실제 지명을 가리킴", w.ALIASES["전라남도"] in w.LOCATIONS)
check("모든 별칭이 유효", all(v in w.LOCATIONS for v in w.ALIASES.values()),
      str([k for k,v in w.ALIASES.items() if v not in w.LOCATIONS]))

print("== 3. AWS 매분자료 파싱 ==")
AWS_SAMPLE = (
    "#START7777\n"
    "#--------------------------------------------------------------\n"
    "# YYMMDDHHMI   STN    WD1    WS1    WDS    WSS   WD10   WS10     TA     RE"
    " RN-15m RN-60m RN-12H RN-DAY     HM     PA     PS     TD\n"
    "202609022305   108  254.9    1.4  246.2    1.8  256.5    1.4   23.8  -99.9"
    "    0.0    0.0    0.0    0.3   82.2 1002.6 1012.4   20.6\n"
    "202609022305    90  349.3    1.3  335.6    1.6  346.4    1.2    3.6    1.0"
    "    0.0    0.0    0.0    0.0   40.2 1012.8 1015.0   -8.7\n"
    "#7777END\n"
)
row = w._parse_aws_min(AWS_SAMPLE, 108)
check("stn=108 행 추출", row is not None and row["ta"] == "23.8", str(row))
check("주석/타 지점 행 무시", w._parse_aws_min(AWS_SAMPLE, 999) is None)
check("re=-99.9 (결측)", w._aws_float(w._parse_aws_min(AWS_SAMPLE, 108)["re"]) is None)
check("re=1.0 (강수감지)", w._aws_float(w._parse_aws_min(AWS_SAMPLE, 90)["re"]) == 1.0)

print("== 4. 값 파싱 ==")
check("결측 -99.9 -> None", w._aws_float("-99.9") is None)
check("결측 -50.0 -> None (경계값)", w._aws_float("-50.0") is None)
check("-49.9 -> 유효값", w._aws_float("-49.9") == -49.9)
check("'23.8' -> 23.8", w._aws_float("23.8") == 23.8)
check("파싱 불가 -> None", w._aws_float("N/A") is None)
check("풍향 0도 -> 북", w._compass(0.0) == "북")
check("풍향 90도 -> 동", w._compass(90.0) == "동")
check("풍향 350도 -> 북", w._compass(350.0) == "북", w._compass(350.0))
check("풍향 None -> None", w._compass(None) is None)

print("== 5. 인증키 처리 ==")
check("URL 인코딩 키 디코딩", w._service_key() == "TEST+key=")
os.environ["KMA_API_KEY"] = ""
try:
    w._service_key()
    check("키 없을 때 명확한 오류", False)
except ToolError as e:
    check("키 없을 때 명확한 오류", "KMA_API_KEY" in str(e))
os.environ["KMA_API_KEY"] = "plainkey"
check("평문 키는 그대로", w._service_key() == "plainkey")

check("APIHUB 키 읽기", w._apihub_authkey() == "TEST-APIHUB-KEY")
os.environ["KMA_APIHUB_KEY"] = ""
try:
    w._apihub_authkey()
    check("APIHUB 키 없을 때 명확한 오류", False)
except ToolError as e:
    check("APIHUB 키 없을 때 명확한 오류", "KMA_APIHUB_KEY" in str(e))
os.environ["KMA_APIHUB_KEY"] = "TEST-APIHUB-KEY"

print("== 6. _get_items (httpx2 목) ==")
class FakeResp:
    def __init__(self, text): self.text = text
    def raise_for_status(self): pass
    def json(self): return json.loads(self.text)
class FakeClient:
    captured = {}
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, params=None, headers=None, timeout=None):
        FakeClient.captured = {"url": url, "params": params}
        return FakeResp(FakeClient.payload)
real_client = w.httpx2.AsyncClient
w.httpx2.AsyncClient = FakeClient

FakeClient.payload = json.dumps({"response":{"header":{"resultCode":"00","resultMsg":"NORMAL_SERVICE"},
    "body":{"items":{"item":[{"category":"T1H","obsrValue":"24.3"}]}}}})
items = asyncio.run(w._get_items("http://x", {"nx":60}))
check("정상 응답 파싱", items == [{"category":"T1H","obsrValue":"24.3"}], str(items))
check("serviceKey 주입", FakeClient.captured["params"]["serviceKey"] == "plainkey")
check("dataType=JSON 주입", FakeClient.captured["params"]["dataType"] == "JSON")

FakeClient.payload = json.dumps({"response":{"header":{"resultCode":"03","resultMsg":"NO_DATA"},"body":{}}})
try:
    asyncio.run(w._get_items("http://x", {}))
    check("resultCode 오류 검출", False)
except ToolError as e:
    check("resultCode 오류 검출", "03" in str(e) and "NO_DATA" in str(e))

FakeClient.payload = (
    "<OpenAPI_ServiceResponse><cmmMsgHeader>"
    "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
    "</cmmMsgHeader></OpenAPI_ServiceResponse>"
)
try:
    asyncio.run(w._get_items("http://x", {}))
    check("XML 오류 응답 검출", False)
except ToolError as e:
    check("XML 오류 응답 검출", "JSON이 아닌" in str(e))

FakeClient.payload = json.dumps({"response":{"header":{"resultCode":"00"},"body":{"items":""}}})
check("빈 items -> []", asyncio.run(w._get_items("http://x", {})) == [])

print("== 7. get_current_weather (AWS 매분자료 목 응답) ==")
SEOUL_ROW = (
    "#START7777\n"
    "# YYMMDDHHMI   STN    WD1    WS1    WDS    WSS   WD10   WS10     TA     RE"
    " RN-15m RN-60m RN-12H RN-DAY     HM     PA     PS     TD\n"
    "202609022305   108  254.9    1.4  246.2    1.8  256.5    1.4   23.8    0.0"
    "    0.0    0.0    0.0    0.3   82.2 1002.6 1012.4   20.6\n"
    "#7777END\n"
)
FakeClient.payload = SEOUL_ROW
w.httpx2.AsyncClient = FakeClient
cw = asyncio.run(w.get_current_weather(location="서울"))
print("   ", cw.model_dump_json(indent=None)[:400])
check("지점명", cw.location == "서울")
check("관측지점번호", cw.station == 108)
check("기온", cw.temperature_c == 23.8)
check("습도", cw.humidity_percent == 82.2)
check("강수 없음 라벨", cw.precipitation_type == "없음")
check("기압", cw.pressure_hpa == 1002.6)
check("이슬점", cw.dew_point_c == 20.6)
check("풍향 라벨", cw.wind_direction is not None, cw.wind_direction)
check("요약문에 기온 포함", "23.8℃" in cw.summary, cw.summary)
check("stn 쿼리 전달", FakeClient.captured["params"]["stn"] == 108)
check("authKey 쿼리 전달", FakeClient.captured["params"]["authKey"] == "TEST-APIHUB-KEY")

SOKCHO_ROW = (
    "#START7777\n"
    "202609022305    90  349.3    1.3  335.6    1.6  346.4    1.2    3.6    1.0"
    "    0.0    0.0    0.0    0.0   40.2 1012.8 1015.0   -8.7\n"
    "#7777END\n"
)
FakeClient.payload = SOKCHO_ROW
cw2 = asyncio.run(w.get_current_weather(location="속초", stn=90))
check("stn 직접 지정 우선", cw2.station == 90)
check("강수 있음 라벨", cw2.precipitation_type == "있음")
check("영하에 가까운 기온", cw2.temperature_c == 3.6)

try:
    asyncio.run(w.get_current_weather(location="속초"))
    check("미등록 지역 자동 거부", False)
except ToolError as e:
    check("미등록 지역 자동 거부", "관측지점번호를 모릅니다" in str(e))

attempts = {"n": 0}
class RetryClient(FakeClient):
    async def get(self, url, params=None, headers=None, timeout=None):
        attempts["n"] += 1
        FakeClient.captured = {"url": url, "params": params}
        return FakeResp("#START7777\n#7777END\n" if attempts["n"] == 1 else SEOUL_ROW)
w.httpx2.AsyncClient = RetryClient
cw3 = asyncio.run(w.get_current_weather(location="서울"))
check("빈 응답 후 재시도로 회복", cw3.temperature_c == 23.8 and attempts["n"] == 2, str(attempts))

class EmptyClient(FakeClient):
    async def get(self, url, params=None, headers=None, timeout=None):
        return FakeResp("#START7777\n#7777END\n")
w.httpx2.AsyncClient = EmptyClient
try:
    asyncio.run(w.get_current_weather(location="서울"))
    check("실황 자료 없음 -> 오류", False)
except ToolError as e:
    check("실황 자료 없음 -> 오류", "실황 자료가 없습니다" in str(e))

class ForbiddenClient(FakeClient):
    async def get(self, url, params=None, headers=None, timeout=None):
        return FakeResp(json.dumps({"result": {"status": 403, "message": "활용신청이 필요한 API 입니다."}}))
w.httpx2.AsyncClient = ForbiddenClient
try:
    asyncio.run(w.get_current_weather(location="서울"))
    check("API Hub 활용신청 오류 검출", False)
except ToolError as e:
    check("API Hub 활용신청 오류 검출", "활용신청" in str(e))
w.httpx2.AsyncClient = FakeClient

print("== 8. get_weather_alerts (특보 목 응답) ==")
WRN = {"response":{"header":{"resultCode":"00"},"body":{"items":{"item":[
    {"stnId":"109","title":"[기상특보] 호우주의보 발표","tmFc":"202609021100","tmSeq":"1"},
    {"stnId":"109","title":"[기상특보] 강풍주의보 해제","tmFc":"202609020500","tmSeq":"2"}]}}}}
MSG = {"response":{"header":{"resultCode":"00"},"body":{"items":{"item":[
    {"stnId":"109","tmFc":"202609021100","tmSeq":"1","t1":"호우주의보: 서울,인천","t2":"","other":"유의사항 참고"}]}}}}
seq = [json.dumps(WRN), json.dumps(MSG)]
class SeqClient(FakeClient):
    async def get(self, url, params=None, headers=None, timeout=None):
        FakeClient.captured = {"url": url, "params": params}
        return FakeResp(seq.pop(0))
w.httpx2.AsyncClient = SeqClient
alerts = asyncio.run(w.get_weather_alerts(region="서울"))
print("   ", alerts.model_dump_json()[:400])
check("특보 2건", alerts.count == 2 and len(alerts.alerts) == 2)
stamps = [a.announced_at for a in alerts.alerts]
check("발표시각 내림차순", stamps == ["202609021100", "202609020500"], str(stamps))
check("구역 메타데이터", (alerts.region, alerts.station_id) == ("서울","109"))
check("본문 병합", alerts.alerts[0].body == "호우주의보: 서울,인천 유의사항 참고", alerts.alerts[0].body)
check("본문 없는 건은 빈 문자열", alerts.alerts[1].body == "")
check("stnId 109 매핑", w.WARNING_AREAS["서울"] == "109")
try:
    asyncio.run(w.get_weather_alerts(region="화성시"))
    check("잘못된 구역 거부", False)
except ToolError as e:
    check("잘못된 구역 거부", "특보구역이 아닙니다" in str(e))

seq2 = [json.dumps(WRN), json.dumps({"response":{"header":{"resultCode":"22","resultMsg":"LIMITED"},"body":{}}})]
class SeqClient2(FakeClient):
    async def get(self, url, params=None, headers=None, timeout=None):
        return FakeResp(seq2.pop(0))
w.httpx2.AsyncClient = SeqClient2
degraded = asyncio.run(w.get_weather_alerts(region="전국"))
check("통보문 실패해도 목록 반환", degraded.count == 2 and degraded.alerts[0].body == "")
w.httpx2.AsyncClient = real_client

print("== 9. MCP 서버 등록 / 스키마 ==")
tools = asyncio.run(w.mcp.list_tools())
names = sorted(t.name for t in tools)
check("툴 2개 등록", names == ["get_current_weather","get_weather_alerts"], str(names))
for t in tools:
    out = getattr(t, "output_schema", None)
    print(f"    {t.name}: input={sorted((t.input_schema or {}).get('properties',{}))}")
    print(f"      output_schema.type={None if not out else out.get('type')}")
    check(f"{t.name} 설명 존재", bool(t.description))
    check(f"{t.name} output_schema 존재", bool(out))
cur = next(t for t in tools if t.name=="get_current_weather")
check("get_current_weather 필수 인자 없음", not (cur.input_schema or {}).get("required"))
al = next(t for t in tools if t.name=="get_weather_alerts")
check("get_weather_alerts 출력은 객체(구형 프로토콜 호환)", (al.output_schema or {}).get("type") == "object",
      str((al.output_schema or {}).get("type")))
check("출력에 alerts 배열 필드", "alerts" in (al.output_schema or {}).get("properties", {}))
res = asyncio.run(w.mcp.list_resources())
check("리소스 kma://locations 등록", any(str(r.uri)=="kma://locations" for r in res), str([str(r.uri) for r in res]))
body = w.supported_locations()
lines = body.splitlines()
check("리소스 본문에 지명·격자 포함", "서울" in body and "nx=60 ny=127" in body,
      lines[1] if len(lines) > 1 else body)
check("지명 표 90개 이상", len(w.LOCATIONS) >= 90, str(len(w.LOCATIONS)))

print()
print(f"결과: {len(fails)} 실패" + ("" if not fails else " -> " + ", ".join(fails)))
sys.exit(1 if fails else 0)
