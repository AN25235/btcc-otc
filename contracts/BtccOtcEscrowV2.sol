// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title BtccOtcEscrow V2.2
 * @notice BTCC OTC 交易平台 — 卖家挂单模式（修复版）
 * 
 * 修复内容：
 * - cancelAcceptance event 参数修正（先 emit 再清零）
 * - createSellOrderFor 校验 seller != address(0)
 * - token 白名单机制（只允许 authority 添加的代币）
 */
contract BtccOtcEscrowV2 is ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum OrderStatus { Open, Accepted, Completed, Cancelled }

    struct Order {
        uint256 orderId;
        address seller;
        address buyer;
        address token;          // USDC or USDT
        uint256 btccAmount;     // BTCC 数量 (8 decimals)
        uint256 unitPrice;      // 单价: 每 BTCC 多少 token (6 decimals)
        uint256 tokenAmount;    // 总价 = btccAmount * unitPrice / 1e8
        string btccAddress;     // 买家 BTCC 收款地址
        string btccTxid;        // BTCC 转账 txid
        OrderStatus status;
        uint256 createdAt;
        uint256 acceptedAt;
    }

    address public platformAuthority;
    address public feeRecipient;
    uint256 public feeRate = 250; // 2.5% = 250/10000
    uint256 public constant ACCEPT_TIMEOUT = 30 minutes;

    mapping(uint256 => Order) public orders;
    uint256 public nextOrderId;

    // Token 白名单
    mapping(address => bool) public allowedTokens;

    event OrderCreated(uint256 indexed orderId, address indexed seller, address token, uint256 btccAmount, uint256 unitPrice, uint256 tokenAmount);
    event OrderAccepted(uint256 indexed orderId, address indexed buyer, string btccAddress);
    event OrderCompleted(uint256 indexed orderId, uint256 sellerAmount, uint256 fee, string btccTxid);
    event OrderCancelled(uint256 indexed orderId);
    event AcceptanceCancelled(uint256 indexed orderId, address indexed buyer);
    event TokenAllowed(address indexed token, bool allowed);

    constructor(address _authority, address _feeRecipient) {
        platformAuthority = _authority;
        feeRecipient = _feeRecipient;
    }

    modifier onlyAuthority() {
        require(msg.sender == platformAuthority, "Not authority");
        _;
    }

    // === Token 白名单管理 ===

    function setTokenAllowed(address token, bool allowed) external onlyAuthority {
        allowedTokens[token] = allowed;
        emit TokenAllowed(token, allowed);
    }

    /**
     * @notice 卖家挂单
     * @param token 接受的代币地址 (USDC/USDT)
     * @param btccAmount BTCC 数量 (8 decimals)
     * @param unitPrice 单价：每 BTCC 多少 token (6 decimals)
     */
    function createSellOrder(
        address token,
        uint256 btccAmount,
        uint256 unitPrice
    ) external returns (uint256) {
        return _createOrder(msg.sender, token, btccAmount, unitPrice);
    }

    /**
     * @notice Authority 代卖家挂单（BTCC 已托管到平台后调用）
     */
    function createSellOrderFor(
        address seller,
        address token,
        uint256 btccAmount,
        uint256 unitPrice
    ) external onlyAuthority returns (uint256) {
        require(seller != address(0), "Invalid seller");
        return _createOrder(seller, token, btccAmount, unitPrice);
    }

    function _createOrder(
        address seller,
        address token,
        uint256 btccAmount,
        uint256 unitPrice
    ) internal returns (uint256) {
        require(seller != address(0), "Invalid seller");
        require(allowedTokens[token], "Token not allowed");
        require(btccAmount > 0, "Invalid btcc amount");
        require(unitPrice > 0, "Invalid unit price");

        uint256 tokenAmount = btccAmount * unitPrice / 1e8;
        require(tokenAmount > 0, "Token amount too small");

        uint256 orderId = nextOrderId++;
        orders[orderId] = Order({
            orderId: orderId,
            seller: seller,
            buyer: address(0),
            token: token,
            btccAmount: btccAmount,
            unitPrice: unitPrice,
            tokenAmount: tokenAmount,
            btccAddress: "",
            btccTxid: "",
            status: OrderStatus.Open,
            createdAt: block.timestamp,
            acceptedAt: 0
        });

        emit OrderCreated(orderId, seller, token, btccAmount, unitPrice, tokenAmount);
        return orderId;
    }

    /**
     * @notice 买家接单 — 锁 USDC/USDT 到合约
     * @param orderId 订单ID
     * @param btccAddress 买家的 BTCC 收款地址
     */
    function acceptOrder(uint256 orderId, string calldata btccAddress) external nonReentrant {
        Order storage order = orders[orderId];
        require(order.status == OrderStatus.Open, "Not open");
        require(msg.sender != order.seller, "Cannot accept own order");
        require(bytes(btccAddress).length > 0, "Invalid btcc address");

        order.buyer = msg.sender;
        order.btccAddress = btccAddress;
        order.status = OrderStatus.Accepted;
        order.acceptedAt = block.timestamp;

        // 买家锁 token 到合约
        IERC20(order.token).safeTransferFrom(msg.sender, address(this), order.tokenAmount);

        emit OrderAccepted(orderId, msg.sender, btccAddress);
    }

    /**
     * @notice 平台确认 BTCC 到账，释放 USDC 给卖家
     * @param orderId 订单ID
     * @param btccTxid BTCC 链上 txid
     */
    function confirmRelease(uint256 orderId, string calldata btccTxid) external onlyAuthority nonReentrant {
        Order storage order = orders[orderId];
        require(order.status == OrderStatus.Accepted, "Not accepted");

        order.status = OrderStatus.Completed;
        order.btccTxid = btccTxid;

        // 计算手续费
        uint256 fee = order.tokenAmount * feeRate / 10000;
        uint256 sellerAmount = order.tokenAmount - fee;

        // 转给卖家和手续费接收者
        IERC20(order.token).safeTransfer(order.seller, sellerAmount);
        if (fee > 0) {
            IERC20(order.token).safeTransfer(feeRecipient, fee);
        }

        emit OrderCompleted(orderId, sellerAmount, fee, btccTxid);
    }

    /**
     * @notice 卖家取消挂单（仅 Open 状态），或 authority 代取消
     */
    function cancelOrder(uint256 orderId) external {
        Order storage order = orders[orderId];
        require(msg.sender == order.seller || msg.sender == platformAuthority, "Not seller or authority");
        require(order.status == OrderStatus.Open, "Not open");

        order.status = OrderStatus.Cancelled;
        emit OrderCancelled(orderId);
    }

    /**
     * @notice Authority 取消接单（超时30分钟未转 BTCC）
     * USDC 退回买家，订单回到 Open 状态
     */
    function cancelAcceptance(uint256 orderId) external onlyAuthority nonReentrant {
        Order storage order = orders[orderId];
        require(order.status == OrderStatus.Accepted, "Not accepted");
        require(block.timestamp > order.acceptedAt + ACCEPT_TIMEOUT, "Not timed out");

        // 先记录买家地址再清零
        address buyer = order.buyer;

        // 退 USDC 给买家
        IERC20(order.token).safeTransfer(buyer, order.tokenAmount);

        // 订单回到 Open
        order.status = OrderStatus.Open;
        order.buyer = address(0);
        order.btccAddress = "";
        order.acceptedAt = 0;

        emit AcceptanceCancelled(orderId, buyer);
    }

    /**
     * @notice 买家主动取消接单（超时后）
     */
    function buyerCancelAcceptance(uint256 orderId) external nonReentrant {
        Order storage order = orders[orderId];
        require(msg.sender == order.buyer, "Not buyer");
        require(order.status == OrderStatus.Accepted, "Not accepted");
        require(block.timestamp > order.acceptedAt + ACCEPT_TIMEOUT, "Not timed out");

        // 退 USDC 给买家
        IERC20(order.token).safeTransfer(order.buyer, order.tokenAmount);

        // 订单回到 Open
        order.status = OrderStatus.Open;
        order.buyer = address(0);
        order.btccAddress = "";
        order.acceptedAt = 0;

        emit AcceptanceCancelled(orderId, msg.sender);
    }

    // === 查询函数 ===

    function getOrder(uint256 orderId) external view returns (Order memory) {
        return orders[orderId];
    }

    function getOpenOrders(uint256 offset, uint256 limit) external view returns (Order[] memory) {
        uint256 count = 0;
        for (uint256 i = offset; i < nextOrderId && count < limit; i++) {
            if (orders[i].status == OrderStatus.Open) count++;
        }
        Order[] memory result = new Order[](count);
        uint256 idx = 0;
        for (uint256 i = offset; i < nextOrderId && idx < count; i++) {
            if (orders[i].status == OrderStatus.Open) {
                result[idx++] = orders[i];
            }
        }
        return result;
    }

    // === 管理函数 ===

    function setFeeRate(uint256 _feeRate) external onlyAuthority {
        require(_feeRate <= 1000, "Fee too high"); // max 10%
        feeRate = _feeRate;
    }

    function setFeeRecipient(address _feeRecipient) external onlyAuthority {
        require(_feeRecipient != address(0), "Invalid address");
        feeRecipient = _feeRecipient;
    }

    function setAuthority(address _authority) external onlyAuthority {
        require(_authority != address(0), "Invalid address");
        platformAuthority = _authority;
    }
}
