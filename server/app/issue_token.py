# -*- coding: utf-8 -*-
"""签发 JWT：AM_TOKEN_SECRET=*** python -m app.issue_token --ttl 30d"""
import argparse, os, sys
from app.auth import sign_token
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ttl", default=os.environ.get("AM_TOKEN_TTL", "30d"))
    a = ap.parse_args()
    if not os.environ.get("AM_TOKEN_SECRET"):
        sys.exit("AM_TOKEN_SECRET 未设置（static 模式无需此脚本）")
    print(sign_token(a.ttl))
