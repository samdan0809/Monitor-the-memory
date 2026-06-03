import os
import csv
import time
import psutil
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
from datetime import datetime

# ===================== 配置 =====================
CSV_FILE = "data.csv"
MAX_DISPLAY = 150  # 左侧实时曲线点数
REFRESH = 1000     # 1秒刷新

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# ===================== 启动菜单 =====================
print("=" * 55)
print("        进程内存监控｜双图实时显示")
print("=" * 55)

full_data = []

# 加载历史数据
if os.path.exists(CSV_FILE):
    while True:
        opt = input("\n检测到 data.csv\n【1】新建监控（清空旧数据）\n【2】加载历史继续监控\n请选择 1/2：")
        if opt == "1":
            os.remove(CSV_FILE)
            with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["时间", "内存(MB)"])
            print("✅ 已清空，新建记录")
            break
        elif opt == "2":
            try:
                with open(CSV_FILE, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    next(reader)
                    for row in reader:
                        if len(row) == 2:
                            t, val = row[0], float(row[1])
                            full_data.append((t, val))
                print(f"✅ 加载历史记录：{len(full_data)} 条")
                break
            except:
                print("❌ 读取失败，自动新建文件")
                break
else:
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["时间", "内存(MB)"])
    print("✅ 无历史文件，已自动创建")

# ===================== 输入PID =====================
while True:
    try:
        TARGET_PID = int(input("\n请输入进程 PID："))
        p = psutil.Process(TARGET_PID)
        print(f"✅ 已绑定进程：{p.name()}")
        break
    except:
        print("❌ PID 无效，请重新输入")

# 左侧实时曲线缓冲区
display_mem = deque(maxlen=MAX_DISPLAY)
if full_data:
    for _, v in full_data[-MAX_DISPLAY:]:
        display_mem.append(v)

start_time = time.time()

# ===================== 曲线平滑函数 =====================
def smooth_curve(x_values, y_values, max_points=300):
    """
    平滑曲线，保留最高点和最低点，中间值做降采样
    :param x_values: x坐标列表
    :param y_values: y坐标列表
    :param max_points: 最大保留点数
    :return: 平滑后的 (xs, ys)
    """
    if len(y_values) <= max_points:
        return x_values, y_values
    
    # 1. 识别所有局部极值点
    key_indices = {0, len(y_values) - 1}  # 保留首尾
    
    for i in range(1, len(y_values) - 1):
        prev_val = y_values[i-1]
        curr_val = y_values[i]
        next_val = y_values[i+1]
        # 局部最高点或最低点
        if (curr_val > prev_val and curr_val > next_val) or (curr_val < prev_val and curr_val < next_val):
            key_indices.add(i)
    
    # 2. 如果极值点太多，进行二次筛选
    key_list = sorted(key_indices)
    if len(key_list) > max_points:
        # 均匀采样极值点
        step = len(key_list) // max_points
        key_list = key_list[::step]
    
    # 3. 如果极值点太少，补充一些点
    if len(key_list) < max_points // 2:
        # 在极值点之间均匀补充点
        new_key_list = [key_list[0]]
        for i in range(1, len(key_list)):
            prev = key_list[i-1]
            curr = key_list[i]
            gap = curr - prev
            if gap > 10:  # 间隔太大时补充点
                num_insert = min(gap // 5, 3)
                for j in range(1, num_insert + 1):
                    new_key_list.append(prev + (gap * j) // (num_insert + 1))
            new_key_list.append(curr)
        key_list = new_key_list
    
    # 4. 确保不超过最大点数
    if len(key_list) > max_points:
        step = len(key_list) // max_points
        key_list = key_list[::step]
    
    # 5. 提取结果
    key_list = sorted(set(key_list))  # 去重并排序
    smooth_x = [x_values[i] for i in key_list]
    smooth_y = [y_values[i] for i in key_list]
    
    return smooth_x, smooth_y


# ===================== 双图布局 =====================
fig = plt.figure(figsize=(15, 6))

# 左图：实时内存（最近150秒）
ax_left = fig.add_subplot(1, 2, 1)
ax_left.set_title(f"📈 实时内存（最近 {MAX_DISPLAY} 秒）", fontsize=13)
ax_left.set_xlabel("时间点")
ax_left.set_ylabel("内存占用 (MB)")
ax_left.grid(alpha=0.3)
line_left, = ax_left.plot([], [], lw=2.5, color="#3498db")

# 右图：完整历史内存（全量）
ax_right = fig.add_subplot(1, 2, 2)
ax_right.set_title("📊 完整内存历史曲线（平滑）", fontsize=13)
ax_right.set_xlabel("总时间轴（秒）")
ax_right.set_ylabel("内存占用 (MB)")
ax_right.grid(alpha=0.3)
line_right, = ax_right.plot([], [], lw=1.6, color="#e74c3c")

# ===================== 获取内存数据 =====================
def get_memory():
    try:
        mem_mb = round(p.memory_info().rss / 1024 / 1024, 2)
        now = datetime.now().strftime("%H:%M:%S")
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now, mem_mb])
        full_data.append((now, mem_mb))
        return mem_mb
    except:
        return None

# ===================== 坐标轴动态缩放 =====================
def update_axis_limits(ax, x_values, y_values):
    """按当前绘制的数据重算坐标轴，允许 Y 轴随内存下降一起缩小。"""
    if not y_values:
        return

    x_min = min(x_values) if x_values else 0
    x_max = max(x_values) if x_values else 1
    if x_min == x_max:
        x_max = x_min + 1
    ax.set_xlim(x_min, x_max)

    y_min = min(y_values)
    y_max = max(y_values)
    if y_min == y_max:
        padding = max(y_max * 0.05, 1)
    else:
        padding = max((y_max - y_min) * 0.12, 1)
    ax.set_ylim(max(0, y_min - padding), y_max + padding)


# ===================== 双图实时更新 =====================
def update(frame):
    mem = get_memory()
    if mem is not None:
        display_mem.append(mem)

    # ---------- 更新左图：实时曲线 ----------
    left_xs = list(range(len(display_mem)))
    left_ys = list(display_mem)
    line_left.set_data(left_xs, left_ys)
    update_axis_limits(ax_left, left_xs, left_ys)

    # ---------- 更新右图：完整历史（平滑处理） ----------
    right_xs = list(range(len(full_data)))
    right_ys = [v for _, v in full_data]
    
    # 对数据进行平滑，保留最高点和最低点
    smooth_x, smooth_y = smooth_curve(right_xs, right_ys, max_points=300)
    line_right.set_data(smooth_x, smooth_y)
    update_axis_limits(ax_right, right_xs, right_ys)

    return line_left, line_right

# ===================== 启动动画 =====================
ani = animation.FuncAnimation(
    fig, update, interval=REFRESH, blit=False, cache_frame_data=False
)

plt.suptitle(f"监控进程：{p.name()} (PID:{TARGET_PID})", fontsize=14)
plt.tight_layout()
print("\n▶  监控已启动，关闭窗口停止")
plt.show()

# ===================== 退出选择：删/留文件 =====================
if os.path.exists(CSV_FILE):
    print("\n========== 退出操作 ==========")
    while True:
        choice = input("【1】保留 data.csv（下次可加载）\n【2】删除 data.csv\n请选择 1/2：")
        if choice == "1":
            print("💾 data.csv 已保留")
            break
        elif choice == "2":
            os.remove(CSV_FILE)
            print("🗑️ data.csv 已删除")
            break
        else:
            print("❌ 请输入 1 或 2")

print("\n✅ 监控已结束")