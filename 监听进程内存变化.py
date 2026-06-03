import os
import csv
import time
import psutil
import threading
import queue
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
CSV_FILE = "data.csv"
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

def add_process_ui():
    pid_str = simpledialog.askstring("添加进程", "请输入进程PID:")
    if not pid_str:
        return
    try:
        pid = int(pid_str)
    except ValueError:
        messagebox.showerror("错误", "PID必须是数字")
        return
    
    with lock:
        if pid in processes:
            messagebox.showwarning("警告", f"进程 {pid} 已在监控中")
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
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        messagebox.showerror("错误", f"无法访问进程 {pid}")

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
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["时间", "PID", "进程名", "内存(MB)"])

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
                with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([data['time'], pid, data['name'], mem])
            except queue.Empty:
                break

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

    ani = animation.FuncAnimation(fig, update_chart, interval=REFRESH, blit=False, cache_frame_data=False)
    root.mainloop()
    print("\n✅ 监控已结束")