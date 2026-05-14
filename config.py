"""BTCC OTC 平台配置文件
部署前设置环境变量或修改此文件中的默认值。
优先从环境变量读取敏感配置。
"""
import os

# ============ 合约配置 ============
CONTRACT_ADDRESS = "0x5F181CB61d4404aaE59A81E5205A8A70f3E71f52"
POLYGON_RPC = os.environ.get("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
POLYGON_CHAIN_ID = 137

# 部署钱包私钥（authority，用于代卖家上链 + confirmRelease）
# ⚠️ 必须通过环境变量设置: export OTC_PRIVATE_KEY=0x...
PRIVATE_KEY = os.environ.get("OTC_PRIVATE_KEY", "")
if not PRIVATE_KEY:
    print("[警告] 未设置 OTC_PRIVATE_KEY 环境变量，合约操作将失败")

# ============ 代币白名单 ============
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDT_ADDRESS = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"

# ============ BTCC 节点 RPC ============
BTCC_RPC_USER = os.environ.get("BTCC_RPC_USER", "")
BTCC_RPC_PASSWORD = os.environ.get("BTCC_RPC_PASSWORD", "")
BTCC_RPC_PORT = int(os.environ.get("BTCC_RPC_PORT", "28476"))

# ============ 路径配置 ============
FOUNDRY_PATH = os.environ.get("FOUNDRY_PATH", "/home/ubuntu/.foundry/bin")
WINE_CLI = os.environ.get("BTCC_CLI_PATH", "/tmp/bitcoin-classic-local/bitcoin-classic-cli.exe")
DB_PATH = os.environ.get("OTC_DB_PATH", "./otc_orders.db")

# ============ 业务参数 ============
CHECK_INTERVAL = 10          # auto_confirm 扫描间隔（秒）
CONFIRMATIONS_REQUIRED = 2   # BTCC 到账确认数
SAFEGUARD_INTERVAL = 300     # 兜底检查间隔（秒）
MIN_WALLET_BALANCE = 10.0    # 钱包余额告警阈值（BTCC）
PLATFORM_FEE_PERCENT = 2.5   # 平台手续费（%）
