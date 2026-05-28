# HJ-212 Web 数据展示平台 - 云服务器部署指南

## 概述

本 Web 平台用于实时展示 HJ-212 协议接收的水质监测数据，支持实时数据、分钟数据、小时数据等多种数据类型的可视化展示。

## 云服务器访问方式

### 1. 基本访问原理

```
┌─────────────────┐      Internet       ┌─────────────────┐
│   您的浏览器     │  ═══════════════►  │   云服务器      │
│  (手机/电脑)    │  ◄═══════════════  │  0.0.0.0:5000   │
└─────────────────┘                     └─────────────────┘
                                              │
                                              ▼
                                        HJ-212 设备连接
                                        (端口 7000)
```

### 2. 安全组/防火墙配置

在云服务器控制台，需要开放以下端口：

| 端口 | 用途 | 访问来源 |
|------|------|----------|
| 5000 | Web 页面访问 | 0.0.0.0/0 (任意IP) 或您的IP |
| 7000 | HJ-212 设备接入 | 设备所在网络IP |

**常见云平台配置方式：**

- **阿里云 ECS**：安全组规则 → 入方向 → 添加规则
- **腾讯云 CVM**：安全组 → 入站规则 → 添加规则  
- **AWS EC2**：Security Groups → Inbound rules → Add rule
- **Azure VM**：Network security groups → Inbound security rules

### 3. 启动服务

在云服务器上执行：

```bash
cd /path/to/server

# 方式1：同时启动 HJ-212 服务器和 Web 服务
python server.py

# 方式2：只启动 Web 服务（用于测试）
python start_web_server.py
```

### 4. 访问页面

浏览器访问以下地址之一：

```
http://<云服务器公网IP>:5000
http://<云服务器域名>:5000
```

例如：
```
http://123.45.67.89:5000
http://your-domain.com:5000
```

## 常见问题

### Q1: 浏览器无法访问页面

**排查步骤：**

1. **检查服务是否启动**
   ```bash
   # 查看 5000 端口是否在监听
   netstat -tlnp | grep 5000
   # 或
   ss -tlnp | grep 5000
   ```

2. **检查防火墙设置**
   ```bash
   # Linux (ufw)
   sudo ufw allow 5000/tcp
   
   # Linux (firewalld)
   sudo firewall-cmd --permanent --add-port=5000/tcp
   sudo firewall-cmd --reload
   
   # Linux (iptables)
   sudo iptables -A INPUT -p tcp --dport 5000 -j ACCEPT
   ```

3. **检查云服务器安全组**
   登录云平台控制台，确保安全组已开放 5000 端口

4. **测试本地访问**
   ```bash
   curl http://localhost:5000
   ```

### Q2: 如何修改 Web 服务端口

编辑 [config.py](config.py) 添加配置：

```python
# config.py
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000  # 修改为您需要的端口
```

或修改 [server.py](server.py) 中的启动参数：

```python
# 在 init_web_server() 中修改
threading.Thread(
    target=start_web_server,
    args=("0.0.0.0", 8080, False),  # 改为 8080 端口
    daemon=True
).start()
```

### Q3: 如何配置域名访问（推荐）

使用 Nginx 反向代理，将 80/443 端口代理到 5000 端口：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_cache_bypass $http_upgrade;
    }
}
```

配置后访问：
```
http://your-domain.com  (无需端口号)
```

### Q4: 如何配置 HTTPS（SSL）

使用 Nginx + Let's Encrypt：

```bash
# 安装 certbot
sudo apt install certbot python3-certbot-nginx

# 申请证书
sudo certbot --nginx -d your-domain.com

# 自动配置 HTTPS
```

或使用 Cloudflare 免费 SSL 代理。

## 生产环境建议

### 1. 使用 Gunicorn 运行（更稳定）

```bash
# 安装 gunicorn
pip install gunicorn

# 启动（支持多worker）
gunicorn -w 4 -b 0.0.0.0:5000 "web_server:app"
```

### 2. 使用 Supervisor 管理进程

创建配置文件 `/etc/supervisor/conf.d/hj212-web.conf`：

```ini
[program:hj212-web]
command=/usr/bin/python3 /path/to/server/web_server.py
directory=/path/to/server
autostart=true
autorestart=true
stderr_logfile=/var/log/hj212-web.err.log
stdout_logfile=/var/log/hj212-web.out.log
```

### 3. 系统服务方式（systemd）

创建文件 `/etc/systemd/system/hj212-web.service`：

```ini
[Unit]
Description=HJ-212 Web Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/server
ExecStart=/usr/bin/python3 /path/to/server/web_server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable hj212-web
sudo systemctl start hj212-web
```

## 快速检查清单

在浏览器访问前，确认：

- [ ] 云服务器已安装依赖：`pip install flask flask-socketio flask-cors`
- [ ] HJ-212 服务器或 Web 服务器已启动
- [ ] 云服务器安全组已开放 5000 端口
- [ ] 云服务器防火墙已开放 5000 端口
- [ ] 使用正确的公网 IP 或域名访问

## 联系支持

如有问题，请检查：
1. 服务器日志：`hj212_server.log`
2. Web 服务器控制台输出
3. 浏览器开发者工具 (F12) → Network → WS (WebSocket)
