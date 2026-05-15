// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title BTCC OTC Escrow
/// @notice Cross-chain OTC trading: BTCC (off-chain) <-> USDC/USDT (Polygon)
/// @dev Platform authority confirms BTCC receipt, then releases stablecoin to seller
contract BtccOtcEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public immutable platformAuthority;
    address public immutable feeRecipient;
    uint256 public constant FEE_BPS = 250; // 2.5%
    uint256 public constant ORDER_TIMEOUT = 1 hours;

    enum OrderStatus { Open, Accepted, Completed, Cancelled }

    struct Order {
        uint256 orderId;
        address buyer;
        address seller;
        address token;          // USDC/USDT address
        uint256 tokenAmount;    // stablecoin amount
        uint256 btccAmount;     // BTCC amount expected
        string btccAddress;     // buyer's BTCC receiving address
        string btccTxid;        // BTCC transaction ID (filled on confirm)
        OrderStatus status;
        uint256 createdAt;
    }

    mapping(uint256 => Order) public orders;
    uint256 public nextOrderId;

    event OrderCreated(uint256 indexed orderId, address indexed buyer, address token, uint256 tokenAmount, uint256 btccAmount, string btccAddress);
    event OrderAccepted(uint256 indexed orderId, address indexed seller);
    event OrderCompleted(uint256 indexed orderId, uint256 sellerAmount, uint256 fee, string btccTxid);
    event OrderCancelled(uint256 indexed orderId);

    modifier onlyPlatform() {
        require(msg.sender == platformAuthority, "Not platform authority");
        _;
    }

    constructor(address _platformAuthority, address _feeRecipient) {
        require(_platformAuthority != address(0), "Invalid authority");
        require(_feeRecipient != address(0), "Invalid fee recipient");
        platformAuthority = _platformAuthority;
        feeRecipient = _feeRecipient;
    }

    /// @notice Buyer creates a sell order (selling stablecoin for BTCC)
    /// @param token USDC/USDT contract address
    /// @param tokenAmount Amount of stablecoin to escrow
    /// @param btccAmount Amount of BTCC expected in return
    /// @param btccAddress Buyer's BTCC wallet address
    function createOrder(
        address token,
        uint256 tokenAmount,
        uint256 btccAmount,
        string calldata btccAddress
    ) external nonReentrant returns (uint256) {
        require(tokenAmount > 0, "Invalid token amount");
        require(btccAmount > 0, "Invalid BTCC amount");
        require(bytes(btccAddress).length > 0 && bytes(btccAddress).length <= 64, "Invalid BTCC address");

        uint256 orderId = nextOrderId++;

        orders[orderId] = Order({
            orderId: orderId,
            buyer: msg.sender,
            seller: address(0),
            token: token,
            tokenAmount: tokenAmount,
            btccAmount: btccAmount,
            btccAddress: btccAddress,
            btccTxid: "",
            status: OrderStatus.Open,
            createdAt: block.timestamp
        });

        // Transfer stablecoin from buyer to this contract
        IERC20(token).safeTransferFrom(msg.sender, address(this), tokenAmount);

        emit OrderCreated(orderId, msg.sender, token, tokenAmount, btccAmount, btccAddress);
        return orderId;
    }

    /// @notice Seller accepts an open order (commits to sending BTCC)
    function acceptOrder(uint256 orderId) external {
        Order storage order = orders[orderId];
        require(order.status == OrderStatus.Open, "Order not open");
        require(msg.sender != order.buyer, "Buyer cannot accept own order");

        order.seller = msg.sender;
        order.status = OrderStatus.Accepted;

        emit OrderAccepted(orderId, msg.sender);
    }

    /// @notice Platform confirms BTCC received, releases stablecoin to seller
    function confirmRelease(uint256 orderId, string calldata btccTxid) external onlyPlatform nonReentrant {
        Order storage order = orders[orderId];
        require(order.status == OrderStatus.Accepted, "Order not accepted");

        uint256 fee = (order.tokenAmount * FEE_BPS) / 10000;
        uint256 sellerAmount = order.tokenAmount - fee;

        order.status = OrderStatus.Completed;
        order.btccTxid = btccTxid;

        // Transfer to seller
        IERC20(order.token).safeTransfer(order.seller, sellerAmount);
        // Transfer fee to platform
        if (fee > 0) {
            IERC20(order.token).safeTransfer(feeRecipient, fee);
        }

        emit OrderCompleted(orderId, sellerAmount, fee, btccTxid);
    }

    /// @notice Buyer cancels order (if Open, or if Accepted but timed out)
    function cancelOrder(uint256 orderId) external nonReentrant {
        Order storage order = orders[orderId];
        require(msg.sender == order.buyer, "Not buyer");

        if (order.status == OrderStatus.Open) {
            // Can always cancel open orders
        } else if (order.status == OrderStatus.Accepted) {
            require(block.timestamp > order.createdAt + ORDER_TIMEOUT, "Not timed out yet");
        } else {
            revert("Cannot cancel");
        }

        order.status = OrderStatus.Cancelled;

        // Refund stablecoin to buyer
        IERC20(order.token).safeTransfer(order.buyer, order.tokenAmount);

        emit OrderCancelled(orderId);
    }

    /// @notice Get order details
    function getOrder(uint256 orderId) external view returns (Order memory) {
        return orders[orderId];
    }

    /// @notice Get all open orders (for frontend listing)
    function getOpenOrders(uint256 offset, uint256 limit) external view returns (Order[] memory) {
        uint256 count = 0;
        uint256 total = nextOrderId;

        // First pass: count open orders
        for (uint256 i = offset; i < total && count < limit; i++) {
            if (orders[i].status == OrderStatus.Open) {
                count++;
            }
        }

        // Second pass: collect
        Order[] memory result = new Order[](count);
        uint256 idx = 0;
        for (uint256 i = offset; i < total && idx < count; i++) {
            if (orders[i].status == OrderStatus.Open) {
                result[idx++] = orders[i];
            }
        }

        return result;
    }
}
