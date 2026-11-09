#!/usr/bin/env python3
"""Exercise the operator interrupt driver on actual Core with a held provider."""
import argparse
import json
from pathlib import Path
import traceback

from fleet_fixture import Fleet, result
from native_fixture import Native
from qualify_two_logins import Budget, LiveSeat, interrupt_live
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet, model, native = Fleet(args.binary, args.evidence), Model(), None
    checks, error = [], None
    try:
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        native = Native(fleet, args.cli, model)
        native.thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        # Adapt only the transport. The live driver and wait code are unchanged.
        class RpcAdapter:
            @property
            def events(self):
                return native.events

            def call(self, method, params):
                return native.request(method, params)

        seat = object.__new__(LiveSeat)
        seat.name, seat.thread, seat.rpc = 'fixture-receiver', native.thread, RpcAdapter()
        seat.evidence = fleet.e
        budget = Budget(fleet.e / 'synthetic-budget.json', 1, 30)
        interrupt_live(seat, budget)
        assert len(budget.record['admissions']) == 1
        terminal = [event for event in native.events if event.get('method') == 'turn/completed']
        assert terminal[-1]['params']['turn']['status'] == 'interrupted'
        checks.append('same_operator_driver_waits_for_native_start_then_proves_named_interrupt')
        checks.append('one_submitted_turn_no_replacement_work_through_rollout_materialization')
        (fleet.e / 'native-terminal.json').write_text(json.dumps(terminal, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        import signal
        signal.setitimer(signal.ITIMER_REAL, 0)
        model.close()
        if native:
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
