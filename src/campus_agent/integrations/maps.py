from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, field_validator

from ..config import Settings
from ..models import StrictModel, failure, success

AMAP_BASE = 'https://restapi.amap.com'


def coordinate(value: str) -> str:
    """Amap uses GCJ-02 longitude,latitude. Never infer a coordinate from a name."""
    parts = value.split(',')
    if len(parts) != 2:
        raise ValueError('坐标格式应为经度,纬度（GCJ-02）')
    longitude, latitude = map(float, parts)
    if not math.isfinite(longitude) or not math.isfinite(latitude) or not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError('坐标超出有效范围')
    return f'{longitude:.6f},{latitude:.6f}'


class PlaceQuery(StrictModel):
    keywords: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=5, ge=1, le=10)


class RouteQuery(StrictModel):
    origin: str
    destination: str
    mode: Literal['walking', 'driving'] = 'walking'

    @field_validator('origin', 'destination')
    @classmethod
    def check_coordinate(cls, value):
        return coordinate(value)


class WeatherQuery(StrictModel):
    city_adcode: str = Field(pattern=r'^\d{6}$')
    target_date: date | None = None


def text_value(value):
    # Amap sometimes encodes missing optional properties as [].
    return value if isinstance(value, str) else ''


class AmapProvider:
    def __init__(self, config: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.config, self.transport = config, transport

    def status(self):
        return {'provider': 'amap', 'status': 'configured_unverified' if self.config.amap_api_key else 'not_configured',
                'message': '已配置密钥，尚不代表调用成功' if self.config.amap_api_key else '需要配置高德Web服务API密钥',
                'coordinate_system': 'GCJ-02'}

    async def _get(self, path, params):
        if not self.config.amap_api_key:
            return failure('not_configured', '地图与天气未配置，请在本地设置AMAP_API_KEY。')
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=8, follow_redirects=False) as client:
                response = await client.get(AMAP_BASE + path,
                    params={**params, 'key': self.config.amap_api_key, 'output': 'JSON'})
                if response.status_code == 429:
                    return failure('rate_limited', '高德服务调用额度或频率受限，请稍后重试。')
                if response.status_code >= 400:
                    return failure('provider_error', '高德服务暂时不可用。')
                data = response.json()
            if not isinstance(data, dict):
                return failure('provider_error', '高德返回数据结构不符合预期。')
            if str(data.get('status')) != '1':
                code = str(data.get('infocode', ''))
                if code in {'10001', '10005', '10006', '10007', '10009', '10012', '10013'}:
                    return failure('authentication', '高德密钥或接口权限无效，请检查Web服务Key配置。')
                if code in {'10003', '10004', '10010', '10014', '10019', '10020', '10021', '10044'}:
                    return failure('rate_limited', '高德额度或调用频率受限。')
                return failure('provider_error', '高德未能完成查询，请检查参数与服务权限。')
            return success(data)
        except (httpx.HTTPError, ValueError, TypeError):
            # Never return exception URLs: Amap keys are query parameters.
            return failure('unavailable', '高德请求失败或返回数据不可解析。')

    async def search_places(self, **kwargs):
        try:
            args = PlaceQuery.model_validate(kwargs)
        except ValueError:
            return failure('invalid_arguments', '请提供地点关键词、城市及1至10条候选数量。')
        response = await self._get('/v3/place/text', {'keywords': args.keywords, 'city': args.city,
                'citylimit': 'true', 'offset': args.limit, 'page': 1, 'extensions': 'base'})
        if not response['ok']:
            return response
        places = []
        candidates = response['data'].get('pois') or []
        if not isinstance(candidates, list) or any(not isinstance(p, dict) for p in candidates):
            return failure('provider_error', '地点数据格式无效。')
        for poi in candidates[:args.limit]:
            raw = text_value(poi.get('location'))
            try:
                loc = coordinate(raw)
            except ValueError:
                continue
            places.append({'place_id': text_value(poi.get('id')), 'name': text_value(poi.get('name')),
                           'address': text_value(poi.get('address')), 'city': text_value(poi.get('cityname')),
                           'district': text_value(poi.get('adname')), 'city_adcode': text_value(poi.get('adcode')),
                           'coordinate': loc})
        return success({'provider': 'amap', 'places': places, 'coordinate_system': 'GCJ-02',
                        'requires_selection': len(places) > 1,
                        'notice': '多个同名地点应先让用户选择；不得默认采用第一项。',
                        'observed_at': datetime.now(timezone.utc).isoformat()})

    async def get_route(self, **kwargs):
        try:
            args = RouteQuery.model_validate(kwargs)
        except ValueError:
            return failure('invalid_arguments', '路线需要有效的GCJ-02起终点坐标；出行方式为walking或driving。')
        response = await self._get('/v3/direction/' + args.mode,
            {'origin': args.origin, 'destination': args.destination, 'extensions': 'base'})
        if not response['ok']:
            return response
        route = response['data'].get('route')
        if not isinstance(route, dict):
            return failure('provider_error', '路线数据格式无效。')
        paths = route.get('paths') or []
        if not isinstance(paths, list):
            return failure('provider_error', '路线数据格式无效。')
        if not paths:
            return failure('not_found', '没有可用路线，不能推算到校时间。')
        try:
            path = paths[0]
            seconds = float(path['duration'])
            distance = float(path['distance'])
            if not math.isfinite(seconds) or not math.isfinite(distance) or seconds < 0 or distance < 0:
                raise ValueError('negative or non-finite route')
            return success({'provider': 'amap', 'origin': args.origin, 'destination': args.destination,
                'mode': args.mode, 'duration_seconds': seconds, 'duration_minutes': math.ceil(seconds / 60),
                'distance_meters': distance,
                'steps': [{'instruction': text_value(s.get('instruction')), 'road': text_value(s.get('road'))}
                          for s in path.get('steps', [])],
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'notice': '耗时为查询时估计值，不是未来到达日的路况预测；请预留候车、排队和误差时间。'})
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
            return failure('provider_error', '路线数据缺少有效的耗时或距离。')

    async def weather(self, **kwargs):
        try:
            args = WeatherQuery.model_validate(kwargs)
        except ValueError:
            return failure('invalid_arguments', '天气查询需要6位城市行政区划代码和可选的YYYY-MM-DD日期。')
        response = await self._get('/v3/weather/weatherInfo', {'city': args.city_adcode, 'extensions': 'all'})
        if not response['ok']:
            return response
        forecasts = response['data'].get('forecasts') or []
        if not isinstance(forecasts, list) or any(not isinstance(f, dict) for f in forecasts):
            return failure('provider_error', '天气数据格式无效。')
        if not forecasts:
            return failure('not_found', '该城市没有可用预报。')
        current = forecasts[0]
        casts = current.get('casts') or []
        if not isinstance(casts, list) or any(not isinstance(c, dict) for c in casts):
            return failure('provider_error', '天气数据格式无效。')
        if args.target_date:
            casts = [c for c in casts if c.get('date') == args.target_date.isoformat()]
        if not casts:
            return failure('out_of_forecast_range', '目标日期不在服务商返回的预报范围内，不能推断当天的天气。')
        fields = ('date', 'dayweather', 'nightweather', 'daytemp', 'nighttemp', 'daywind', 'nightwind', 'daypower', 'nightpower')
        return success({'provider': 'amap', 'city': text_value(current.get('city')), 'city_adcode': args.city_adcode,
                        'report_time': text_value(current.get('reporttime')),
                        'forecasts': [{k: text_value(c.get(k)) for k in fields} for c in casts],
                        'notice': '预报用于提醒，不会自动改变已确认的日程；实际天气以最新预报为准。'})


class CampusLocations:
    def __init__(self, config: Settings):
        self.config = config

    def get(self, campus_id: str):
        if not self.config.campus_locations_file:
            return failure('not_configured', '校区尚未绑定真实地点；合成校区没有可默认使用的现实坐标。')
        try:
            data = json.loads(Path(self.config.campus_locations_file).read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('invalid campus mapping')
            value = data.get(campus_id)
            if not value:
                return failure('not_found', '该校区尚未配置真实地点。')
            gate = coordinate(value['gate_coordinate'])
            adcode = value['city_adcode']
            if not re.fullmatch(r'\d{6}', adcode) or not value.get('display_name'):
                raise ValueError('invalid location metadata')
            return success({'campus_id': campus_id, 'display_name': value['display_name'],
                            'gate_coordinate': gate, 'city_adcode': adcode,
                            'coordinate_system': 'GCJ-02', 'source': 'operator_configured'})
        except (OSError, ValueError, KeyError, TypeError):
            return failure('configuration', '真实校区地点配置格式无效，请检查本地配置文件。')
