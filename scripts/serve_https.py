"""Run the invite app behind a loopback HTTPS tunnel; never log secrets."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bootstrap', type=Path, help='Optional operator-owned local configuration bootstrap')
    parser.add_argument('--base-port', type=int, default=8010)
    parser.add_argument('--public-origin', required=True)
    args = parser.parse_args()
    if args.bootstrap:
        runpy.run_path(str(args.bootstrap.resolve(strict=True)), run_name='campus_https_bootstrap')
    os.environ['COOKIE_SECURE'] = 'true'
    os.environ['CAMPUS_PUBLIC_ORIGIN'] = args.public_origin
    os.environ['GRADIO_ANALYTICS_ENABLED'] = 'false'
    # Uvicorn trusts proxy headers only from the local SSH connection.
    os.environ['FORWARDED_ALLOW_IPS'] = '127.0.0.1'
    from campus_agent.config import Settings
    if Settings().auth_mode != 'pilot':
        raise SystemExit('HTTPS sharing requires AUTH_MODE=pilot; demo sharing is refused.')
    from campus_agent.cli import main as campus_main
    sys.argv = ['campus', 'all', '--host', '127.0.0.1', '--base-port', str(args.base_port)]
    campus_main()


if __name__ == '__main__':
    main()
