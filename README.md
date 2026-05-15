# BTCC OTC 交易平台

基于 Polygon 链上 Escrow 合约的 BTCC/USDC 场外交易平台。

## 功能特点

- **链上托管**：卖家 BTCC 锁定后才挂单，买家 USDC 锁定后平台自动完成交割
- **自动撮合**：到账自动上链、接单自动释放，全程无需人工干预
- **兜底机制**：异常自动恢复 + 告警 + 手动修复工具
- **多钱包支持**：MetaMask / OKX Wallet
- **安全防护**：API 限流、Token 白名单、30分钟超时自动取消

## 架构

```
用户浏览器 (MetaMask/OKX)
    ↓
Nginx (/otc/) → Flask 后端 (app.py, port 8081)
    ↓
SQLite (otc_orders.db)
    ↓
auto_confirm.py ←→ BTCC 节点 (RPC)
    ↓
Polygon 合约 (Escrow V2.2)
```

## 交易流程

### 卖家（卖 BTCC 换 USDC）
1. 连接钱包
2. 填写数量和单价，获取托管地址
3. 将 BTCC 转到托管地址
4. 到账后平台自动上链挂单
5. 买家接单后 USDC 自动到卖家钱包

### 买家（用 USDC 买 BTCC）
1. 连接钱包
2. 选择市场订单，点击接单
3. 首次需授权 USDC（一次授权 1000 额度）
4. 平台自动转 BTCC 到买家地址

## 部署

### 前置要求
- Python 3.8+
- Flask
- Foundry (cast)
- Bitcoin-Classic 节点（wine 运行）
- Nginx（反代）

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/YOUR_USER/btcc-otc.git
cd btcc-otc

# 2. 安装依赖
pip3 install flask

# 3. 安装 Foundry
curl -L https://foundry.paradigm.xyz | bash
foundryup

# 4. 修改配置
cp config.py config_local.py
# 编辑 config_local.py 填入你的私钥、RPC 凭证等

# 5. 启动服务
python3 app.py &          # Flask 后端
python3 auto_confirm.py & # 自动确认脚本
python3 safeguard.py &    # 兜底检查
```

### Systemd 服务（推荐）

```bash
# 复制 service 文件
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable btcc-otc-flask btcc-otc-autoconfirm btcc-otc-safeguard
sudo systemctl start btcc-otc-flask btcc-otc-autoconfirm btcc-otc-safeguard
```

### Nginx 配置

```nginx
location /otc/ {
    proxy_pass http://127.0.0.1:8081/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 紧急操作

```bash
# 关闭平台
sudo systemctl stop btcc-otc-flask btcc-otc-autoconfirm btcc-otc-safeguard

# 恢复平台
sudo systemctl start btcc-otc-flask btcc-otc-autoconfirm btcc-otc-safeguard

# 查看系统状态
python3 safeguard.py status

# 手动转 BTCC 给买家
python3 safeguard.py transfer <order_id>

# 手动释放 USDC 给卖家
python3 safeguard.py confirm <order_id>
```

## 合约

- **地址**：`0x5F181CB61d4404aaE59A81E5205A8A70f3E71f52` (Polygon)
- **验证**：[Blockscout](https://polygon.blockscout.com/address/0x5f181cb61d4404aae59a81e5205a8a70f3e71f52) | [Sourcify](https://sourcify.dev/#/lookup/0x5F181CB61d4404aaE59A81E5205A8A70f3E71f52)
- **功能**：Escrow 托管、Token 白名单、超时取消、Authority 代操作
- **源码**：`contracts/BtccOtcEscrowV2.sol`（当前版本）、`contracts/BtccOtcEscrow.sol`（V1）

## 文件结构

```
├── app.py              # Flask 后端 API
├── auto_confirm.py     # 自动确认脚本（核心）
├── safeguard.py        # 兜底检查 + 手动修复工具
├── config.py           # 配置文件模板
├── rpc_client.py       # BTCC 节点 RPC 封装
├── templates/
│   └── index.html      # 前端页面
├── static/
│   └── ethers.min.js   # ethers.js v6
├── contracts/
│   ├── BtccOtcEscrowV2.sol  # 当前合约 V2.2
│   └── BtccOtcEscrow.sol    # V1 合约（已弃用）
├── systemd/            # systemd 服务文件
└── README.md
```

## License

MIT
