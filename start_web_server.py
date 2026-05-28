#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动 HJ-212 Web 服务器（独立模式）
用于单独运行前端展示服务，接收从 server.py 发送的数据

使用方式：
    python start_web_server.py

然后访问：http://localhost:5000
"""

import sys
from web_server import start_web_server

if __name__ == "__main__":
    print("=" * 60)
    print("🌊 HJ-212 Web 服务器")
    print("=" * 60)
    print("\n启动参数:")
    print("  地址: 0.0.0.0")
    print("  端口: 5000")
    print("\n访问地址:")
    print("  http://localhost:5000")
    print("  http://127.0.0.1:5000")
    print("\n按 Ctrl+C 停止服务器")
    print("=" * 60 + "\n")
    
    try:
        start_web_server(host="0.0.0.0", port=5000, debug=False)
    except KeyboardInterrupt:
        print("\n✋ 服务器已停止")
        sys.exit(0)
