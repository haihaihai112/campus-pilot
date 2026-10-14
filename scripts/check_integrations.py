"""Record actual local API/UI callback responses; no provider success is assumed."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from gradio_client import Client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8010')
    parser.add_argument('--output', default='reports/integrations-smoke.json')
    args = parser.parse_args()
    report = {'observed_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'local real HTTP/MCP and Gradio callbacks; not a live-provider success evaluation'}
    # Local-only target: ignore ambient HTTP_PROXY so a system proxy cannot break loopback calls.
    with httpx.Client(base_url=args.base, timeout=30, trust_env=False) as client:
        report['health'] = client.get('/health').json()
        assert report['health']['status'] == 'ok'
        report['integrations'] = client.get('/v1/integrations').json()
        assert report['integrations']['ok']
        report['ui_http_status'] = client.get('/ui/').status_code
        assert report['ui_http_status'] == 200
        turn = client.post('/v1/chat', json={'message': '查看外部工具连接状态'}).json()
        for _ in range(120):
            current = client.get('/v1/turns/' + turn['turn_id']).json()
            if current['status'] not in ('working', 'queued'):
                break
            time.sleep(0.25)
        assert current['status'] == 'completed', current
        assert any(t.get('tool') == 'get_integration_status' for t in current['trace'])
        report['turn'] = current
    ui = Client(args.base + '/ui/', verbose=False)
    report['ui_connection_callback'] = ui.predict(api_name='/connections')
    report['ui_invalid_route_callback'] = ui.predict('invalid', 'invalid', 'walking', api_name='/route')
    assert '有效' in report['ui_invalid_route_callback']
    report['ui_visual_check'] = 'not_performed: browser automation unavailable; callbacks checked via Gradio client'
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved {target}; all local callback assertions passed')


if __name__ == '__main__':
    main()
