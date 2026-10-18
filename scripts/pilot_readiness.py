"""Operator CLI; every report uses a new path and contains no chat transcripts."""
import argparse
import asyncio
import json
from pathlib import Path

from campus_agent.pilot_ops import (backup_runtime, estimate_budget, export_usage,
                                    restore_runtime, simulate_burst)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    burst = commands.add_parser('burst')
    burst.add_argument('--runtime', required=True)
    burst.add_argument('--users', type=int, choices=[5, 10], required=True)
    burst.add_argument('--delay-ms', type=int, default=100)
    burst.add_argument('--active-limit', type=int, default=2)
    burst.add_argument('--pending-limit', type=int, default=32)
    export = commands.add_parser('export-usage')
    export.add_argument('--runtime', required=True)
    export.add_argument('--turn-id', action='append', required=True)
    budget = commands.add_parser('budget')
    budget.add_argument('--sample', required=True)
    budget.add_argument('--pricing', required=True)
    budget.add_argument('--participants', type=int, required=True)
    budget.add_argument('--tasks-per-person', type=float, required=True)
    budget.add_argument('--server-cost', type=float, required=True, help='Same currency as pricing')
    backup = commands.add_parser('backup')
    backup.add_argument('--runtime', required=True)
    backup.add_argument('--destination', required=True)
    backup.add_argument('--services-stopped', action='store_true')
    restore = commands.add_parser('restore')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--destination', required=True)
    for command in (burst, export, budget, backup, restore):
        command.add_argument('--output', required=True, help='New JSON report path; never overwrite')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error('Use a new output path')
    if args.command == 'burst':
        result = asyncio.run(simulate_burst(Path(args.runtime), args.users, args.delay_ms,
                                           args.active_limit, args.pending_limit))
    elif args.command == 'export-usage':
        result = export_usage(Path(args.runtime), args.turn_id)
    elif args.command == 'budget':
        result = estimate_budget(json.loads(Path(args.sample).read_text(encoding='utf-8')),
                                 json.loads(Path(args.pricing).read_text(encoding='utf-8')),
                                 args.participants, args.tasks_per_person, args.server_cost)
    elif args.command == 'backup':
        result = backup_runtime(Path(args.runtime), Path(args.destination), services_stopped=args.services_stopped)
    else:
        result = restore_runtime(Path(args.backup), Path(args.destination))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'command': args.command, 'report': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
