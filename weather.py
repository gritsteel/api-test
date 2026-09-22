"""기상청(KMA) 공공데이터 기반 MCP 날씨 서버.

노출하는 툴:
  - get_current_weather : 기상청 API Hub 지상관측 AWS 매분자료(nph-aws2_min) — 지점의 현재 관측값
  - get_weather_alerts  : 공공데이터포털 기상특보목록조회(getWthrWrnList) + 통보문(getWthrWrnMsg)

두 툴은 서로 다른 포털의 서로 다른 인증키를 쓴다.
  - ``KMA_APIHUB_KEY`` : apihub.kma.go.kr 에서 "지상관측 AWS 매분자료" 활용신청 후
    발급되는 authKey. get_current_weather 가 쓴다.
  - ``KMA_API_KEY``    : data.go.kr(공공데이터포털) 에서 "기상청_기상특보 조회서비스"
    활용신청 후 발급되는 일반 인증키. get_weather_alerts 가 쓴다. 포털이 보여주는
    URL 인코딩된 키(%2B, %3D 가 섞인 문자열)를 그대로 넣어도 동작한다.

격자(nx, ny)는 기상청 Lambert Conformal Conic 변환식으로 위·경도에서
직접 계산한다. 5km 격자이므로 경계 지점에서는 기상청 좌표표와 한 칸
차이가 날 수 있고, 그래서 kma://locations 리소스에 nx/ny/위경도를 모두 담아
검증할 수 있게 했다. (get_current_weather 자체는 관측지점번호 stn 을 쓰므로
이 격자는 더 이상 API 호출에는 쓰이지 않는다.)
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

import httpx2
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

mcp = MCPServer("weather")

# --------------------------------------------------------------------------
# 상수
# --------------------------------------------------------------------------

AWS_MIN_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min"
WTHR_WRN_BASE = "https://apis.data.go.kr/1360000/WthrWrnInfoService"
USER_AGENT = "kma-weather-mcp/1.0"
KST = timezone(timedelta(hours=9))

# get_current_weather 가 자동으로 지점번호를 찾아주는 지역. 기상청 종관기상관측
# (ASOS) 지점번호이며 실제 API 응답으로 확인된 것만 등록했다. 그 외 지역은
# get_current_weather(stn=...) 로 직접 지점번호를 넘겨야 한다.
STATIONS: dict[str, int] = {
    "서울": 108, "인천": 112, "부산": 159, "대구": 143,
    "광주": 156, "대전": 133, "울산": 152, "제주": 184,
}

COMPASS_16 = [
    "북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
    "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서",
]

# 특보구역 지점번호(stnId). 108은 전국(본청).
WARNING_AREAS = {
    "전국": "108",
    "서울": "109", "인천": "109", "경기": "109", "서울경기": "109",
    "강원": "105",
    "충북": "131",
    "대전": "133", "세종": "133", "충남": "133",
    "전북": "146",
    "광주": "156", "전남": "156",
    "대구": "143", "경북": "143",
    "부산": "159", "울산": "159", "경남": "159",
    "제주": "184",
}

# 내장 지명 → 위·경도 표. 여기 없는 곳은 latitude/longitude 로 직접 넘기면 된다.
LOCATIONS: dict[str, tuple[float, float]] = {
    # 특별시 · 광역시 · 특별자치시
    "서울": (37.5665, 126.9780),
    "부산": (35.1796, 129.0756),
    "대구": (35.8714, 128.6014),
    "인천": (37.4563, 126.7052),
    "광주": (35.1595, 126.8526),
    "대전": (36.3504, 127.3845),
    "울산": (35.5384, 129.3114),
    "세종": (36.4801, 127.2890),
    # 경기
    "수원": (37.2636, 127.0286),
    "성남": (37.4200, 127.1265),
    "고양": (37.6584, 126.8320),
    "용인": (37.2411, 127.1776),
    "부천": (37.5035, 126.7660),
    "안산": (37.3219, 126.8309),
    "안양": (37.3943, 126.9568),
    "남양주": (37.6360, 127.2165),
    "화성": (37.1996, 126.8310),
    "평택": (36.9921, 127.1128),
    "의정부": (37.7382, 127.0337),
    "파주": (37.7599, 126.7799),
    "김포": (37.6153, 126.7156),
    "광명": (37.4787, 126.8646),
    "이천": (37.2721, 127.4350),
    "포천": (37.8949, 127.2003),
    "동두천": (37.9036, 127.0606),
    "가평": (37.8315, 127.5096),
    "연천": (38.0966, 127.0748),
    # 강원
    "춘천": (37.8813, 127.7300),
    "원주": (37.3422, 127.9202),
    "강릉": (37.7519, 128.8761),
    "동해": (37.5247, 129.1143),
    "태백": (37.1640, 128.9856),
    "속초": (38.2070, 128.5918),
    "삼척": (37.4499, 129.1655),
    "홍천": (37.6971, 127.8888),
    "영월": (37.1836, 128.4617),
    "평창": (37.3705, 128.3900),
    "정선": (37.3805, 128.6608),
    "철원": (38.1465, 127.3134),
    "인제": (38.0695, 128.1707),
    "양양": (38.0754, 128.6190),
    "고성": (38.3806, 128.4677),
    # 충청
    "청주": (36.6424, 127.4890),
    "충주": (36.9910, 127.9259),
    "제천": (37.1326, 128.1910),
    "천안": (36.8151, 127.1139),
    "아산": (36.7898, 127.0018),
    "서산": (36.7848, 126.4503),
    "당진": (36.8894, 126.6459),
    "공주": (36.4465, 127.1190),
    "보령": (36.3333, 126.6127),
    "홍성": (36.6009, 126.6608),
    "논산": (36.1872, 127.0987),
    # 전라
    "전주": (35.8242, 127.1480),
    "군산": (35.9676, 126.7369),
    "익산": (35.9483, 126.9576),
    "정읍": (35.5699, 126.8560),
    "남원": (35.4164, 127.3905),
    "무주": (35.9241, 127.6605),
    "목포": (34.8118, 126.3922),
    "여수": (34.7604, 127.6622),
    "순천": (34.9506, 127.4875),
    "나주": (35.0160, 126.7108),
    "광양": (34.9407, 127.6959),
    "해남": (34.5734, 126.5990),
    "완도": (34.3110, 126.7550),
    "흑산도": (34.6841, 125.4356),
    # 경상
    "포항": (36.0190, 129.3435),
    "경주": (35.8562, 129.2247),
    "구미": (36.1195, 128.3446),
    "안동": (36.5684, 128.7294),
    "김천": (36.1398, 128.1136),
    "영주": (36.8056, 128.6240),
    "울진": (36.9930, 129.4004),
    "영덕": (36.4152, 129.3656),
    "울릉도": (37.4843, 130.9057),
    "독도": (37.2394, 131.8686),
    "창원": (35.2280, 128.6811),
    "진주": (35.1800, 128.1076),
    "김해": (35.2285, 128.8894),
    "통영": (34.8544, 128.4331),
    "거제": (34.8806, 128.6212),
    "사천": (35.0037, 128.0642),
    "밀양": (35.5038, 128.7469),
    "거창": (35.6866, 127.9095),
    "합천": (35.5666, 128.1658),
    "남해": (34.8376, 127.8925),
    # 제주
    "제주": (33.4996, 126.5312),
    "서귀포": (33.2541, 126.5601),
    "성산": (33.3868, 126.8801),
    "고산": (33.2938, 126.1628),
}

# 짧은 별칭 / 자주 쓰는 다른 표기
ALIASES = {
    "서울특별시": "서울", "서울시": "서울",
    "부산광역시": "부산", "부산시": "부산",
    "대구광역시": "대구", "대구시": "대구",
    "인천광역시": "인천", "인천시": "인천",
    "광주광역시": "광주",
    "대전광역시": "대전", "대전시": "대전",
    "울산광역시": "울산", "울산시": "울산",
    "세종특별자치시": "세종", "세종시": "세종",
    "제주도": "제주", "제주시": "제주", "제주특별자치도": "제주",
    "강원도": "춘천", "강원특별자치도": "춘천",
    "경기도": "수원",
    "충청북도": "청주", "충북": "청주",
    "충청남도": "홍성", "충남": "홍성",
    "전라북도": "전주", "전북": "전주", "전북특별자치도": "전주",
    "전라남도": "목포", "전남": "목포",
    "경상북도": "안동", "경북": "안동",
    "경상남도": "창원", "경남": "창원",
    "여의도": "서울", "강남": "서울", "해운대": "부산",
}

# --------------------------------------------------------------------------
# 격자 변환 (기상청 Lambert Conformal Conic)
# --------------------------------------------------------------------------

_RE = 6371.00877 / 5.0  # 지구 반경(km) / 격자 간격(km)
_DEGRAD = math.pi / 180.0
_SLAT1, _SLAT2 = 30.0 * _DEGRAD, 60.0 * _DEGRAD
_OLON, _OLAT = 126.0 * _DEGRAD, 38.0 * _DEGRAD
_XO, _YO = 43, 136

_SN = math.log(math.cos(_SLAT1) / math.cos(_SLAT2)) / math.log(
    math.tan(math.pi * 0.25 + _SLAT2 * 0.5) / math.tan(math.pi * 0.25 + _SLAT1 * 0.5)
)
_SF = math.pow(math.tan(math.pi * 0.25 + _SLAT1 * 0.5), _SN) * math.cos(_SLAT1) / _SN
_RO = _RE * _SF / math.pow(math.tan(math.pi * 0.25 + _OLAT * 0.5), _SN)


def latlon_to_grid(latitude: float, longitude: float) -> tuple[int, int]:
    """위·경도를 기상청 동네예보 격자 좌표 (nx, ny) 로 변환한다."""
    ra = _RE * _SF / math.pow(math.tan(math.pi * 0.25 + latitude * _DEGRAD * 0.5), _SN)
    theta = longitude * _DEGRAD - _OLON
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= _SN
    nx = math.floor(ra * math.sin(theta) + _XO + 0.5)
    ny = math.floor(_RO - ra * math.cos(theta) + _YO + 0.5)
    return int(nx), int(ny)


def _normalize(name: str) -> str:
    return name.replace(" ", "").strip()


def resolve_location(
    location: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[str, float, float, int, int]:
    """지명 또는 위·경도를 (표시명, 위도, 경도, nx, ny) 로 해석한다."""
    if latitude is not None and longitude is not None:
        nx, ny = latlon_to_grid(latitude, longitude)
        label = location.strip() or f"{latitude:.4f}, {longitude:.4f}"
        return label, latitude, longitude, nx, ny

    key = _normalize(location)
    if not key:
        raise ToolError("location 또는 latitude/longitude 중 하나는 지정해야 합니다.")

    key = ALIASES.get(key, key)
    if key in LOCATIONS:
        lat, lon = LOCATIONS[key]
        nx, ny = latlon_to_grid(lat, lon)
        return key, lat, lon, nx, ny

    # 부분 일치. "경기도 수원시" 처럼 앞뒤에 행정구역명이 붙은 경우를 흡수한다.
    matches = sorted({name for name in LOCATIONS if name in key or key.startswith(name)})
    if len(matches) == 1:
        name = matches[0]
        lat, lon = LOCATIONS[name]
        nx, ny = latlon_to_grid(lat, lon)
        return name, lat, lon, nx, ny
    if len(matches) > 1:
        raise ToolError(f"'{location}' 이(가) 여러 지역에 해당합니다: {', '.join(matches)}")

    raise ToolError(
        f"'{location}' 을(를) 찾을 수 없습니다. latitude/longitude 를 직접 넘기거나 "
        f"kma://locations 리소스에서 지원 지명을 확인하세요."
    )

# --------------------------------------------------------------------------
# 출력 스키마
# --------------------------------------------------------------------------


class CurrentWeather(BaseModel):
    """get_current_weather 의 출력 스키마."""

    location: str = Field(description="해석된 지점 이름")
    latitude: float = Field(description="지점 위도")
    longitude: float = Field(description="지점 경도")
    station: int = Field(description="기상청 관측지점번호 (stn)")
    observed_at: str = Field(description="관측 시각 (KST, ISO 8601)")
    temperature_c: float | None = Field(description="1분 평균 기온 (섭씨, TA)")
    humidity_percent: float | None = Field(description="1분 평균 상대습도 (%, HM)")
    precipitation_type: str = Field(description="강수감지 상태: 있음/없음/알 수 없음 (RE)")
    precipitation_1h_mm: float | None = Field(description="60분 누적 강수량 (mm, RN-60m)")
    wind_speed_ms: float | None = Field(description="1분 평균 풍속 (m/s, WS1)")
    wind_direction: str | None = Field(description="풍향 16방위 라벨 (WD1)")
    wind_direction_deg: float | None = Field(description="1분 평균 풍향 (도, WD1)")
    pressure_hpa: float | None = Field(description="1분 평균 현지기압 (hPa, PA)")
    dew_point_c: float | None = Field(description="이슬점온도 (섭씨, TD)")
    summary: str = Field(description="사람이 읽기 좋은 한 줄 요약")


class Alert(BaseModel):
    """발표된 기상특보 한 건."""

    station_id: str = Field(description="특보구역 지점번호 (stnId)")
    title: str = Field(description="특보 제목")
    announced_at: str = Field(description="발표 시각 (tmFc)")
    sequence: str = Field(description="발표 번호 (tmSeq)")
    body: str = Field(description="특보 통보문 본문. 조회하지 못하면 빈 문자열")


class AlertList(BaseModel):
    """get_weather_alerts 의 출력 스키마.

    최상위를 배열(RootModel[list[Alert]])로 두는 편이 간결하지만, 배열 루트
    스키마는 프로토콜 개정판 2026-07-28 부터만 허용되고 그 이전 버전을
    협상한 클라이언트에서는 tools/list 응답 직렬화 자체가 실패한다.
    즉 이 툴 하나가 아니라 서버 전체가 안 붙는다. 그래서 객체로 감싼다.
    """

    region: str = Field(description="조회한 특보구역")
    station_id: str = Field(description="특보구역 지점번호 (stnId)")
    searched_from: str = Field(description="조회 시작일 (YYYYMMDD)")
    searched_to: str = Field(description="조회 종료일 (YYYYMMDD)")
    count: int = Field(description="조회된 특보 건수")
    alerts: list[Alert] = Field(description="발표 시각 내림차순 특보 목록")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _service_key() -> str:
    """환경변수에서 공공데이터포털 인증키를 읽는다."""
    key = os.environ.get("KMA_API_KEY", "").strip()
    if not key:
        raise ToolError(
            "환경변수 KMA_API_KEY 가 설정되지 않았습니다. data.go.kr 에서 "
            "'기상청_단기예보 조회서비스' 활용신청 후 발급받은 일반 인증키를 "
            "MCP 서버 설정의 env 에 넣어 주세요."
        )
    # 포털이 보여주는 URL 인코딩된 키를 그대로 붙여넣어도 되게 한 번 디코딩한다.
    return unquote(key) if "%" in key else key


async def _get_items(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """공공데이터포털 JSON 응답에서 items 배열을 꺼낸다."""
    query = {"serviceKey": _service_key(), "dataType": "JSON", **params}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    try:
        async with httpx2.AsyncClient() as client:
            response = await client.get(url, params=query, headers=headers, timeout=30.0)
            response.raise_for_status()
            text = response.text
    except Exception as exc:
        raise ToolError(f"기상청 API 요청 실패 ({type(exc).__name__}): {exc}") from exc

    if not text.lstrip().startswith("{"):
        # 인증키 오류 등은 dataType=JSON 이어도 XML 로 돌아온다.
        raise ToolError(f"기상청 API가 JSON이 아닌 응답을 반환했습니다: {text[:300]}")

    payload = response.json()
    header = payload.get("response", {}).get("header", {})
    code = header.get("resultCode")
    if code not in ("00", "0"):
        raise ToolError(f"기상청 API 오류 [{code}] {header.get('resultMsg', '알 수 없는 오류')}")

    body = payload.get("response", {}).get("body") or {}
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return list(items)


def _compass(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    return COMPASS_16[int((degrees + 11.25) % 360 // 22.5)]


def _apihub_authkey() -> str:
    """환경변수에서 기상청 API Hub 인증키(authKey)를 읽는다."""
    key = os.environ.get("KMA_APIHUB_KEY", "").strip()
    if not key:
        raise ToolError(
            "환경변수 KMA_APIHUB_KEY 가 설정되지 않았습니다. apihub.kma.go.kr 에서 "
            "'지상관측 AWS 매분자료' 활용신청 후 발급받은 authKey 를 "
            "MCP 서버 설정의 env 에 넣어 주세요."
        )
    return key


_AWS_FIELDS = (
    "tm", "stn", "wd1", "ws1", "wds", "wss", "wd10", "ws10",
    "ta", "re", "rn15m", "rn60m", "rn12h", "rnday", "hm", "pa", "ps", "td",
)


async def _fetch_aws_min(stn: int, tm2: str) -> str:
    """API Hub 지상관측 AWS 매분자료(nph-aws2_min)를 호출해 원본 텍스트를 돌려준다."""
    params = {"tm2": tm2, "stn": stn, "disp": 0, "help": 1, "authKey": _apihub_authkey()}
    headers = {"User-Agent": USER_AGENT}

    try:
        async with httpx2.AsyncClient() as client:
            response = await client.get(AWS_MIN_URL, params=params, headers=headers, timeout=30.0)
            response.raise_for_status()
            text = response.text
    except Exception as exc:
        raise ToolError(f"기상청 API Hub 요청 실패 ({type(exc).__name__}): {exc}") from exc

    if text.lstrip().startswith("{"):
        # 활용신청 누락, 인증키 오류 등은 JSON 오류 객체로 돌아온다.
        try:
            message = json.loads(text).get("result", {}).get("message", text[:200])
        except (json.JSONDecodeError, AttributeError):
            message = text[:200]
        raise ToolError(f"기상청 API Hub 오류: {message}")
    return text


def _parse_aws_min(text: str, stn: int) -> dict[str, str] | None:
    """nph-aws2_min 응답에서 stn 에 해당하는 데이터 행을 꺼낸다. 없으면 None."""
    target = str(stn)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < len(_AWS_FIELDS) or parts[1] != target:
            continue
        return dict(zip(_AWS_FIELDS, parts))
    return None


def _aws_float(value: str | None) -> float | None:
    """AWS 매분자료 값을 실수로. -50 이하는 결측/에러 코드라 None 으로 취급한다."""
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return None if number <= -50 else number


# --------------------------------------------------------------------------
# 툴
# --------------------------------------------------------------------------


_AWS_RETRY_BUFFERS_MIN = (2, 7)  # 관측 지연을 감안해 몇 분 전 시각까지 재시도할지


@mcp.tool()
async def get_current_weather(
    location: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    stn: int | None = None,
) -> CurrentWeather:
    """한국 지점의 현재 날씨를 기상청 API Hub 지상관측 AWS 매분자료로 조회한다.

    Args:
        location: 한국 지명 (예: 서울, 부산, 인천). STATIONS 에 등록된 8개
            광역시(서울·인천·부산·대구·광주·대전·울산·제주)만 지점번호를 자동으로
            찾는다. 그 외 지역은 stn 을 직접 넘겨야 한다.
        latitude: 위도. longitude 와 함께 주면 지명 대신 이 좌표를 쓴다.
        longitude: 경도.
        stn: 기상청 관측지점번호. 지정하면 location/좌표로 지점번호를 찾는 대신
            이 값을 그대로 쓴다 (location/좌표는 표시용 지점명 계산에만 쓰인다).
    """
    label, lat, lon, _nx, _ny = resolve_location(location, latitude, longitude)

    station = stn if stn is not None else STATIONS.get(label)
    if station is None:
        raise ToolError(
            f"'{label}' 의 관측지점번호를 모릅니다. 자동 지원 지역: {', '.join(STATIONS)}. "
            "get_current_weather(stn=...) 로 기상청 지점번호를 직접 지정할 수도 있습니다."
        )

    now = datetime.now(KST)
    row = None
    for buffer_minutes in _AWS_RETRY_BUFFERS_MIN:
        tm2 = (now - timedelta(minutes=buffer_minutes)).strftime("%Y%m%d%H%M")
        text = await _fetch_aws_min(station, tm2)
        row = _parse_aws_min(text, station)
        if row is not None:
            break
    if row is None:
        raise ToolError(f"{label} (지점 {station}) 의 실황 자료가 없습니다.")

    temperature = _aws_float(row["ta"])
    humidity = _aws_float(row["hm"])
    rain_1h = _aws_float(row["rn60m"])
    wind_speed = _aws_float(row["ws1"])
    wind_deg = _aws_float(row["wd1"])
    pressure = _aws_float(row["pa"])
    dew_point = _aws_float(row["td"])

    detected = _aws_float(row["re"])
    if detected is None:
        precip = "알 수 없음"
    elif detected >= 1:
        precip = "있음"
    else:
        precip = "없음"

    observed_at = datetime.strptime(row["tm"], "%Y%m%d%H%M").replace(tzinfo=KST).isoformat()

    parts = [f"{label} 현재"]
    if temperature is not None:
        parts.append(f"기온 {temperature:g}℃")
    if humidity is not None:
        parts.append(f"습도 {humidity:g}%")
    parts.append({"없음": "강수 없음", "있음": "강수 있음"}.get(precip, "강수 알 수 없음"))
    if rain_1h:
        parts.append(f"60분 누적강수량 {rain_1h:g}mm")
    if wind_speed is not None:
        direction = _compass(wind_deg)
        parts.append(f"{direction}풍 {wind_speed:g}m/s" if direction else f"풍속 {wind_speed:g}m/s")

    return CurrentWeather(
        location=label,
        latitude=lat,
        longitude=lon,
        station=station,
        observed_at=observed_at,
        temperature_c=temperature,
        humidity_percent=humidity,
        precipitation_type=precip,
        precipitation_1h_mm=rain_1h,
        wind_speed_ms=wind_speed,
        wind_direction=_compass(wind_deg),
        wind_direction_deg=wind_deg,
        pressure_hpa=pressure,
        dew_point_c=dew_point,
        summary=", ".join(parts),
    )


@mcp.tool()
async def get_weather_alerts(region: str = "전국", days: int = 2) -> AlertList:
    """발표된 기상특보(주의보·경보)를 조회한다.

    Args:
        region: 특보구역. 전국, 서울, 경기, 강원, 충북, 충남, 대전, 세종,
            전북, 전남, 광주, 경북, 대구, 경남, 부산, 울산, 제주 중 하나.
        days: 오늘부터 며칠 전까지 볼지. 1이면 오늘만, 2면 어제부터. 최대 7.
    """
    key = _normalize(region)
    station = WARNING_AREAS.get(ALIASES.get(key, key), WARNING_AREAS.get(key))
    if station is None:
        raise ToolError(f"'{region}' 은 특보구역이 아닙니다. 가능한 값: {', '.join(sorted(set(WARNING_AREAS)))}")

    span = max(1, min(days, 7))
    today = datetime.now(KST)
    window = {
        "stnId": station,
        "fromTmFc": (today - timedelta(days=span - 1)).strftime("%Y%m%d"),
        "toTmFc": today.strftime("%Y%m%d"),
        "pageNo": 1,
        "numOfRows": 30,
    }

    items = await _get_items(f"{WTHR_WRN_BASE}/getWthrWrnList", window)

    # 통보문 본문은 별도 오퍼레이션이다. 실패해도 목록은 그대로 돌려준다.
    bodies: dict[tuple[str, str], str] = {}
    try:
        for message in await _get_items(f"{WTHR_WRN_BASE}/getWthrWrnMsg", window):
            text = " ".join(
                str(message.get(field)).strip()
                for field in ("t1", "t2", "t3", "other")
                if message.get(field)
            )
            bodies[(str(message.get("tmFc", "")), str(message.get("tmSeq", "")))] = text
    except Exception:
        pass

    alerts = [
        Alert(
            station_id=str(item.get("stnId") or station),
            title=str(item.get("title") or "제목 없음"),
            announced_at=str(item.get("tmFc") or ""),
            sequence=str(item.get("tmSeq") or ""),
            body=bodies.get((str(item.get("tmFc", "")), str(item.get("tmSeq", ""))), ""),
        )
        for item in items
    ]
    alerts.sort(key=lambda alert: alert.announced_at, reverse=True)

    return AlertList(
        region=region,
        station_id=station,
        searched_from=window["fromTmFc"],
        searched_to=window["toTmFc"],
        count=len(alerts),
        alerts=alerts,
    )


@mcp.resource("kma://locations", name="지원 지명 목록", mime_type="text/plain")
def supported_locations() -> str:
    """내장 지명 표. 여기 없는 곳은 latitude/longitude 로 조회한다."""
    lines = [f"{name}\t{lat:.4f}, {lon:.4f}\tnx={nx} ny={ny}" for name, (lat, lon) in sorted(LOCATIONS.items())
             for nx, ny in [latlon_to_grid(lat, lon)]]
    return "지명\t위경도\t격자\n" + "\n".join(lines)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
