import base64
import io
import math
import re
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox
from search import search_games
from bs4 import BeautifulSoup
from PIL import Image, ImageTk, ImageFilter, ImageOps
import requests
from bs4 import BeautifulSoup
import winreg


ALLOWED_SUFFIXES: Iterable[str] = (".lua", ".manifest", ".json", ".vdf")
NODE_TIMEOUT = 1
SEGMENT_MIN_BYTES = 2 * 1024 * 1024
SEGMENT_WORKERS = 4
SEGMENT_CHUNK_SIZE = 8192
STEAM_ENV_KEYS = ("STEAM_PATH", "SteamPath", "STEAMPATH")
DEFAULT_STEAM_PATHS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\Program Files (x86)\Steam",
)
OFFICIAL_SITE_URL = "https://github.com/ecxwxz/steamtoolsmanager"
BACKGROUND_IMAGE_PATH = Path("./background.png")
BACKGROUND_BLUR_RADIUS = 12
BACKGROUND_OPACITY = 0.35
BACKGROUND_BASE_COLOR = "#f0f0f0"


class SteamManifestDownloader:
    """UI shell showing the layout without backend functionality."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Steam Manifest 下载器 & 自动入库工具 作者: ecxwxz")
        self.root.iconbitmap(default="1.ico")
        self.root.resizable(True, True)
        self.settings = self.load_settings()
        self.download_source = tk.StringVar(
            value=self.settings.get("download_source", "domestic")
        )
        self.auto_import = tk.BooleanVar(
            value=self.settings.get("auto_import", False)
        )
        self.search_enhance = tk.BooleanVar(
            value=self.settings.get("search_enhance", False)
        )
        self.auto_import_status = tk.StringVar(
            value="开启" if self.auto_import.get() else "关闭"
        )
        self.search_enhance_status = tk.StringVar(
            value="开启" if self.search_enhance.get() else "关闭"
        )
        self.current_task: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.log_dir = os.path.join(os.getcwd(), "log")
        os.makedirs(self.log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = os.path.join(self.log_dir, f"{timestamp}.log")
        self.game_image_photo = None
        self.header_image_url = None
        self.background_label: tk.Label | None = None
        self.background_photo = None
        self.background_image_path = BACKGROUND_IMAGE_PATH
        self._background_size = (0, 0)
        self.progress_animating = False
        self.current_game_folder: Optional[str] = None
        self.search_results: list[dict[str, object]] = []
        self.search_dropdown: tk.Toplevel | None = None
        self.search_result_images: list[tk.PhotoImage] = []
        self._search_tree: ttk.Treeview | None = None
        self._search_image_task_id = 0
        self._prefetched_game: dict[str, object] | None = None
        self._search_in_progress = False
        self._image_request_id = 0
        self._setup_background()
        self.create_widgets()
        if self.background_label is not None:
            self.background_label.lower()
        self.root.update_idletasks()
        self._refresh_background_image()
        width = max(640, self.root.winfo_reqwidth())
        height = max(540, self.root.winfo_reqheight())
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, height)
        self.root.resizable(False, False)
        self.log_area.configure(state="disabled")
        # 在程序日志中显示更新信息（大字体标题 + 版本 + 简短说明）
        try:
            self._show_update_banner()
        except Exception:
            pass
        self.root.after(100, self._process_log_queue)

    def _setup_background(self):
        """Prepare a blurred background image if Pillow and the asset are available."""
        if any(module is None for module in (Image, ImageTk, ImageFilter, ImageOps)):
            return
        if not self.background_image_path.exists():
            return
        self.background_label = tk.Label(self.root, bd=0)
        self.background_label.place(x=0, y=0, relwidth=1, relheight=1)
        self.background_label.lower()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._refresh_background_image()

    def _refresh_background_image(self, width: int | None = None, height: int | None = None):
        if (
            self.background_label is None
            or any(module is None for module in (Image, ImageTk, ImageFilter, ImageOps))
        ):
            return
        if not self.background_image_path.exists():
            return
        if width is None or height is None:
            width = self.root.winfo_width() or self.root.winfo_reqwidth()
            height = self.root.winfo_height() or self.root.winfo_reqheight()
        if width <= 1 or height <= 1:
            return
        if (width, height) == self._background_size:
            return
        try:
            source = Image.open(self.background_image_path).convert("RGB")
        except OSError as exc:
            self.log(f"背景图片无法加载：{exc}")
            return
        fitted = ImageOps.fit(source, (width, height), method=Image.LANCZOS)
        blurred = fitted.filter(ImageFilter.GaussianBlur(BACKGROUND_BLUR_RADIUS))
        base = Image.new("RGB", (width, height), BACKGROUND_BASE_COLOR)
        blended = Image.blend(base, blurred, BACKGROUND_OPACITY)
        photo = ImageTk.PhotoImage(blended)
        self.background_label.configure(image=photo)
        self.background_photo = photo
        self._background_size = (width, height)

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        self._refresh_background_image(event.width, event.height)
        if self.background_label is not None:
            self.background_label.lower()

    def create_widgets(self):
        # 游戏名称搜索区域
        search_frame = ttk.LabelFrame(self.root, text="游戏名称搜索")
        search_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(search_frame, text="关键词:").pack(side="left", padx=(10, 5))
        self.search_entry = ttk.Entry(search_frame)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=5, pady=5)
        self.search_entry.bind("<Return>", lambda e: self.perform_search())

        self.search_btn = ttk.Button(
            search_frame,
            text="搜索",
            command=self.perform_search,
            width=10,
        )
        self.search_btn.pack(side="left", padx=5)

        # AppID 输入区域
        input_frame = ttk.Frame(self.root)
        input_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(input_frame, text="AppID:").pack(side="left")
        self.appid_entry = ttk.Entry(input_frame, width=20)
        self.appid_entry.pack(side="left", padx=5)
        self.appid_entry.bind("<Return>", lambda e: self.start_download())

        self.download_btn = ttk.Button(
            input_frame,
            text="开始下载",
            command=self.start_download,
        )
        self.download_btn.pack(side="left", padx=5)

        # 中间区域：左侧显示游戏信息，右侧显示日志
        content_frame = ttk.Frame(self.root)
        content_frame.pack(fill="both", expand=True, padx=10, pady=5)

        info_frame = ttk.LabelFrame(content_frame, text="游戏信息")
        info_frame.pack(side="left", fill="both", expand=True)

        self.game_name_var = tk.StringVar(value="游戏名称：未选择")
        ttk.Label(
            info_frame,
            textvariable=self.game_name_var,
            font=("Microsoft YaHei", 12, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.game_image = tk.Label(
            info_frame,
            text="图片预览",
            borderwidth=1,
            relief="sunken",
            width=40,
            height=12,
        )
        self.game_image.pack(fill="both", expand=True, padx=10, pady=5)

        self.progress_var = tk.DoubleVar(value=0)
        progress_frame = ttk.Frame(info_frame)
        progress_frame.pack(fill="x", padx=10, pady=(5, 10))
        ttk.Label(progress_frame, text="下载进度:").pack(side="left")
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # 日志区域独立在右侧
        log_frame = ttk.LabelFrame(content_frame, text="日志")
        log_frame.pack(side="right", fill="both", expand=False, padx=(10, 0))
        log_frame.configure(width=220)

        self.log_area = tk.Text(log_frame, wrap="word")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_area.yview)
        self.log_area.configure(yscrollcommand=scrollbar.set)
        self.log_area.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 底部按钮
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", padx=10, pady=5)


        ttk.Button(
            bottom_frame,
            text="打开下载目录",
            command=self.open_download_folder,
        ).pack(side="left")
        self.settings_btn = ttk.Button(
            bottom_frame,
            text="设置",
            command=self.open_settings_window,
            width=10,
        )
        self.settings_btn.pack(side="left", padx=5)
        ttk.Button(
            bottom_frame,
            text="官网",
            command=self.open_official_site,
            width=8,
        ).pack(side="left")
        ttk.Button(bottom_frame, text="退出", command=self.root.quit).pack(
            side="right"
        )

    def log(self, message: str):
        self.log_area.configure(state="normal")
        self.log_area.insert("end", message + "\n")
        self.log_area.see("end")
        self.log_area.configure(state="disabled")
        self.root.update_idletasks()
        self._write_log_file(message)

    def on_feature_disabled(self, feature_name: str):
        message = f"{feature_name} 该功能还没有开发，敬请期待！"
        self.log(message)
        messagebox.showinfo("功能不可用", message)

    def perform_search(self):
        term = self.search_entry.get().strip()
        if not term:
            messagebox.showinfo("搜索", "请输入关键词")
            return
        if self._search_in_progress:
            return
        self._search_in_progress = True
        self.search_btn.configure(state="disabled")
        self._hide_search_dropdown()
        threading.Thread(
            target=self._run_search_in_background, args=(term,), daemon=True
        ).start()

    def _run_search_in_background(self, term: str):
        try:
            results = search_games(
                term, limit=10, translate_fallback=self.search_enhance.get()
            )
        except Exception as exc:  # noqa: BLE001
            self._enqueue_log(f"搜索失败：{exc}")
            self.root.after(
                0,
                lambda: (
                    messagebox.showerror("错误", f"搜索失败：{exc}"),
                    self._finish_search([]),
                ),
            )
            return

        enriched: list[dict[str, object]] = []
        for item in results:
            appid = str(item.get("appid") or "")
            image_url = item.get("image") or ""
            enriched.append(
                {
                    "name": item.get("name") or "",
                    "appid": appid,
                    "image": image_url,
                    "image_data": None,
                }
            )
        self.root.after(0, lambda: self._finish_search(enriched))

    def _finish_search(self, results: list[dict[str, object]]):
        self._search_in_progress = False
        self.search_btn.configure(state="normal")
        self.search_results = results
        if not results:
            messagebox.showinfo("提示", "未找到结果")
            return
        self._show_search_dropdown(results)

    def _show_search_dropdown(self, results: list[dict[str, object]]):
        self._hide_search_dropdown()
        self.search_dropdown = tk.Toplevel(self.root)
        self.search_dropdown.overrideredirect(True)
        self.search_dropdown.attributes("-topmost", True)

        container = ttk.Frame(
            self.search_dropdown, padding=4, relief="solid", borderwidth=1
        )
        container.pack(fill="both", expand=True)

        row_height = 68
        style = ttk.Style(self.search_dropdown)
        style.configure("Search.Treeview", rowheight=row_height, font=("Microsoft YaHei", 10))
        style.configure(
            "Search.Treeview.Heading", padding=(6, 6), font=("Microsoft YaHei", 10, "bold")
        )

        columns = ("appid",)
        visible_rows = max(1, min(len(results), 5))
        tree = ttk.Treeview(
            container,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            height=visible_rows,
            style="Search.Treeview",
        )
        tree.heading("#0", text="游戏")
        tree.heading("appid", text="AppID")
        tree.column("#0", width=260)
        tree.column("appid", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self._search_tree = tree
        self.search_result_images = [None] * len(results)
        for idx, item in enumerate(results):
            tree.insert(
                "",
                "end",
                iid=str(idx),
                text=item.get("name", ""),
                image="",
                values=(item.get("appid", ""),),
            )

        tree.bind("<Double-1>", lambda e: self._on_select_search_result(tree))
        tree.bind("<Return>", lambda e: self._on_select_search_result(tree))
        tree.focus_set()

        def close_on_focus_out(event):
            if self.search_dropdown and event.widget is not self.search_dropdown:
                self._hide_search_dropdown()

        self.search_dropdown.bind("<FocusOut>", close_on_focus_out)
        self.search_dropdown.bind("<Escape>", lambda e: self._hide_search_dropdown())

        self.root.update_idletasks()
        x = self.search_entry.winfo_rootx()
        y = self.search_entry.winfo_rooty() + self.search_entry.winfo_height()
        width = max(self.search_entry.winfo_width(), 380)
        heading_height = 32
        padding = 16  # container padding + borders
        height = row_height * visible_rows + heading_height + padding
        self.search_dropdown.geometry(f"{width}x{height}+{x}+{y}")
        self._start_search_image_loading(tree, results)

    def _start_search_image_loading(
        self, tree: ttk.Treeview, results: list[dict[str, object]]
    ) -> None:
        # Load thumbnails in parallel to reduce perceived latency while keeping the UI free.
        task_id = self._search_image_task_id + 1
        self._search_image_task_id = task_id

        def worker():
            max_workers = max(1, min(8, len(results)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(self._build_search_photo, idx, item): idx
                    for idx, item in enumerate(results)
                }
                for future in as_completed(future_map):
                    if task_id != self._search_image_task_id or self._search_tree is not tree:
                        break
                    try:
                        idx, photo = future.result()
                    except Exception:
                        continue
                    if photo is None:
                        continue
                    self.search_result_images[idx] = photo

                    def update(i=idx, p=photo):
                        if (
                            task_id != self._search_image_task_id
                            or self._search_tree is not tree
                            or self.search_dropdown is None
                            or not tree.winfo_exists()
                        ):
                            return
                        try:
                            tree.item(str(i), image=p)
                        except tk.TclError:
                            pass

                    self.root.after(0, update)

        threading.Thread(target=worker, daemon=True).start()

    def _build_search_photo(
        self, idx: int, item: dict[str, object]
    ) -> tuple[int, tk.PhotoImage | None]:
        image_data = item.get("image_data")
        if image_data is None:
            image_url = item.get("image") or ""
            if image_url:
                image_data = self._download_image_bytes(image_url)
        if not image_data:
            return idx, None

        item["image_data"] = image_data
        photo = self._make_search_thumbnail(image_data, item.get("image"))
        return idx, photo

    def _on_select_search_result(self, tree: ttk.Treeview):
        selection = tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        if idx >= len(self.search_results):
            self._hide_search_dropdown()
            return
        data = self.search_results[idx]
        appid = str(data.get("appid") or "")
        name = data.get("name") or None
        image_url = data.get("image") or None
        image_data = data.get("image_data")

        self.appid_entry.delete(0, tk.END)
        self.appid_entry.insert(0, appid)
        folder_name = self._sanitize_filename(name) if name else appid
        self._prefetched_game = {
            "appid": appid,
            "name": name,
            "header_url": image_url,
            "image_data": image_data,
            "folder_name": folder_name,
        }
        self._apply_game_info_to_ui(name, image_url, image_data, folder_name)
        self._hide_search_dropdown()

    def _hide_search_dropdown(self):
        if self.search_dropdown is not None:
            try:
                self.search_dropdown.destroy()
            except tk.TclError:
                pass
        self.search_dropdown = None
        self.search_result_images = []
        self._search_tree = None
        self._search_image_task_id += 1

    def _make_search_thumbnail(
        self, image_data: bytes | None, image_url: str | None
    ) -> tk.PhotoImage | None:
        if image_data is None and image_url:
            image_data = self._download_image_bytes(image_url)
        if image_data and Image is not None and ImageTk is not None:
            try:
                image = Image.open(io.BytesIO(image_data))
                image.thumbnail((90, 45), Image.LANCZOS)
                return ImageTk.PhotoImage(image)
            except OSError:
                pass
        if image_data:
            try:
                encoded = base64.b64encode(image_data)
                return tk.PhotoImage(data=encoded)
            except tk.TclError:
                return None
        return None

    def open_settings_window(self):
        settings_window = tk.Toplevel(self.root)
        settings_window.title("设置")
        settings_window.resizable(True, True)
        ttk.Label(
            settings_window,
            text="本开源免费软件由ecxwxz开发。",
            wraplength=260,
        ).pack(padx=20, pady=(15, 5))

        source_frame = ttk.LabelFrame(settings_window, text="下载源设置")
        source_frame.pack(fill="x", padx=15, pady=10)
        ttk.Radiobutton(
            source_frame,
            text="国内源（功能受限）",
            variable=self.download_source,
            value="domestic",
            command=lambda: self.set_download_source("domestic"),
        ).pack(anchor="w", padx=10, pady=3)
        ttk.Radiobutton(
            source_frame,
            text="国外源（需要魔法）",
            variable=self.download_source,
            value="overseas",
            command=lambda: self.set_download_source("overseas"),
        ).pack(anchor="w", padx=10, pady=3)

        auto_frame = ttk.LabelFrame(settings_window, text="自动入库")
        auto_frame.pack(fill="x", padx=15, pady=10)
        auto_row = ttk.Frame(auto_frame)
        auto_row.pack(fill="x", padx=10, pady=5)
        ttk.Checkbutton(
            auto_row,
            text="steamtools 自动入库",
            variable=self.auto_import,
            command=self.toggle_auto_import,
        ).pack(side="left")
        ttk.Label(
            auto_row,
            textvariable=self.auto_import_status,
            foreground="#1a73e8",
        ).pack(side="right")

        search_frame = ttk.LabelFrame(settings_window, text="搜索增强")
        search_frame.pack(fill="x", padx=15, pady=10)
        search_row = ttk.Frame(search_frame)
        search_row.pack(fill="x", padx=10, pady=5)
        ttk.Checkbutton(
            search_row,
            text="搜索增强（需要魔法）",
            variable=self.search_enhance,
            command=self.toggle_search_enhance,
        ).pack(side="left")
        ttk.Label(
            search_row,
            textvariable=self.search_enhance_status,
            foreground="#1a73e8",
        ).pack(side="right")

        ttk.Button(
            settings_window,
            text="关闭",
            command=settings_window.destroy,
        ).pack(pady=10)

        settings_window.update_idletasks()
        sw = max(320, settings_window.winfo_reqwidth())
        sh = max(220, settings_window.winfo_reqheight())
        settings_window.geometry(f"{sw}x{sh}")
        # center the settings window relative to the main window
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        center_x = root_x + (root_w - sw) // 2
        center_y = root_y + (root_h - sh) // 2
        settings_window.geometry(f"+{center_x}+{center_y}")
        settings_window.minsize(sw, sh)

    def open_official_site(self):
        try:
            if not webbrowser.open_new_tab(OFFICIAL_SITE_URL):
                raise RuntimeError("无法打开浏览器")
        except Exception as exc:  # pragma: no cover - GUI feedback
            self.log(f"打开官网失败：{exc}")
            messagebox.showerror("错误", f"无法打开官网：{exc}")
        # noop - keep settings window size behavior handled above

    def set_download_source(self, value: str):
        self.download_source.set(value)
        human_readable = (
            "国内源（资源较少）" if value == "domestic" else "国外源（需要魔法）"
        )
        self.log(f"下载源已切换为：{human_readable}")
        self.settings["download_source"] = value
        self.save_settings()

    def toggle_auto_import(self):
        state = "已启用" if self.auto_import.get() else "已禁用"
        self.log(f"自动入库功能{state}")
        self.auto_import_status.set("开启" if self.auto_import.get() else "关闭")
        self.settings["auto_import"] = self.auto_import.get()
        self.save_settings()

    def toggle_search_enhance(self):
        state = "已开启" if self.search_enhance.get() else "已关闭"
        self.log(f"搜索增强{state}")
        self.search_enhance_status.set("开启" if self.search_enhance.get() else "关闭")
        self.settings["search_enhance"] = self.search_enhance.get()
        self.save_settings()

    def open_download_folder(self):
        download_root = os.path.join(os.getcwd(), "download")
        if not os.path.isdir(download_root):
            os.makedirs(download_root, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(download_root)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", download_root], check=False)
            else:
                subprocess.run(["xdg-open", download_root], check=False)
            self.log(f"已打开下载目录：{download_root}")
        except OSError as exc:
            self.log(f"无法打开目录：{exc}")

    def start_download(self):
        appid = self.appid_entry.get().strip()
        if not appid:
            messagebox.showwarning("提示", "请输入 AppID")
            return
        if self.current_task and self.current_task.is_alive():
            messagebox.showinfo("提示", "已有任务在执行，请稍候。")
            return

        source = self.download_source.get()

        self.progress_var.set(0)
        self.log(f"开始执行 {source} 源下载，AppID = {appid}")
        self.download_btn.configure(state="disabled")
        self._start_progress_animation()
        self.current_task = threading.Thread(
            target=self._background_job, args=(source, appid), daemon=True
        )
        self.current_task.start()

    def _background_job(self, source: str, appid: str):
        if self._prefetched_game and self._prefetched_game.get("appid") == appid:
            name = self._prefetched_game.get("name")
            header_url = self._prefetched_game.get("header_url")
            image_data = self._prefetched_game.get("image_data")
            folder_name = self._prefetched_game.get("folder_name") or appid
        else:
            name, header_url, image_data, folder_name = self._collect_game_info(appid)
        self.root.after(
            0,
            lambda: self._apply_game_info_to_ui(
                name, header_url, image_data, folder_name
            ),
        )
        self._run_download_flow(source, appid, folder_name)

    def _run_download_flow(self, source: str, appid: str, folder_name: str):
        folder_name = folder_name or appid
        download_root = os.path.join(os.getcwd(), "download")
        os.makedirs(download_root, exist_ok=True)
        try:
            if source == "domestic":
                success = self._handle_domestic_download(appid, folder_name, download_root)
            else:
                success = self._handle_overseas_download(appid, folder_name, download_root)
        except Exception as exc:  # noqa: BLE001
            self._enqueue_log(f"下载过程中出现异常：{exc}")
            success = False

        if success and self.auto_import.get():
            self._enqueue_log("下载完成，开始自动入库...")
            auto_ok = False
            try:
                auto_ok = self._auto_import_lua(appid)
            except Exception as exc:  # pragma: no cover - defensive
                self._enqueue_log(f"自动入库失败：{exc}")
                auto_ok = False
            if auto_ok:
                self._enqueue_log("自动入库完成。")
            else:
                self._enqueue_log("自动入库未完成。")
        if success:
            self._enqueue_log("任务完成。")
        else:
            self._enqueue_log("任务失败，请查看日志。")

        self.root.after(0, self._on_task_finished)

    def _handle_domestic_download(self, appid: str, folder_name: str, download_root: str) -> bool:
        base_url = (
            "https://gh.llkk.cc/https://github.com/SteamAutoCracks/ManifestHub"
            f"/archive/refs/heads/{appid}.zip"
        )
        zip_path = os.path.join(download_root, f"{appid}.zip")
        if not self._check_domestic_url(base_url):
            return False
        self._enqueue_log("国内源连接正常，开始下载文件...")
        if not self._download_file_stream(base_url, zip_path):
            return False
        target_dir = os.path.join(download_root, folder_name)
        return self._extract_and_cleanup(zip_path, target_dir)

    def _handle_overseas_download(self, appid: str, folder_name: str, download_root: str) -> bool:
        node = self._find_first_valid_node(appid)
        if node is None:
            self._enqueue_log("没有可用的国外节点，请稍后再试。")
            return False
        self._enqueue_log(f"使用节点 {node} 下载")
        download_url = self._get_overseas_download_url(appid, node)
        zip_path = os.path.join(download_root, f"{appid}_src{node}.zip")
        headers = {
            "Host": "api-psi-eight-12.vercel.app",
            "Sec-Ch-Ua": '"Chromium";v="141", "Not?A_Brand";v="8"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
                "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
            ),
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Referer": "https://api-psi-eight-12.vercel.app/",
            "Accept-Encoding": "gzip, deflate, br",
            "Priority": "u=0, i",
        }
        if not self._download_file_stream(download_url, zip_path, headers=headers, timeout=(5, 30)):
            return False
        target_dir = os.path.join(download_root, folder_name)
        return self._extract_and_cleanup(zip_path, target_dir)

    def _extract_and_cleanup(self, zip_path: str, target_dir: str) -> bool:
        try:
            self._process_downloaded_archive(zip_path, target_dir)
        except RuntimeError as exc:
            self._enqueue_log(str(exc))
            return False
        else:
            # 成功后以一句话报告：下载完成并保留文件至目标目录（避免显示临时压缩包路径）
            self._enqueue_log(f"下载完成，保留的文件已保存至 {target_dir}")
        finally:
            # 尝试删除临时压缩包，但不将压缩包路径作为常规日志输出，除非删除失败
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError as exc:
                    self._enqueue_log(f"无法删除压缩包 {zip_path}: {exc}")
        return True

    def _process_downloaded_archive(self, zip_path: str, target_dir: str) -> None:
        archive_path = Path(zip_path)
        target_root = Path(target_dir)
        staging_dir = target_root.with_name(target_root.name + "_staging")

        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        target_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archive.extractall(staging_dir)
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError(f"{archive_path} 不是有效的压缩包: {exc}") from exc

        for file_path in staging_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            dest = target_root / file_path.name
            try:
                shutil.move(str(file_path), str(dest))
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"移动 {file_path} -> {dest} 失败: {exc}")

        shutil.rmtree(staging_dir, ignore_errors=True)

    def _check_domestic_url(self, url: str) -> bool:
        # Fast probe to avoid downloading the entire archive during health check.
        try:
            resp = requests.head(url, timeout=(5, 5), allow_redirects=True)
            if resp.status_code == 404:
                self._enqueue_log("Domestic source missing this game, try overseas source")
                return False
            resp.raise_for_status()
            return True
        except requests.RequestException:
            pass

        try:
            headers = {"Range": "bytes=0-0"}
            with requests.get(url, headers=headers, stream=True, timeout=(5, 5)) as resp:
                if resp.status_code == 404:
                    self._enqueue_log("Domestic source missing this game, try overseas source")
                    return False
                resp.raise_for_status()
                resp.raw.read(1)
                return True
        except requests.RequestException as exc:
            self._enqueue_log(f"Domestic source check failed: {exc}")
            return False

    def _download_file_stream(
        self,
        url: str,
        save_path: str,
        headers: dict | None = None,
        timeout: float | tuple[float, float] = 30,
    ) -> bool:
        self._enqueue_log("Start downloading...")
        req_headers = headers.copy() if headers else {}
        total_bytes = 0
        supports_ranges = False

        try:
            try:
                head_resp = requests.head(url, headers=req_headers, timeout=10)
                head_resp.raise_for_status()
                total_bytes = int(head_resp.headers.get("content-length") or 0)
                supports_ranges = (
                    head_resp.headers.get("accept-ranges", "").lower() == "bytes"
                )
            except (requests.RequestException, ValueError, TypeError):
                total_bytes = 0

            if total_bytes > 0:
                self.root.after(0, self._set_progress_to_determinate)

            if supports_ranges and total_bytes >= SEGMENT_MIN_BYTES:
                self._enqueue_log("Using parallel segmented download to speed up")
                ok = self._download_with_segments(
                    url, save_path, total_bytes, req_headers, timeout
                )
                if ok:
                    self.root.after(0, lambda: self.progress_var.set(100))
                    return True
                self._enqueue_log("Segmented download failed, falling back to single stream")

            ok = self._download_single_stream(
                url, save_path, req_headers, timeout, total_bytes
            )
            if ok:
                self.root.after(0, lambda: self.progress_var.set(100))
            return ok
        except Exception as exc:  # noqa: BLE001
            try:
                self.root.after(0, lambda: self.progress_var.set(0))
            except Exception:
                pass
            self._enqueue_log(f"Download failed: {exc}")
            return False

    def _download_single_stream(
        self,
        url: str,
        save_path: str,
        headers: dict[str, str],
        timeout: float | tuple[float, float],
        total_bytes: int,
    ) -> bool:
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()

                if total_bytes <= 0:
                    try:
                        total_bytes = int(resp.headers.get("content-length") or 0)
                    except (TypeError, ValueError):
                        total_bytes = 0
                    if total_bytes > 0:
                        self.root.after(0, self._set_progress_to_determinate)

                downloaded = 0
                last_percent = 0.0
                with open(save_path, "wb") as file_handle:
                    for chunk in resp.iter_content(chunk_size=SEGMENT_CHUNK_SIZE):
                        if not chunk:
                            continue
                        file_handle.write(chunk)
                        if total_bytes > 0:
                            downloaded += len(chunk)
                            percent = min(100.0, downloaded * 100.0 / total_bytes)
                            if percent - last_percent >= 1 or downloaded == total_bytes:
                                last_percent = percent
                                self.root.after(
                                    0, lambda p=percent: self.progress_var.set(p)
                                )
            return True
        except (requests.RequestException, OSError) as exc:
            self._enqueue_log(f"Download failed: {exc}")
            return False

    def _download_with_segments(
        self,
        url: str,
        save_path: str,
        total_bytes: int,
        headers: dict[str, str],
        timeout: float | tuple[float, float],
    ) -> bool:
        segment_count = max(
            1, min(SEGMENT_WORKERS, math.ceil(total_bytes / SEGMENT_MIN_BYTES))
        )
        segment_size = math.ceil(total_bytes / segment_count)
        part_paths: list[str | None] = [None] * segment_count
        progress_lock = threading.Lock()
        downloaded = 0
        last_percent = 0.0

        def cleanup_parts():
            for path_str in part_paths:
                if path_str and os.path.exists(path_str):
                    try:
                        os.remove(path_str)
                    except OSError:
                        pass

        def download_part(idx: int, start: int, end: int) -> bool:
            nonlocal downloaded, last_percent
            part_headers = dict(headers)
            part_headers["Range"] = f"bytes={start}-{end}"
            part_path = f"{save_path}.part{idx}"
            try:
                with requests.get(
                    url, headers=part_headers, stream=True, timeout=timeout
                ) as resp:
                    resp.raise_for_status()
                    status_ok = resp.status_code == 206 or (
                        resp.status_code == 200 and start == 0 and end >= total_bytes - 1
                    )
                    if not status_ok:
                        return False
                    with open(part_path, "wb") as part_file:
                        for chunk in resp.iter_content(chunk_size=SEGMENT_CHUNK_SIZE):
                            if not chunk:
                                continue
                            part_file.write(chunk)
                            with progress_lock:
                                downloaded += len(chunk)
                                percent = min(100.0, downloaded * 100.0 / total_bytes)
                                if percent - last_percent >= 1 or downloaded == total_bytes:
                                    last_percent = percent
                                    self.root.after(
                                        0, lambda p=percent: self.progress_var.set(p)
                                    )
                part_paths[idx] = part_path
                return True
            except (requests.RequestException, OSError):
                return False

        with ThreadPoolExecutor(max_workers=segment_count) as executor:
            futures = []
            for idx in range(segment_count):
                start = idx * segment_size
                end = min(total_bytes - 1, start + segment_size - 1)
                futures.append(executor.submit(download_part, idx, start, end))

            all_ok = all(f.result() for f in futures)

        if not all_ok or any(path is None for path in part_paths):
            cleanup_parts()
            return False

        try:
            with open(save_path, "wb") as target:
                for path_str in part_paths:
                    if path_str is None:
                        return False
                    with open(path_str, "rb") as part_file:
                        shutil.copyfileobj(part_file, target, SEGMENT_CHUNK_SIZE * 2)
        except OSError:
            cleanup_parts()
            return False

        cleanup_parts()
        return True
    def _find_first_valid_node(self, appid: str, total_nodes: int = 6) -> int | None:
        with ThreadPoolExecutor(max_workers=total_nodes) as executor:
            future_map = {
                executor.submit(self._check_overseas_node, appid, node): node
                for node in range(total_nodes)
            }
            for future in as_completed(future_map):
                node = future_map[future]
                if future.result():
                    return node
        return None

    def _check_overseas_node(self, appid: str, node: int) -> bool:
        url = self._get_overseas_download_url(appid, node)
        try:
            response = requests.get(url, timeout=NODE_TIMEOUT)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def _get_overseas_download_url(self, appid: str, node: int) -> str:
        base32_id = self._base32_encode(appid)
        return f"https://api-psi-eight-12.vercel.app/download?id={base32_id}&src={node}"

    def _auto_import_lua(self, appid: str) -> bool:
        try:
            self._copy_lua_to_steam(appid)
            return True
        except (FileNotFoundError, RuntimeError) as exc:
            self._enqueue_log(str(exc))
        except Exception as exc:  # noqa: BLE001
            self._enqueue_log(f"自动入库失败：{exc}")
        return False

    def _copy_lua_to_steam(self, appid: str) -> None:
        lua_file = self._find_lua_file(appid)
        steam_root = self._resolve_steam_root()
        target_dir = steam_root / "config" / "stplug-in"
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / lua_file.name
        shutil.copy2(lua_file, destination)
        self._enqueue_log(f"已将 {lua_file} 拷贝到 {destination}")

    def _find_lua_file(self, appid: str) -> Path:
        file_name = f"{appid}.lua"
        search_paths = [
            Path.cwd() / "download" / file_name,
            Path.cwd() / file_name,
        ]
        for path in search_paths:
            if path.is_file():
                return path
        download_root = Path.cwd() / "download"
        if download_root.exists():
            for match in download_root.rglob(file_name):
                if match.is_file():
                    return match
        raise FileNotFoundError(f"未找到 {file_name}，请确认文件位于 download 目录或当前目录下。")

    def _resolve_steam_root(self) -> Path:
        for key in STEAM_ENV_KEYS:
            steam_path = os.environ.get(key)
            if steam_path:
                candidate = Path(steam_path).expanduser().resolve()
                if candidate.exists():
                    return candidate
        registry_path = self._read_steam_path_from_registry()
        if registry_path and registry_path.exists():
            return registry_path
        for path in DEFAULT_STEAM_PATHS:
            candidate = Path(path)
            if candidate.exists():
                return candidate
        raise RuntimeError("未能自动定位 Steam 安装路径，请设置 STEAM_PATH 或手动指定。")

    def _read_steam_path_from_registry(self) -> Optional[Path]:
        if winreg is None:
            return None
        registry_keys = (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        )
        for hive, subkey in registry_keys:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "SteamPath")
                    if value:
                        return Path(value).expanduser().resolve()
            except OSError:
                continue
        return None

    def _enqueue_log(self, message: str):
        self.log_queue.put(message)

    def _process_log_queue(self):
        while not self.log_queue.empty():
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log(message)
        self.root.after(100, self._process_log_queue)

    def _write_log_file(self, message: str):
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as log_file:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_file.write(f"[{stamp}] {message}\n")
        except OSError:
            pass

    def _show_update_banner(self):
        """在日志面板中以大字显示更新日志（标题、版本、内容）。"""
        if not hasattr(self, "log_area") or self.log_area is None:
            return
        # 允许写入、插入并恢复只读
        try:
            self.log_area.configure(state="normal")
            # 设置样式标签
            try:
                self.log_area.tag_configure("update_title", font=("Microsoft YaHei", 16, "bold"))
                self.log_area.tag_configure("update_version", font=("Microsoft YaHei", 12, "bold"))
            except Exception:
                # 如果字体不可用或其他问题，忽略样式设置
                pass

            # 插入更新内容
            self.log_area.insert("end", "更新日志\n", ("update_title",))
            self.log_area.insert("end", "版本号:1.2\n", ("update_version",))
            self.log_area.insert("end", "细化了设置选项，解决了一些bug\n\n")
            self.log_area.see("end")
        finally:
            try:
                self.log_area.configure(state="disabled")
            except Exception:
                pass

    def _collect_game_info(self, appid: str) -> tuple[str | None, str | None, bytes | None, str]:
        name = None
        header_url = None
        image_data = None

     # 先用官方接口
        api_name, api_img = self._fetch_game_info(appid)
        if api_name:
            name = api_name
        if api_img:
            header_url = api_img
            image_data = self._download_image_bytes(api_img)

        # 如果官方拿不到名字或封面，再用代理补齐
        if not name or not header_url:
            proxy_name, proxy_img = self._fetch_game_info_from_proxy(appid)
            name = name or proxy_name
            if not header_url and proxy_img:
                header_url = proxy_img
                image_data = self._download_image_bytes(proxy_img)

        folder_name = self._sanitize_filename(name) if name else appid
        return name, header_url, image_data, folder_name


    def _apply_game_info_to_ui(
        self,
        name: str | None,
        header_url: str | None,
        image_data: bytes | None,
        folder_name: str,
    ):
        self.game_name_var.set(f"游戏名称：{name}" if name else "游戏名称：未知")
        self.header_image_url = header_url
        self._image_request_id += 1
        current_request = self._image_request_id
        self.current_game_folder = folder_name

        if image_data is not None:
            self._update_game_image(header_url, image_data)
            return

        # Avoid blocking UI when image is still loading; fetch asynchronously.
        self.game_image.configure(image="", text="图片加载中..." if header_url else "图片预览")
        self.game_image_photo = None
        if not header_url:
            return

        threading.Thread(
            target=self._download_and_set_image,
            args=(header_url, current_request),
            daemon=True,
        ).start()
        self.current_game_folder = folder_name

    def _download_and_set_image(self, header_url: str, request_id: int):
        image_data = self._download_image_bytes(header_url)

        def apply():
            if request_id != self._image_request_id:
                return
            if image_data:
                self._update_game_image(header_url, image_data)
            else:
                self.game_image.configure(text=f"图片：{header_url}", image="")

        self.root.after(0, apply)

    def _fetch_game_info(self, appid: str) -> tuple[str | None, str | None]:
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": appid, "cc": "CN", "l": "schinese"}
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
        except requests.RequestException as exc:
            self.log(f"获取游戏信息失败：{exc}")
            return None, None

        payload = resp.json().get(str(appid))
        if not payload or not payload.get("success"):
            return None, None
        data = payload.get("data", {})
        return data.get("name"), data.get("header_image")

    def _fetch_game_info_from_proxy(self, appid: str) -> tuple[str | None, str | None]:
        if BeautifulSoup is None:
            return None, None
        base32_id = self._base32_encode(appid)
        url = f"https://api-psi-eight-12.vercel.app/proxy?id={base32_id}"
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
        except requests.RequestException as exc:
            self.log(f"获取代理页面失败：{exc}")
            return None, None

        soup = BeautifulSoup(resp.content, "html.parser")
        game_info_div = soup.find("div", class_="game-info")
        if not game_info_div:
            return None, None

        name = None
        header_url = None
        title = game_info_div.find("h2")
        if title:
            name = title.get_text(strip=True)
        img_tag = game_info_div.find("img")
        if img_tag and img_tag.get("src"):
            header_url = img_tag["src"]
        return name, header_url

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
        return cleaned or "steam_app"

    @staticmethod
    def _base32_encode(input_str: str) -> str:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        bits = "".join(bin(ord(c))[2:].zfill(8) for c in input_str)
        result = []
        for i in range(0, len(bits), 5):
            chunk = bits[i : i + 5].ljust(5, "0")
            result.append(alphabet[int(chunk, 2)])
        return "".join(result)

    def _download_image_bytes(self, url: str | None) -> bytes | None:
        if not url:
            return None
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException:
            return None

    def _update_game_image(self, header_url: str | None, image_data: bytes | None = None):
        if not header_url:
            self.game_image.configure(image="", text="图片预览")
            self.game_image_photo = None
            return

        max_width = self.game_image.winfo_width() or 360
        max_height = self.game_image.winfo_height() or 180

        if image_data is None:
            image_data = self._download_image_bytes(header_url)
            if image_data is None:
                self.game_image.configure(text=f"图片：{header_url}", image="")
                return

        if Image is not None and ImageTk is not None:
            try:
                image = Image.open(io.BytesIO(image_data))
                image.thumbnail((max_width, max_height), Image.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.game_image.configure(image=photo, text="")
                self.game_image_photo = photo
                return
            except OSError as exc:
                self.log(f"处理图片失败：{exc}")

        try:
            encoded = base64.b64encode(image_data)
            photo = tk.PhotoImage(data=encoded)
            width, height = photo.width(), photo.height()
            scale = max(width / max_width, height / max_height, 1)
            if scale > 1:
                factor = max(1, math.ceil(scale))
                photo = photo.subsample(factor, factor)
            self.game_image.configure(image=photo, text="")
            self.game_image_photo = photo
        except tk.TclError as exc:
            self.log(f"图片无法显示：{exc}")
            self.game_image.configure(text=f"图片：{header_url}", image="")

    def _set_progress_to_determinate(self):
        if self.progress_animating:
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            self.progress_animating = False
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(0)

    def _start_progress_animation(self):
        if self.progress_animating:
            return
        self.progress_animating = True
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(15)

    def _stop_progress_animation(self):
        if not self.progress_animating:
            return
        self.progress_animating = False
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(100)

    def _on_task_finished(self):
        self._stop_progress_animation()
        self.download_btn.configure(state="normal")

    def load_settings(self):
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"download_source": "domestic", "auto_import": False, "search_enhance": False}

    def save_settings(self):
        data = {
            "download_source": self.download_source.get(),
            "auto_import": self.auto_import.get(),
            "search_enhance": self.search_enhance.get(),
        }
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)


if __name__ == "__main__":
    root = tk.Tk()
    app = SteamManifestDownloader(root)
    root.mainloop()
