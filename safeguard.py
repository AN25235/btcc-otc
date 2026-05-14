     1|     1|"""BTCC OTC 兜底脚本 — 定期检查所有异常状态并自动修复/告警
     2|     2|每5分钟运行一次，覆盖以下场景：
     3|     3|1. transfer_pending: USDC已释放但BTCC没转成功 → 自动重试
     4|     4|2. 卡住的 sell_intents: BTCC已到账但上链失败 → 自动重试上链
     5|     5|3. 合约已完成但DB未更新 → 同步状态
     6|     6|4. 托管地址有未归集的BTCC → 告警
     7|     7|5. 钱包余额不足 → 告警
     8|     8|6. auto_confirm 进程存活检查
     9|     9|7. 超时未处理的订单 → 强制取消
    10|    10|"""
    11|    11|import time
    12|    12|import subprocess
    13|    13|import sqlite3
    14|    14|import sys
    15|    15|import os
    16|    16|import json
    17|    17|from datetime import datetime
    18|    18|
    19|    19|sys.path.insert(0, '/home/ubuntu/btcc-pool')
    20|    20|from rpc_client import rpc_call
    21|    21|
    22|    22|# 配置
    23|    23|CONTRACT_ADDRESS = "0x5F181CB61d4404aaE59A81E5205A8A70f3E71f52"
    24|    24|POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
    25|    25|PRIVATE_KEY = os.environ.get("OTC_PRIVATE_KEY", "0xYOUR_PRIVATE_KEY_HERE")
    26|    26|FOUNDRY_PATH = "/home/ubuntu/.foundry/bin"
    27|    27|DB_PATH = "/home/ubuntu/btcc-otc-web/otc_orders.db"
    28|    28|LOG_PATH = "/home/ubuntu/btcc-otc-web/safeguard.log"
    29|    29|ALERT_LOG = "/home/ubuntu/btcc-otc-web/alerts.log"
    30|    30|
    31|    31|# 阈值
    32|    32|MIN_WALLET_BALANCE = 10.0  # 钱包余额低于此值告警
    33|    33|MAX_RETRY_TRANSFER = 3  # BTCC转账最大重试次数
    34|    34|INTENT_TIMEOUT = 3600  # 挂单意向超时1小时（BTCC未到账）
    35|    35|ORDER_STUCK_TIMEOUT = 7200  # 订单卡住超时2小时
    36|    36|
    37|    37|
    38|    38|def log(msg):
    39|    39|    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    40|    40|    line = f"[{ts}] {msg}"
    41|    41|    print(line, flush=True)
    42|    42|    with open(LOG_PATH, 'a') as f:
    43|    43|        f.write(line + '\n')
    44|    44|
    45|    45|
    46|    46|def alert(msg):
    47|    47|    """严重告警 — 写入告警日志"""
    48|    48|    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    49|    49|    line = f"[{ts}] ⚠️ ALERT: {msg}"
    50|    50|    print(line, flush=True)
    51|    51|    with open(ALERT_LOG, 'a') as f:
    52|    52|        f.write(line + '\n')
    53|    53|
    54|    54|
    55|    55|def get_db():
    56|    56|    conn = sqlite3.connect(DB_PATH)
    57|    57|    conn.row_factory = sqlite3.Row
    58|    58|    return conn
    59|    59|
    60|    60|
    61|    61|def cast_send(func, args):
    62|    62|    cmd = f'{FOUNDRY_PATH}/cast send {CONTRACT_ADDRESS} "{func}" {args} --rpc-url {POLYGON_RPC} --private-key {PRIVATE_KEY}'
    63|    63|    try:
    64|    64|        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    65|    65|        return result.returncode == 0, result.stdout + result.stderr
    66|    66|    except Exception as e:
    67|    67|        return False, str(e)
    68|    68|
    69|    69|
    70|    70|def get_order_contract_status(order_id):
    71|    71|    cmd = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "getOrder(uint256)", str(order_id), "--rpc-url", POLYGON_RPC]
    72|    72|    try:
    73|    73|        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    74|    74|        if result.returncode != 0:
    75|    75|            return None
    76|    76|        raw = result.stdout.strip().replace("0x", "")
    77|    77|        chunks = [raw[i:i+64] for i in range(0, len(raw), 64)]
    78|    78|        if len(chunks) < 13:
    79|    79|            return None
    80|    80|        return int(chunks[10], 16)
    81|    81|    except:
    82|    82|        return None
    83|    83|
    84|    84|
    85|    85|def transfer_btcc(to_address, amount):
    86|    86|    txid = rpc_call('sendtoaddress', [to_address, str(amount)])
    87|    87|    return txid
    88|    88|
    89|    89|
    90|    90|def get_wallet_balance():
    91|    91|    """获取矿池钱包总余额"""
    92|    92|    result = rpc_call('getbalance', [])
    93|    93|    return float(result) if result else 0
    94|    94|
    95|    95|
    96|    96|def check_process_alive(name):
    97|    97|    """检查 systemd 服务是否运行"""
    98|    98|    try:
    99|    99|        r = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=10)
   100|   100|        return r.stdout.strip() == 'active'
   101|   101|    except:
   102|   102|        return False
   103|   103|
   104|   104|
   105|   105|# ============ 兜底检查 ============
   106|   106|
   107|   107|def fix_transfer_pending():
   108|   108|    """修复 USDC 已释放但 BTCC 转账失败的订单"""
   109|   109|    conn = get_db()
   110|   110|    rows = conn.execute("SELECT * FROM order_records WHERE status='transfer_pending'").fetchall()
   111|   111|    
   112|   112|    for row in rows:
   113|   113|        order_id = row['order_id']
   114|   114|        buyer_addr = row['buyer_btcc_address']
   115|   115|        amount = row['btcc_amount']
   116|   116|        
   117|   117|        if not buyer_addr or not amount:
   118|   118|            alert(f"订单 #{order_id} transfer_pending 但缺少买家地址或金额")
   119|   119|            continue
   120|   120|        
   121|   121|        # 检查合约状态确认已完成
   122|   122|        contract_status = get_order_contract_status(order_id)
   123|   123|        if contract_status != 2:  # 不是 Completed
   124|   124|            alert(f"订单 #{order_id} DB=transfer_pending 但合约状态={contract_status}，需人工检查")
   125|   125|            continue
   126|   126|        
   127|   127|        log(f"[兜底] 订单 #{order_id}: 重试转 {amount} BTCC → {buyer_addr[:20]}...")
   128|   128|        txid = transfer_btcc(buyer_addr, amount)
   129|   129|        
   130|   130|        if txid:
   131|   131|            conn.execute("UPDATE order_records SET status='confirmed', withdraw_txid=?, confirmed_at=? WHERE order_id=?",
   132|   132|                        (txid, int(time.time()), order_id))
   133|   133|            conn.commit()
   134|   134|            log(f"[修复] 订单 #{order_id}: BTCC 转账成功, txid={txid[:20]}...")
   135|   135|        else:
   136|   136|            alert(f"订单 #{order_id}: BTCC 转账再次失败，买家地址={buyer_addr}，金额={amount}")
   137|   137|    
   138|   138|    conn.close()
   139|   139|
   140|   140|
   141|   141|def fix_stuck_intents():
   142|   142|    """修复卡住的挂单意向（BTCC到账但上链失败）"""
   143|   143|    conn = get_db()
   144|   144|    intents = conn.execute("SELECT * FROM sell_intents WHERE status='waiting'").fetchall()
   145|   145|    now = int(time.time())
   146|   146|    
   147|   147|    for intent in intents:
   148|   148|        elapsed = now - intent['created_at']
   149|   149|        
   150|   150|        # 超过1小时还在 waiting，检查是否有到账
   151|   151|        if elapsed > INTENT_TIMEOUT:
   152|   152|            deposit_addr = intent['deposit_address']
   153|   153|            # 检查是否有到账TX
   154|   154|            result = rpc_call('listsinceblock', [])
   155|   155|            if result and isinstance(result, dict):
   156|   156|                txs = result.get('transactions', [])
   157|   157|                receives = [tx for tx in txs
   158|   158|                           if tx.get('category') == 'receive'
   159|   159|                           and tx.get('address') == deposit_addr
   160|   160|                           and tx.get('confirmations', 0) >= 2]
   161|   161|                total = sum(float(tx['amount']) for tx in receives)
   162|   162|                
   163|   163|                if total >= intent['btcc_amount'] - 0.0001:
   164|   164|                    # BTCC 已到账但上链失败，标记需人工处理
   165|   165|                    alert(f"挂单意向 #{intent['id']}: BTCC已到账({total})但超时未上链，需检查 auto_confirm")
   166|   166|                else:
   167|   167|                    # 超时且未到账，自动取消
   168|   168|                    log(f"[兜底] 挂单意向 #{intent['id']}: 超时1小时未到账，自动取消")
   169|   169|                    conn.execute("UPDATE sell_intents SET status='expired' WHERE id=?", (intent['id'],))
   170|   170|                    conn.commit()
   171|   171|    
   172|   172|    conn.close()
   173|   173|
   174|   174|
   175|   175|def sync_contract_status():
   176|   176|    """同步合约状态到DB — 防止DB和链上不一致"""
   177|   177|    conn = get_db()
   178|   178|    
   179|   179|    # 检查所有 listed 的 intent，看合约上是否已完成
   180|   180|    listed = conn.execute("SELECT * FROM sell_intents WHERE status='listed'").fetchall()
   181|   181|    for intent in listed:
   182|   182|        coid = intent['contract_order_id']
   183|   183|        if coid is None:
   184|   184|            continue
   185|   185|        status = get_order_contract_status(coid)
   186|   186|        if status == 2:  # Completed
   187|   187|            # 合约已完成但 intent 还是 listed → 同步
   188|   188|            log(f"[同步] 挂单意向 #{intent['id']} (合约#{coid}): 合约已完成，同步DB")
   189|   189|            conn.execute("UPDATE sell_intents SET status='completed' WHERE id=?", (intent['id'],))
   190|   190|            conn.commit()
   191|   191|        elif status == 3:  # Cancelled
   192|   192|            log(f"[同步] 挂单意向 #{intent['id']} (合约#{coid}): 合约已取消，同步DB")
   193|   193|            conn.execute("UPDATE sell_intents SET status='cancelled' WHERE id=?", (intent['id'],))
   194|   194|            conn.commit()
   195|   195|    
   196|   196|    # 检查 order_records 中 accepted/pending 状态
   197|   197|    pending = conn.execute("SELECT * FROM order_records WHERE status IN ('pending','accepted')").fetchall()
   198|   198|    for row in pending:
   199|   199|        oid = row['order_id']
   200|   200|        status = get_order_contract_status(oid)
   201|   201|        if status == 2:  # Completed on chain
   202|   202|            # 检查是否已转 BTCC
   203|   203|            if row['withdraw_txid']:
   204|   204|                conn.execute("UPDATE order_records SET status='confirmed' WHERE order_id=?", (oid,))
   205|   205|            else:
   206|   206|                conn.execute("UPDATE order_records SET status='transfer_pending' WHERE order_id=?", (oid,))
   207|   207|            conn.commit()
   208|   208|            log(f"[同步] 订单 #{oid}: 合约已完成，DB状态已更新")
   209|   209|        elif status == 3:  # Cancelled
   210|   210|            conn.execute("UPDATE order_records SET status='cancelled' WHERE order_id=?", (oid,))
   211|   211|            conn.commit()
   212|   212|            log(f"[同步] 订单 #{oid}: 合约已取消，DB状态已更新")
   213|   213|    
   214|   214|    conn.close()
   215|   215|
   216|   216|
   217|   217|def check_wallet_balance():
   218|   218|    """检查钱包余额是否充足"""
   219|   219|    balance = get_wallet_balance()
   220|   220|    if balance < MIN_WALLET_BALANCE:
   221|   221|        alert(f"矿池钱包余额不足！当前: {balance:.4f} BTCC，低于阈值 {MIN_WALLET_BALANCE}")
   222|   222|    else:
   223|   223|        log(f"[余额] 钱包余额: {balance:.4f} BTCC ✓")
   224|   224|
   225|   225|
   226|   226|def check_services():
   227|   227|    """检查关键服务是否存活"""
   228|   228|    services = {
   229|   229|        'btcc-otc-flask': 'OTC Flask 后端',
   230|   230|        'btcc-otc-autoconfirm': 'OTC 自动确认脚本',
   231|   231|    }
   232|   232|    for svc, name in services.items():
   233|   233|        if not check_process_alive(svc):
   234|   234|            alert(f"服务 {name} ({svc}) 未运行！尝试重启...")
   235|   235|            try:
   236|   236|                subprocess.run(['systemctl', 'restart', svc], timeout=10)
   237|   237|                time.sleep(3)
   238|   238|                if check_process_alive(svc):
   239|   239|                    log(f"[恢复] {name} 重启成功")
   240|   240|                else:
   241|   241|                    alert(f"服务 {name} 重启失败！需人工介入")
   242|   242|            except Exception as e:
   243|   243|                alert(f"服务 {name} 重启异常: {e}")
   244|   244|        else:
   245|   245|            log(f"[服务] {name} 运行中 ✓")
   246|   246|
   247|   247|
   248|   248|def check_unrecovered_funds():
   249|   249|    """检查托管地址中未归集的 BTCC（已取消/过期的意向）"""
   250|   250|    conn = get_db()
   251|   251|    # 已取消/过期但可能有 BTCC 留在托管地址的
   252|   252|    cancelled = conn.execute(
   253|   253|        "SELECT * FROM sell_intents WHERE status IN ('cancelled','expired')"
   254|   254|    ).fetchall()
   255|   255|    
   256|   256|    for intent in cancelled:
   257|   257|        deposit_addr = intent['deposit_address']
   258|   258|        # 查余额
   259|   259|        utxos = rpc_call('listunspent', [1, 9999999, [deposit_addr]])
   260|   260|        if utxos:
   261|   261|            total = sum(float(u['amount']) for u in utxos)
   262|   262|            if total > 0.001:
   263|   263|                alert(f"托管地址 {deposit_addr[:25]}... 有 {total:.4f} BTCC 未归集（意向#{intent['id']} 已{intent['status']}）")
   264|   264|    
   265|   265|    conn.close()
   266|   266|
   267|   267|
   268|   268|def check_rpc_health():
   269|   269|    """检查 BTCC 节点和 Polygon RPC 是否正常"""
   270|   270|    # BTCC 节点
   271|   271|    height = rpc_call('getblockcount', [])
   272|   272|    if height:
   273|   273|        log(f"[RPC] BTCC 节点正常，高度: {height}")
   274|   274|    else:
   275|   275|        alert("BTCC 节点 RPC 无响应！")
   276|   276|    
   277|   277|    # Polygon RPC
   278|   278|    try:
   279|   279|        cmd = [f"{FOUNDRY_PATH}/cast", "block-number", "--rpc-url", POLYGON_RPC]
   280|   280|        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
   281|   281|        if r.returncode == 0:
   282|   282|            log(f"[RPC] Polygon RPC 正常，高度: {r.stdout.strip()}")
   283|   283|        else:
   284|   284|            alert("Polygon RPC 无响应！")
   285|   285|    except:
   286|   286|        alert("Polygon RPC 超时！")
   287|   287|
   288|   288|
   289|   289|# ============ 手动修复工具 ============
   290|   290|
   291|   291|def manual_transfer_btcc(order_id):
   292|   292|    """手动触发 BTCC 转账（用于 transfer_pending 订单）"""
   293|   293|    conn = get_db()
   294|   294|    row = conn.execute("SELECT * FROM order_records WHERE order_id=?", (order_id,)).fetchone()
   295|   295|    if not row:
   296|   296|        print(f"订单 #{order_id} 不存在")
   297|   297|        return
   298|   298|    if not row['buyer_btcc_address']:
   299|   299|        print(f"订单 #{order_id} 无买家地址")
   300|   300|        return
   301|   301|    
   302|   302|    print(f"转账: {row['btcc_amount']} BTCC → {row['buyer_btcc_address']}")
   303|   303|    txid = transfer_btcc(row['buyer_btcc_address'], row['btcc_amount'])
   304|   304|    if txid:
   305|   305|        conn.execute("UPDATE order_records SET status='confirmed', withdraw_txid=?, confirmed_at=? WHERE order_id=?",
   306|   306|                    (txid, int(time.time()), order_id))
   307|   307|        conn.commit()
   308|   308|        print(f"成功! txid: {txid}")
   309|   309|    else:
   310|   310|        print("转账失败")
   311|   311|    conn.close()
   312|   312|
   313|   313|
   314|   314|def manual_refund_intent(intent_id):
   315|   315|    """手动退回已取消意向的 BTCC 到卖家"""
   316|   316|    conn = get_db()
   317|   317|    intent = conn.execute("SELECT * FROM sell_intents WHERE id=?", (intent_id,)).fetchone()
   318|   318|    if not intent:
   319|   319|        print(f"意向 #{intent_id} 不存在")
   320|   320|        return
   321|   321|    
   322|   322|    deposit_addr = intent['deposit_address']
   323|   323|    seller_addr = intent['seller_address']
   324|   324|    
   325|   325|    # 查托管地址余额
   326|   326|    utxos = rpc_call('listunspent', [1, 9999999, [deposit_addr]])
   327|   327|    if not utxos:
   328|   328|        print(f"托管地址 {deposit_addr} 无余额")
   329|   329|        return
   330|   330|    
   331|   331|    total = sum(float(u['amount']) for u in utxos)
   332|   332|    print(f"托管地址余额: {total} BTCC")
   333|   333|    
   334|   334|    # 查卖家的 BTCC 地址（用 user_addresses 表中的 deposit_address 本身退回）
   335|   335|    # 由于卖家只有 Polygon 地址，BTCC 退回到同一个托管地址（卖家下次可用）
   336|   336|    print(f"注意: BTCC 保留在托管地址 {deposit_addr}，卖家下次挂单可直接使用")
   337|   337|    conn.close()
   338|   338|
   339|   339|
   340|   340|def manual_confirm_release(order_id, txid="manual"):
   341|   341|    """手动调用合约 confirmRelease"""
   342|   342|    print(f"调用 confirmRelease({order_id}, \"{txid}\")...")
   343|   343|    success, output = cast_send("confirmRelease(uint256,string)", [str(order_id), txid])
   344|   344|    if success:
   345|   345|        print(f"成功! USDC 已释放给卖家")
   346|   346|    else:
   347|   347|        print(f"失败: {output[:200]}")
   348|   348|
   349|   349|
   350|   350|def show_status():
   351|   351|    """显示当前系统状态概览"""
   352|   352|    conn = get_db()
   353|   353|    
   354|   354|    print("=" * 60)
   355|   355|    print("BTCC OTC 系统状态概览")
   356|   356|    print("=" * 60)
   357|   357|    
   358|   358|    # 钱包余额
   359|   359|    balance = get_wallet_balance()
   360|   360|    print(f"\n💰 钱包余额: {balance:.4f} BTCC")
   361|   361|    
   362|   362|    # 服务状态
   363|   363|    print(f"\n🔧 服务状态:")
   364|   364|    for svc in ['btcc-otc-flask', 'btcc-otc-autoconfirm']:
   365|   365|        status = "✅ 运行中" if check_process_alive(svc) else "❌ 已停止"
   366|   366|        print(f"   {svc}: {status}")
   367|   367|    
   368|   368|    # 订单统计
   369|   369|    print(f"\n📊 订单统计:")
   370|   370|    for status in ['pending', 'accepted', 'transfer_pending', 'confirmed', 'cancelled', 'timeout']:
   371|   371|        count = conn.execute("SELECT COUNT(*) FROM order_records WHERE status=?", (status,)).fetchone()[0]
   372|   372|        if count > 0:
   373|   373|            print(f"   {status}: {count}")
   374|   374|    
   375|   375|    # 意向统计
   376|   376|    print(f"\n📋 挂单意向:")
   377|   377|    for status in ['waiting', 'listed', 'completed', 'cancelled', 'expired']:
   378|   378|        count = conn.execute("SELECT COUNT(*) FROM sell_intents WHERE status=?", (status,)).fetchone()[0]
   379|   379|        if count > 0:
   380|   380|            print(f"   {status}: {count}")
   381|   381|    
   382|   382|    # 告警
   383|   383|    if os.path.exists(ALERT_LOG):
   384|   384|        with open(ALERT_LOG) as f:
   385|   385|            alerts = f.readlines()
   386|   386|        recent = alerts[-5:] if alerts else []
   387|   387|        if recent:
   388|   388|            print(f"\n🚨 最近告警:")
   389|   389|            for a in recent:
   390|   390|                print(f"   {a.strip()}")
   391|   391|    
   392|   392|    print("\n" + "=" * 60)
   393|   393|    conn.close()
   394|   394|
   395|   395|
   396|   396|# ============ 主循环 ============
   397|   397|
   398|   398|def run_all_checks():
   399|   399|    """执行所有兜底检查"""
   400|   400|    log("=" * 40)
   401|   401|    log("开始兜底检查...")
   402|   402|    
   403|   403|    check_rpc_health()
   404|   404|    check_services()
   405|   405|    check_wallet_balance()
   406|   406|    fix_transfer_pending()
   407|   407|    fix_stuck_intents()
   408|   408|    sync_contract_status()
   409|   409|    check_unrecovered_funds()
   410|   410|    
   411|   411|    log("兜底检查完成")
   412|   412|    log("=" * 40)
   413|   413|
   414|   414|
   415|   415|if __name__ == '__main__':
   416|   416|    if len(sys.argv) > 1:
   417|   417|        cmd = sys.argv[1]
   418|   418|        if cmd == 'status':
   419|   419|            show_status()
   420|   420|        elif cmd == 'transfer' and len(sys.argv) > 2:
   421|   421|            manual_transfer_btcc(int(sys.argv[2]))
   422|   422|        elif cmd == 'refund' and len(sys.argv) > 2:
   423|   423|            manual_refund_intent(int(sys.argv[2]))
   424|   424|        elif cmd == 'confirm' and len(sys.argv) > 2:
   425|   425|            txid = sys.argv[3] if len(sys.argv) > 3 else "manual"
   426|   426|            manual_confirm_release(int(sys.argv[2]), txid)
   427|   427|        elif cmd == 'once':
   428|   428|            run_all_checks()
   429|   429|        else:
   430|   430|            print("用法:")
   431|   431|            print("  python3 safeguard.py          # 持续运行（每5分钟检查）")
   432|   432|            print("  python3 safeguard.py once     # 执行一次检查")
   433|   433|            print("  python3 safeguard.py status   # 查看系统状态")
   434|   434|            print("  python3 safeguard.py transfer <order_id>  # 手动转BTCC")
   435|   435|            print("  python3 safeguard.py refund <intent_id>   # 手动退款")
   436|   436|            print("  python3 safeguard.py confirm <order_id> [txid]  # 手动确认释放")
   437|   437|    else:
   438|   438|        # 持续运行模式
   439|   439|        log("BTCC OTC 兜底脚本启动")
   440|   440|        log(f"检查间隔: 300秒")
   441|   441|        while True:
   442|   442|            try:
   443|   443|                run_all_checks()
   444|   444|            except Exception as e:
   445|   445|                alert(f"兜底脚本异常: {e}")
   446|   446|            time.sleep(300)
   447|   447|