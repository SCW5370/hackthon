#!/usr/bin/env python3
"""
SafeExec V1 密钥生成脚本
生成 Ed25519 密钥对
"""
import argparse
import base64
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.lease_authority import LeaseAuthority


def main():
    parser = argparse.ArgumentParser(description="Generate Ed25519 keypair for SafeExec")
    parser.add_argument("--private-key", help="Path to write private key (or stdout if not specified)")
    parser.add_argument("--public-key", help="Path to write public key (or stdout if not specified)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")

    args = parser.parse_args()

    # 生成密钥对
    private_b64, public_b64 = LeaseAuthority.generate_keypair()

    print("=" * 60)
    print("SafeExec V1 Ed25519 Keypair Generated")
    print("=" * 60)
    print()

    # 写入私钥
    if args.private_key:
        if os.path.exists(args.private_key) and not args.overwrite:
            print(f"ERROR: {args.private_key} already exists. Use --overwrite to replace.")
            sys.exit(1)
        with open(args.private_key, "w") as f:
            f.write(private_b64 + "\n")
        os.chmod(args.private_key, 0o600)  # 仅所有者可读写
        print(f"Private key written to: {args.private_key}")
        print(f"  (mode: 0600, owner only)")
    else:
        print("Private Key (BASE64):")
        print(f"  {private_b64}")

    print()

    # 写入公钥
    if args.public_key:
        if os.path.exists(args.public_key) and not args.overwrite:
            print(f"ERROR: {args.public_key} already exists. Use --overwrite to replace.")
            sys.exit(1)
        with open(args.public_key, "w") as f:
            f.write(public_b64 + "\n")
        print(f"Public key written to: {args.public_key}")
    else:
        print("Public Key (BASE64):")
        print(f"  {public_b64}")

    print()
    print("=" * 60)
    print("IMPORTANT:")
    print("  - Private key: Keep secret, deploy ONLY on Runtime (X5)")
    print("  - Public key: Share with Guard (Windows)")
    print("  - NEVER commit private key to source control!")
    print("=" * 60)


if __name__ == "__main__":
    main()
