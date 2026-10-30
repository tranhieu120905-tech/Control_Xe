"""Render the real Tk window with Win32 PrintWindow, without hardware access."""
import ctypes
import argparse
from ctypes import wintypes
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dieukhienxe import CarDashboard, parse_args


def capture(root, destination):
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    dc = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(dc)
    bitmap = gdi32.CreateCompatibleBitmap(dc, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, 2):
            raise RuntimeError("PrintWindow failed")
        class BitmapInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                        ("image_size", wintypes.DWORD), ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
                        ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
        info = BitmapInfo(ctypes.sizeof(BitmapInfo), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.create_string_buffer(width*height*4)
        gdi32.SelectObject(memory, previous)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, data, ctypes.byref(info), 0):
            raise RuntimeError("GetDIBits failed")
        image = Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
        print(f"Preview: {destination} ({width}x{height})")
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, dc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", default="1240x720")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]/"artifacts"/"dashboard-preview.png")
    options = parser.parse_args()
    root = tk.Tk()
    app = CarDashboard(root, parse_args(["--demo"]))
    root.geometry(options.size)
    failures = []
    def render():
        try:
            capture(root, options.output)
        except Exception as exc:
            failures.append(exc)
        finally:
            app.close()
    root.after(500, render)
    root.mainloop()
    if failures:
        raise failures[0]
