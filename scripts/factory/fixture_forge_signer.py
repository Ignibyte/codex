#!/usr/bin/python3
"""Synthetic Ed25519 Forge pass helper. Never use a production key here."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--name', default='factory-fixture')
    parser.add_argument('--keygen', action='store_true')
    args = parser.parse_args()
    if args.keygen:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(json.dumps({'publicKey': b64(public)}))
        return
    key = Ed25519PrivateKey.from_private_bytes(args.key.read_bytes())
    now = int(time.time())
    payload = {'name': args.name, 'session': None, 'iat': now, 'exp': now + 60,
               'nonce': str(uuid.uuid4())}
    segment = b64(json.dumps(payload, separators=(',', ':')).encode())
    signature = b64(key.sign(b'forge-pass|' + segment.encode()))
    print(json.dumps({'X-Forge-Identity': args.name,
                      'X-Forge-Assertion': segment + '.' + signature,
                      'Authorization': 'Bearer signed-fixture-identity'}))


if __name__ == '__main__':
    main()
