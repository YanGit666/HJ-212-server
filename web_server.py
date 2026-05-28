# web_server.py - Flask + SocketIO Web 服务器
# 用于实时显示 HJ-212 接收到的数据

import json
import logging
from datetime import datetime
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import threading

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("WebServer")

# Flask 应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'hj212-web-server-secret'
CORS(app)

# SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# 数据存储（最近 1000 条记录，按数据类型分类）
MAX_RECORDS = 1000
data_storage = {
    "realtime": [],   # 实时数据 (CN=2011)
    "minute": [],     # 分钟数据 (CN=2051)
    "hour": [],       # 小时数据 (CN=2061)
    "day": [],        # 日数据 (CN=2031)
    "mix": [],        # 混合样数据 (CN=2063)
    "calib": [],      # 自动标样核查数据 (CN=2062)
}
data_lock = threading.Lock()

# HJ212 CN 码映射
CN_TYPE_MAP = {
    "2011": "realtime",
    "2051": "minute",
    "2061": "hour",
    "2031": "day",
    "2063": "mix",
    "2062": "calib",
}

# 水质因子编码表
FACTOR_CODES = {
    "w01010": ("水温", "℃"),
    "w01001": ("pH", ""),
    "w21003": ("氨氮", "mg/L"),
    "w01014": ("电导率", "μS/cm"),
    "w01018": ("COD", "mg/L"),
    "w01003": ("浊度", "NTU"),
}

# 数据类型描述
DATA_TYPE_DESC = {
    "realtime": "实时数据",
    "minute": "分钟数据",
    "hour": "小时数据",
    "day": "日数据",
    "mix": "混合样数据",
    "calib": "自动标样核查",
}


def add_record(cn, mn, device_name, cp_data):
    """
    添加新数据记录（由 server.py 调用）
    
    Args:
        cn: 命令码 (如 "2011")
        mn: 设备 MN 码
        device_name: 设备名称
        cp_data: CP 数据区解析后的字典
    """
    data_type = CN_TYPE_MAP.get(cn, "realtime")
    
    record = {
        "id": f"{datetime.now().timestamp()}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_time": cp_data.get("DataTime", ""),
        "mn": mn,
        "device_name": device_name,
        "data_type": data_type,
        "data_type_desc": DATA_TYPE_DESC.get(data_type, "未知"),
        "factors": {},
        "begin_time": cp_data.get("BeginTime", ""),
        "end_time": cp_data.get("EndTime", ""),
    }
    
    # 处理监测因子数据
    factors = cp_data.get("factors", {})
    for code, info in factors.items():
        factor_name, unit = FACTOR_CODES.get(code, (code, ""))
        flag = str(info.get("Flag", "N"))
        
        factor_data = {
            "code": code,
            "name": factor_name,
            "unit": unit,
            "flag": flag,
        }
        
        # 检查数据类型：实时数据显示 Rtd，分钟/小时数据显示 Min/Avg/Max
        min_val = info.get("Min")
        avg_val = info.get("Avg")
        max_val = info.get("Max")
        rtd_val = info.get("Rtd")
        
        if min_val is not None and avg_val is not None and max_val is not None:
            # 分钟/小时数据
            factor_data["min"] = min_val
            factor_data["avg"] = avg_val
            factor_data["max"] = max_val
            factor_data["display_type"] = "min_max"
        elif rtd_val is not None:
            # 实时数据
            factor_data["rtd"] = rtd_val
            factor_data["display_type"] = "rtd"
        else:
            # 其他情况，优先显示 Rtd，其次 Avg
            if rtd_val is not None:
                factor_data["rtd"] = rtd_val
                factor_data["display_type"] = "rtd"
            elif avg_val is not None:
                factor_data["avg"] = avg_val
                factor_data["display_type"] = "avg"
        
        record["factors"][code] = factor_data
    
    with data_lock:
        # 添加到对应类型的列表
        data_storage[data_type].insert(0, record)
        
        # 限制记录数量
        if len(data_storage[data_type]) > MAX_RECORDS:
            data_storage[data_type] = data_storage[data_type][:MAX_RECORDS]
    
    log.info(f"📊 新数据: {record['data_type_desc']} MN={mn} 因子数={len(record['factors'])}")
    
    # 通过 WebSocket 广播新数据
    socketio.emit('new_data', record, namespace='/hj212')
    
    return record


def get_records(data_type=None, limit=100):
    """
    获取记录（用于 API 接口）
    
    Args:
        data_type: 数据类型 (realtime/minute/hour/day/mix/calib)，None 表示全部
        limit: 返回记录数限制
    """
    with data_lock:
        if data_type and data_type in data_storage:
            return data_storage[data_type][:limit]
        else:
            # 合并所有类型，按时间排序
            all_records = []
            for records in data_storage.values():
                all_records.extend(records)
            all_records.sort(key=lambda x: x["timestamp"], reverse=True)
            return all_records[:limit]


def get_stats():
    """获取统计信息"""
    with data_lock:
        stats = {
            "realtime_count": len(data_storage["realtime"]),
            "minute_count": len(data_storage["minute"]),
            "hour_count": len(data_storage["hour"]),
            "day_count": len(data_storage["day"]),
            "mix_count": len(data_storage["mix"]),
            "calib_count": len(data_storage["calib"]),
            "total_count": sum(len(v) for v in data_storage.values()),
        }
    return stats


@app.route('/')
def index():
    """主页 - 显示数据表格"""
    return render_template('index.html')


@app.route('/api/records/<data_type>')
def api_records(data_type):
    """获取指定类型的数据记录"""
    limit = int(request.args.get('limit', 100))
    records = get_records(data_type, limit)
    return json.jsonify({
        "success": True,
        "data": records,
        "count": len(records)
    })


@app.route('/api/records')
def api_all_records():
    """获取所有数据记录"""
    limit = int(request.args.get('limit', 100))
    records = get_records(None, limit)
    return json.jsonify({
        "success": True,
        "data": records,
        "count": len(records)
    })


@app.route('/api/stats')
def api_stats():
    """获取统计数据"""
    return json.jsonify({
        "success": True,
        "stats": get_stats()
    })


# WebSocket 事件处理
@socketio.on('connect', namespace='/hj212')
def handle_connect():
    """客户端连接"""
    log.info(f"🌐 客户端连接: {request.sid}")
    # 发送当前统计信息
    emit('stats', get_stats())
    # 发送最新数据
    records = get_records(None, 50)
    emit('initial_data', records)


@socketio.on('disconnect', namespace='/hj212')
def handle_disconnect():
    """客户端断开"""
    log.info(f"🌐 客户端断开: {request.sid}")


@socketio.on('request_data', namespace='/hj212')
def handle_request_data(data):
    """客户端请求数据"""
    data_type = data.get('type', None)
    limit = data.get('limit', 100)
    records = get_records(data_type, limit)
    emit('data_response', {
        "type": data_type,
        "records": records
    })


from flask import request

def start_web_server(host='0.0.0.0', port=5000, debug=False):
    """
    启动 Web 服务器
    
    Args:
        host: 监听地址
        port: 监听端口
        debug: 是否开启调试模式
    """
    log.info(f"🚀 Web 服务器启动: http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug, use_reloader=False)


if __name__ == '__main__':
    start_web_server(debug=True)
