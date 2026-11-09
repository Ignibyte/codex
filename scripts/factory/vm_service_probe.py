#!/usr/bin/env python3
"""One bounded guest-local native RPC; invoked under the explicit seat UID."""
import json
import sys
from session_fixture import Rpc

transcript = []
rpc = Rpc(sys.argv[1], transcript)
try:
    value = json.load(sys.stdin)
    response = rpc.call(value['method'], value['params'])
    print(json.dumps({'result': response, 'transcript': transcript}))
finally:
    rpc.close()
