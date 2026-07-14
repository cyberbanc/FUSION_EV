from __future__ import annotations

from typing import Any

PREDICTION_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "currentEpoch",
        "outputs": [{"type": "uint256", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "oracle",
        "outputs": [{"type": "address", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"type": "uint256", "name": ""}],
        "name": "rounds",
        "outputs": [
            {"type": "uint256", "name": "epoch"},
            {"type": "uint256", "name": "startTimestamp"},
            {"type": "uint256", "name": "lockTimestamp"},
            {"type": "uint256", "name": "closeTimestamp"},
            {"type": "int256", "name": "lockPrice"},
            {"type": "int256", "name": "closePrice"},
            {"type": "uint256", "name": "lockOracleId"},
            {"type": "uint256", "name": "closeOracleId"},
            {"type": "uint256", "name": "totalAmount"},
            {"type": "uint256", "name": "bullAmount"},
            {"type": "uint256", "name": "bearAmount"},
            {"type": "uint256", "name": "rewardBaseCalAmount"},
            {"type": "uint256", "name": "rewardAmount"},
            {"type": "bool", "name": "oracleCalled"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

AGGREGATOR_V3_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"type": "uint80", "name": "roundId"},
            {"type": "int256", "name": "answer"},
            {"type": "uint256", "name": "startedAt"},
            {"type": "uint256", "name": "updatedAt"},
            {"type": "uint80", "name": "answeredInRound"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
