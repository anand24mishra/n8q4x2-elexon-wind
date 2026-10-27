import asyncio
import httpx
import pytest
from app.elexon import SOURCES, fetch_dataset, load_january, UpstreamError


def test_requests_exact_user_supplied_window_parameters():
    seen = []
    def responder(req):
        seen.append((str(req.url),dict(req.url.params)))
        if '/WINDFOR/' in str(req.url):
            return httpx.Response(200, json=[{'startTime':'2026-01-02T00:00:00Z', 'publishTime':'2026-01-01T08:30:00Z','generation':100}])
        return httpx.Response(200, json=[
            {'startTime':'2026-01-02T00:00:00Z','fuelType':'WIND','generation':110},
            {'startTime':'2026-01-02T00:00:00Z','fuelType':'CCGT','generation':900},
        ])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await fetch_dataset(client,'WINDFOR'),await fetch_dataset(client,'FUELHH')
    windfor, fuelhh = asyncio.run(run())
    assert len(windfor)==1 and len(fuelhh)==2
    assert seen[0][1] == SOURCES['WINDFOR']
    assert seen[1][1] == SOURCES['FUELHH']
    assert seen[0][0].startswith('https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream?')
    assert seen[1][0].startswith('https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream?')


def test_invalid_or_empty_data_is_not_treated_as_success():
    async def run(response):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:response)) as client:
            await fetch_dataset(client,'WINDFOR')
    with pytest.raises(UpstreamError):
        asyncio.run(run(httpx.Response(200,json=[])))
    with pytest.raises(UpstreamError):
        asyncio.run(run(httpx.Response(404,json={'detail':'not found'})))


def test_loader_keeps_wind_only(monkeypatch):
    async def stub(_, dataset):
        if dataset == 'WINDFOR':
            return [{'startTime':'2026-01-02T00:00:00Z','publishTime':'2026-01-01T09:00:00Z','generation':200}]
        return [{'fuelType':'WIND','generation':250},{'fuelType':'CCGT','generation':500}]
    monkeypatch.setattr('app.elexon.fetch_dataset',stub)
    progress=[]
    actual, forecast = asyncio.run(load_january(lambda done,total:progress.append((done,total))))
    assert len(actual) == 1 and len(forecast) == 1
    assert progress == [(1,2),(2,2)]
