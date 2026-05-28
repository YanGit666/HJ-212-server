# config.py

# HJ-212 服务器配置
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 7000

# Web 服务器配置
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

# MN -> 备注（MN为27字符EPC-96编码，由环保局分配，这里用模拟值）
ALLOWED_DEVICES = {
    "30000032000000101E19D6F1": "水质监测站01",
    "011000000000032010000123401": "水质监测站01",
    "011000000000032010000123402": "水质监测站02",
}

PASSWORD     = "123456"
HJ212_ST     = "22"       # 22=地表水质自动监测

HEARTBEAT_TIMEOUT = 120   # 秒

# 云服务器访问说明：
# 1. 启动 server.py 后，Web 服务会自动在 WEB_PORT 端口启动
# 2. 确保云服务器的安全组/防火墙已开放 WEB_PORT 端口
# 3. 浏览器访问：http://<云服务器公网IP>:WEB_PORT