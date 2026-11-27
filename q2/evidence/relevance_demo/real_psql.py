"""在 Windows Terminal 視窗裡，對 WSL 內的 Postgres 跑 LIKE 查詢，只截該視窗（kibana 同款：不加排版）。
輸出 psql_raw.png。前置：q2demo-pg 在跑且 fb_posts 已灌好。
"""
import ctypes, ctypes.wintypes as wt, os, subprocess, time
from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
SQL = "SELECT id, content FROM fb_posts WHERE content LIKE '%颱風%';\n"
open(os.path.join(HERE, "_like.sql"), "w", encoding="utf-8").write(SQL)
BAT = os.path.join(HERE, "_run_like.cmd")
open(BAT, "w", encoding="utf-8").write(
    "@echo off\r\nchcp 65001 >nul\r\ncls\r\n"
    f'type "{os.path.join(HERE, "_like.sql")}" | wsl -u root -- docker exec -i q2demo-pg psql -U postgres -X -a -P pager=off -P format=wrapped -P columns=118\r\n'
    "pause >nul\r\n")
WT = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe")
subprocess.Popen([WT, "-w", "new", "--size", "122,30", "--title", "psql - q2demo", "cmd", "/c", BAT])
u = ctypes.windll.user32
hwnd = 0
for _ in range(40):
    time.sleep(0.5)
    hwnd = u.FindWindowW("CASCADIA_HOSTING_WINDOW_CLASS", None)
    if hwnd:
        break
print("hwnd", hwnd)
time.sleep(6)
u.keybd_event(0x12, 0, 0, 0); u.SetForegroundWindow(hwnd); u.keybd_event(0x12, 0, 2, 0)
time.sleep(1.5)
r = wt.RECT()
ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))
print(r.left, r.top, r.right, r.bottom)
ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(os.path.join(HERE, "psql_raw.png"))
