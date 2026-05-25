# cli.py - HJ212 服务端 CLI 命令行工具

import socket
import threading
import time
import cmd
import logging
from datetime import datetime

from config import SERVER_HOST, SERVER_PORT, ALLOWED_DEVICES
from hj212 import build_packet, parse_packet, crc16, CN_DESC

log = logging.getLogger("HJ212.CLI")

# ─────────────────────────────────────────
# CN 定义
# ─────────────────────────────────────────
CN_GET_TIME     = "1011"   # 提取现场机时间
CN_SET_TIME     = "1012"   # 设置现场机时间
CN_GET_REALTIME = "2011"   # 提取实时数据
CN_ACK          = "9011"   # 请求应答
CN_EXEC_ACK     = "9012"   # 执行结果


# ─────────────────────────────────────────
# 从设备命令队列中等待指定 CN 的报文
# ─────────────────────────────────────────
def _wait_cmd_response(dev: dict, expected_cn: str, timeout: float = 5.0) -> dict:
    """
    从 dev['cmd_queue'] 中取出报文，直到匹配 expected_cn 或超时。
    支持接收多个预期 CN（用于处理现场机可能先发其他包的情况）。
    """
    # 支持单个 CN 或 CN 列表
    if isinstance(expected_cn, str):
        expected_cns = {expected_cn}
    else:
        expected_cns = set(expected_cn)
    
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            line = dev["cmd_queue"].get(timeout=remaining)
        except Exception:
            break
        if line is None:
            continue
        result = parse_packet(line.strip())
        if not result.get("valid"):
            continue
        
        cn = result.get("cn")
        if cn in expected_cns:
            return result
        
        # 忽略心跳和数据上报包（这些是正常的后台流量）
        if cn in (CN_HEARTBEAT, CN_REALTIME_DATA, CN_HOUR_DATA,
                  CN_DAY_DATA, CN_MIX_DATA, CN_CALIB_DATA):
            log.debug(f"  忽略后台数据包 CN={cn}")
            continue
            
        # 其他非预期包记录警告
        log.warning(f"  收到非预期 CN={cn}，期望 {expected_cns}")
        
    return {"valid": False, "error": "timeout"}


# ─────────────────────────────────────────
# 向现场机发送命令并等待应答
# 实现 HJ212 完整交互流程：
#   Step1: 上位机发请求（Flag=9）
#   Step2: 现场机返回请求应答（CN=9011）
#   Step3: 现场机发送响应命令（Flag=8）
#   Step4: 现场机返回执行结果（CN=9012）
# ─────────────────────────────────────────
def send_command(dev: dict, mn: str, cn: str,
                 cp_data: str = "", timeout: float = 5.0) -> dict:
    result = {
        "success":    False,
        "qn_rtn":     None,
        "exe_rtn":    None,
        "cp":         {},
        "error":      "",
    }

    conn = dev.get("conn")
    if not conn:
        result["error"] = "设备连接不可用"
        return result

    # 启用命令模式：handle_client 会把报文推入 cmd_queue
    dev["cmd_mode"] = True
    # 清空历史残留
    while not dev["cmd_queue"].empty():
        try:
            dev["cmd_queue"].get_nowait()
        except Exception:
            break

    try:
        # ── Step 1: 发送请求命令 Flag=9（需要请求应答+后续响应）──
        qn = datetime.now().strftime("%Y%m%d%H%M%S") + "000"
        pkt1 = build_packet(mn=mn, cn=cn, cp_data=cp_data, qn=qn, flag=9)
        log.info(f"  → 发送请求 CN={cn} Flag=9: {pkt1.strip()}")
        conn.sendall(pkt1.encode("ascii"))

        # ── Step 2: 等待 CN=9011 请求应答 ──
        resp1 = _wait_cmd_response(dev, CN_ACK, timeout)
        if not resp1.get("valid"):
            result["error"] = f"Step2 超时或无效: {resp1.get('error','')}"
            log.warning(f"  ✗ {result['error']}")
            return result

        log.info(f"  ← 收到请求应答 CN={resp1.get('cn')} raw_cp={resp1.get('raw_cp')}")

        qn_rtn = _parse_simple_cp(resp1.get("raw_cp", ""), "QnRtn")
        result["qn_rtn"] = qn_rtn
        log.info(f"  ℹ QnRtn={qn_rtn}")

        if str(qn_rtn) != "1":
            result["error"] = f"现场机拒绝请求 QnRtn={qn_rtn}"
            log.warning(f"  ✗ {result['error']}")
            return result

        # ── Step 3: 等待现场机主动发送响应命令 CN=1011/Flag=8 ──
        resp2 = _wait_cmd_response(dev, cn, timeout)
        if not resp2.get("valid"):
            result["error"] = f"Step3 超时或无效: {resp2.get('error','')}"
            log.warning(f"  ✗ {result['error']}")
            return result

        log.info(f"  ← 收到现场机响应 CN={resp2.get('cn')} raw_cp={resp2.get('raw_cp')}")
        result["cp"]     = resp2.get("cp", {})
        result["raw_cp"] = resp2.get("raw_cp", "")

        # ── Step 4: 等待 CN=9012 执行结果 ──
        resp3 = _wait_cmd_response(dev, CN_EXEC_ACK, timeout)
        if not resp3.get("valid"):
            result["error"] = f"Step4 超时或无效: {resp3.get('error','')}"
            log.warning(f"  ✗ {result['error']}")
            return result

        log.info(f"  ← 收到执行结果 CN={resp3.get('cn')} raw_cp={resp3.get('raw_cp')}")

        exe_rtn = _parse_simple_cp(resp3.get("raw_cp", ""), "ExeRtn")
        result["exe_rtn"] = exe_rtn

        if str(exe_rtn) == "1":
            result["success"] = True
            log.info(f"  ✅ 执行成功 ExeRtn={exe_rtn}")
        else:
            result["error"] = f"执行失败 ExeRtn={exe_rtn}"
            log.warning(f"  ✗ {result['error']}")

    finally:
        dev["cmd_mode"] = False

    return result


def _parse_simple_cp(raw_cp: str, key: str) -> str:
    """从 raw_cp 字符串中提取 key=value"""
    for part in raw_cp.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return ""


# ─────────────────────────────────────────
# CLI 主类
# ─────────────────────────────────────────
class HJ212CLI(cmd.Cmd):
    intro  = "\n🌊 HJ212 服务端命令行  输入 help 查看命令\n"
    prompt = "hj212> "

    def __init__(self, connected_devices: dict, devices_lock: threading.Lock):
        super().__init__()
        self._devices      = connected_devices   # 共享在线设备表
        self._devices_lock = devices_lock

    # ── 内部：获取设备连接 ──────────────────
    def _get_conn(self, mn: str):
        with self._devices_lock:
            dev = self._devices.get(mn)
            if not dev:
                print(f"❌ 设备不在线: MN={mn}")
                return None, None
            return dev.get("conn"), dev

    # ── 内部：列出设备编号 ──────────────────
    def _list_online(self) -> list:
        with self._devices_lock:
            return list(self._devices.keys())

    # ─────────────────────────────────────────
    # list - 列出在线设备
    # ─────────────────────────────────────────
    def do_list(self, _):
        """列出所有在线设备
用法: list"""
        mns = self._list_online()
        if not mns:
            print("  无设备在线")
            return
        print(f"\n  在线设备 ({len(mns)} 台):")
        print(f"  {'序号':<4} {'MN':<28} {'名称':<16} {'上次心跳'}")
        print(f"  {'─'*70}")
        for i, mn in enumerate(mns, 1):
            with self._devices_lock:
                dev  = self._devices[mn]
                name = ALLOWED_DEVICES.get(mn, "未知")
                ago  = int(time.time() - dev["last_heartbeat"])
                print(f"  {i:<4} {mn:<28} {name:<16} {ago}s 前")
        print()

    # ─────────────────────────────────────────
    # gettime - 提取现场机时间 CN=1011
    # ─────────────────────────────────────────
    def do_gettime(self, line):
        """提取现场机时间 (CN=1011)
用法: gettime <MN>
示例: gettime 30000032000000101E19D6F1"""
        mn = line.strip()
        if not mn:
            print("  用法: gettime <MN>")
            return

        with self._devices_lock:
            dev = self._devices.get(mn)
        if not dev:
            print(f"❌ 设备不在线: MN={mn}")
            return

        print(f"\n  📡 提取现场机时间 MN={mn}")
        print(f"  {'─'*50}")

        # CP 中填写 PolId（监测因子编码，留空表示提取数采仪时间）
        cp_data = ""

        result = send_command(dev, mn, CN_GET_TIME, cp_data)

        if result["success"]:
            systime = _parse_simple_cp(result.get("raw_cp", ""), "SystemTime")
            if systime and len(systime) >= 14:
                fmt = (f"{systime[0:4]}-{systime[4:6]}-{systime[6:8]} "
                       f"{systime[8:10]}:{systime[10:12]}:{systime[12:14]}")
                print(f"\n  ✅ 现场机时间: {fmt}")
            else:
                print(f"\n  ✅ 执行成功，SystemTime={systime}")
        else:
            print(f"\n  ❌ 失败: {result['error']}")
        print()

    # ─────────────────────────────────────────
    # settime - 设置现场机时间 CN=1012
    # ─────────────────────────────────────────
    def do_settime(self, line):
        """设置现场机时间为服务器当前时间 (CN=1012)
用法: settime <MN>
示例: settime 30000032000000101E19D6F1"""
        mn = line.strip()
        if not mn:
            print("  用法: settime <MN>")
            return

        with self._devices_lock:
            dev = self._devices.get(mn)
        if not dev:
            print(f"❌ 设备不在线: MN={mn}")
            return

        now     = datetime.now()
        systime = now.strftime("%Y%m%d%H%M%S")
        cp_data = f"SystemTime={systime}"

        print(f"\n  📡 设置现场机时间 MN={mn} → {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  {'─'*50}")

        result = send_command(dev, mn, CN_SET_TIME, cp_data)

        if result["success"]:
            print(f"\n  ✅ 时间设置成功")
        else:
            print(f"\n  ❌ 失败: {result['error']}")
        print()

    # ─────────────────────────────────────────
    # getdata - 提取实时数据 CN=2011
    # ─────────────────────────────────────────
    def do_getdata(self, line):
        """提取现场机实时数据 (CN=2011)
用法: getdata <MN>
示例: getdata 30000032000000101E19D6F1"""
        mn = line.strip()
        if not mn:
            print("  用法: getdata <MN>")
            return

        with self._devices_lock:
            dev = self._devices.get(mn)
        if not dev:
            print(f"❌ 设备不在线: MN={mn}")
            return

        print(f"\n  📡 提取实时数据 MN={mn}")
        print(f"  {'─'*50}")

        result = send_command(dev, mn, CN_GET_REALTIME)

        if result["success"]:
            print(f"\n  ✅ 收到数据: {result.get('raw_cp','')}")
        else:
            print(f"\n  ❌ 失败: {result['error']}")
        print()

    # ─────────────────────────────────────────
    # exit / quit
    # ─────────────────────────────────────────
    def do_exit(self, _):
        """退出 CLI"""
        print("  再见 👋")
        return True

    def do_quit(self, _):
        """退出 CLI"""
        return self.do_exit(_)

    def default(self, line):
        print(f"  未知命令: {line}，输入 help 查看可用命令")
