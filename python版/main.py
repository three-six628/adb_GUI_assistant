import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import shlex
import threading
import queue
import time
import tempfile
from datetime import datetime

# Windows 隐藏控制台窗口（彻底消除黑框）
def get_hidden_startupinfo():
    """返回 STARTUPINFO，用于隐藏子进程控制台窗口"""
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo
    return None

# 尝试导入剪贴板与图像处理库，否则尝试pip安装（Windows 专用）

try:
    import win32clipboard
    from PIL import Image, ImageTk
    HAS_CLIPBOARD_IMAGE = True
    HAS_PIL = True
except ImportError:
    subprocess.Popen(
    'pip install pywin32 Pillow',
    shell=True,
    startupinfo=get_hidden_startupinfo()
)
    HAS_CLIPBOARD_IMAGE = False
    HAS_PIL = False
    try:
        import win32clipboard
        from PIL import Image, ImageTk
        HAS_CLIPBOARD_IMAGE = True
        HAS_PIL = True
    except ImportError:
        pass


class AdbHelper:
    """ADB 命令静态工具类（所有子进程均隐藏窗口）"""
    
    @staticmethod
    def execute_command(cmd, device=None):
        full_cmd = ['adb']
        if device:
            full_cmd.extend(['-s', device])
        if isinstance(cmd, str):
            full_cmd.append(cmd)
        else:
            full_cmd.extend(cmd)
        try:
            startupinfo = get_hidden_startupinfo()
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                check=False,
                startupinfo=startupinfo
            )
            return result.stdout, result.stderr, result.returncode
        except FileNotFoundError:
            return '', 'adb 未找到，请确保 adb 已安装并加入环境变量', -1

    @staticmethod
    def get_devices():
        stdout, stderr, rc = AdbHelper.execute_command('devices')
        devices = []
        if rc == 0:
            lines = stdout.strip().split('\n')
            for line in lines[1:]:
                if line.strip() and '\tdevice' in line:
                    serial, state = line.split('\t')
                    devices.append((serial, state))
        return devices

    @staticmethod
    def connect_device(ip_port):
        return AdbHelper.execute_command(f'connect {ip_port}')

    @staticmethod
    def install_app(device, apk_path, options):
        cmd = ['install'] + options + [apk_path]
        return AdbHelper.execute_command(cmd, device)

    @staticmethod
    def push_file(device, local, remote):
        return AdbHelper.execute_command(['push', local, remote], device)

    @staticmethod
    def pull_file(device, remote, local):
        return AdbHelper.execute_command(['pull', remote, local], device)

    @staticmethod
    def shell_command(device, command):
        """单次 adb shell 命令（隐藏窗口），作为持久化会话的降级方案"""
        return AdbHelper.execute_command(['shell', command], device)


def unescape_ls_filename(name):
    """还原 adb shell ls 输出的转义文件名（如空格转义）"""
    if not name:
        return name
    is_dir = name.endswith('/')
    raw = name.rstrip('/')
    unescaped = raw.replace(r'\ ', ' ')
    if is_dir:
        unescaped += '/'
    return unescaped


def setup_modern_style(root):
    style = ttk.Style(root)
    available_themes = style.theme_names()
    if 'vista' in available_themes:
        style.theme_use('vista')
    elif 'xpnative' in available_themes:
        style.theme_use('xpnative')
    elif 'clam' in available_themes:
        style.theme_use('clam')
    else:
        style.theme_use('default')

    if sys.platform == 'win32':
        default_font = ('微软雅黑', 9)
        fixed_font = ('Consolas', 10)
    else:
        default_font = ('TkDefaultFont', 9)
        fixed_font = ('Monospace', 10)
    
    style.configure('.', font=default_font)
    style.configure('TButton', padding=6, relief='flat', background='#f0f0f0')
    style.configure('TEntry', padding=4)
    style.configure('TLabel', padding=2)
    style.configure('TNotebook.Tab', padding=[10, 2])
    style.map('TButton',
              background=[('active', '#e5f1fb'), ('pressed', '#cce4f7')],
              relief=[('pressed', 'sunken'), ('!pressed', 'flat')])
    
    style.fixed_font = fixed_font
    return style


class AdbGuiApp:
    def __init__(self, root):
        self.root = root
        self.root.title('ADB 图形工具 - 设备列表')
        self.root.geometry('650x450')
        self.style = setup_modern_style(root)
        self.devices = []
        self.selected_device = None

        self.setup_ui()
        self.check_adb()
        self.refresh_devices()

    def check_adb(self):
        stdout, stderr, rc = AdbHelper.execute_command('version')
        if rc != 0:
            messagebox.showerror('错误', f'ADB 不可用:\n{stderr}')
            self.root.destroy()
            sys.exit(1)

    def setup_ui(self):
        top_frame = ttk.Frame(self.root, padding=5)
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text='刷新设备', command=self.refresh_devices).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text='连接 WiFi 设备', command=self.connect_wifi).pack(side=tk.LEFT, padx=2)

        tree_frame = ttk.Frame(self.root, padding=5)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('serial', 'state'), show='headings', height=12)
        self.tree.heading('serial', text='设备序列号')
        self.tree.heading('state', text='状态')
        self.tree.column('serial', width=350)
        self.tree.column('state', width=100)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Double-1>', self.on_device_double_click)

        bottom_frame = ttk.Frame(self.root, padding=5)
        bottom_frame.pack(fill=tk.X)
        ttk.Button(bottom_frame, text='打开设备', command=self.open_device).pack(side=tk.RIGHT, padx=2)

    def refresh_devices(self):
        self.devices = AdbHelper.get_devices()
        self.tree.delete(*self.tree.get_children())
        for serial, state in self.devices:
            self.tree.insert('', tk.END, values=(serial, state))

    def connect_wifi(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('连接 WiFi 设备')
        dialog.geometry('320x150')
        dialog.resizable(False, False)
        ttk.Label(dialog, text='IP地址:端口').pack(pady=(15,5))
        entry = ttk.Entry(dialog, width=25)
        entry.pack(pady=5)
        entry.focus_set()

        def do_connect():
            ip_port = entry.get().strip()
            if not ip_port:
                messagebox.showwarning('警告', '请输入 IP:端口')
                return
            stdout, stderr, rc = AdbHelper.connect_device(ip_port)
            if rc == 0 and 'connected' in stdout.lower():
                messagebox.showinfo('成功', f'连接成功: {stdout}')
                self.refresh_devices()
                dialog.destroy()
            else:
                messagebox.showerror('失败', f'连接失败: {stderr or stdout}')
        ttk.Button(dialog, text='连接', command=do_connect).pack(pady=(10,5))

    def on_device_double_click(self, event):
        self.open_device()

    def open_device(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning('警告', '请先选择一个设备')
            return
        item = self.tree.item(selection[0])
        serial = item['values'][0]
        self.selected_device = serial
        DeviceOperationWindow(self.root, serial, self.style)


class PersistentShell:
    """
    持久化 ADB Shell 管理器
    - 同步命令：优先使用 adb exec-out（速度快，无回显），失败时自动降级为 adb shell（兼容所有版本）
    - 异步交互：使用 adb shell 管道（保留交互式体验）
    - 所有子进程均隐藏控制台窗口
    """
    
    def __init__(self, device_serial):
        self.device_serial = device_serial
        self.async_process = None   # 用于交互的 adb shell 进程
        self.output_queue = queue.Queue()
        self._async_alive = False
        self._start_async_session()
        self._poll_async_output()

    def _start_async_session(self):
        """启动交互式 adb shell 进程（隐藏窗口）"""
        try:
            startupinfo = get_hidden_startupinfo()
            self.async_process = subprocess.Popen(
                ['adb', '-s', self.device_serial, 'shell'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                startupinfo=startupinfo
            )
            self._async_alive = True
        except Exception as e:
            print(f"[PersistentShell] 启动交互式 shell 失败: {e}")
            self.async_process = None
            self._async_alive = False

    def _poll_async_output(self):
        """后台线程读取交互式 shell 的标准输出"""
        if not self._async_alive or not self.async_process:
            return

        def reader():
            while self._async_alive and self.async_process.poll() is None:
                try:
                    line = self.async_process.stdout.readline()
                    if line:
                        self.output_queue.put(line)
                except:
                    break
            self._async_alive = False

        threading.Thread(target=reader, daemon=True).start()

    def execute_sync(self, command, timeout=15):
        """
        同步执行命令，返回 (stdout, stderr)
        优先使用 adb exec-out（高效），若不支持则自动降级为 adb shell（兼容）
        """
        startupinfo = get_hidden_startupinfo()

        # 尝试1：adb exec-out
        exec_cmd = ['adb', '-s', self.device_serial, 'exec-out', command]
        try:
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                startupinfo=startupinfo
            )
            if result.returncode == 0:
                # exec-out 成功，直接返回
                return result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        # 尝试2：adb shell
        shell_cmd = ['adb', '-s', self.device_serial, 'shell', command]
        try:
            result = subprocess.run(
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                startupinfo=startupinfo
            )
            return result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return '', f'命令执行超时（{timeout}秒）'
        except Exception as e:
            return '', str(e)

    def execute_async(self, command):
        """异步发送命令（用于交互式终端）"""
        if not self._async_alive or not self.async_process or self.async_process.poll() is not None:
            self._start_async_session()
            if not self._async_alive:
                return False
        try:
            self.async_process.stdin.write(command + '\n')
            self.async_process.stdin.flush()
            return True
        except:
            self._async_alive = False
            return False

    def get_async_output_line(self):
        """非阻塞获取一行异步输出"""
        try:
            return self.output_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        self._async_alive = False
        if self.async_process:
            try:
                self.async_process.terminate()
            except:
                pass
            self.async_process = None


class ScreenshotPreviewWindow(tk.Toplevel):
    """截图预览窗口，支持保存到目录与复制图像到剪贴板（使用PIL精确缩放，保证图片完整可见）"""
    def __init__(self, parent, image_path, style=None):
        super().__init__(parent)
        self.title('截图预览')
        self.geometry('800x600')
        self.style = style
        self.image_path = image_path
        self.pil_image = None      # 原始 PIL Image 对象
        self.photo_image = None    # 当前显示的 PhotoImage
        self.canvas_image_id = None

        # 窗口关闭时自动删除临时文件
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # 加载图片（使用PIL，若不可用则回退）
        self.load_image()

        # 创建界面
        self.setup_ui()

        # 绑定窗口大小变化事件
        self.bind('<Configure>', self.on_resize)

        # 初始显示图片（等窗口布局完成后）
        self.after(100, self.display_image)

    def load_image(self):
        """加载原始图片，优先使用PIL以获得精确缩放能力"""
        if HAS_PIL:
            try:
                self.pil_image = Image.open(self.image_path)
                self.orig_width, self.orig_height = self.pil_image.size
            except Exception as e:
                messagebox.showerror('错误', f'无法加载截图文件:\n{e}')
                self.destroy()
        else:
            # 降级：使用 tk.PhotoImage（不支持精确缩放，但能显示）
            try:
                self.photo_image = tk.PhotoImage(file=self.image_path)
                self.orig_width = self.photo_image.width()
                self.orig_height = self.photo_image.height()
            except Exception as e:
                messagebox.showerror('错误', f'无法加载截图文件:\n{e}')
                self.destroy()

    def setup_ui(self):
        # 图片显示区域（Canvas 便于居中）
        self.canvas = tk.Canvas(self, bg='gray', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 按钮框架
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(btn_frame, text='保存到...', command=self.save_image).pack(side=tk.LEFT, padx=5)
        self.copy_btn = ttk.Button(btn_frame, text='复制到剪贴板', command=self.copy_image)
        self.copy_btn.pack(side=tk.LEFT, padx=5)

        if not HAS_CLIPBOARD_IMAGE:
            self.copy_btn.config(state=tk.DISABLED)
            self.copy_btn.config(text='复制（需pywin32+PIL）')

        ttk.Button(btn_frame, text='关闭', command=self.on_close).pack(side=tk.RIGHT, padx=5)

    def display_image(self):
        """根据当前画布尺寸精确缩放图片并居中显示"""
        if not self.pil_image and not self.photo_image:
            return

        # 获取画布实际尺寸（考虑边框内边距）
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        # 如果画布尚未布局完成，使用窗口尺寸估算
        if canvas_width <= 1 or canvas_height <= 1:
            canvas_width = self.winfo_width() - 20
            canvas_height = self.winfo_height() - 80
        if canvas_width <= 1:
            canvas_width = 800
            canvas_height = 600

        # 计算缩放比例（始终缩放到适合画布，不放大）
        if HAS_PIL and self.pil_image:
            # --- 使用 PIL 精确缩放 ---
            ratio = min(canvas_width / self.orig_width, canvas_height / self.orig_height)
            ratio = min(ratio, 1.0)  # 不放大
            new_width = int(self.orig_width * ratio)
            new_height = int(self.orig_height * ratio)

            # 高质量缩放
            resized = self.pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.photo_image = ImageTk.PhotoImage(resized)
        else:
            # --- 降级：使用 PhotoImage 的 subsample/zoom（仅支持整数倍）---
            # 计算整数倍缩小因子（取整）
            x_factor = max(1, int(self.orig_width / canvas_width))
            y_factor = max(1, int(self.orig_height / canvas_height))
            factor = max(x_factor, y_factor)
            if factor > 1:
                self.photo_image = self.photo_image.subsample(factor, factor)
            # 若图片比画布小，不放大

        # 清除画布原有内容，显示新图片（居中）
        self.canvas.delete('all')
        x_center = canvas_width // 2
        y_center = canvas_height // 2
        self.canvas_image_id = self.canvas.create_image(
            x_center, y_center,
            image=self.photo_image,
            anchor=tk.CENTER
        )

    def on_resize(self, event):
        """窗口大小变化时重新缩放图片（防抖）"""
        if hasattr(self, '_resize_job'):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(200, self.display_image)

    def save_image(self):
        """将临时图片保存到用户指定位置"""
        filename = filedialog.asksaveasfilename(
            defaultextension='.png',
            filetypes=[('PNG files', '*.png'), ('All files', '*.*')],
            initialfile=os.path.basename(self.image_path)
        )
        if filename:
            try:
                import shutil
                shutil.copy2(self.image_path, filename)
                messagebox.showinfo('成功', f'截图已保存到:\n{filename}')
            except Exception as e:
                messagebox.showerror('错误', f'保存失败:\n{e}')

    def copy_image(self):
        """将图像数据复制到剪贴板（Windows 专用）"""
        if not HAS_CLIPBOARD_IMAGE:
            messagebox.showwarning('提示', '当前系统缺少 pywin32 或 Pillow，无法复制图像数据。\n请安装: pip install pywin32 Pillow')
            return

        try:
            # 使用 PIL 打开图像
            img = Image.open(self.image_path)
            # 转换为 BMP 格式（Windows 剪贴板常用）
            import io
            output = io.BytesIO()
            img.convert('RGB').save(output, format='BMP')
            data = output.getvalue()[14:]  # 去掉 BMP 文件头（14字节）
            output.close()

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
            messagebox.showinfo('成功', '截图已复制到剪贴板，可直接粘贴')
        except Exception as e:
            messagebox.showerror('错误', f'复制到剪贴板失败:\n{e}')

    def on_close(self):
        """关闭窗口，删除临时图片文件"""
        try:
            if os.path.exists(self.image_path):
                os.remove(self.image_path)
        except Exception:
            pass
        self.destroy()


class DeviceOperationWindow(tk.Toplevel):
    def __init__(self, parent, device_serial, style):
        super().__init__(parent)
        self.title(f'设备操作 - {device_serial}')
        self.geometry('750x600')
        self.device_serial = device_serial
        self.style = style

        # 创建持久化 shell 管理器
        self.shell = PersistentShell(device_serial)

        self.setup_ui()
        self._poll_async_output()

    def _poll_async_output(self):
        """轮询显示交互式命令的输出"""
        if hasattr(self, 'shell_output'):
            line = self.shell.get_async_output_line()
            if line:
                self.shell_output.insert(tk.END, line)
                self.shell_output.see(tk.END)
        self.after(100, self._poll_async_output)

    # UI 初始化
    def setup_ui(self):
        notebook = ttk.Notebook(self, padding=3)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        install_frame = ttk.Frame(notebook, padding=10)
        notebook.add(install_frame, text='安装应用')
        self.setup_install_tab(install_frame)

        export_file_frame = ttk.Frame(notebook, padding=10)
        notebook.add(export_file_frame, text='导出文件')
        self.setup_export_file_tab(export_file_frame)

        export_app_frame = ttk.Frame(notebook, padding=10)
        notebook.add(export_app_frame, text='导出应用')
        self.setup_export_app_tab(export_app_frame)

        shell_frame = ttk.Frame(notebook, padding=10)
        notebook.add(shell_frame, text='远程命令')
        self.setup_shell_tab(shell_frame)

        other_frame = ttk.Frame(notebook, padding=10)
        notebook.add(other_frame, text='其他工具')
        self.setup_other_tab(other_frame)

    # 安装应用
    def setup_install_tab(self, parent):
        path_frame = ttk.Frame(parent)
        path_frame.pack(fill=tk.X, pady=(0,10))
        ttk.Label(path_frame, text='APK路径:').pack(side=tk.LEFT)
        self.apk_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.apk_path_var, width=60).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(path_frame, text='浏览', command=self.browse_apk).pack(side=tk.LEFT)

        options_frame = ttk.LabelFrame(parent, text='安装选项', padding=8)
        options_frame.pack(fill=tk.X, pady=5)
        self.install_options = {}
        option_items = [
            ('授予所有运行时权限', '-g'),
            ('允许测试包', '-t'),
            ('重新安装并保留数据', '-r'),
            ('允许降级安装', '-d')
        ]
        for i, (desc, cmd) in enumerate(option_items):
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(options_frame, text=f'{desc}（{cmd}）', variable=var)
            cb.grid(row=0, column=i, sticky=tk.W, padx=10)
            self.install_options[cmd] = var

        ttk.Button(parent, text='安装', command=self.install_apk).pack(pady=15)

    def browse_apk(self):
        filename = filedialog.askopenfilename(filetypes=[('APK files', '*.apk'), ('All files', '*.*')])
        if filename:
            self.apk_path_var.set(filename)

    def install_apk(self):
        apk_path = self.apk_path_var.get().strip()
        if not apk_path:
            messagebox.showwarning('警告', '请选择APK文件')
            return
        if not os.path.isfile(apk_path):
            messagebox.showerror('错误', 'APK文件不存在')
            return
        options = [cmd for cmd, var in self.install_options.items() if var.get()]

        stdout, stderr, rc = AdbHelper.install_app(self.device_serial, apk_path, options)
        if rc == 0:
            messagebox.showinfo('成功', '应用安装成功')
        else:
            if messagebox.askyesno('安装失败', '直接安装失败，是否尝试分步安装（push + pm install）？'):
                self.step_install(apk_path, options)

    def step_install(self, apk_path, options):
        remote_temp = '/data/local/tmp/temp_install.apk'
        stdout, stderr, rc = AdbHelper.push_file(self.device_serial, apk_path, remote_temp)
        if rc != 0:
            messagebox.showerror('失败', f'推送文件失败: {stderr}')
            return
        opt_str = ' '.join(options)
        pm_cmd = f'pm install {opt_str} {remote_temp}'
        stdout, stderr = self.shell.execute_sync(pm_cmd)
        self.shell.execute_sync(f'rm {remote_temp}')
        if 'Success' in stdout:
            messagebox.showinfo('成功', '应用安装成功（分步安装）')
        else:
            messagebox.showerror('失败', f'分步安装失败: {stderr or stdout}')

    # 导出文件
    def setup_export_file_tab(self, parent):
        self.current_path = '/sdcard'
        addr_frame = ttk.Frame(parent)
        addr_frame.pack(fill=tk.X, pady=(0,10))
        ttk.Label(addr_frame, text='路径:').pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.current_path)
        ttk.Entry(addr_frame, textvariable=self.path_var, width=70).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(addr_frame, text='前往', command=self.goto_path).pack(side=tk.LEFT)

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.file_tree = ttk.Treeview(list_frame, columns=('name', 'type', 'size'), show='headings', height=18)
        self.file_tree.heading('name', text='名称')
        self.file_tree.heading('type', text='类型')
        self.file_tree.heading('size', text='大小')
        self.file_tree.column('name', width=300)
        self.file_tree.column('type', width=80)
        self.file_tree.column('size', width=100)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_tree.configure(selectmode='extended')
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_tree.configure(yscrollcommand=scrollbar.set)
        self.file_tree.bind('<Double-1>', self.on_file_double_click)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(10,0))
        ttk.Button(btn_frame, text='返回上级', command=self.go_parent).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text='导出选中文件', command=self.export_selected_file).pack(side=tk.RIGHT, padx=2)

        self.load_file_list()

    def load_file_list(self):
        self.file_tree.delete(*self.file_tree.get_children())
        
        # 使用兼容的同步执行方法（自动降级）
        stdout, stderr = self.shell.execute_sync(f'ls -1p {shlex.quote(self.current_path)}')
        if not stdout:
            # 降级尝试 ls -p（兼容旧设备）
            stdout, stderr = self.shell.execute_sync(f'ls -p {shlex.quote(self.current_path)}')

        if not stdout:
            error_msg = stderr.strip() if stderr else '未知错误'
            messagebox.showerror('错误', f'无法读取目录:\n{error_msg}')
            return

        items = [item for item in stdout.strip().split('\n') if item]

        for raw_name in items:
            if not raw_name:
                continue
            real_name = unescape_ls_filename(raw_name)
            is_dir = real_name.endswith('/')
            display_name = real_name.rstrip('/')
            file_type = '文件夹' if is_dir else '文件'
            self.file_tree.insert('', tk.END, values=(display_name, file_type, ''))

    def goto_path(self):
        path = self.path_var.get().strip()
        if path:
            self.current_path = path
            self.load_file_list()

    def on_file_double_click(self, event):
        selection = self.file_tree.selection()
        if not selection:
            return
        item = self.file_tree.item(selection[0])
        name, file_type, _ = item['values']
        if file_type == '文件夹':
            self.current_path = os.path.join(self.current_path, name).replace('\\', '/')
            self.path_var.set(self.current_path)
            self.load_file_list()

    def go_parent(self):
        parent = os.path.dirname(self.current_path)
        if parent:
            self.current_path = parent
            self.path_var.set(self.current_path)
            self.load_file_list()

    def export_selected_file(self):
        selections = self.file_tree.selection()
        if not selections:
            messagebox.showwarning('警告', '请选择要导出的文件')
            return
        for sel in selections:
            item = self.file_tree.item(sel)
            name, file_type, _ = item['values']
            if file_type == '文件夹':
                messagebox.showwarning('警告', f'跳过文件夹: {name}，导出文件夹请压缩')
                continue
            remote_path = os.path.join(self.current_path, name).replace('\\', '/')
            local_dir = filedialog.askdirectory(title=f'选择保存目录 - {name}')
            if local_dir:
                local_path = os.path.join(local_dir, name)
                stdout, stderr, rc = AdbHelper.pull_file(self.device_serial, remote_path, local_path)
                if rc == 0:
                    messagebox.showinfo('成功', f'{name} 已导出到: {local_path}')
                else:
                    messagebox.showerror('失败', f'{name} 导出失败: {stderr}')

    # 导出应用（兼容旧版 ADB）
    def setup_export_app_tab(self, parent):
        self.show_system_var = tk.BooleanVar()
        ttk.Checkbutton(parent, text='显示系统应用', variable=self.show_system_var,
                        command=self.load_app_list).pack(anchor=tk.W, pady=(0,10))

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.app_tree = ttk.Treeview(list_frame, columns=('package', 'name'), show='headings', height=18)
        self.app_tree.heading('package', text='包名')
        self.app_tree.heading('name', text='应用名')
        self.app_tree.column('package', width=350)
        self.app_tree.column('name', width=200)
        self.app_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.app_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.app_tree.configure(yscrollcommand=scrollbar.set)

        export_frame = ttk.Frame(parent)
        export_frame.pack(fill=tk.X, pady=(10,0))
        ttk.Label(export_frame, text='导出位置:').pack(side=tk.LEFT)
        self.export_dir_var = tk.StringVar()
        ttk.Entry(export_frame, textvariable=self.export_dir_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(export_frame, text='浏览', command=self.browse_export_dir).pack(side=tk.LEFT)

        ttk.Button(parent, text='导出选中应用', command=self.export_selected_app).pack(pady=15)

        self.load_app_list()

    def load_app_list(self):
        show_system = self.show_system_var.get()
        cmd = 'pm list packages' if show_system else 'pm list packages -3'
        stdout, stderr = self.shell.execute_sync(cmd)
        if not stdout:
            messagebox.showerror('错误', f'获取应用列表失败: {stderr}')
            return
        packages = []
        for line in stdout.strip().split('\n'):
            if line.startswith('package:'):
                pkg = line[8:].strip()
                packages.append(pkg)
        self.app_tree.delete(*self.app_tree.get_children())
        for pkg in packages:
            self.app_tree.insert('', tk.END, values=(pkg, ''))

    def browse_export_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.export_dir_var.set(directory)

    def export_selected_app(self):
        selections = self.app_tree.selection()
        if not selections:
            messagebox.showwarning('警告', '请选择要导出的应用')
            return
        export_dir = self.export_dir_var.get().strip()
        if not export_dir:
            messagebox.showwarning('警告', '请选择导出位置')
            return
        if not os.path.isdir(export_dir):
            messagebox.showerror('错误', '导出目录不存在')
            return

        for sel in selections:
            item = self.app_tree.item(sel)
            pkg = item['values'][0]
            stdout, stderr = self.shell.execute_sync(f'pm path {pkg}')
            if not stdout:
                messagebox.showerror('错误', f'获取应用 {pkg} 路径失败: {stderr}')
                continue
            match = re.search(r'package:(.+)', stdout)
            if match:
                apk_path = match.group(1).strip()
                local_path = os.path.join(export_dir, f'{pkg}.apk')
                stdout, stderr, rc = AdbHelper.pull_file(self.device_serial, apk_path, local_path)
                if rc == 0:
                    messagebox.showinfo('成功', f'{pkg} 已导出到: {local_path}')
                else:
                    messagebox.showerror('失败', f'{pkg} 导出失败: {stderr}')
            else:
                messagebox.showerror('错误', f'无法解析应用 {pkg} 的路径')

    # 远程命令（异步交互）
    def setup_shell_tab(self, parent):
        output_frame = ttk.Frame(parent)
        output_frame.pack(fill=tk.BOTH, expand=True)
        self.shell_output = tk.Text(output_frame, wrap=tk.WORD, height=20, width=80,
                                    font=self.style.fixed_font)
        self.shell_output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(output_frame, orient=tk.VERTICAL, command=self.shell_output.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.shell_output.configure(yscrollcommand=scrollbar.set)

        input_frame = ttk.Frame(parent)
        input_frame.pack(fill=tk.X, pady=(10,0))
        self.shell_input_var = tk.StringVar()
        entry = ttk.Entry(input_frame, textvariable=self.shell_input_var, width=70)
        entry.pack(side=tk.LEFT, padx=(0,5), fill=tk.X, expand=True)
        entry.bind('<Return>', self.send_shell_command)
        ttk.Button(input_frame, text='发送', command=self.send_shell_command).pack(side=tk.LEFT)

    def send_shell_command(self, event=None):
        command = self.shell_input_var.get().strip()
        if not command:
            return
        self.shell_output.insert(tk.END, f'$ {command}\n')
        self.shell_output.see(tk.END)
        self.shell_input_var.set('')

        if not self.shell.execute_async(command):
            # 降级：使用单次命令
            stdout, stderr, _ = AdbHelper.shell_command(self.device_serial, command)
            if stdout:
                self.shell_output.insert(tk.END, stdout)
            if stderr:
                self.shell_output.insert(tk.END, f'Error: {stderr}')
            self.shell_output.insert(tk.END, '\n')
            self.shell_output.see(tk.END)

    # 其他工具
    def setup_other_tab(self, parent):
        ttk.Button(parent, text='截屏', command=self.take_screenshot).pack(pady=5)
        ttk.Button(parent, text='重启设备', command=self.reboot_device).pack(pady=5)
        ttk.Button(parent, text='设备信息', command=self.show_device_info).pack(pady=5)

    def take_screenshot(self):
        """截图：先保存到临时目录，再打开预览窗口"""
        # 1. 设备上截图
        remote_path = '/sdcard/screenshot_temp.png'
        stdout, stderr, rc = AdbHelper.shell_command(self.device_serial, f'screencap -p {remote_path}')
        if rc != 0:
            messagebox.showerror('错误', f'截图失败: {stderr}')
            return

        # 2. 创建本地临时目录
        temp_dir = os.path.join(tempfile.gettempdir(), 'adb_gui_screenshots')
        os.makedirs(temp_dir, exist_ok=True)

        # 3. 生成带时间戳的文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        local_temp = os.path.join(temp_dir, f'screenshot_{timestamp}.png')

        # 4. 拉取到本地临时文件
        stdout, stderr, rc = AdbHelper.pull_file(self.device_serial, remote_path, local_temp)
        # 无论拉取是否成功，都删除设备上的临时文件
        AdbHelper.shell_command(self.device_serial, f'rm {remote_path}')

        if rc != 0:
            messagebox.showerror('错误', f'拉取截图失败: {stderr}')
            return

        # 5. 打开预览窗口
        ScreenshotPreviewWindow(self, local_temp, self.style)

    def reboot_device(self):
        if messagebox.askyesno('请慎重考虑', '确定要重启设备吗？'):
            stdout, stderr, rc = AdbHelper.execute_command('reboot', self.device_serial)
            if rc == 0:
                messagebox.showinfo('成功', '重启命令已发送')
            else:
                messagebox.showerror('失败', f'重启失败: {stderr}')

    def show_device_info(self):
        info_win = tk.Toplevel(self)
        info_win.title('设备信息')
        info_win.geometry('650x450')
        
        frame = ttk.Frame(info_win)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        text = tk.Text(frame, wrap=tk.WORD, font=self.style.fixed_font)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=scrollbar.set)
        
        stdout, stderr = self.shell.execute_sync('getprop')
        if stdout:
            text.insert(tk.END, stdout)
        else:
            text.insert(tk.END, f'获取信息失败: {stderr}')
        
        text.configure(state=tk.DISABLED)

    def destroy(self):
        """关闭窗口时关闭持久化 shell"""
        if hasattr(self, 'shell'):
            self.shell.close()
        super().destroy()


def main():
    root = tk.Tk()
    app = AdbGuiApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()