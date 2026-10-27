import asyncio
from fastapi.testclient import TestClient
from app import main

client = TestClient(main.app)


def test_health():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_dashboard_requires_real_data(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'CACHE_FILE', tmp_path / 'no-data.json')
    response = client.get('/api/dashboard')
    assert response.status_code == 503
    assert 'Elexon' in response.json()['detail']


def test_invalid_cutoff_is_rejected():
    assert client.get('/api/dashboard?cutoff=99:99').status_code == 422


def test_synthetic_mode_cannot_be_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'CACHE_FILE', tmp_path / 'no-data.json')
    # This parameter is not part of the API. It cannot activate fabricated data.
    assert client.get('/api/dashboard?source=simulation').status_code == 503


def test_refresh_keeps_last_successful_cache_on_upstream_error(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'CACHE_FILE', tmp_path / 'cache.json')
    async def success(_):
        return ([{'fuelType':'WIND','startTime':'2026-01-02T00:00:00Z','publishTime':'2026-01-02T01:00:00Z','generation':100}],
                [{'startTime':'2026-01-02T00:00:00Z','publishTime':'2026-01-01T09:00:00Z','generation':115}])
    monkeypatch.setattr(main, 'load_january', success)
    asyncio.run(main.refresh_data())
    response = client.get('/api/dashboard')
    assert response.status_code == 200
    assert response.json()['source'] == 'elexon'
    assert response.json()['metrics']['maeMw'] == 15
    assert response.json()['rawRecords'] == {'actual':1,'forecast':1}
    saved = (tmp_path / 'cache.json').read_text()

    async def failure(_):
        raise RuntimeError('upstream offline')
    monkeypatch.setattr(main, 'load_january', failure)
    asyncio.run(main.refresh_data())
    assert main.state['status'] == 'failed'
    assert (tmp_path / 'cache.json').read_text() == saved
    assert client.get('/api/dashboard').status_code == 200
def test_vercel_env_sets_tmp_cache_path(monkeypatch):
    """When the VERCEL environment variable is present, the cache must live in
    /tmp so the application can write to it inside Vercel's read-only Lambda
    filesystem.  This test reloads the module with the env var set and confirms
    the path prefix.
    """
    import importlib
    monkeypatch.setenv("VERCEL", "1")
    # Reload to pick up the new env var at module level.
    import app.main as m
    importlib.reload(m)
    assert str(m.CACHE_FILE).startswith("/tmp"), (
        f"Expected /tmp cache on Vercel, got: {m.CACHE_FILE}"
    )
    # Restore: reload without the env var so other tests see the normal path.
    monkeypatch.delenv("VERCEL", raising=False)
    importlib.reload(m)
