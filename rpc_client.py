"""BTCC Mining Pool - Node RPC Client"""
import json
import subprocess
import os

WINE_CLI = os.environ.get("BTCC_CLI_PATH", "/tmp/bitcoin-classic-local/bitcoin-classic-cli.exe")
RPC_USER = os.environ.get("BTCC_RPC_USER", "YOUR_RPC_USER")
RPC_PASS = os.environ.get("BTCC_RPC_PASSWORD", "YOUR_RPC_PASSWORD")
RPC_PORT = os.environ.get("BTCC_RPC_PORT", "28476")
RPC_ARGS = f'-rpcuser={RPC_USER} -rpcpassword={RPC_PASS} -rpcport={RPC_PORT}'


def rpc_call(method, params=None):
    """Call Bitcoin Classic node via wine CLI"""
    env = os.environ.copy()
    env['DISPLAY'] = ':99'

    cmd = ['wine', WINE_CLI] + RPC_ARGS.split() + [method]
    if params:
        for p in params:
            cmd.append(str(p))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None
