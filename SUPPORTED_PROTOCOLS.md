# Supported Protocols & DeFi Integrations

> 本文件记录当前系统已支持的 DeFi 协议及其版本、合约地址、支持状态。
> 目标是持续扩展支持的协议数量，量变达到质变。

---

## Supported Protocols Overview

| # | Protocol | Version | Architecture | Status | Notes |
|---|----------|---------|-------------|--------|-------|
| 1 | Uniswap | V2 | Direct Pair | ✅ 完整支持 | Factory: `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f` |
| 2 | Uniswap | V3 | Concentrated Pool | ✅ 完整支持 | Factory: `0x1F98431c8aD98523631AE4a59f267346ea31F984` |
| 3 | Uniswap | V4 | Singleton | ✅ 已支持 | PoolManager: `0x0000000000000000000000000000000000000044` |
| 4 | Uniswap | V1 | ETH-ERC20 Swap | 🔧 V1 excluded | 已废弃，无流动性，暂不实现 |

## Uniswap V2

- **状态**: ✅ 完整支持
- **Factory**: `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f`
- **Router**: `0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D`
- **部署区块**: 10000835
- **池子识别**: `getPair()` 快速发现 + `PairCreated` 事件穷举发现
- **事件索引**: Swap, Mint, Burn

## Uniswap V3

- **状态**: ✅ 完整支持
- **Factory**: `0x1F98431c8aD98523631AE4a59f267346ea31F984`
- **Router**: `0xE592427A0AEce92De3Edee1F18E0157C05861564`
- **Position Manager**: `0xC36442b4a4522E871399CD717aBDD847Ab11FE88`
- **部署区块**: 12369621
- **池子识别**: `getPool()` 快速发现 + `PoolCreated` 事件穷举发现
- **事件索引**: Swap, Mint, Burn, Collect
- **支持费率**: 100, 500, 3000, 10000

## Uniswap V4

- **状态**: ✅ 已支持 (基础发现)
- **PoolManager**: `0x0000000000000000000000000000000000000044` (CREATE2 地址)
- **架构**: Singleton 模式，所有池子由 PoolManager 统一管理
- **池子识别**: `getId(PoolKey)` 快速发现
- **池子 Key**: `(currency0, currency1, fee, tickSpacing, hooks)`
- **V4 特点**:
  - 池子由 PoolKey 标识，而非独立合约地址
  - Hooks 机制允许自定义逻辑
  - 支持动态费率
- **待完善**: Swap/ModifyLiquidity 事件索引

## 未来计划 (TODO)

| 协议 | 版本 | 优先度 | 说明 |
|------|------|--------|------|
| Sushiswap | V2/V3 | ⭐⭐⭐ | 与 Uniswap 兼容的 Factory |
| Curve | StableSwap | ⭐⭐⭐ | 稳定币兑换协议 |
| PancakeSwap | V2/V3 | ⭐⭐ | BSC 链上的 Uniswap 分叉 |
| Balancer | V2 | ⭐⭐ | 多代币池 |
| Trader Joe | V2.1 | ⭐ | Avalanche 链 |

## 如何添加新协议

1. 在 `config/protocols.ethereum.yaml` 中添加协议配置
2. 在 `src/discovery/` 下创建适配器 (继承 `PoolDiscoveryAdapter`)
3. 在 `src/discovery/engine.py` 的 `_ADAPTER_MAP` 中注册
4. 在 `abis/` 下添加必要的 ABI 文件
5. 在本文件 `SUPPORTED_PROTOCOLS.md` 中更新记录
