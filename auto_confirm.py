     1|     1|"""BTCC OTC 自动确认脚本 v5 — 卖家先锁 BTCC 再上链
     2|     2|流程：
     3|     3|1. 扫描 sell_intents 表中 status='waiting' 的挂单意向
     4|     4|2. 检测 BTCC 到账 → 调合约 createSellOrder 上链
     5|     5|3. 扫描已接单订单（status='pending'），确认 BTCC 到账 → confirmRelease
     6|     6|4. 30分钟超时自动取消接单
     7|     7|5. 取消的订单退回 BTCC 给卖家
     8|     8|"""
     9|     9|import time
import os
    10|    10|import subprocess
    11|    11|import sys
    12|    12|import sqlite3
    13|    13|
    14|    14|sys.path.insert(0, '/home/ubuntu/btcc-pool')
    15|    15|from rpc_client import rpc_call
    16|    16|
    17|    17|# 配置
    18|    18|CONTRACT_ADDRESS = "0x5F181CB61d4404aaE59A81E5205A8A70f3E71f52"
    19|    19|POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
    20|    20|PRIVATE_KEY = os.environ.get("OTC_PRIVATE_KEY", "0xYOUR_PRIVATE_KEY_HERE")
    21|    21|FOUNDRY_PATH = "/home/ubuntu/.foundry/bin"
    22|    22|CHECK_INTERVAL = 10
    23|    23|CONFIRMATIONS_REQUIRED = 2
    24|    24|DB_PATH = "/home/ubuntu/btcc-otc-web/otc_orders.db"
    25|    25|
    26|    26|
    27|    27|def get_db():
    28|    28|    conn = sqlite3.connect(DB_PATH)
    29|    29|    conn.row_factory = sqlite3.Row
    30|    30|    return conn
    31|    31|
    32|    32|
    33|    33|def get_receive_txs_for_address(address):
    34|    34|    """获取指定地址的所有 receive 交易"""
    35|    35|    result = rpc_call('listsinceblock', [])
    36|    36|    if not result or not isinstance(result, dict):
    37|    37|        return []
    38|    38|    txs = result.get('transactions', [])
    39|    39|    receives = [tx for tx in txs
    40|    40|                if tx.get('category') == 'receive'
    41|    41|                and tx.get('address') == address
    42|    42|                and tx.get('confirmations', 0) >= CONFIRMATIONS_REQUIRED]
    43|    43|    return receives
    44|    44|
    45|    45|
    46|    46|def is_txid_used(conn, txid):
    47|    47|    """检查 txid 是否已被使用"""
    48|    48|    row = conn.execute("SELECT order_id FROM used_txids WHERE txid=?", (txid,)).fetchone()
    49|    49|    return row is not None
    50|    50|
    51|    51|
    52|    52|def mark_txid_used(conn, txid, order_id, amount):
    53|    53|    """标记 txid 已使用"""
    54|    54|    conn.execute("INSERT OR IGNORE INTO used_txids (txid, order_id, amount, used_at) VALUES (?, ?, ?, ?)",
    55|    55|                 (txid, order_id, amount, int(time.time())))
    56|    56|    conn.commit()
    57|    57|
    58|    58|
    59|    59|def cast_send(func, args):
    60|    60|    """发送合约交易"""
    61|    61|    cmd = f'{FOUNDRY_PATH}/cast send {CONTRACT_ADDRESS} "{func}" {args} --rpc-url {POLYGON_RPC} --private-key {PRIVATE_KEY}'
    62|    62|    try:
    63|    63|        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    64|    64|        return result.returncode == 0, result.stdout + result.stderr
    65|    65|    except Exception as e:
    66|    66|        return False, str(e)
    67|    67|
    68|    68|
    69|    69|def confirm_release(order_id, txid):
    70|    70|    """调用合约确认释放 USDC"""
    71|    71|    args = f'{order_id} "{txid}"'
    72|    72|    success, output = cast_send("confirmRelease(uint256,string)", args)
    73|    73|    if success:
    74|    74|        print(f"[成功] 订单 #{order_id} USDC 已释放", flush=True)
    75|    75|    else:
    76|    76|        print(f"[失败] 订单 #{order_id} 确认失败: {output[:200]}", flush=True)
    77|    77|    return success
    78|    78|
    79|    79|
    80|    80|def transfer_btcc(to_address, amount):
    81|    81|    """把 BTCC 转给买家"""
    82|    82|    txid = rpc_call('sendtoaddress', [to_address, str(amount)])
    83|    83|    if txid:
    84|    84|        print(f"[转账] 已转 {amount} BTCC → {to_address[:25]}..., txid: {txid[:20]}...", flush=True)
    85|    85|    else:
    86|    86|        print(f"[失败] 转账 {amount} BTCC → {to_address} 失败", flush=True)
    87|    87|    return txid
    88|    88|
    89|    89|
    90|    90|def get_order_contract_status(order_id):
    91|    91|    """从合约获取订单状态"""
    92|    92|    cmd = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "getOrder(uint256)", str(order_id), "--rpc-url", POLYGON_RPC]
    93|    93|    try:
    94|    94|        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    95|    95|        if result.returncode != 0:
    96|    96|            return None
    97|    97|        raw = result.stdout.strip().replace("0x", "")
    98|    98|        chunks = [raw[i:i+64] for i in range(0, len(raw), 64)]
    99|    99|        if len(chunks) < 13:
   100|   100|            return None
   101|   101|        return int(chunks[10], 16)
   102|   102|    except:
   103|   103|        return None
   104|   104|
   105|   105|
   106|   106|def find_matching_tx(conn, order, receives):
   107|   107|    """为订单找匹配的 TX：金额 >= 订单金额，时间 > 订单创建时间，txid 未使用"""
   108|   108|    btcc_amount = order['btcc_amount']
   109|   109|    created_at = order['created_at']
   110|   110|
   111|   111|    # 按时间排序，优先匹配最早的
   112|   112|    sorted_txs = sorted(receives, key=lambda x: x.get('time', 0))
   113|   113|
   114|   114|    for tx in sorted_txs:
   115|   115|        txid = tx['txid']
   116|   116|        amount = float(tx['amount'])
   117|   117|        tx_time = tx.get('time', 0)
   118|   118|
   119|   119|        # 金额匹配（允许 0.0001 误差）
   120|   120|        if amount < btcc_amount - 0.0001:
   121|   121|            continue
   122|   122|
   123|   123|        # TX 时间 > 订单创建时间（允许60秒误差）
   124|   124|        if tx_time < created_at - 60:
   125|   125|            continue
   126|   126|
   127|   127|        # txid 未被使用
   128|   128|        if is_txid_used(conn, txid):
   129|   129|            continue
   130|   130|
   131|   131|        return tx
   132|   132|
   133|   133|    return None
   134|   134|
   135|   135|
   136|   136|def create_sell_order_onchain(seller_address, token, btcc_amount, unit_price):
   137|   137|    """调合约 createSellOrderFor（由 authority 代卖家创建）"""
   138|   138|    # btccAmount 用 8 位小数，unitPrice 用 6 位小数
   139|   139|    btcc_wei = int(btcc_amount * 1e8)
   140|   140|    price_wei = int(unit_price * 1e6)
   141|   141|    args = f'{seller_address} {token} {btcc_wei} {price_wei}'
   142|   142|    success, output = cast_send("createSellOrderFor(address,address,uint256,uint256)", args)
   143|   143|    if success:
   144|   144|        print(f"[上链] 卖家 {seller_address[:10]}... 挂单成功: {btcc_amount} BTCC @ {unit_price}", flush=True)
   145|   145|    else:
   146|   146|        print(f"[失败] 上链失败: {output[:200]}", flush=True)
   147|   147|    return success
   148|   148|
   149|   149|
   150|   150|def process_sell_intents(conn):
   151|   151|    """扫描等待 BTCC 到账的挂单意向"""
   152|   152|    intents = conn.execute("SELECT * FROM sell_intents WHERE status='waiting'").fetchall()
   153|   153|    if not intents:
   154|   154|        return
   155|   155|
   156|   156|    for intent in intents:
   157|   157|        deposit_addr = intent['deposit_address']
   158|   158|        btcc_amount = intent['btcc_amount']
   159|   159|        created_at = intent['created_at']
   160|   160|
   161|   161|        # 获取该地址的 receive 交易
   162|   162|        receives = get_receive_txs_for_address(deposit_addr)
   163|   163|        if not receives:
   164|   164|            continue
   165|   165|
   166|   166|        # 找匹配的 TX
   167|   167|        sorted_txs = sorted(receives, key=lambda x: x.get('time', 0))
   168|   168|        matched_tx = None
   169|   169|        for tx in sorted_txs:
   170|   170|            txid = tx['txid']
   171|   171|            amount = float(tx['amount'])
   172|   172|            tx_time = tx.get('time', 0)
   173|   173|            if amount < btcc_amount - 0.0001:
   174|   174|                continue
   175|   175|            if tx_time < created_at - 60:
   176|   176|                continue
   177|   177|            if is_txid_used(conn, txid):
   178|   178|                continue
   179|   179|            matched_tx = tx
   180|   180|            break
   181|   181|
   182|   182|        if not matched_tx:
   183|   183|            continue
   184|   184|
   185|   185|        txid = matched_tx['txid']
   186|   186|        amount = float(matched_tx['amount'])
   187|   187|        print(f"[到账] 挂单意向 #{intent['id']}: TX {txid[:20]}... 金额={amount} >= {btcc_amount}", flush=True)
   188|   188|
   189|   189|        # 上链创建订单
   190|   190|        success = create_sell_order_onchain(
   191|   191|            intent['seller_address'], intent['token'],
   192|   192|            intent['btcc_amount'], intent['unit_price']
   193|   193|        )
   194|   194|
   195|   195|        if success:
   196|   196|            mark_txid_used(conn, txid, -intent['id'], amount)
   197|   197|            # 获取刚创建的合约订单 ID（nextOrderId - 1）
   198|   198|            try:
   199|   199|                cmd = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "nextOrderId()", "--rpc-url", POLYGON_RPC]
   200|   200|                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
   201|   201|                contract_order_id = int(r.stdout.strip(), 16) - 1
   202|   202|            except:
   203|   203|                contract_order_id = None
   204|   204|            conn.execute("""
   205|   205|                UPDATE sell_intents SET status='listed', matched_txid=?, confirmed_at=?, contract_order_id=?
   206|   206|                WHERE id=?
   207|   207|            """, (txid, int(time.time()), contract_order_id, intent['id']))
   208|   208|            conn.commit()
   209|   209|            print(f"[完成] 挂单意向 #{intent['id']} 已上链, 合约订单 #{contract_order_id}", flush=True)
   210|   210|
   211|   211|            # 退回多余金额
   212|   212|            excess = amount - btcc_amount
   213|   213|            if excess > 0.0001:
   214|   214|                # 退回到卖家的托管地址（下次可以用）
   215|   215|                print(f"[提示] 挂单意向 #{intent['id']}: 多转了 {excess:.8f} BTCC，保留在托管地址", flush=True)
   216|   216|
   217|   217|
   218|   218|def process_cancelled_intents(conn):
   219|   219|    """检测合约上被取消的订单，退 BTCC 给卖家"""
   220|   220|    listed_intents = conn.execute("SELECT * FROM sell_intents WHERE status='listed'").fetchall()
   221|   221|    for intent in listed_intents:
   222|   222|        contract_order_id = intent['contract_order_id']
   223|   223|        if contract_order_id is None:
   224|   224|            continue
   225|   225|        # 查合约状态
   226|   226|        status = get_order_contract_status(contract_order_id)
   227|   227|        if status == 3:  # Cancelled
   228|   228|            # 退 BTCC 给卖家（从托管地址转回）
   229|   229|            deposit_addr = intent['deposit_address']
   230|   230|            btcc_amount = intent['btcc_amount']
   231|   231|            seller_address = intent['seller_address']
   232|   232|
   233|   233|            # 查托管地址余额
   234|   234|            receives = get_receive_txs_for_address(deposit_addr)
   235|   235|            total_received = sum(float(tx['amount']) for tx in receives)
   236|   236|
   237|   237|            if total_received >= btcc_amount - 0.0001:
   238|   238|                # 需要卖家的 BTCC 退回地址 — 用 user_addresses 表中的 deposit_address 本身
   239|   239|                # 实际退回到卖家的 BTCC 链地址（需要卖家提供，暂退到托管地址不动）
   240|   240|                # 这里直接标记为 cancelled，BTCC 留在托管地址供卖家下次使用
   241|   241|                print(f"[取消] 挂单意向 #{intent['id']} 合约订单 #{contract_order_id} 已被取消，BTCC 保留在托管地址 {deposit_addr[:20]}...", flush=True)
   242|   242|
   243|   243|            conn.execute("UPDATE sell_intents SET status='cancelled' WHERE id=?", (intent['id'],))
   244|   244|            conn.commit()
   245|   245|
   246|   246|
   247|   247|def get_order_buyer_btcc_address(order_id):
   248|   248|    """从合约获取订单的买家 BTCC 地址"""
   249|   249|    cmd = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "getOrder(uint256)", str(order_id), "--rpc-url", POLYGON_RPC]
   250|   250|    try:
   251|   251|        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
   252|   252|        if result.returncode != 0:
   253|   253|            return None, None
   254|   254|        raw = result.stdout.strip().replace("0x", "")
   255|   255|        chunks = [raw[i:i+64] for i in range(0, len(raw), 64)]
   256|   256|        if len(chunks) < 13:
   257|   257|            return None, None
   258|   258|        status = int(chunks[10], 16)
   259|   259|        # btccAddress 是动态字符串，offset 在 chunks[8]（相对于 tuple 起始）
   260|   260|        offset_val = int(chunks[8], 16)
   261|   261|        base = 1  # tuple 从 chunks[1] 开始
   262|   262|        addr_chunk_idx = base + offset_val // 32
   263|   263|        addr_len = int(chunks[addr_chunk_idx], 16)
   264|   264|        if addr_len > 0:
   265|   265|            # 读取完整字符串（可能跨多个 chunk）
   266|   266|            data_start = (addr_chunk_idx + 1) * 64
   267|   267|            data_hex = raw[data_start:data_start + addr_len * 2]
   268|   268|            btcc_address = bytes.fromhex(data_hex).decode('utf-8', errors='ignore')
   269|   269|        else:
   270|   270|            btcc_address = ''
   271|   271|        return status, btcc_address
   272|   272|    except:
   273|   273|        return None, None
   274|   274|
   275|   275|
   276|   276|def process_accepted_orders(conn):
   277|   277|    """检测合约上已接单的订单，BTCC 已在托管地址，直接 confirmRelease + 转 BTCC"""
   278|   278|    # 查找已上链的 sell_intents（BTCC 已锁定）
   279|   279|    listed_intents = conn.execute("SELECT * FROM sell_intents WHERE status='listed'").fetchall()
   280|   280|    if not listed_intents:
   281|   281|        return
   282|   282|
   283|   283|    for intent in listed_intents:
   284|   284|        # 查合约上以该卖家创建的订单，找到 Accepted 状态的
   285|   285|        # 遍历合约订单
   286|   286|        cmd = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "nextOrderId()", "--rpc-url", POLYGON_RPC]
   287|   287|        try:
   288|   288|            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
   289|   289|            next_id = int(result.stdout.strip(), 16)
   290|   290|        except:
   291|   291|            continue
   292|   292|
   293|   293|        for oid in range(next_id):
   294|   294|            status = get_order_contract_status(oid)
   295|   295|            if status != 1:  # 只处理 Accepted
   296|   296|                continue
   297|   297|
   298|   298|            # 检查是否已在 order_records 里处理过
   299|   299|            existing = conn.execute("SELECT * FROM order_records WHERE order_id=? AND status IN ('confirmed','transfer_pending')", (oid,)).fetchone()
   300|   300|            if existing:
   301|   301|                continue
   302|   302|
   303|   303|            # 获取买家 BTCC 地址
   304|   304|            _, buyer_btcc_addr = get_order_buyer_btcc_address(oid)
   305|   305|            if not buyer_btcc_addr:
   306|   306|                continue
   307|   307|
   308|   308|            # 确认这个订单对应的 intent（通过卖家地址匹配）
   309|   309|            # 获取订单的 seller
   310|   310|            cmd2 = [f"{FOUNDRY_PATH}/cast", "call", CONTRACT_ADDRESS, "getOrder(uint256)", str(oid), "--rpc-url", POLYGON_RPC]
   311|   311|            try:
   312|   312|                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
   313|   313|                raw2 = r2.stdout.strip().replace("0x", "")
   314|   314|                chunks2 = [raw2[i:i+64] for i in range(0, len(raw2), 64)]
   315|   315|                seller = "0x" + chunks2[2][24:]
   316|   316|                btcc_amount = int(chunks2[5], 16) / 1e8
   317|   317|            except:
   318|   318|                continue
   319|   319|
   320|   320|            # 匹配 intent（优先用 contract_order_id 精确匹配，否则用 seller+amount）
   321|   321|            matching_intent = None
   322|   322|            for i in listed_intents:
   323|   323|                if i['contract_order_id'] == oid:
   324|   324|                    matching_intent = i
   325|   325|                    break
   326|   326|            if not matching_intent:
   327|   327|                for i in listed_intents:
   328|   328|                    if i['seller_address'].lower() == seller.lower() and abs(i['btcc_amount'] - btcc_amount) < 0.0001:
   329|   329|                        matching_intent = i
   330|   330|                        break
   331|   331|
   332|   332|            if not matching_intent:
   333|   333|                continue
   334|   334|
   335|   335|            deposit_addr = matching_intent['deposit_address']
   336|   336|            txid = matching_intent['matched_txid'] or ''
   337|   337|
   338|   338|            print(f"[自动完成] 订单 #{oid}: 买家已接单，BTCC 已在托管，执行 confirmRelease", flush=True)
   339|   339|
   340|   340|            # 1. confirmRelease
   341|   341|            if not confirm_release(oid, txid):
   342|   342|                continue
   343|   343|
   344|   344|            # 2. 转 BTCC 给买家
   345|   345|            withdraw_txid = transfer_btcc(buyer_btcc_addr, btcc_amount)
   346|   346|
   347|   347|            # 3. 记录
   348|   348|            conn.execute("""
   349|   349|                INSERT OR REPLACE INTO order_records 
   350|   350|                (order_id, seller_address, buyer_btcc_address, btcc_amount, deposit_address, status, matched_txid, withdraw_txid, created_at, confirmed_at)
   351|   351|                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   352|   352|            """, (oid, seller, buyer_btcc_addr, btcc_amount, deposit_addr,
   353|   353|                  'confirmed' if withdraw_txid else 'transfer_pending',
   354|   354|                  txid, withdraw_txid, int(time.time()), int(time.time())))
   355|   355|            conn.commit()
   356|   356|
   357|   357|            # 4. 更新 intent 状态
   358|   358|            conn.execute("UPDATE sell_intents SET status='completed' WHERE id=?", (matching_intent['id'],))
   359|   359|            conn.commit()
   360|   360|
   361|   361|            if withdraw_txid:
   362|   362|                print(f"[完成] 订单 #{oid}: USDC→卖家, {btcc_amount} BTCC→{buyer_btcc_addr[:20]}...", flush=True)
   363|   363|            else:
   364|   364|                print(f"[警告] 订单 #{oid}: USDC已释放但BTCC转账失败，等待重试", flush=True)
   365|   365|
   366|   366|
   367|   367|def main():
   368|   368|    print("=" * 50, flush=True)
   369|   369|    print("BTCC OTC 自动确认脚本 v5 (卖家先锁BTCC)", flush=True)
   370|   370|    print(f"合约: {CONTRACT_ADDRESS}", flush=True)
   371|   371|    print(f"检查间隔: {CHECK_INTERVAL}秒", flush=True)
   372|   372|    print(f"确认数要求: {CONFIRMATIONS_REQUIRED}", flush=True)
   373|   373|    print("=" * 50, flush=True)
   374|   374|
   375|   375|    while True:
   376|   376|        try:
   377|   377|            conn = get_db()
   378|   378|            
   379|   379|            # === 处理挂单意向（等 BTCC 到账后上链）===
   380|   380|            process_sell_intents(conn)
   381|   381|            
   382|   382|            # === 处理已接单的订单（BTCC 已在托管，直接 confirmRelease）===
   383|   383|            process_accepted_orders(conn)
   384|   384|            
   385|   385|            # === 处理合约上被取消的挂单（标记 cancelled）===
   386|   386|            process_cancelled_intents(conn)
   387|   387|            
   388|   388|            # === 处理 transfer_pending 订单（USDC已释放但BTCC转买家失败，重试）===
   389|   389|            pending_transfers = conn.execute("SELECT * FROM order_records WHERE status='transfer_pending'").fetchall()
   390|   390|            for row in pending_transfers:
   391|   391|                print(f"[重试] 订单 #{row['order_id']}: 重试转 BTCC 给买家", flush=True)
   392|   392|                withdraw_txid = transfer_btcc(row['buyer_btcc_address'], row['btcc_amount'])
   393|   393|                if withdraw_txid:
   394|   394|                    conn.execute("""
   395|   395|                        UPDATE order_records SET status='confirmed', withdraw_txid=? WHERE order_id=?
   396|   396|                    """, (withdraw_txid, row['order_id']))
   397|   397|                    conn.commit()
   398|   398|                    print(f"[完成] 订单 #{row['order_id']}: 重试成功，BTCC已转给买家", flush=True)
   399|   399|
   400|   400|            # === 处理被取消的订单（退回卖家 BTCC）===
   401|   401|            # 注意：新流程中 BTCC 在挂单时已锁定，取消由 process_cancelled_intents 处理
   402|   402|            # 旧的 pending order_records 逻辑保留但跳过缺失函数
   403|   403|            pending_orders = conn.execute("SELECT * FROM order_records WHERE status='pending'").fetchall()
   404|   404|            for row in pending_orders:
   405|   405|                contract_status = get_order_contract_status(row['order_id'])
   406|   406|                if contract_status == 3:  # 已取消
   407|   407|                    conn.execute("UPDATE order_records SET status='cancelled' WHERE order_id=?", (row['order_id'],))
   408|   408|                    conn.commit()
   409|   409|                    print(f"[取消] 订单 #{row['order_id']} 已取消", flush=True)
   410|   410|                    continue
   411|   411|
   412|   412|            # === 30分钟超时：卖家接单后未转账，平台自动取消 ===
   413|   413|            now = int(time.time())
   414|   414|            for row in pending_orders:
   415|   415|                if row['status'] != 'pending':
   416|   416|                    continue
   417|   417|                contract_status = get_order_contract_status(row['order_id'])
   418|   418|                if contract_status != 1:  # 只处理已接单的
   419|   419|                    continue
   420|   420|                elapsed = now - row['created_at']
   421|   421|                if elapsed > 1800:  # 30分钟 = 1800秒
   422|   422|                    # 检查是否有到账（用已有的 get_receive_txs_for_address）
   423|   423|                    receives = get_receive_txs_for_address(row['deposit_address'])
   424|   424|                    total_received = sum(float(tx['amount']) for tx in receives)
   425|   425|                    if total_received >= row['btcc_amount'] - 0.0001:
   426|   426|                        continue  # 已到账，不取消，等正常确认流程
   427|   427|                    # 超时未转账，平台调用 cancelAcceptance
   428|   428|                    print(f"[超时] 订单 #{row['order_id']}: 接单后30分钟未转账，自动取消接单", flush=True)
   429|   429|                    success, output = cast_send("cancelAcceptance(uint256)", [str(row['order_id'])])
   430|   430|                    if success:
   431|   431|                        conn.execute("UPDATE order_records SET status='timeout' WHERE order_id=?", (row['order_id'],))
   432|   432|                        conn.commit()
   433|   433|                        print(f"[取消] 订单 #{row['order_id']} 超时自动取消成功", flush=True)
   434|   434|                    else:
   435|   435|                        print(f"[失败] 订单 #{row['order_id']} 自动取消失败: {output[:100]}", flush=True)
   436|   436|
   437|   437|            # === 正常扫描 pending 订单 ===
   438|   438|            rows = conn.execute("SELECT * FROM order_records WHERE status='pending'").fetchall()
   439|   439|
   440|   440|            if not rows:
   441|   441|                conn.close()
   442|   442|                time.sleep(CHECK_INTERVAL)
   443|   443|                continue
   444|   444|
   445|   445|            print(f"[扫描] {len(rows)} 个待确认订单", flush=True)
   446|   446|
   447|   447|            # 按托管地址分组，减少 RPC 调用
   448|   448|            addr_orders = {}
   449|   449|            for row in rows:
   450|   450|                addr = row['deposit_address']
   451|   451|                if addr not in addr_orders:
   452|   452|                    addr_orders[addr] = []
   453|   453|                addr_orders[addr].append(row)
   454|   454|
   455|   455|            for deposit_addr, orders in addr_orders.items():
   456|   456|                # 获取该地址的 receive 交易
   457|   457|                receives = get_receive_txs_for_address(deposit_addr)
   458|   458|                if not receives:
   459|   459|                    for order in orders:
   460|   460|                        print(f"[等待] 订单 #{order['order_id']}: {deposit_addr[:20]}... 无到账TX", flush=True)
   461|   461|                    continue
   462|   462|
   463|   463|                # 为每个订单匹配 TX
   464|   464|                for order in orders:
   465|   465|                    matched_tx = find_matching_tx(conn, order, receives)
   466|   466|                    if not matched_tx:
   467|   467|                        print(f"[等待] 订单 #{order['order_id']}: 需要 {order['btcc_amount']} BTCC, 未匹配到TX", flush=True)
   468|   468|                        continue
   469|   469|
   470|   470|                    txid = matched_tx['txid']
   471|   471|                    amount = float(matched_tx['amount'])
   472|   472|                    print(f"[匹配] 订单 #{order['order_id']}: TX {txid[:20]}... 金额={amount} >= {order['btcc_amount']}", flush=True)
   473|   473|
   474|   474|                    # 1. 确认释放 USDC
   475|   475|                    if not confirm_release(order['order_id'], txid):
   476|   476|                        continue
   477|   477|
   478|   478|                    # 2. 标记 txid 已使用
   479|   479|                    mark_txid_used(conn, txid, order['order_id'], amount)
   480|   480|
   481|   481|                    # 3. 转 BTCC 给买家
   482|   482|                    withdraw_txid = None
   483|   483|                    if order['buyer_btcc_address']:
   484|   484|                        withdraw_txid = transfer_btcc(order['buyer_btcc_address'], order['btcc_amount'])
   485|   485|                        if not withdraw_txid:
   486|   486|                            # 转账失败，不标记完成，下次重试时跳过confirmRelease（合约已完成）
   487|   487|                            # 但txid已标记使用，所以记录状态为 'transfer_pending'
   488|   488|                            conn.execute("""
   489|   489|                                UPDATE order_records 
   490|   490|                                SET status='transfer_pending', matched_txid=?, confirmed_at=?
   491|   491|                                WHERE order_id=?
   492|   492|                            """, (txid, int(time.time()), order['order_id']))
   493|   493|                            conn.commit()
   494|   494|                            print(f"[警告] 订单 #{order['order_id']}: USDC已释放但BTCC转账失败，等待重试", flush=True)
   495|   495|                            continue
   496|   496|
   497|   497|                    # 4. 退回多余金额给卖家
   498|   498|                    excess = amount - order['btcc_amount']
   499|   499|                    if excess > 0.0001 and order['seller_btcc_address']:
   500|   500|                        refund_txid = transfer_btcc(order['seller_btcc_address'], round(excess, 8))
   501|