import os
import csv
import time
import psutil
import threading
import queue
import io
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.animation as animation
from collections import deque
from datetime import datetime

# ===================== 配置 =====================
MAX_DISPLAY = 150
REFRESH = 1000

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 全局变量
processes = {}
data_queue = queue.Queue()
chart_running = True
lock = threading.Lock()
COLORS = ['#3498db', '#e74c3c', '#2ecc71', '#9b59b6', '#f39c12', '#1abc9c']

import json
import hmac
import hashlib

CONFIG_FILE = "alert_config.json"

# 报警配置
ALERT_CONFIG = {
    'webhook_type': 'dingtalk',  # dingtalk/wechat/custom
    'webhook_url': '',  # Webhook URL
    'dingtalk_secret': '',  # 钉钉机器人密钥（签名用）
    'wechat_secret': '',  # 企业微信机器人密钥（签名用）
    'memory_warning_mb': 1000,  # 内存警告线（MB）
    'alert_cooldown': 300,  # 冷却时间（秒），防止频繁报警
    'last_alert_time': 0,  # 上次报警时间
    'scheduled_push_hour': None,  # 定时推送时间（小时），None表示不启用
    'last_scheduled_push': None  # 上次定时推送日期
}

alert_frame = None  # 报警配置窗口引用

def load_config():
    """加载配置文件"""
    global ALERT_CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                ALERT_CONFIG.update(saved)
            print("✅ 配置文件加载成功")
        except Exception as e:
            print(f"配置文件加载失败: {e}")

def save_config():
    """保存配置文件"""
    try:
        # 只保存需要持久化的配置
        to_save = {
            'webhook_type': ALERT_CONFIG['webhook_type'],
            'webhook_url': ALERT_CONFIG['webhook_url'],
            'dingtalk_secret': ALERT_CONFIG['dingtalk_secret'],
            'wechat_secret': ALERT_CONFIG['wechat_secret'],
            'memory_warning_mb': ALERT_CONFIG['memory_warning_mb'],
            'alert_cooldown': ALERT_CONFIG['alert_cooldown'],
            'scheduled_push_hour': ALERT_CONFIG['scheduled_push_hour']
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, indent=2)
        print("✅ 配置文件保存成功")
    except Exception as e:
        print(f"配置文件保存失败: {e}")

# GUI引用
root = None
process_tree = None
status_label = None
all_history = []  # 全局历史数据

# ===================== 进程监控线程 =====================
def monitor_process(pid):
    try:
        p = psutil.Process(pid)
        proc_name = p.name()
        while chart_running:
            with lock:
                if pid not in processes:
                    break
            try:
                mem_mb = round(p.memory_info().rss / 1024 / 1024, 2)
                now = datetime.now().strftime("%H:%M:%S")
                data_queue.put({'pid': pid, 'name': proc_name, 'time': now, 'memory': mem_mb})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                data_queue.put({'pid': pid, 'name': proc_name, 'time': None, 'memory': None})
                break
            time.sleep(REFRESH / 1000)
    except Exception as e:
        print(f"监控线程异常: {e}")

# ===================== 曲线平滑函数 =====================
def smooth_curve(x_values, y_values, max_points=300):
    if len(y_values) <= max_points:
        return x_values, y_values
    key_indices = {0, len(y_values) - 1}
    for i in range(1, len(y_values) - 1):
        prev_val = y_values[i-1]
        curr_val = y_values[i]
        next_val = y_values[i+1]
        if (curr_val > prev_val and curr_val > next_val) or (curr_val < prev_val and curr_val < next_val):
            key_indices.add(i)
    key_list = sorted(key_indices)
    if len(key_list) > max_points:
        step = len(key_list) // max_points
        key_list = key_list[::step]
    key_list = sorted(set(key_list))
    smooth_x = [x_values[i] for i in key_list]
    smooth_y = [y_values[i] for i in key_list]
    return smooth_x, smooth_y

# ===================== GUI界面 =====================
def update_process_tree():
    """更新进程列表树（调用者负责加锁）"""
    if not process_tree:
        return
    for item in process_tree.get_children():
        process_tree.delete(item)
    for idx, (pid, info) in enumerate(processes.items()):
        color = COLORS[idx % len(COLORS)]
        process_tree.insert('', 'end', text=str(pid), values=(info['name'], color))

def get_pid_by_port(port):
    """根据端口号获取所有正在监听的本机进程列表"""
    try:
        LOCAL_ADDRESSES = {'0.0.0.0', '127.0.0.1', '::', '::1', 'localhost', ''}
        results = []
        
        for kind in ['inet', 'inet6', 'all']:
            try:
                for conn in psutil.net_connections(kind=kind):
                    if conn.status != psutil.CONN_LISTEN:
                        continue
                    if not hasattr(conn, 'laddr') or not conn.laddr or conn.laddr.port != port:
                        continue
                    
                    ip_addr = conn.laddr.ip if conn.laddr else ''
                    if ip_addr in LOCAL_ADDRESSES or ip_addr.startswith('127.') or ip_addr.startswith('192.168.') or ip_addr.startswith('10.'):
                        if conn.pid is not None:
                            try:
                                p = psutil.Process(conn.pid)
                                proc_name = p.name()
                            except:
                                proc_name = "未知"
                            results.append({
                                'pid': conn.pid,
                                'name': proc_name,
                                'ip': ip_addr,
                                'port': conn.laddr.port
                            })
            except Exception as e:
                continue
        
        # 去重（同一个进程可能监听多个地址）
        unique_results = []
        seen_pids = set()
        for r in results:
            if r['pid'] not in seen_pids:
                seen_pids.add(r['pid'])
                unique_results.append(r)
        
        if not unique_results:
            print(f"❌ 未找到监听端口 {port} 的进程")
        else:
            print(f"✅ 找到 {len(unique_results)} 个监听端口 {port} 的进程")
            for r in unique_results:
                print(f"  PID:{r['pid']} | {r['name']} | {r['ip']}:{r['port']}")
        
        return unique_results
    except psutil.AccessDenied:
        messagebox.showwarning("警告", "⚠️ 需要管理员权限才能获取监听端口信息\n请以管理员身份运行程序")
        return []
    except Exception as e:
        print(f"获取端口进程失败: {e}")
        return []

def add_process_ui():
    # 创建自定义对话框
    dialog = tk.Toplevel(root)
    dialog.title("添加进程")
    dialog.geometry("300x200")
    dialog.transient(root)
    dialog.grab_set()
    
    ttk.Label(dialog, text="选择添加方式:").pack(pady=10)
    
    var = tk.IntVar(value=1)
    
    def on_radio_change():
        entry.focus()
    
    ttk.Radiobutton(dialog, text="通过 PID 添加", variable=var, value=1, command=on_radio_change).pack(anchor="w", padx=20)
    ttk.Radiobutton(dialog, text="通过 端口号 添加", variable=var, value=2, command=on_radio_change).pack(anchor="w", padx=20)
    
    ttk.Label(dialog, text="输入值:").pack(pady=5)
    entry = ttk.Entry(dialog, width=20)
    entry.pack(pady=5)
    entry.focus()
    # 绑定回车键确认
    entry.bind('<Return>', lambda event: on_ok())
    
    def on_ok():
        value = entry.get().strip()
        if not value:
            messagebox.showerror("错误", "请输入值")
            return
        
        try:
            if var.get() == 1:
                # 通过PID
                pid = int(value)
            else:
                # 通过端口号
                port = int(value)
                processes_list = get_pid_by_port(port)
                if not processes_list:
                    messagebox.showerror("错误", f"未找到监听端口 {port} 的进程")
                    return
                
                if len(processes_list) == 1:
                    # 只有一个进程，直接使用
                    pid = processes_list[0]['pid']
                else:
                    # 多个进程，弹出选择框
                    select_dialog = tk.Toplevel(dialog)
                    select_dialog.title(f"选择进程 (端口 {port})")
                    select_dialog.geometry("400x300")
                    select_dialog.transient(dialog)
                    select_dialog.grab_set()
                    
                    ttk.Label(select_dialog, text=f"找到 {len(processes_list)} 个监听端口 {port} 的进程:").pack(pady=10)
                    
                    listbox = tk.Listbox(select_dialog, width=50, height=10)
                    for i, proc in enumerate(processes_list):
                        listbox.insert(i, f"PID:{proc['pid']} | {proc['name']} | {proc['ip']}:{proc['port']}")
                    listbox.pack(pady=5)
                    listbox.selection_set(0)
                    
                    selected_pid = [None]
                    
                    def on_select_ok():
                        selected = listbox.curselection()
                        if selected:
                            idx = selected[0]
                            selected_pid[0] = processes_list[idx]['pid']
                        select_dialog.destroy()
                    
                    def on_select_cancel():
                        selected_pid[0] = None
                        select_dialog.destroy()
                    
                    btn_frame = ttk.Frame(select_dialog)
                    btn_frame.pack(pady=10)
                    ttk.Button(btn_frame, text="确定", command=on_select_ok).pack(side="left", padx=10)
                    ttk.Button(btn_frame, text="取消", command=on_select_cancel).pack(side="left", padx=10)
                    
                    dialog.wait_window(select_dialog)
                    
                    if selected_pid[0] is None:
                        return
                    pid = selected_pid[0]
        except ValueError:
            messagebox.showerror("错误", "请输入数字")
            return
        
        with lock:
            if pid in processes:
                messagebox.showwarning("警告", f"进程 {pid} 已在监控中")
                dialog.destroy()
                return
        
        try:
            p = psutil.Process(pid)
            proc_name = p.name()
            with lock:
                processes[pid] = {'proc': p, 'name': proc_name, 'data': deque(maxlen=MAX_DISPLAY)}
            
            t = threading.Thread(target=monitor_process, args=(pid,), daemon=True)
            t.start()
            
            update_process_tree()
            if status_label:
                status_label.config(text=f"✅ 已添加: {proc_name} (PID:{pid})")
            dialog.destroy()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            messagebox.showerror("错误", f"无法访问进程 {pid}")
    
    def on_cancel():
        dialog.destroy()
    
    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=10)
    ttk.Button(btn_frame, text="确定", command=on_ok).pack(side="left", padx=10)
    ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side="right", padx=10)

def remove_process_ui():
    selected = process_tree.selection()
    if not selected:
        messagebox.showwarning("警告", "请先选择要移除的进程")
        return
    
    item = selected[0]
    pid = int(process_tree.item(item, 'text'))
    
    proc_name = None
    with lock:
        if pid in processes:
            proc_name = processes[pid]['name']
            del processes[pid]
        else:
            messagebox.showwarning("警告", "进程不在监控列表中")
            return
    
    # 在锁外更新UI
    update_process_tree()
    if status_label:
        status_label.config(text=f"✅ 已移除: {proc_name} (PID:{pid})")

def clear_all_data():
    """一键清空所有历史数据"""
    if not messagebox.askyesno("确认删除", "确定要清空所有历史数据吗？此操作不可撤销！"):
        return
    
    global all_history
    with lock:
        all_history = []
        for pid in processes:
            processes[pid]['data'].clear()
    
    if status_label:
        status_label.config(text="✅ 已清空所有历史数据")

# ===================== 报警功能 =====================
import requests
import base64

def capture_screenshot(max_size_kb=15):
    """捕获当前窗口截图并压缩到指定大小"""
    try:
        fig = plt.gcf()
        # 通过调整dpi来控制图片大小
        for dpi in [50, 40, 30, 25, 20]:
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0)
            buf_size = buf.tell()
            if buf_size <= max_size_kb * 1024:
                buf.seek(0)
                return base64.b64encode(buf.read()).decode('utf-8')
        print(f"截图压缩后仍超过大小限制 ({buf_size} bytes)")
        return None
    except Exception as e:
        print(f"截图失败: {e}")
        return None

def get_dingtalk_signature():
    """生成钉钉签名（HMAC-SHA256）"""
    secret = ALERT_CONFIG.get('dingtalk_secret')
    if not secret:
        return ''
    timestamp = str(int(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode('utf-8')
    return f"&timestamp={timestamp}&sign={sign}"

def get_wechat_signature():
    """生成企业微信签名（SHA256）"""
    secret = ALERT_CONFIG.get('wechat_secret')
    if not secret:
        return ''
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}"
    # 企业微信签名通常是直接在body中添加timestamp和sign
    sign = hashlib.sha256(f"{timestamp}{secret}".encode('utf-8')).hexdigest()
    return timestamp, sign

def send_webhook(message, image_base64=None):
    """发送Webhook通知（支持钉钉和企业微信）"""
    if not ALERT_CONFIG['webhook_url']:
        print("Webhook URL未配置")
        return False
    
    try:
        webhook_type = ALERT_CONFIG['webhook_type']
        url = ALERT_CONFIG['webhook_url']
        sign = get_dingtalk_signature()
        
        if webhook_type == 'dingtalk':
            # 钉钉机器人格式
            # 添加签名
            dingtalk_url = url + sign if sign else url
            
            # 1. 先发送文本消息
            text_data = {
                "msgtype": "text",
                "text": {"content": message}
            }
            response = requests.post(dingtalk_url, json=text_data, timeout=10)
            if response.status_code != 200:
                print(f"钉钉文本消息发送失败: {response.status_code}")
                return False
            
            # 2. 如果有图片，单独发送图片消息
            if image_base64:
                image_data = {
                    "msgtype": "image",
                    "image": {"base64": image_base64}
                }
                response = requests.post(dingtalk_url, json=image_data, timeout=10)
                if response.status_code != 200:
                    print(f"钉钉图片消息发送失败: {response.status_code}")
                    return False
            
            print("钉钉消息发送成功")
            return True
        
        elif webhook_type == 'wechat':
            # 企业微信机器人格式
            # 先发送文本消息
            data = {
                "msgtype": "text",
                "text": {"content": message}
            }
            secret = ALERT_CONFIG.get('wechat_secret')
            if secret:
                timestamp, sign = get_wechat_signature()
                data['timestamp'] = timestamp
                data['sign'] = sign
            
            response = requests.post(url, json=data, timeout=10)
            if response.status_code != 200:
                print(f"企业微信文本消息发送失败: {response.status_code}")
                return False
            
            # 如果有图片，单独发送
            if image_base64:
                data = {
                    "msgtype": "image",
                    "image": {"base64": image_base64, "md5": ""}
                }
                if secret:
                    timestamp, sign = get_wechat_signature()
                    data['timestamp'] = timestamp
                    data['sign'] = sign
                response = requests.post(url, json=data, timeout=10)
                if response.status_code != 200:
                    print(f"企业微信图片消息发送失败: {response.status_code}")
                    return False
            
            print("企业微信消息发送成功")
            return True
        
        else:
            # 自定义格式
            # 先发送文本消息
            data = {'text': message}
            response = requests.post(url, json=data, timeout=10)
            if response.status_code != 200:
                print(f"自定义Webhook文本消息发送失败: {response.status_code}")
                return False
            
            # 如果有图片，单独发送
            if image_base64:
                data = {'text': message, 'image': image_base64}
                response = requests.post(url, json=data, timeout=10)
                if response.status_code != 200:
                    print(f"自定义Webhook图片消息发送失败: {response.status_code}")
                    return False
            
            print("自定义Webhook消息发送成功")
            return True
    except Exception as e:
        print(f"Webhook发送异常: {e}")
        return False

def check_alerts():
    """检查报警规则"""
    global ALERT_CONFIG
    now = time.time()
    
    # 检查内存警告
    alert_messages = []
    with lock:
        for pid, info in processes.items():
            if info['data'] and info['data'][-1] > ALERT_CONFIG['memory_warning_mb']:
                alert_messages.append(f"⚠️ {info['name']} (PID:{pid}) 内存占用过高: {info['data'][-1]:.2f} MB")
    
    # 检查冷却时间
    if alert_messages and now - ALERT_CONFIG['last_alert_time'] > ALERT_CONFIG['alert_cooldown']:
        message = "\n".join(alert_messages)
        screenshot = capture_screenshot()
        send_webhook(message, screenshot)
        ALERT_CONFIG['last_alert_time'] = now
        if status_label:
            status_label.config(text="⚠️ 已发送报警通知")
    
    # 检查定时推送
    if ALERT_CONFIG['scheduled_push_hour'] is not None:
        today = datetime.now().date()
        if ALERT_CONFIG['last_scheduled_push'] != today:
            hour = datetime.now().hour
            if hour == ALERT_CONFIG['scheduled_push_hour']:
                message = f"📊 定时报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                with lock:
                    for pid, info in processes.items():
                        current_mem = info['data'][-1] if info['data'] else 0
                        message += f"  {info['name']} (PID:{pid}): {current_mem:.2f} MB\n"
                screenshot = capture_screenshot()
                send_webhook(message, screenshot)
                ALERT_CONFIG['last_scheduled_push'] = today
                if status_label:
                    status_label.config(text="📅 已发送定时报告")

def show_alert_config():
    """显示报警配置窗口"""
    global alert_frame
    if alert_frame and alert_frame.winfo_exists():
        alert_frame.destroy()
    
    alert_frame = tk.Toplevel(root)
    alert_frame.title("报警配置")
    alert_frame.geometry("450x500")
    alert_frame.transient(root)
    alert_frame.grab_set()
    
    row = 0
    
    # Webhook类型
    ttk.Label(alert_frame, text="Webhook类型:").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    type_var = tk.StringVar(value=ALERT_CONFIG['webhook_type'])
    type_frame = ttk.Frame(alert_frame)
    type_frame.grid(row=row, column=1, sticky="w", padx=10, pady=5)
    ttk.Radiobutton(type_frame, text="钉钉", variable=type_var, value='dingtalk').pack(side="left", padx=5)
    ttk.Radiobutton(type_frame, text="企业微信", variable=type_var, value='wechat').pack(side="left", padx=5)
    ttk.Radiobutton(type_frame, text="自定义", variable=type_var, value='custom').pack(side="left", padx=5)
    row += 1
    
    # Webhook URL
    ttk.Label(alert_frame, text="Webhook URL:").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    webhook_entry = ttk.Entry(alert_frame, width=45)
    webhook_entry.grid(row=row, column=1, padx=10, pady=5)
    webhook_entry.insert(0, ALERT_CONFIG['webhook_url'])
    row += 1
    
    # 钉钉密钥
    ttk.Label(alert_frame, text="钉钉密钥（加签用）:").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    dingtalk_secret_entry = ttk.Entry(alert_frame, width=45)
    dingtalk_secret_entry.grid(row=row, column=1, padx=10, pady=5)
    dingtalk_secret_entry.insert(0, ALERT_CONFIG.get('dingtalk_secret', ''))
    row += 1
    
    # 企业微信密钥
    ttk.Label(alert_frame, text="企业微信密钥（加签用）:").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    wechat_secret_entry = ttk.Entry(alert_frame, width=45)
    wechat_secret_entry.grid(row=row, column=1, padx=10, pady=5)
    wechat_secret_entry.insert(0, ALERT_CONFIG.get('wechat_secret', ''))
    row += 1
    
    # 内存警告线
    ttk.Label(alert_frame, text="内存警告线 (MB):").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    warning_entry = ttk.Entry(alert_frame, width=10)
    warning_entry.grid(row=row, column=1, sticky="w", padx=10, pady=5)
    warning_entry.insert(0, str(ALERT_CONFIG['memory_warning_mb']))
    row += 1
    
    # 冷却时间
    ttk.Label(alert_frame, text="冷却时间 (秒):").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    cooldown_entry = ttk.Entry(alert_frame, width=10)
    cooldown_entry.grid(row=row, column=1, sticky="w", padx=10, pady=5)
    cooldown_entry.insert(0, str(ALERT_CONFIG['alert_cooldown']))
    row += 1
    
    # 定时推送
    ttk.Label(alert_frame, text="定时推送时间 (小时，0-23):").grid(row=row, column=0, sticky="w", padx=10, pady=5)
    hour_entry = ttk.Entry(alert_frame, width=10)
    hour_entry.grid(row=row, column=1, sticky="w", padx=10, pady=5)
    if ALERT_CONFIG['scheduled_push_hour'] is not None:
        hour_entry.insert(0, str(ALERT_CONFIG['scheduled_push_hour']))
    row += 1
    
    ttk.Label(alert_frame, text="(留空表示不启用定时推送)").grid(row=row, column=1, sticky="w", padx=10, pady=0)
    row += 1
    
    def on_save():
        ALERT_CONFIG['webhook_type'] = type_var.get()
        ALERT_CONFIG['webhook_url'] = webhook_entry.get().strip()
        ALERT_CONFIG['dingtalk_secret'] = dingtalk_secret_entry.get().strip()
        ALERT_CONFIG['wechat_secret'] = wechat_secret_entry.get().strip()
        try:
            ALERT_CONFIG['memory_warning_mb'] = float(warning_entry.get())
        except ValueError:
            messagebox.showerror("错误", "内存警告线必须是数字")
            return
        try:
            ALERT_CONFIG['alert_cooldown'] = int(cooldown_entry.get())
        except ValueError:
            messagebox.showerror("错误", "冷却时间必须是整数")
            return
        try:
            hour_str = hour_entry.get().strip()
            ALERT_CONFIG['scheduled_push_hour'] = int(hour_str) if hour_str else None
        except ValueError:
            messagebox.showerror("错误", "定时时间必须是0-23的整数")
            return
        
        save_config()
        alert_frame.destroy()
        messagebox.showinfo("成功", "配置已保存并持久化")
    
    def on_test():
        webhook_type = type_var.get()
        url = webhook_entry.get().strip()
        dingtalk_secret = dingtalk_secret_entry.get().strip()
        wechat_secret = wechat_secret_entry.get().strip()
        if not url:
            messagebox.showwarning("警告", "请先填写Webhook URL")
            return
        
        old_type = ALERT_CONFIG['webhook_type']
        old_url = ALERT_CONFIG['webhook_url']
        old_dingtalk_secret = ALERT_CONFIG['dingtalk_secret']
        old_wechat_secret = ALERT_CONFIG['wechat_secret']
        ALERT_CONFIG['webhook_type'] = webhook_type
        ALERT_CONFIG['webhook_url'] = url
        ALERT_CONFIG['dingtalk_secret'] = dingtalk_secret
        ALERT_CONFIG['wechat_secret'] = wechat_secret
        
        screenshot = capture_screenshot()
        if send_webhook("🔔 测试消息 - 进程内存监控系统", screenshot):
            messagebox.showinfo("成功", "测试消息发送成功")
        else:
            messagebox.showerror("失败", "测试消息发送失败")
        
        ALERT_CONFIG['webhook_type'] = old_type
        ALERT_CONFIG['webhook_url'] = old_url
        ALERT_CONFIG['dingtalk_secret'] = old_dingtalk_secret
        ALERT_CONFIG['wechat_secret'] = old_wechat_secret
    
    btn_frame = ttk.Frame(alert_frame)
    btn_frame.grid(row=row, column=0, columnspan=2, pady=20)
    ttk.Button(btn_frame, text="测试", command=on_test).pack(side="left", padx=10)
    ttk.Button(btn_frame, text="保存", command=on_save).pack(side="left", padx=10)
    ttk.Button(btn_frame, text="取消", command=alert_frame.destroy).pack(side="left", padx=10)

def exit_app():
    global chart_running
    chart_running = False
    # 短暂等待线程清理
    time.sleep(0.2)
    # 强制退出
    root.destroy()
    os._exit(0)

# ===================== 主程序 =====================
if __name__ == "__main__":
    root = tk.Tk()
    root.title("多进程内存监控系统")
    root.geometry("1200x700")
    root.protocol("WM_DELETE_WINDOW", exit_app)

    # 左侧面板
    left_frame = ttk.Frame(root, width=250)
    left_frame.pack(side="left", fill="y", padx=5, pady=5)
    left_frame.pack_propagate(False)

    ttk.Label(left_frame, text="📊 进程监控", font=('Arial', 14, 'bold')).pack(pady=10)

    ttk.Label(left_frame, text="监控进程列表:").pack(anchor="w", padx=5)
    process_tree = ttk.Treeview(left_frame, columns=('name', 'color'), show='tree headings')
    process_tree.heading('#0', text='PID')
    process_tree.heading('name', text='进程名')
    process_tree.heading('color', text='颜色')
    process_tree.column('#0', width=80)
    process_tree.column('name', width=120)
    process_tree.column('color', width=40)
    process_tree.pack(fill="both", expand=True, padx=5, pady=5)

    btn_frame = ttk.Frame(left_frame)
    btn_frame.pack(fill="x", padx=5, pady=5)
    ttk.Button(btn_frame, text="➕ 添加进程", command=add_process_ui).pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="➖ 移除进程", command=remove_process_ui).pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="🗑️ 清空数据", command=clear_all_data).pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="🔔 报警配置", command=show_alert_config).pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="🚪 退出", command=exit_app).pack(fill="x", pady=2)

    status_label = ttk.Label(left_frame, text="就绪", relief="sunken")
    status_label.pack(fill="x", padx=5, pady=5)

    # 右侧图表区域
    right_frame = ttk.Frame(root)
    right_frame.pack(side="right", fill="both", expand=True, padx=5, pady=5)

    fig = plt.figure(figsize=(10, 6))
    ax_left = fig.add_subplot(1, 2, 1)
    ax_right = fig.add_subplot(1, 2, 2)

    canvas = FigureCanvasTkAgg(fig, master=right_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True)

    def update_chart(frame):
        global all_history
        while not data_queue.empty():
            try:
                data = data_queue.get_nowait()
                pid = data['pid']
                mem = data['memory']
                if mem is None:
                    with lock:
                        if pid in processes:
                            del processes[pid]
                    update_process_tree()
                    continue
                with lock:
                    if pid in processes:
                        processes[pid]['data'].append(mem)
                all_history.append((pid, data['time'], mem))
            except queue.Empty:
                break
        
        # 检查报警
        check_alerts()

        ax_left.clear()
        ax_left.set_title(f"📈 实时内存（最近 {MAX_DISPLAY} 点）", fontsize=11)
        ax_left.set_xlabel("时间点")
        ax_left.set_ylabel("内存 (MB)")
        ax_left.grid(alpha=0.3)
        all_ys = []
        with lock:
            for idx, (pid, info) in enumerate(processes.items()):
                data = list(info['data'])
                xs = list(range(len(data)))
                color = COLORS[idx % len(COLORS)]
                ax_left.plot(xs, data, lw=2, color=color, label=f"{info['name']} ({pid})")
                all_ys.extend(data)
        if all_ys:
            y_min, y_max = min(all_ys), max(all_ys)
            padding = max((y_max - y_min) * 0.12, 1) if y_max != y_min else max(y_max * 0.05, 1)
            ax_left.set_ylim(max(0, y_min - padding), y_max + padding)
        ax_left.legend(loc='upper left', fontsize=6)

        ax_right.clear()
        ax_right.set_title("📊 完整历史（平滑）", fontsize=11)
        ax_right.set_xlabel("时间点")
        ax_right.set_ylabel("内存 (MB)")
        ax_right.grid(alpha=0.3)
        all_history_ys = []
        with lock:
            for idx, (pid, info) in enumerate(processes.items()):
                pid_history = [(i, h[2]) for i, h in enumerate(all_history) if h[0] == pid]
                if pid_history:
                    xs = [x for x, _ in pid_history]
                    ys = [y for _, y in pid_history]
                    smooth_x, smooth_y = smooth_curve(xs, ys, max_points=300)
                    color = COLORS[idx % len(COLORS)]
                    ax_right.plot(smooth_x, smooth_y, lw=1.5, color=color, label=f"{info['name']} ({pid})")
                    all_history_ys.extend(ys)
        if all_history_ys:
            y_min, y_max = min(all_history_ys), max(all_history_ys)
            padding = max((y_max - y_min) * 0.12, 1) if y_max != y_min else max(y_max * 0.05, 1)
            ax_right.set_ylim(max(0, y_min - padding), y_max + padding)
        ax_right.legend(loc='upper left', fontsize=6)

        canvas.draw()
        return []

# 加载配置
    load_config()
    
    # 启动动画
    ani = animation.FuncAnimation(fig, update_chart, interval=REFRESH, blit=False, cache_frame_data=False)
    root.mainloop()
    print("\n✅ 监控已结束")