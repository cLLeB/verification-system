"""Shamir secret sharing: reconstruction from any k, threshold behaviour."""

from __future__ import annotations

import pytest

from face_service import secretsharing as ss


def test_split_and_combine_exact_k():
    secret = b"master-key-0123456789"
    shares = ss.split(secret, n=5, k=3)
    assert len(shares) == 5
    # any 3 reconstruct
    assert ss.combine(shares[:3]) == secret
    assert ss.combine([shares[0], shares[2], shares[4]]) == secret


def test_more_than_k_also_works():
    secret = b"\x00\x01\x02\xff\xfe"
    shares = ss.split(secret, n=6, k=4)
    assert ss.combine(shares) == secret          # all 6
    assert ss.combine(shares[:5]) == secret       # 5 of 6


def test_fewer_than_k_does_not_reconstruct():
    secret = b"topsecret"
    shares = ss.split(secret, n=5, k=3)
    # 2 shares (< k) should not yield the secret
    assert ss.combine(shares[:2]) != secret


def test_different_share_subsets_agree():
    secret = b"abcdefgh"
    shares = ss.split(secret, n=7, k=3)
    a = ss.combine([shares[0], shares[1], shares[2]])
    b = ss.combine([shares[3], shares[4], shares[6]])
    assert a == b == secret


def test_binary_secret_all_byte_values():
    secret = bytes(range(256))
    shares = ss.split(secret, n=4, k=2)
    assert ss.combine(shares[:2]) == secret


def test_verify_helper():
    secret = b"key"
    shares = ss.split(secret, n=3, k=2)
    assert ss.verify(shares[:2], secret)
    assert not ss.verify(shares[:2], b"wrong")


def test_duplicate_index_rejected():
    shares = ss.split(b"xy", n=3, k=2)
    with pytest.raises(ValueError):
        ss.combine([shares[0], shares[0]])


def test_validation():
    with pytest.raises(ValueError):
        ss.split(b"", 3, 2)
    with pytest.raises(ValueError):
        ss.split(b"x", n=2, k=3)      # k > n
    with pytest.raises(ValueError):
        ss.split(b"x", n=1, k=1)      # k < 2
    with pytest.raises(ValueError):
        ss.combine([{"index": 1, "data": "00"}])   # need >= 2
