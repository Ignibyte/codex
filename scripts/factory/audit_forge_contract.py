#!/usr/bin/env python3
"""Pin CF-012's read-only source audit. This is not a deployed-service test."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--forge', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    expected = '97270040748040af4d7a9e7bd4ec9dd5e6af5b5e'
    before = {'commit': git(args.forge, 'rev-parse', 'HEAD'),
              'status': git(args.forge, 'status', '--porcelain')}
    assert before == {'commit': expected, 'status': ''}, before
    files = ['CLAUDE.md', 'docs/architecture/key-identity.md',
             'crates/forge-auth/src/identity.rs', 'crates/forge-mcp-bin/src/identity.rs',
             'crates/forge-domain/src/mailbox.rs',
             'crates/forge-mcp/src/tools/pipeline/mailbox.rs',
             'crates/forge-mcp/src/tools/pipeline/prompts.rs',
             'crates/forge-mcp/src/tools/pipeline/handoff.rs',
             'crates/forge-mcp/src/tools/seat_events/emit.rs',
             'crates/forge-mcp/src/tools/seat_events/list.rs',
             'crates/forge-mcp/src/tools/seat_events/mod.rs',
             'crates/forge-workers/src/cron.rs',
             'migrations/00000000000038_seat_events.sql',
             'migrations/00000000000067_identities_and_forge_calls.sql']
    manifest = {name: hashlib.sha256((args.forge / name).read_bytes()).hexdigest() for name in files}
    # Source anchors only check that the audit refers to this exact interface.
    # They do not execute database transactions or prove runtime behavior.
    anchors = {
        'crates/forge-mcp-bin/src/identity.rs': ['FROM identities WHERE name = $1',
            'identity::verify_signature', '"revoked" =>', 'ctx.seat = Some(name.to_string())'],
        'crates/forge-mcp/src/tools/pipeline/mailbox.rs': ['FOR UPDATE OF r',
            'ClaimGrant::Reclaim', 'body has been purged', 'may_read_response'],
        'crates/forge-domain/src/mailbox.rs': ['ClaimDenial::CrossSeat', 'MailboxState::Expired'],
        'crates/forge-mcp/src/tools/seat_events/emit.rs': ['pub session_id: String',
            'idempotent: false', 'INSERT INTO seat_events'],
        'crates/forge-mcp/src/tools/seat_events/list.rs': ['const MAX_SEAT_EVENTS: i64 = 1000',
            'ts > $2', 'ORDER BY ts DESC, id DESC'],
        'crates/forge-workers/src/cron.rs': ['const MAILBOX_TTL_DAYS_DEFAULT: i64 = 14',
            'const SEAT_EVENT_RETENTION_DAYS_DEFAULT: i64 = 30',
            'const SEAT_EVENT_HEARTBEAT_HOURS_DEFAULT: i64 = 48'],
    }
    for name, fragments in anchors.items():
        content = (args.forge / name).read_text()
        for fragment in fragments:
            assert fragment in content, (name, fragment)
    root = Path(__file__).resolve().parents[2]
    contract = root / 'docs/factory/FORGE_ADAPTER_CONTRACT.md'
    requests = root / 'docs/factory/CORE_REQUESTS.md'
    for name in ['FCR-001', 'FCR-002', 'FCR-003']:
        assert name in requests.read_text() and name in contract.read_text()
    assert (contract.parent / 'CORE_REQUESTS.md').is_file()
    after = {'commit': git(args.forge, 'rev-parse', 'HEAD'),
             'status': git(args.forge, 'status', '--porcelain')}
    assert before == after
    record = {'passed': True, 'exit_code': 0, 'kind': 'read_only_source_audit', 'runtimeAcceptance': False,
              'command': __import__('sys').argv, 'before': before, 'after': after,
              'sourceHashes': manifest, 'sourceAnchors': anchors,
              'sourceMutation': False, 'databaseAccess': False, 'modelCalls': 0,
              'documents': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [contract, requests]},
              'auditRunnerSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.evidence / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'passed': True, 'sourceFiles': len(manifest), 'runtimeAcceptance': False}))


if __name__ == '__main__':
    main()
