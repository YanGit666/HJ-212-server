# server.py - 模拟环保局数据中心 (HJ212-2017)

import socket
import threading
import time
import logging
import queue
from datetime import datetime
from cli import HJ212CLI

from config import SERVER_HOST, SERVER_PORT, WEB_HOST, WEB_PORT, ALLOWED_DEVICES, HEARTBEAT_TIMEOUT
from hj212 import (
    parse_packet, build_ack, build_set_time,
    FACTOR_CODES, FLAG_DESC, CN_DESC,
    CN_HEARTBEAT, CN_REALTIME_DATA, CN_MINUTE_DATA, CN_HOUR_DATA,
    CN_DAY_DATA, CN_MIX_DATA, CN_CALIB_DATA,
)

# 日志配置：控制台只显示警告及以上，详细日志写入文件
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),  # 控制台输出
        logging.FileHandler("hj212_server.log", encoding="utf-8"),
    ]
)

# 控制台只显示 WARNING 及以上级别，避免干扰 CLI 输入
for handler in logging.root.handlers:
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
        handler.setLevel(logging.WARNING)

print("📝 详细日志已写入 hj212_server.log，控制台仅显示警告信息")
log = logging.getLogger("HJ212")

# ─────────────────────────────────────────
# Web 服务器集成（用于前端显示）
# ─────────────────────────────────────────
WEB_SERVER_ENABLED = False
add_record_func = None

def init_web_server():
    """初始化 Web 服务器"""
    global WEB_SERVER_ENABLED, add_record_func
    try:
        from web_server import add_record, start_web_server
        add_record_func = add_record
        WEB_SERVER_ENABLED = True
        # 在后台线程启动 Web 服务器
        threading.Thread(
            target=start_web_server,
            args=(WEB_HOST, WEB_PORT, False),
            daemon=True
        ).start()
        log.info(f"✅ Web 服务器已启动: http://{WEB_HOST}:{WEB_PORT}")
        log.info(f"   访问地址: http://<云服务器公网IP>:{WEB_PORT}")
        log.info(f"   本地访问: http://127.0.0.1:{WEB_PORT}")
    except ImportError as e:
        log.warning(f"⚠️ Web 服务器模块导入失败: {e}")
    except Exception as e:
        log.error(f"❌ Web 服务器启动失败: {e}")

def send_to_web(cn: str, mn: str, cp: dict):
    """发送数据到 Web 服务器（前端显示）"""
    if WEB_SERVER_ENABLED and add_record_func:
        try:
            device_name = ALLOWED_DEVICES.get(mn, "未知设备")
            add_record_func(cn, mn, device_name, cp)
        except Exception as e:
            log.debug(f"发送数据到 Web 服务器失败: {e}")

# ─────────────────────────────────────────
# 在线设备表
# ─────────────────────────────────────────
connected_devices = {}
devices_lock      = threading.Lock()


def update_device(mn: str, addr, conn: socket.socket, data: dict = None):
    with devices_lock:
        if mn not in connected_devices:
            name = ALLOWED_DEVICES.get(mn, "未知设备")
            log.info(f"✅ 新设备接入: MN={mn} ({name}) addr={addr}")
            connected_devices[mn] = {
                "addr":           addr,
                "conn":           conn,
                "last_heartbeat": time.time(),
                "last_data":      {},
                "alarm_count":    0,
                "pkt_count":      0,
                # 命令模式：CLI 正在占用设备收发控制权
                "cmd_mode":       False,
                "cmd_queue":      queue.Queue(),
            }
        else:
            connected_devices[mn]["last_heartbeat"] = time.time()
            connected_devices[mn]["conn"]           = conn
            connected_devices[mn]["pkt_count"]      += 1
        if data:
            connected_devices[mn]["last_data"] = data


# ─────────────────────────────────────────
# 打印水质数据
# ─────────────────────────────────────────
def print_factor_data(mn: str, cp: dict, cn: str):
    name    = ALLOWED_DEVICES.get(mn, "未知设备")
    dt      = cp.get("DataTime", "Unknown")
    cn_name = CN_DESC.get(cn, cn)
    factors = cp.get("factors", {})
    
    # 混合样数据：提取采样时段
    begin_time = cp.get("BeginTime", "")
    end_time   = cp.get("EndTime", "")

    # 发送到 Web 服务器（前端显示）
    send_to_web(cn, mn, cp)

    log.info("")
    log.info(f"  ╔══════════════════════════════════════════╗")
    log.info(f"  ║  📊 {cn_name}({cn})")
    log.info(f"  ║  设备: {mn}")
    log.info(f"  ║  名称: {name}")
    log.info(f"  ║  时间: {dt}")
    
    # 混合样数据时显示采样时段
    if begin_time and end_time:
        log.info(f"  ║  采样时段: {begin_time} ~ {end_time}")
    
    log.info(f"  ╠══════════════════════════════════════════╣")

    has_exceed = False
    for code, info in factors.items():
        fname, unit = FACTOR_CODES.get(code, (code, ""))
        flag        = str(info.get("Flag", "N"))
        fdesc       = FLAG_DESC.get(flag, flag)
        icon        = "⚠️ " if flag == "T" else ("❌ " if flag == "F" else "✅ ")
        if flag == "T":
            has_exceed = True
        
        # 检查是否有统计数据 (Min/Avg/Max) - 用于分钟/小时数据
        min_val = info.get("Min")
        avg_val = info.get("Avg")
        max_val = info.get("Max")
        rtd_val = info.get("Rtd")
        
        if min_val is not None and avg_val is not None and max_val is not None:
            # 分钟/小时数据：显示 Min/Avg/Max
            log.info(f"  ║  {icon} {fname:<8} Min={min_val:>8} Avg={avg_val:>8} Max={max_val:>8} {unit:<6} [{fdesc}]")
        elif rtd_val is not None:
            # 实时数据：显示 Rtd
            log.info(f"  ║  {icon} {fname:<8} {str(rtd_val):>10} {unit:<8} [{fdesc}]")
        else:
            # 其他情况
            val = rtd_val or avg_val or "--"
            log.info(f"  ║  {icon} {fname:<8} {str(val):>10} {unit:<8} [{fdesc}]")

    log.info(f"  ╚══════════════════════════════════════════╝")

    if has_exceed:
        log.warning(f"  ⚠️  设备 {mn} 存在超标指标！")
        with devices_lock:
            if mn in connected_devices:
                connected_devices[mn]["alarm_count"] += 1


# ─────────────────────────────────────────
# 处理单个客户端连接
# ─────────────────────────────────────────
def handle_client(conn: socket.socket, addr):
    log.info(f"新连接: {addr}")
    buffer = ""
    mn     = None

    conn.settimeout(HEARTBEAT_TIMEOUT)

    try:
        while True:
            try:
                data = conn.recv(1024).decode("ascii", errors="ignore")
            except socket.timeout:
                log.warning(f"设备 {mn or addr} 心跳超时")
                break
            if not data:
                break

            buffer += data

            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                line = line.strip()
                if not line:
                    continue

                log.info(f"📨 原始报文: {line}")

                # 检查设备是否处于命令模式（CLI 正在收发）
                # 如果是，将报文推入 cmd_queue，由 CLI 处理
                tmp_mn = _extract_mn(line)
                if tmp_mn:
                    with devices_lock:
                        dev = connected_devices.get(tmp_mn)
                        if dev and dev.get("cmd_mode"):
                            dev["cmd_queue"].put(line)
                            continue  # 跳过正常处理，交给 CLI

                result = parse_packet(line)
                if not result.get("valid"):
                    log.warning(f"无效包: {line[:60]}")
                    continue

                if not result.get("crc_ok"):
                    log.warning(f"CRC 错误: MN={result.get('mn','?')}")
                    continue

                mn   = result["mn"]
                cn   = result["cn"]
                qn   = result["qn"]
                cp   = result["cp"]
                flag = int(result.get("flag", 0))

                # 验证设备
                if mn not in ALLOWED_DEVICES:
                    log.warning(f"未授权设备: MN={mn}")
                    continue

                # 验证密码
                if result["pw"] != "123456":
                    log.warning(f"密码错误: MN={mn}")
                    continue

                # ✅ 传入 conn
                update_device(mn, addr, conn, cp.get("factors"))

                # ─── 分发处理 ───
                need_ack = (flag & 0x01) == 1   # Flag bit A=1 需要应答

                if cn == CN_HEARTBEAT:
                    log.info(f"💓 心跳 MN={mn} QN={qn}")

                elif cn in (CN_REALTIME_DATA, CN_MINUTE_DATA, CN_HOUR_DATA,
                            CN_DAY_DATA, CN_MIX_DATA, CN_CALIB_DATA):
                    print_factor_data(mn, cp, cn)

                    # 每次数据上报时下发校时
                    timesync = build_set_time(mn)
                    conn.sendall(timesync.encode("ascii"))
                    log.info(f"⏰ 校时下发: MN={mn}")

                else:
                    log.info(f"CN={cn} ({CN_DESC.get(cn, '未知')}) MN={mn}")

                # 统一应答
                if need_ack:
                    ack = build_ack(mn, qn)
                    conn.sendall(ack.encode("ascii"))

    except Exception as e:
        log.error(f"异常 addr={addr}: {e}")
    finally:
        conn.close()
        if mn:
            with devices_lock:
                connected_devices.pop(mn, None)
            log.info(f"设备下线: MN={mn}")


def _extract_mn(raw: str) -> str | None:
    """从原始报文字符串中快速提取 MN 值"""
    try:
        if not raw.startswith("##"):
            return None
        length = int(raw[2:6])
        data_area = raw[6: 6 + length]
        for part in data_area.split(";"):
            if part.startswith("MN="):
                return part[3:].strip()
    except Exception:
        pass
    return None


# ─────────────────────────────────────────
# 定时打印在线状态
# ─────────────────────────────────────────
def status_reporter():
    while True:
        time.sleep(60)
        with devices_lock:
            if not connected_devices:
                log.info("📋 无设备在线")
                continue
            log.info(f"📋 在线设备: {len(connected_devices)} 台")
            for mn, info in connected_devices.items():
                name    = ALLOWED_DEVICES.get(mn, "未知")
                elapsed = int(time.time() - info["last_heartbeat"])
                log.info(
                    f"  MN={mn} ({name}) "
                    f"上次心跳={elapsed}s前 "
                    f"收包={info['pkt_count']} "
                    f"超标={info['alarm_count']}次"
                )


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────
def main():
    log.info(f"🌊 HJ212-2017 模拟服务器启动  {SERVER_HOST}:{SERVER_PORT}")
    log.info(f"   允许设备: {list(ALLOWED_DEVICES.keys())}")

    # 启动 Web 服务器（用于前端显示）
    init_web_server()

    threading.Thread(target=status_reporter, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((SERVER_HOST, SERVER_PORT))
    srv.listen(10)
    log.info("✅ 等待设备接入...")

    # 服务器在后台线程运行
    def accept_loop():
        try:
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=handle_client,
                                 args=(conn, addr), daemon=True).start()
        except Exception as e:
            log.error(f"accept_loop: {e}")

    threading.Thread(target=accept_loop, daemon=True).start()

    # CLI 在主线程运行（阻塞）✅
    cli = HJ212CLI(connected_devices, devices_lock)
    try:
        cli.cmdloop()
    except KeyboardInterrupt:
        log.info("服务器关闭")
    finally:
        srv.close()

if __name__ == "__main__":
    main()
