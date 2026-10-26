#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
escritorio_overlay.py  -  "Escritorio HD"
=========================================================================
Reconstruye el Escritorio de Windows 10/11 (un solo monitor) en una capa
propia, transparente y por pixel, ocultando los iconos nativos mientras el
usuario esta en el escritorio.

DEPENDENCIAS
    pip install pillow                      (obligatorio)
    pip install numpy opencv-python         (solo para la animacion de grietas)
    (el resto usa solo ctypes de la biblioteca estandar; no hace falta pywin32)
    Sin numpy/opencv el escritorio funciona igual, solo sin animacion.

USO
    python escritorio_overlay.py              -> inicia en modo escritorio
    pythonw escritorio_overlay.py             -> igual, sin consola
    python escritorio_overlay.py --restaurar  -> solo vuelve a mostrar los
                                                 iconos nativos y termina

DATOS DE ENTRADA (ya deben existir)
    %TEMP%\\Iconos_Escritorio_HD\\posiciones_escritorio.json
    %TEMP%\\Iconos_Escritorio_HD\\*.png

FUNCIONAMIENTO
    1. Modo escritorio: SysListView32 oculto + capa transparente a pantalla
       completa con los iconos, etiquetas y resaltado azul al pasar el mouse.
    2. Al pasar a cualquier ventana que no sea del shell se restauran los
       iconos nativos y se oculta la capa.
    3. El boton "Escritorio HD" de la barra de tareas (ventana minimizada)
       vuelve al modo escritorio (releyendo el JSON). Por defecto tambien
       minimiza las demas ventanas, como "Mostrar escritorio"
       (ver MINIMIZAR_AL_VOLVER).
    4. Doble clic sobre un icono lo abre (busca el elemento por su nombre en
       las carpetas Escritorio del usuario y publica).

ANIMACION DE GRIETAS (ver el bloque CONFIGURACION DE LA ANIMACION)
    Recursos buscados en: carpeta del script, carpeta de iconos del TEMP y
    carpeta actual:  grieta0.png ... grieta4.png, mascara.png, FUEGO.webm
    * Un clic sobre cualquier icono inicia la secuencia (DISPARO_ANIMACION).
    * Cada etapa = una grieta nueva + temblor de pantalla. Duraciones:
      1 s, 1 s, 1 s, 0.5 s y luego grieta4.png fija + video de fuego.
    * Cada icono afectado tiene 3 fases: FIJO -> COLGADO (pendulo anclado
      en una esquina superior) -> LIBRE (fisica, cae al fondo). El ciclo en
      que pasa a colgado (0, 1 o 2) se sortea por icono.
    * Con el mouse se pueden empujar (pasar rapido) o arrastrar (clic izq.).
    * El video solo se ve donde mascara.png es opaca. Escala y posicion del
      video: VIDEO_ESCALA, VIDEO_POS_X, VIDEO_POS_Y.
    * El boton "Escritorio HD" de la barra reinicia el escritorio y la animacion.

NOTAS
    * Los iconos nunca deben quedar ocultos: se restauran con try/finally,
      atexit, SetConsoleCtrlHandler, SIGINT/SIGTERM/SIGBREAK y cierre de
      ventana. Si el proceso se mata a la fuerza (taskkill /F), ejecuta
      "python escritorio_overlay.py --restaurar" o simplemente inicia el
      script de nuevo: al arrancar restaura un ListView oculto previamente.
    * El Z-order: HWND_BOTTOM dejaria la capa DETRAS de la ventana del
      fondo de pantalla (Progman/WorkerW) y quedaria invisible, asi que la
      capa se coloca justo encima de esa ventana y debajo de todas las
      demas (equivalente practico a "nivel escritorio").
    * Las zonas sin iconos tienen alfa 0 y dejan pasar los clics; cada celda
      "limites" se pinta con alfa 2 (invisible) para recibir el mouse.
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import math
import os
import random
import signal
import sys
import tempfile
import threading
import time
import traceback
from ctypes import (POINTER, Structure, byref, c_int, c_long, c_size_t,
                    c_ssize_t, c_ubyte, c_uint, c_ushort, c_ulong, c_void_p,
                    c_wchar_p, sizeof)

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------
NOMBRE_CARPETA = "Iconos_Escritorio_HD"
NOMBRE_JSON = "posiciones_escritorio.json"
TITULO_APP = "Escritorio HD"
MINIMIZAR_AL_VOLVER = True      # al volver, minimiza las ventanas (Mostrar escritorio)
INTERVALO_SONDEO_MS = 200       # respaldo de EVENT_SYSTEM_FOREGROUND
GRACIA_TRANSICION_S = 1.0       # ignora cambios de primer plano tras volver

CLASE_OVERLAY = "EscritorioHD_Overlay"
CLASE_BOTON = "EscritorioHD_Boton"

# Clases que se consideran "shell": no hacen salir del modo escritorio
CLASES_SHELL = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow",
    "XamlExplorerHostIslandWindow", "TaskListThumbnailWnd",
    "TaskListOverlayWnd", "#32768",
}
# Ventanas CoreWindow que pertenecen al shell (menu Inicio, busqueda...)
CLASES_HOST_UWP = {"Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"}
EXE_SHELL = {"startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
             "searchui.exe", "shellexperiencehost.exe", "shellhost.exe"}

ESPECIALES = {   # iconos "virtuales" del escritorio -> CLSID del shell
    "este equipo": "shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
    "this pc": "shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
    "papelera de reciclaje": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
    "recycle bin": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
    "red": "shell:::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",
    "network": "shell:::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",
    "panel de control": "shell:::{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}",
    "control panel": "shell:::{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}",
}

# Colores del resaltado (RGBA)
COL_HOVER = (96, 165, 250, 62)
COL_SEL = (59, 130, 246, 105)
COL_SEL_HOVER = (59, 130, 246, 135)
COL_BORDE = (125, 180, 255, 170)

# ==========================================================================
# CONFIGURACION DE LA ANIMACION (grietas + fuego)
# ==========================================================================
ANIMACION_ACTIVA = True
DISPARO_ANIMACION = "clic_icono"        # "clic_icono" | "al_iniciar"
CARPETAS_RECURSOS = None                # lista extra de carpetas donde buscar
ARCHIVO_GRIETA = "grieta{n}.png"        # n = 0..4
ARCHIVO_MASCARA = "mascara.png"
ARCHIVO_VIDEO = "FUEGO.webm"

# Duracion de cada etapa en segundos: grieta0, grieta1, grieta2, grieta3.
# La etapa 4 (grieta4 + video) no termina.
DURACIONES_ETAPA = (2.0, 2.0, 2.0, 1.0)

# Temblor de pantalla al empezar cada etapa (0..4)
TEMBLOR_DURACION = 0.45                 # segundos
TEMBLOR_AMPLITUD = (6, 8, 10, 14, 20)   # pixeles a 96 dpi, por etapa

# --- Video de fuego: escala y posicion ------------------------------------
VIDEO_AJUSTE = "cubrir"     # "cubrir": cubre la pantalla; "original": tamano real
VIDEO_ESCALA = 1          # multiplicador sobre el ajuste anterior (1.5 = +50 %)
VIDEO_POS_X = 0             # desplazamiento del CENTRO del video respecto al
VIDEO_POS_Y = -120            #   centro de la pantalla, en pixeles (+ derecha/abajo)
VIDEO_MEZCLA = "normal"     # "normal" | "aditivo" (el negro desaparece)
VIDEO_FPS_MAX = 30          # tope de refresco (el video se decodifica a su ritmo)

# --- Fisica ----------------------------------------------------------------
PROB_ICONO_AFECTADO = 0.7   # fraccion de iconos que reaccionan en las etapas 0-3
CAIDA_FINAL_DURACION = 0.2  # en la etapa 4 caen TODOS: segundos entre el primero
                            #   (el mas cercano al centro) y el ultimo (el mas lejano)
ANIM_SEMILLA = None         # numero para repetir siempre la misma secuencia
GRAVEDAD = 2600.0           # px/s^2 a 96 dpi
REBOTE = 0.35               # 0 = sin rebote, 1 = rebote perfecto
AMORT_PENDULO = 0.7         # amortiguacion del balanceo (1/s)
EMPUJE_RATON = 12.0         # fuerza con que el mouse empuja al pasar
AGARRE_K, AGARRE_D = 300.0, 30.0   # rigidez/amortiguacion al arrastrar
OMEGA_MAX = 14.0            # rad/s maximos de giro

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
except ImportError:                                   # pragma: no cover
    Image = None

try:                                                  # solo para la animacion
    import numpy as np
    import cv2
    HAY_ANIM = True
except ImportError:                                   # pragma: no cover
    np = cv2 = None
    HAY_ANIM = False

# --------------------------------------------------------------------------
# Tipos y prototipos de la API de Windows (seguros para Python de 64 bits)
# --------------------------------------------------------------------------
DWORD = c_ulong
BOOL = c_int
UINT = c_uint
WORD = c_ushort
LPCWSTR = c_wchar_p
LPWSTR = c_wchar_p
WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

WNDPROC = WINFUNCTYPE(c_ssize_t, c_void_p, c_uint, c_size_t, c_ssize_t)
WNDENUMPROC = WINFUNCTYPE(c_int, c_void_p, c_ssize_t)
WINEVENTPROC = WINFUNCTYPE(None, c_void_p, DWORD, c_void_p, c_long, c_long,
                           DWORD, DWORD)
HANDLER_ROUTINE = WINFUNCTYPE(BOOL, DWORD)


class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


class SIZE(Structure):
    _fields_ = [("cx", c_long), ("cy", c_long)]


class MSG(Structure):
    _fields_ = [("hwnd", c_void_p), ("message", c_uint), ("wParam", c_size_t),
                ("lParam", c_ssize_t), ("time", DWORD), ("pt", POINT)]


class BLENDFUNCTION(Structure):
    _fields_ = [("BlendOp", c_ubyte), ("BlendFlags", c_ubyte),
                ("SourceConstantAlpha", c_ubyte), ("AlphaFormat", c_ubyte)]


class WNDCLASSEXW(Structure):
    _fields_ = [("cbSize", c_uint), ("style", c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", c_int), ("cbWndExtra", c_int),
                ("hInstance", c_void_p), ("hIcon", c_void_p),
                ("hCursor", c_void_p), ("hbrBackground", c_void_p),
                ("lpszMenuName", c_wchar_p), ("lpszClassName", c_wchar_p),
                ("hIconSm", c_void_p)]


class RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long),
                ("bottom", c_long)]


class TRACKMOUSEEVENT(Structure):
    _fields_ = [("cbSize", DWORD), ("dwFlags", DWORD),
                ("hwndTrack", c_void_p), ("dwHoverTime", DWORD)]


class BITMAPINFOHEADER(Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", c_long), ("biHeight", c_long),
                ("biPlanes", WORD), ("biBitCount", WORD),
                ("biCompression", DWORD), ("biSizeImage", DWORD),
                ("biXPelsPerMeter", c_long), ("biYPelsPerMeter", c_long),
                ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


class BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 1)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def _proto(fn, restype, *argtypes):
    """Declara restype/argtypes de una funcion de la API."""
    fn.restype = restype
    fn.argtypes = list(argtypes)


_proto(user32.EnumWindows, BOOL, WNDENUMPROC, c_ssize_t)
_proto(user32.FindWindowW, c_void_p, LPCWSTR, LPCWSTR)
_proto(user32.FindWindowExW, c_void_p, c_void_p, c_void_p, LPCWSTR, LPCWSTR)
_proto(user32.ShowWindow, BOOL, c_void_p, c_int)
_proto(user32.IsWindow, BOOL, c_void_p)
_proto(user32.IsWindowVisible, BOOL, c_void_p)
_proto(user32.IsIconic, BOOL, c_void_p)
_proto(user32.GetClassNameW, c_int, c_void_p, LPWSTR, c_int)
_proto(user32.GetWindowThreadProcessId, DWORD, c_void_p, POINTER(DWORD))
_proto(user32.GetForegroundWindow, c_void_p)
_proto(user32.GetWindow, c_void_p, c_void_p, c_uint)
_proto(user32.GetWindowLongW, c_long, c_void_p, c_int)
_proto(user32.SetWinEventHook, c_void_p, c_uint, c_uint, c_void_p,
       WINEVENTPROC, DWORD, DWORD, DWORD)
_proto(user32.UnhookWinEvent, BOOL, c_void_p)
_proto(user32.RegisterClassExW, WORD, POINTER(WNDCLASSEXW))
_proto(user32.CreateWindowExW, c_void_p, DWORD, LPCWSTR, LPCWSTR, DWORD,
       c_int, c_int, c_int, c_int, c_void_p, c_void_p, c_void_p, c_void_p)
_proto(user32.DefWindowProcW, c_ssize_t, c_void_p, c_uint, c_size_t, c_ssize_t)
_proto(user32.DestroyWindow, BOOL, c_void_p)
_proto(user32.PostQuitMessage, None, c_int)
_proto(user32.GetMessageW, c_int, POINTER(MSG), c_void_p, c_uint, c_uint)
_proto(user32.TranslateMessage, BOOL, POINTER(MSG))
_proto(user32.DispatchMessageW, c_ssize_t, POINTER(MSG))
_proto(user32.PostMessageW, BOOL, c_void_p, c_uint, c_size_t, c_ssize_t)
_proto(user32.SendMessageW, c_ssize_t, c_void_p, c_uint, c_size_t, c_ssize_t)
_proto(user32.SetTimer, c_size_t, c_void_p, c_size_t, c_uint, c_void_p)
_proto(user32.KillTimer, BOOL, c_void_p, c_size_t)
_proto(user32.SetWindowPos, BOOL, c_void_p, c_void_p, c_int, c_int, c_int,
       c_int, c_uint)
_proto(user32.UpdateLayeredWindow, BOOL, c_void_p, c_void_p, POINTER(POINT),
       POINTER(SIZE), c_void_p, POINTER(POINT), DWORD, POINTER(BLENDFUNCTION),
       DWORD)
_proto(user32.GetDC, c_void_p, c_void_p)
_proto(user32.ReleaseDC, c_int, c_void_p, c_void_p)
_proto(user32.GetSystemMetrics, c_int, c_int)
_proto(user32.LoadCursorW, c_void_p, c_void_p, c_void_p)
_proto(user32.LoadImageW, c_void_p, c_void_p, LPCWSTR, c_uint, c_int, c_int,
       c_uint)
_proto(user32.DestroyIcon, BOOL, c_void_p)
_proto(user32.TrackMouseEvent, BOOL, POINTER(TRACKMOUSEEVENT))
_proto(user32.GetCursorPos, BOOL, POINTER(POINT))
_proto(user32.GetAsyncKeyState, ctypes.c_short, c_int)
_proto(user32.GetKeyState, ctypes.c_short, c_int)
_proto(user32.MessageBoxW, c_int, c_void_p, LPCWSTR, LPCWSTR, c_uint)
_proto(user32.SystemParametersInfoW, BOOL, c_uint, c_uint, POINTER(RECT), c_uint)

_proto(kernel32.GetModuleHandleW, c_void_p, LPCWSTR)
_proto(kernel32.SetConsoleCtrlHandler, BOOL, HANDLER_ROUTINE, BOOL)
_proto(kernel32.OpenProcess, c_void_p, DWORD, BOOL, DWORD)
_proto(kernel32.QueryFullProcessImageNameW, BOOL, c_void_p, DWORD, LPWSTR,
       POINTER(DWORD))
_proto(kernel32.CloseHandle, BOOL, c_void_p)
_proto(kernel32.GetCurrentProcessId, DWORD)
_proto(kernel32.GetLongPathNameW, DWORD, LPCWSTR, LPWSTR, DWORD)
_proto(kernel32.CreateMutexW, c_void_p, c_void_p, BOOL, LPCWSTR)

_proto(gdi32.CreateCompatibleDC, c_void_p, c_void_p)
_proto(gdi32.CreateDIBSection, c_void_p, c_void_p, POINTER(BITMAPINFO), c_uint,
       POINTER(c_void_p), c_void_p, DWORD)
_proto(gdi32.SelectObject, c_void_p, c_void_p, c_void_p)
_proto(gdi32.DeleteObject, BOOL, c_void_p)
_proto(gdi32.DeleteDC, BOOL, c_void_p)
_proto(gdi32.GetDeviceCaps, c_int, c_void_p, c_int)

_proto(shell32.ShellExecuteW, c_void_p, c_void_p, LPCWSTR, LPCWSTR, LPCWSTR,
       LPCWSTR, c_int)
_proto(shell32.SHGetFolderPathW, c_long, c_void_p, c_int, c_void_p, DWORD,
       LPWSTR)

# --------------------------------------------------------------------------
# Constantes de Win32
# --------------------------------------------------------------------------
SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE, SW_SHOW = 0, 4, 7, 5
WS_POPUP = 0x80000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x8, 0x80, 0x40000
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
GWL_STYLE, GWL_EXSTYLE = -16, -20
GW_HWNDNEXT, GW_HWNDPREV = 2, 3
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
HWND_BOTTOM, HWND_NOTOPMOST = 1, -2
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 2, 0, 1
CS_DBLCLKS = 0x8
MA_NOACTIVATE = 3
EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT = 0x0003, 0x0000
TME_LEAVE = 0x2
VK_LBUTTON, VK_CONTROL = 0x01, 0x11
SC_RESTORE = 0xF120
SIZE_RESTORED, SIZE_MAXIMIZED = 0, 2
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
ICON_SMALL, ICON_BIG = 0, 1
ERROR_ALREADY_EXISTS = 183

WM_DESTROY, WM_SIZE, WM_CLOSE = 0x0002, 0x0005, 0x0010
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
WM_MOUSEACTIVATE, WM_SETICON = 0x0021, 0x0080
WM_COMMAND, WM_SYSCOMMAND, WM_TIMER = 0x0111, 0x0112, 0x0113
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0203
WM_RBUTTONDOWN, WM_MOUSELEAVE = 0x0204, 0x02A3
WM_APP_REACTIVAR = 0x8001      # mensaje propio (WM_APP + 1)

ID_TIMER_FG, ID_TIMER_SEL, ID_TIMER_ANIM = 1, 2, 3

# Estado global: ¿los iconos nativos fueron ocultados por este programa?
_ESTADO = {"oculto": False}


class ErrorDatos(Exception):
    """Falta la carpeta/JSON o su contenido no es valido."""


# --------------------------------------------------------------------------
# Utilidades de ventanas / shell
# --------------------------------------------------------------------------
def mensaje(texto, titulo=TITULO_APP, icono=0x10):
    """Muestra un cuadro de mensaje (y lo imprime por si hay consola)."""
    try:
        print(f"[{titulo}] {texto}", file=sys.stderr)
    except Exception:
        pass
    try:
        user32.MessageBoxW(None, texto, titulo, icono | 0x10000 | 0x40000)
    except Exception:
        pass


def clase_de(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_de(hwnd):
    pid = DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    return pid.value


def exe_de(pid):
    """Nombre del ejecutable (minusculas) de un proceso, o ''."""
    h = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def ruta_larga(ruta):
    """Convierte una ruta corta 8.3 en ruta larga."""
    buf = ctypes.create_unicode_buffer(32768)
    n = kernel32.GetLongPathNameW(ruta, buf, 32768)
    return buf.value if 0 < n < 32768 else ruta


def buscar_listview():
    """Devuelve el HWND del SysListView32 del escritorio (o None).

    Recorre las ventanas de primer nivel: el SHELLDLL_DefView puede colgar de
    Progman o de un WorkerW, segun la version de Windows / fondo animado.
    """
    resultado = []

    def cb(hwnd, _lp):
        defview = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if defview:
            lv = user32.FindWindowExW(defview, None, "SysListView32", None)
            if lv:
                resultado.append(lv)
                return 0           # detiene la enumeracion
        return 1

    proc = WNDENUMPROC(cb)
    user32.EnumWindows(proc, 0)
    return resultado[0] if resultado else None


def listview_oculto(lv):
    return not (user32.GetWindowLongW(lv, GWL_STYLE) & WS_VISIBLE)


def ocultar_iconos_nativos():
    lv = buscar_listview()
    if not lv:
        print("Aviso: no se encontro el ListView del escritorio.", file=sys.stderr)
        return False
    _ESTADO["oculto"] = True          # antes de ocultar: asi siempre se restaura
    user32.ShowWindow(lv, SW_HIDE)
    return True


def mostrar_iconos_nativos(force=True):
    """Vuelve a mostrar los iconos nativos. Idempotente y segura en atexit."""
    try:
        if not force and not _ESTADO["oculto"]:
            return
        lv = buscar_listview()
        if lv:
            user32.ShowWindow(lv, SW_SHOW)
        _ESTADO["oculto"] = False
    except Exception:
        pass


def ventana_host_escritorio():
    """Ventana visible Progman/WorkerW mas alta en Z (la del fondo/DefView)."""
    cand = []

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and clase_de(hwnd) in ("Progman", "WorkerW"):
            cand.append(hwnd)
            return 0
        return 1

    proc = WNDENUMPROC(cb)       # EnumWindows recorre de arriba hacia abajo
    user32.EnumWindows(proc, 0)
    return cand[0] if cand else None


def minimizar_todas():
    """Equivalente a 'Mostrar escritorio' (comando 419 de la barra de tareas)."""
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    if tray:
        user32.PostMessageW(tray, WM_COMMAND, 419, 0)


def suelo_pantalla(alto):
    """Borde inferior del area de trabajo (encima de la barra de tareas)."""
    r = RECT()
    if user32.SystemParametersInfoW(0x0030, 0, byref(r), 0):   # SPI_GETWORKAREA
        return r.bottom
    return alto


def obtener_dpi():
    f = getattr(user32, "GetDpiForSystem", None)
    if f is not None:
        try:
            f.restype = c_uint
            d = f()
            if d:
                return int(d)
        except Exception:
            pass
    hdc = user32.GetDC(None)
    try:
        return gdi32.GetDeviceCaps(hdc, 88) or 96     # LOGPIXELSX
    finally:
        user32.ReleaseDC(None, hdc)


def configurar_dpi():
    """Proceso DPI-aware por monitor para que las coordenadas coincidan."""
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [c_int]
        if shcore.SetProcessDpiAwareness(2) == 0:       # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _carpeta_especial(csidl):
    buf = ctypes.create_unicode_buffer(32768)
    if shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
        return buf.value
    return ""


def abrir_elemento(texto):
    """Abre el elemento del escritorio cuyo nombre mostrado es `texto`."""
    objetivo = None
    t = texto.strip().lower()
    for csidl in (0x10, 0x19):           # escritorio del usuario y publico
        carpeta = _carpeta_especial(csidl)
        if not carpeta or not os.path.isdir(carpeta):
            continue
        for nombre in os.listdir(carpeta):
            if nombre.lower() == "desktop.ini":
                continue
            if t in (nombre.lower(), os.path.splitext(nombre)[0].lower()):
                objetivo = os.path.join(carpeta, nombre)
                break
        if objetivo:
            break
    if not objetivo:
        objetivo = ESPECIALES.get(t)
    if not objetivo:
        return
    if objetivo.startswith("shell:"):
        shell32.ShellExecuteW(None, "open", "explorer.exe", objetivo, None, 1)
    else:
        shell32.ShellExecuteW(None, "open", objetivo, None,
                              os.path.dirname(objetivo), 1)


# --------------------------------------------------------------------------
# Datos de entrada
# --------------------------------------------------------------------------
def cargar_datos():
    """Lee el JSON (utf-8-sig). Lanza ErrorDatos con un mensaje claro."""
    carpeta = ruta_larga(os.path.join(tempfile.gettempdir(), NOMBRE_CARPETA))
    if not os.path.isdir(carpeta):
        raise ErrorDatos(f"No existe la carpeta de iconos:\n{carpeta}")
    ruta = os.path.join(carpeta, NOMBRE_JSON)
    if not os.path.isfile(ruta):
        raise ErrorDatos(f"No se encontro el archivo:\n{ruta}")
    try:
        with open(ruta, "r", encoding="utf-8-sig") as f:
            datos = json.load(f)
    except (OSError, ValueError) as e:
        raise ErrorDatos(f"No se pudo leer {NOMBRE_JSON}:\n{e}")
    if not isinstance(datos, dict) or not isinstance(datos.get("iconos"), list) \
            or not datos["iconos"]:
        raise ErrorDatos(f"{NOMBRE_JSON} no contiene una lista 'iconos' valida.")
    return carpeta, datos


# --------------------------------------------------------------------------
# Renderizado con Pillow (independiente de Win32)
# --------------------------------------------------------------------------
def _rect(d):
    """{'x','y','ancho','alto'} -> (x, y, w, h) o None."""
    try:
        return (int(d["x"]), int(d["y"]), int(d["ancho"]), int(d["alto"]))
    except (TypeError, KeyError, ValueError):
        return None


def _union(rs):
    x0 = min(r[0] for r in rs)
    y0 = min(r[1] for r in rs)
    x1 = max(r[0] + r[2] for r in rs)
    y1 = max(r[1] + r[3] for r in rs)
    return (x0, y0, x1 - x0, y1 - y0)


def _componer_en(dst, src, x, y):
    """alpha_composite tolerante a coordenadas negativas / fuera de limites."""
    sx, sy = max(0, -x), max(0, -y)
    dx, dy = max(0, x), max(0, y)
    w = min(src.width - sx, dst.width - dx)
    h = min(src.height - sy, dst.height - dy)
    if w > 0 and h > 0:
        dst.alpha_composite(src.crop((sx, sy, sx + w, sy + h)), (dx, dy))


def _a_bgra_premult(img):
    """RGBA (PIL) -> bytes BGRA con alfa premultiplicado (para UpdateLayeredWindow)."""
    r, g, b, a = img.split()
    return Image.merge("RGBA", (b, g, r, a)).convert("RGBa").tobytes()


def _medir(fuente, s):
    try:
        return fuente.getlength(s)
    except AttributeError:
        return fuente.getsize(s)[0]


def cargar_fuente(dpi):
    """Segoe UI de ~9 pt escalada al DPI."""
    px = max(8, round(9 * dpi / 72))
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for ruta in (os.path.join(windir, "Fonts", "segoeui.ttf"), "segoeui.ttf",
                 "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(ruta, px), px
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px), px
    except TypeError:
        return ImageFont.load_default(), px


def ajustar_texto(texto, fuente, ancho, max_lineas=2):
    """Divide en <= max_lineas lineas; la ultima se trunca con '…'."""
    restante = " ".join(texto.split())
    lineas = []

    def elipsis(s):
        while s and _medir(fuente, s + "…") > ancho:
            s = s[:-1]
        return s.rstrip() + "…"

    while restante and len(lineas) < max_lineas:
        if _medir(fuente, restante) <= ancho:
            lineas.append(restante)
            break
        if len(lineas) == max_lineas - 1:
            lineas.append(elipsis(restante))
            break
        k = 0                                   # mayor prefijo que cabe
        for j in range(1, len(restante) + 1):
            if _medir(fuente, restante[:j]) <= ancho:
                k = j
            else:
                break
        k = max(k, 1)
        corte = restante.rfind(" ", 0, k + 1)   # preferir cortar en espacio
        if corte > 0:
            lineas.append(restante[:corte].rstrip())
            restante = restante[corte + 1:].lstrip()
        else:
            lineas.append(restante[:k])
            restante = restante[k:].lstrip()
    return lineas


class Elemento:
    __slots__ = ("texto", "lim", "caja", "contenido")


class Escena:
    """Prepara iconos/etiquetas y compone regiones RGBA bajo demanda."""

    def __init__(self, carpeta, datos, ancho, alto, dpi):
        self.w, self.h = ancho, alto
        self.fuente, px = cargar_fuente(dpi)
        try:
            asc, desc = self.fuente.getmetrics()
            self.alto_linea = asc + desc
        except AttributeError:
            self.alto_linea = px + 4
        self.escala = dpi / 96.0
        self.elementos = []
        self._cache = {}
        for it in datos["iconos"]:
            try:
                el = self._crear(carpeta, it)
            except Exception as e:                       # un icono malo no tumba todo
                print(f"Aviso: icono omitido ({it.get('texto')!r}): {e}",
                      file=sys.stderr)
                continue
            if el is not None:
                self.elementos.append(el)
        if not self.elementos:
            raise ErrorDatos("No se pudo preparar ningun icono del JSON.")

    # -- construccion de un icono ------------------------------------------
    def _cargar_png(self, ruta, lado):
        """PNG -> RGBA escalado con LANCZOS conservando proporcion."""
        img = Image.open(ruta).convert("RGBA")
        s = lado / max(img.width, img.height)
        nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
        if (nw, nh) != img.size:
            img = img.resize((nw, nh), LANCZOS)
        return img

    def _crear(self, carpeta, it):
        texto = str(it.get("texto") or "")
        lim = _rect(it.get("limites"))
        ico = _rect(it.get("icono"))
        eti = _rect(it.get("etiqueta"))
        if lim is None:
            partes = [r for r in (ico, eti) if r]
            if not partes:
                raise ValueError("sin geometria")
            lim = _union(partes)

        # Cuadrado del icono: lado menor, centrado en "icono"
        if ico and ico[2] > 0 and ico[3] > 0:
            lado = min(ico[2], ico[3])
            ix = ico[0] + (ico[2] - lado) // 2
            iy = ico[1] + (ico[3] - lado) // 2
        else:                                   # parte superior de "limites"
            lado = max(16, min(lim[2], lim[3] // 2))
            ix = lim[0] + (lim[2] - lado) // 2
            iy = lim[1] + max(2, lim[3] // 20)
        if not eti or eti[2] <= 0 or eti[3] <= 0:
            top = iy + lado + 2
            eti = (lim[0], top, lim[2], max(self.alto_linea, lim[1] + lim[3] - top))

        imagen = None
        archivo = it.get("archivo")
        if archivo:
            ruta = os.path.join(carpeta, str(archivo))
            if os.path.isfile(ruta):
                try:
                    imagen = self._cargar_png(ruta, lado)
                except Exception as e:
                    print(f"Aviso: no se pudo cargar {archivo}: {e}", file=sys.stderr)
            else:
                print(f"Aviso: falta el PNG {archivo}", file=sys.stderr)

        lineas = ajustar_texto(texto, self.fuente, max(1, eti[2])) if texto else []

        # Caja de dibujo = union de limites, icono y etiqueta (+ margen)
        eti_ext = (eti[0], eti[1], eti[2], max(eti[3], 2 * self.alto_linea))
        cx, cy, cw, ch = _union([lim, (ix, iy, lado, lado), eti_ext])
        cx, cy, cw, ch = cx - 4, cy - 4, cw + 8, ch + 8
        x0, y0 = max(0, cx), max(0, cy)
        x1, y1 = min(self.w, cx + cw), min(self.h, cy + ch)
        if x1 <= x0 or y1 <= y0:
            return None                          # fuera de pantalla
        caja = (x0, y0, x1 - x0, y1 - y0)

        # Contenido (icono + etiqueta) en coordenadas relativas a la caja
        contenido = Image.new("RGBA", (caja[2], caja[3]), (0, 0, 0, 0))
        if imagen is not None:
            px = ix + (lado - imagen.width) // 2 - caja[0]
            py = iy + (lado - imagen.height) // 2 - caja[1]
            _componer_en(contenido, imagen, px, py)
        if lineas:
            _componer_en(contenido, self._capa_texto(caja, lineas, eti), 0, 0)

        el = Elemento()
        el.texto, el.lim, el.caja, el.contenido = texto, lim, caja, contenido
        return el

    def _capa_texto(self, caja, lineas, eti):
        """Texto blanco con sombra suave, centrado en la etiqueta."""
        size = (caja[2], caja[3])
        m_txt = Image.new("L", size, 0)
        m_som = Image.new("L", size, 0)
        dt, ds = ImageDraw.Draw(m_txt), ImageDraw.Draw(m_som)
        for k, linea in enumerate(lineas):
            x = eti[0] - caja[0] + int((eti[2] - _medir(self.fuente, linea)) // 2)
            y = eti[1] - caja[1] + k * self.alto_linea
            ds.text((x + 1, y + 1), linea, font=self.fuente, fill=255)
            dt.text((x, y), linea, font=self.fuente, fill=255)
        m_som = m_som.filter(ImageFilter.GaussianBlur(1.2)).point(
            lambda v: min(255, int(v * 1.7)))
        capa = Image.new("RGBA", size, (0, 0, 0, 0))
        sombra = Image.new("RGBA", size, (0, 0, 0, 255))
        sombra.putalpha(m_som)
        blanco = Image.new("RGBA", size, (255, 255, 255, 255))
        blanco.putalpha(m_txt)
        capa.alpha_composite(sombra)
        capa.alpha_composite(blanco)
        return capa

    # -- composicion ----------------------------------------------------------
    def tile(self, i, estado):
        """Imagen RGBA de un icono. estado: bit0=hover, bit1=seleccionado."""
        clave = (i, estado)
        t = self._cache.get(clave)
        if t is not None:
            return t
        el = self.elementos[i]
        cx, cy, cw, ch = el.caja
        t = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        lx, ly = el.lim[0] - cx, el.lim[1] - cy
        lw, lh = el.lim[2], el.lim[3]
        # Fondo casi invisible (alfa 2): permite recibir el mouse en la celda
        ImageDraw.Draw(t).rectangle([lx, ly, lx + lw - 1, ly + lh - 1],
                                    fill=(0, 0, 0, 2))
        if estado:
            relleno = {1: COL_HOVER, 2: COL_SEL, 3: COL_SEL_HOVER}[estado]
            capa = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            ImageDraw.Draw(capa).rounded_rectangle(
                [lx, ly, lx + lw - 1, ly + lh - 1],
                radius=max(2, round(3 * self.escala)), fill=relleno,
                outline=COL_BORDE, width=1)
            t.alpha_composite(capa)
        t.alpha_composite(el.contenido)
        self._cache[clave] = t
        return t

    def componer(self, rect, hover, sel):
        """Compone la region rect=(x,y,w,h) de la pantalla."""
        x, y, w, h = rect
        reg = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for i, el in enumerate(self.elementos):
            cx, cy, cw, ch = el.caja
            if cx >= x + w or cy >= y + h or cx + cw <= x or cy + ch <= y:
                continue
            est = (1 if i == hover else 0) | (2 if i in sel else 0)
            _componer_en(reg, self.tile(i, est), cx - x, cy - y)
        return reg

    def hit(self, px, py):
        """Indice del icono cuya zona 'limites' contiene el punto, o -1."""
        for i in range(len(self.elementos) - 1, -1, -1):
            x, y, w, h = self.elementos[i].lim
            if x <= px < x + w and y <= py < y + h:
                return i
        return -1


# --------------------------------------------------------------------------
# Animacion de grietas: recursos, fisica y compositor (numpy + OpenCV)
# --------------------------------------------------------------------------
def buscar_recurso(nombre, carpetas):
    """Busca un archivo (sin distinguir mayusculas) en una lista de carpetas."""
    for c in carpetas:
        if not c or not os.path.isdir(c):
            continue
        ruta = os.path.join(c, nombre)
        if os.path.isfile(ruta):
            return ruta
        try:
            for f in os.listdir(c):
                if f.lower() == nombre.lower():
                    return os.path.join(c, f)
        except OSError:
            pass
    return None


def _img_a_np(img):
    """Imagen PIL RGBA -> array (alto, ancho, 4) BGRA premultiplicado."""
    return np.frombuffer(_a_bgra_premult(img), np.uint8).reshape(
        img.height, img.width, 4).copy()


def _src_over(src, dst):
    """Composicion 'src sobre dst' con alfa premultiplicado (uint8, via OpenCV).
    Con alfa 0 y color > 0 se comporta como mezcla aditiva."""
    src = np.ascontiguousarray(src)
    dst = np.ascontiguousarray(dst)
    inv = cv2.bitwise_not(cv2.extractChannel(src, 3))
    inv4 = cv2.merge((inv, inv, inv, inv))
    return cv2.add(src, cv2.multiply(dst, inv4, scale=1.0 / 255.0))


def _pegar(buf, spr, x, y):
    """Pega el sprite `spr` en `buf` en (x, y) relativo a buf (recorta bordes)."""
    h, w = spr.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(buf.shape[1], x + w), min(buf.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    buf[y0:y1, x0:x1] = _src_over(spr[y0 - y:y1 - y, x0 - x:x1 - x],
                                  buf[y0:y1, x0:x1])


def _fusionar(rects):
    """Une rectangulos (x0, y0, x1, y1) que se tocan para repintar menos."""
    rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
    cambiado = True
    while cambiado:
        cambiado = False
        salida = []
        for r in rects:
            for j, o in enumerate(salida):
                if r[0] <= o[2] and r[2] >= o[0] and r[1] <= o[3] and r[3] >= o[1]:
                    salida[j] = (min(r[0], o[0]), min(r[1], o[1]),
                                 max(r[2], o[2]), max(r[3], o[3]))
                    cambiado = True
                    break
            else:
                salida.append(r)
        rects = salida
    return rects


class RecursosAnim:
    """Carga UNA sola vez las grietas y la mascara y localiza el video."""

    def __init__(self, ancho, alto, carpeta_iconos):
        self.W, self.H = ancho, alto
        carpetas = list(CARPETAS_RECURSOS or []) + [
            os.path.dirname(os.path.abspath(__file__)), carpeta_iconos,
            os.getcwd()]
        self.grietas = []
        for n in range(5):
            nombre = ARCHIVO_GRIETA.format(n=n)
            ruta = buscar_recurso(nombre, carpetas)
            if not ruta:
                print(f"Aviso: falta {nombre}", file=sys.stderr)
                self.grietas.append(None)
                continue
            try:
                img = Image.open(ruta).convert("RGBA").resize((ancho, alto), LANCZOS)
                self.grietas.append(_img_a_np(img))
            except Exception as e:
                print(f"Aviso: no se pudo cargar {nombre}: {e}", file=sys.stderr)
                self.grietas.append(None)
        if not any(g is not None for g in self.grietas):
            raise RuntimeError("no se encontro ninguna imagen grieta0..4.png")

        # Mascara: solo se dibuja el video donde hay color solido (alfa > 0)
        self.mascara = None
        self.bbox = None
        ruta_m = buscar_recurso(ARCHIVO_MASCARA, carpetas)
        if ruta_m:
            try:
                img = Image.open(ruta_m).convert("RGBA")
                canal = img.getchannel("A")
                if canal.getextrema() == (255, 255):      # sin transparencia:
                    canal = img.convert("L")              # usar luminosidad
                m = np.array(canal.resize((ancho, alto), LANCZOS), dtype=np.uint8)
                filas = np.flatnonzero(m.any(axis=1))
                cols = np.flatnonzero(m.any(axis=0))
                if filas.size and cols.size:
                    self.mascara = m
                    self.bbox = (int(cols[0]), int(filas[0]),
                                 int(cols[-1]) + 1, int(filas[-1]) + 1)
            except Exception as e:
                print(f"Aviso: no se pudo leer {ARCHIVO_MASCARA}: {e}", file=sys.stderr)
        else:
            print(f"Aviso: falta {ARCHIVO_MASCARA}", file=sys.stderr)
        self.ruta_video = buscar_recurso(ARCHIVO_VIDEO, carpetas)
        if not self.ruta_video:
            print(f"Aviso: falta {ARCHIVO_VIDEO}", file=sys.stderr)


class Cuerpo:
    """Icono con fisica: fijo -> colgado (pendulo) -> libre (cae)."""
    __slots__ = ("i", "tile", "cw", "ch", "c0x", "c0y", "px", "py", "rx", "ry",
                 "ip", "i0", "ciclo_colgar", "ciclo_caer", "modo", "th", "om",
                 "cx", "cy", "vx", "vy", "ph", "wph", "grab", "key", "bbox",
                 "dormido")


class Animacion:
    """Secuencia de grietas + temblor + fisica de iconos + video enmascarado.

    Mantiene `base` (grieta + fuego) y una lista de sprites (un icono = un
    sprite). En cada tick solo repinta las zonas sucias dentro del DIB.
    """

    def __init__(self, escena, recursos, dpi, suelo):
        self.esc, self.rec = escena, recursos
        self.W, self.H = recursos.W, recursos.H
        self.k = dpi / 96.0
        self.suelo = min(suelo, self.H)
        self.rng = random.Random(ANIM_SEMILLA)
        self.activa = False
        self.dib = None
        self.cuerpos = {}
        self._tiles = {}
        self._hilo = None
        self._parar = threading.Event()
        self._lock = threading.Lock()
        self._fuego = None

    # -- utilidades -----------------------------------------------------------
    def _estado(self, i):
        return (1 if i == self.hover else 0) | (2 if i in self.sel else 0)

    def _tile_np(self, i, est):
        t = self._tiles.get((i, est))
        if t is None:
            img = self.esc.tile(i, est)
            t = np.frombuffer(_a_bgra_premult(img), np.uint8).reshape(
                img.height, img.width, 4)
            self._tiles[(i, est)] = t
        return t

    def _centro(self, b):
        if b.modo == "colgado":
            c, s = math.cos(b.th), math.sin(b.th)
            return b.px + c * b.rx - s * b.ry, b.py + s * b.rx + c * b.ry
        if b.modo == "libre":
            return b.cx, b.cy
        return b.c0x, b.c0y

    @staticmethod
    def _rotar(tile, ang):
        """Gira un sprite premultiplicado (angulo horario positivo en pantalla)."""
        h, w = tile.shape[:2]
        c, s = abs(math.cos(ang)), abs(math.sin(ang))
        nw, nh = int(math.ceil(w * c + h * s)) + 2, int(math.ceil(w * s + h * c)) + 2
        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -math.degrees(ang), 1.0)
        M[0, 2] += nw / 2.0 - w / 2.0
        M[1, 2] += nh / 2.0 - h / 2.0
        return cv2.warpAffine(tile, M, (nw, nh), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT,
                              borderValue=(0, 0, 0, 0))

    # -- inicio / fin -----------------------------------------------------------
    def iniciar(self, dib, hover, sel):
        self.dib = dib
        self.hover, self.sel = hover, set(sel)
        self.cero = np.zeros((self.H, self.W, 4), np.uint8)
        self.fondo = self.cero
        self.base = self.cero.copy()
        self.sprites = [[el.caja[0], el.caja[1], self._tile_np(i, self._estado(i))]
                        for i, el in enumerate(self.esc.elementos)]
        self.cuerpos = {}
        n = len(self.esc.elementos)
        for i in range(n):
            if self.rng.random() < PROB_ICONO_AFECTADO:
                self._crear_cuerpo(i)
        if not self.cuerpos and n:
            self._crear_cuerpo(self.rng.randrange(n))
        self.sucios, self.sucio_total = [], True
        self.pendientes = []
        self.t_ult = None
        self.cur_prev, self.vm = None, (0.0, 0.0)
        self.boton_prev, self.grab_b = False, None
        self.temblor = (0.0, 0.0)
        self.activa = True
        self._entrar_etapa(0, time.perf_counter())

    def detener(self):
        self._parar.set()
        h, self._hilo = self._hilo, None
        if h is not None and h.is_alive():
            h.join(timeout=1.5)
        self.activa = False
        self.dib = None
        self.base = self.fondo = self.cero = None
        self.sprites, self.cuerpos, self.pendientes = [], {}, []

    def _crear_cuerpo(self, i):
        el = self.esc.elementos[i]
        cx, cy, cw, ch = el.caja
        lx, ly, lw, lh = el.lim
        b = Cuerpo()
        b.i, b.tile, b.cw, b.ch = i, self._tile_np(i, 0), cw, ch
        b.c0x, b.c0y = cx + cw / 2.0, cy + ch / 2.0
        izq = self.rng.random() < 0.5                 # esquina superior izq. o der.
        b.px, b.py = (lx if izq else lx + lw), ly
        b.rx, b.ry = b.c0x - b.px, b.c0y - b.py
        b.i0 = (cw * cw + ch * ch) / 12.0
        b.ip = b.rx * b.rx + b.ry * b.ry + b.i0
        b.ciclo_colgar = self.rng.randint(0, 2)       # ciclo en que empieza a colgar
        b.ciclo_caer = self.rng.randint(b.ciclo_colgar + 1,
                                        min(b.ciclo_colgar + 2, 4))
        b.modo = "fijo"
        b.th = b.om = b.vx = b.vy = b.ph = b.wph = 0.0
        b.cx, b.cy = b.c0x, b.c0y
        b.grab, b.key, b.dormido = None, None, False
        b.bbox = (cx, cy, cx + cw, cy + ch)
        self.cuerpos[i] = b

    def _colgar(self, b):
        b.modo, b.th, b.dormido = "colgado", 0.0, False
        b.om = self.rng.uniform(0.6, 1.4) * self.rng.choice((-1, 1))   # primer tiron

    def _soltar(self, b):
        if b.modo == "colgado":
            c, s = math.cos(b.th), math.sin(b.th)
            rpx, rpy = c * b.rx - s * b.ry, s * b.rx + c * b.ry
            b.cx, b.cy = b.px + rpx, b.py + rpy
            b.vx, b.vy = -b.om * rpy, b.om * rpx
            b.ph, b.wph = b.th, b.om
        else:
            b.cx, b.cy, b.vx, b.vy, b.ph = b.c0x, b.c0y, 0.0, 0.0, 0.0
            b.wph = self.rng.uniform(-1.5, 1.5)
        b.vx += self.rng.uniform(-80, 80) * self.k
        b.modo, b.grab, b.dormido = "libre", None, False
        if self.grab_b is b:
            self.grab_b = None

    def _entrar_etapa(self, k, ahora):
        self.etapa, self.t_etapa = k, ahora
        g = self.rec.grietas[k] if k < len(self.rec.grietas) else None
        self.fondo = g if g is not None else self.cero
        self.base[...] = self.fondo
        for b in self.cuerpos.values():
            if b.modo == "fijo" and k >= b.ciclo_colgar:
                self._colgar(b)
            if b.modo == "colgado" and k >= b.ciclo_caer and k < 4:
                self._soltar(b)
        amp = TEMBLOR_AMPLITUD[min(k, len(TEMBLOR_AMPLITUD) - 1)] * self.k
        self.temblor = (ahora, amp)
        self.sucio_total = True
        if k == 4:
            self._programar_caida_total(ahora)
            self._iniciar_video()

    def _programar_caida_total(self, ahora):
        """Etapa 4: TODOS los iconos caen, empezando por los del centro."""
        cx0, cy0 = self.W / 2.0, self.H / 2.0
        lista = []
        for i in range(len(self.esc.elementos)):
            b = self.cuerpos.get(i)
            if b is not None and b.modo == "libre":
                continue                                   # ya cayo
            if b is None:                                  # no era uno de los sorteados
                self._crear_cuerpo(i)
                b = self.cuerpos[i]
            lista.append((math.hypot(b.c0x - cx0, b.c0y - cy0), b))
        dmax = max((d for d, _ in lista), default=1.0) or 1.0
        self.pendientes = [(ahora + d / dmax * CAIDA_FINAL_DURACION
                            + self.rng.uniform(0.0, 0.08), b) for d, b in lista]

    # -- estado externo (hover / seleccion) y hit-test ---------------------------
    def actualizar_estado(self, indices, hover, sel):
        self.hover, self.sel = hover, set(sel)
        for i in indices:
            if not 0 <= i < len(self.sprites):
                continue
            b = self.cuerpos.get(i)
            if b is not None and b.modo != "fijo":
                continue
            x, y, w, h = self.esc.elementos[i].caja
            self.sprites[i] = [x, y, self._tile_np(i, self._estado(i))]
            self.sucios.append((x, y, x + w, y + h))

    def hit(self, px, py):
        """Icono estatico bajo el punto (los que ya cuelgan o caen se ignoran)."""
        for i in range(len(self.esc.elementos) - 1, -1, -1):
            b = self.cuerpos.get(i)
            if b is not None and b.modo != "fijo":
                continue
            x, y, w, h = self.esc.elementos[i].lim
            if x <= px < x + w and y <= py < y + h:
                return i
        return -1

    # -- fisica -------------------------------------------------------------------
    def _paso_pendulo(self, b, h, cur):
        G = GRAVEDAD * self.k
        c, s = math.cos(b.th), math.sin(b.th)
        rpx = c * b.rx - s * b.ry
        alfa = rpx * G / b.ip - AMORT_PENDULO * b.om       # gravedad + roce
        if b is self.grab_b:
            gx, gy = b.grab
            qx = c * (b.rx + gx) - s * (b.ry + gy)
            qy = s * (b.rx + gx) + c * (b.ry + gy)
            fx = AGARRE_K * (cur[0] - (b.px + qx)) - AGARRE_D * (-b.om * qy)
            fy = AGARRE_K * (cur[1] - (b.py + qy)) - AGARRE_D * (b.om * qx)
            alfa += (qx * fy - qy * fx) / b.ip
        b.om = max(-OMEGA_MAX, min(OMEGA_MAX, b.om + alfa * h))
        b.th += b.om * h

    def _paso_libre(self, b, h, cur):
        G = GRAVEDAD * self.k
        b.vy += G * h
        if b is self.grab_b:
            c, s = math.cos(b.ph), math.sin(b.ph)
            gx, gy = b.grab
            qx, qy = c * gx - s * gy, s * gx + c * gy
            fx = AGARRE_K * (cur[0] - (b.cx + qx)) - AGARRE_D * (b.vx - b.wph * qy)
            fy = AGARRE_K * (cur[1] - (b.cy + qy)) - AGARRE_D * (b.vy + b.wph * qx)
            b.vx += fx * h
            b.vy += fy * h
            b.wph += (qx * fy - qy * fx) / b.i0 * h
        b.wph = max(-OMEGA_MAX, min(OMEGA_MAX, b.wph))
        b.cx += b.vx * h
        b.cy += b.vy * h
        b.ph += b.wph * h
        sn, cs = abs(math.sin(b.ph)), abs(math.cos(b.ph))
        hx = (b.cw * cs + b.ch * sn) / 2.0 - 4
        hy = (b.cw * sn + b.ch * cs) / 2.0 - 4
        if b.cx < hx:
            b.cx, b.vx = hx, abs(b.vx) * REBOTE
        elif b.cx > self.W - hx:
            b.cx, b.vx = self.W - hx, -abs(b.vx) * REBOTE
        tope = self.suelo - hy
        if b.cy >= tope:                                  # choca con el suelo
            b.cy = tope
            if b.vy > 0:
                b.vy = -b.vy * REBOTE
                b.vx *= 0.8
                b.wph *= 0.6
            if abs(b.vy) < 90 * self.k:                   # en reposo
                b.vy = 0.0
                b.vx *= max(0.0, 1 - 5 * h)
                b.wph *= max(0.0, 1 - 8 * h)
                obj = round(b.ph / (math.pi / 2)) * (math.pi / 2)
                b.ph += (obj - b.ph) * min(1.0, 8 * h)    # se acuesta plano
                if (abs(b.vx) < 4 * self.k and abs(b.wph) < 0.03
                        and abs(obj - b.ph) < 0.003 and not b is self.grab_b):
                    b.ph, b.vx, b.wph, b.dormido = obj, 0.0, 0.0, True

    def _empujar(self, b, cur, dt):
        """El mouse empuja al icono (iguala parcialmente su velocidad)."""
        cx, cy = self._centro(b)
        if math.hypot(cur[0] - cx, cur[1] - cy) > 0.42 * math.hypot(b.cw, b.ch):
            return
        acople = min(1.0, EMPUJE_RATON * dt) * 0.6
        if b.modo == "colgado":
            qx, qy = cur[0] - b.px, cur[1] - b.py
            jx = (self.vm[0] - (-b.om * qy)) * acople
            jy = (self.vm[1] - (b.om * qx)) * acople
            b.om = max(-OMEGA_MAX, min(OMEGA_MAX, b.om + (qx * jy - qy * jx) / b.ip))
        else:
            qx, qy = cur[0] - b.cx, cur[1] - b.cy
            jx = (self.vm[0] - (b.vx - b.wph * qy)) * acople
            jy = (self.vm[1] - (b.vy + b.wph * qx)) * acople
            b.vx += jx
            b.vy += jy
            b.wph += (qx * jy - qy * jx) / b.i0
            b.dormido = False

    def _intentar_agarrar(self, cur):
        mejor, dmin = None, 1e18
        for b in self.cuerpos.values():
            if b.modo == "fijo":
                continue
            cx, cy = self._centro(b)
            d = math.hypot(cur[0] - cx, cur[1] - cy)
            if d < 0.45 * math.hypot(b.cw, b.ch) and d < dmin:
                mejor, dmin = b, d
        if mejor is not None:
            cx, cy = self._centro(mejor)
            ang = mejor.th if mejor.modo == "colgado" else mejor.ph
            c, s = math.cos(ang), math.sin(ang)
            dx, dy = cur[0] - cx, cur[1] - cy
            mejor.grab = (c * dx + s * dy, -s * dx + c * dy)   # coordenadas del cuerpo
            mejor.dormido = False
            self.grab_b = mejor

    # -- video enmascarado (hilo aparte: decodifica, escala y aplica la mascara) --
    def _iniciar_video(self):
        if not (self.rec.ruta_video and self.rec.mascara is not None):
            return
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._bucle_video,
                                      args=(self._parar,), daemon=True)
        self._hilo.start()

    def _bucle_video(self, parar):
        cap = None
        try:
            ruta, W, H = self.rec.ruta_video, self.W, self.H
            cap = cv2.VideoCapture(ruta)
            if not cap.isOpened():
                print(f"Aviso: OpenCV no pudo abrir {ruta}", file=sys.stderr)
                return
            vfps = cap.get(cv2.CAP_PROP_FPS)
            vfps = vfps if 1.0 <= vfps <= 240.0 else 30.0
            geom = None
            siguiente = ultimo_pub = time.perf_counter()
            fallos = 0
            while not parar.is_set():
                ok, frame = cap.read()
                if not ok:                                # fin: reiniciar en bucle
                    cap.release()
                    cap = cv2.VideoCapture(ruta)
                    fallos += 1
                    if fallos > 3 or not cap.isOpened():
                        break
                    continue
                fallos = 0
                if geom is None:
                    geom = self._geometria_video(frame.shape[1], frame.shape[0])
                    if geom is None:
                        print("Aviso: el video no se solapa con la mascara.",
                              file=sys.stderr)
                        return
                ahora = time.perf_counter()
                if ahora - ultimo_pub >= 1.0 / VIDEO_FPS_MAX - 0.002:
                    ultimo_pub = ahora
                    self._publicar_frame(frame, geom)
                siguiente += 1.0 / vfps                   # ritmo nativo del video
                espera = siguiente - time.perf_counter()
                if espera > 0:
                    parar.wait(espera)
                else:
                    siguiente = time.perf_counter()
        except Exception:
            traceback.print_exc()
        finally:
            if cap is not None:
                cap.release()

    def _geometria_video(self, vw, vh):
        """Tamano y posicion del video segun VIDEO_AJUSTE/ESCALA/POS_X/POS_Y."""
        W, H = self.W, self.H
        base = max(W / vw, H / vh) if VIDEO_AJUSTE == "cubrir" else 1.0
        s = base * VIDEO_ESCALA
        tw, th = max(1, round(vw * s)), max(1, round(vh * s))
        ox = round(W / 2.0 + VIDEO_POS_X - tw / 2.0)
        oy = round(H / 2.0 + VIDEO_POS_Y - th / 2.0)
        bx0, by0, bx1, by1 = self.rec.bbox
        rx0, ry0 = max(ox, bx0, 0), max(oy, by0, 0)
        rx1, ry1 = min(ox + tw, bx1, W), min(oy + th, by1, H)
        if rx1 <= rx0 or ry1 <= ry0:
            return None
        m = np.ascontiguousarray(self.rec.mascara[ry0:ry1, rx0:rx1])
        return {"tw": tw, "th": th, "ox": ox, "oy": oy, "r": (rx0, ry0, rx1, ry1),
                "m": m, "m3": cv2.merge((m, m, m))}

    def _publicar_frame(self, frame, g):
        rx0, ry0, rx1, ry1 = g["r"]
        if frame.shape[1] != g["tw"] or frame.shape[0] != g["th"]:
            frame = cv2.resize(frame, (g["tw"], g["th"]), interpolation=cv2.INTER_LINEAR)
        crop = np.ascontiguousarray(
            frame[ry0 - g["oy"]:ry1 - g["oy"], rx0 - g["ox"]:rx1 - g["ox"]])
        spr = np.empty((ry1 - ry0, rx1 - rx0, 4), np.uint8)
        spr[..., :3] = cv2.multiply(crop, g["m3"], scale=1.0 / 255.0)  # premultiplica
        spr[..., 3] = g["m"] if VIDEO_MEZCLA != "aditivo" else 0
        with self._lock:
            self._fuego = (rx0, ry0, spr)

    def _tomar_fuego(self):
        with self._lock:
            f, self._fuego = self._fuego, None
        return f

    # -- bucle de animacion ---------------------------------------------------------
    def tick(self, cur, boton):
        """Avanza la animacion. `cur` = cursor en coordenadas de la capa.
        Devuelve (hubo_cambio, (dx, dy)) con el desplazamiento del temblor."""
        if not self.activa:
            return False, (0, 0)
        ahora = time.perf_counter()
        dt = 0.016 if self.t_ult is None else min(0.05, max(0.001, ahora - self.t_ult))
        self.t_ult = ahora

        # etapas (grietas)
        while self.etapa < 4:
            dur = (DURACIONES_ETAPA[self.etapa]
                   if self.etapa < len(DURACIONES_ETAPA) else None)
            if dur is None or ahora - self.t_etapa < dur:
                break
            self._entrar_etapa(self.etapa + 1, ahora)

        # caida final escalonada (del centro hacia afuera)
        if self.pendientes:
            esperan = []
            for t, b in self.pendientes:
                if ahora >= t:
                    if b.modo != "libre":
                        self._soltar(b)
                else:
                    esperan.append((t, b))
            self.pendientes = esperan

        # velocidad del mouse (suavizada) y agarre
        if self.cur_prev is not None:
            vx, vy = (cur[0] - self.cur_prev[0]) / dt, (cur[1] - self.cur_prev[1]) / dt
            self.vm = (0.5 * self.vm[0] + 0.5 * vx, 0.5 * self.vm[1] + 0.5 * vy)
        self.cur_prev = cur
        if boton and not self.boton_prev and self.grab_b is None:
            self._intentar_agarrar(cur)
        if not boton and self.grab_b is not None:
            self.grab_b.grab, self.grab_b = None, None
        self.boton_prev = boton
        if math.hypot(*self.vm) > 120 * self.k:
            for b in self.cuerpos.values():
                if b.modo != "fijo" and b is not self.grab_b:
                    self._empujar(b, cur, dt)

        # fisica con subpasos
        n = max(1, int(math.ceil(dt / 0.008)))
        h = dt / n
        for _ in range(n):
            for b in self.cuerpos.values():
                if b.modo == "colgado":
                    self._paso_pendulo(b, h, cur)
                elif b.modo == "libre" and not b.dormido:
                    self._paso_libre(b, h, cur)

        # sprites de los cuerpos en movimiento
        for b in self.cuerpos.values():
            if b.modo == "fijo":
                continue
            cx, cy = self._centro(b)
            ang = b.th if b.modo == "colgado" else b.ph
            key = (round(math.degrees(ang) * 4), round(cx), round(cy))
            if key == b.key:
                continue
            b.key = key
            spr = self._rotar(b.tile, ang)
            sh, sw = spr.shape[:2]
            sx, sy = int(round(cx - sw / 2.0)), int(round(cy - sh / 2.0))
            self.sucios.append(b.bbox)
            b.bbox = (sx, sy, sx + sw, sy + sh)
            self.sucios.append(b.bbox)
            self.sprites[b.i] = [sx, sy, spr]

        # fuego: se mezcla sobre la grieta en `base`
        fuego = self._tomar_fuego()
        if fuego is not None:
            rx0, ry0, spr = fuego
            rh, rw = spr.shape[:2]
            self.base[ry0:ry0 + rh, rx0:rx0 + rw] = _src_over(
                spr, self.fondo[ry0:ry0 + rh, rx0:rx0 + rw])
            self.sucios.append((rx0, ry0, rx0 + rw, ry0 + rh))

        # repintar zonas sucias dentro del DIB
        if self.sucio_total:
            rects = [(0, 0, self.W, self.H)]
        else:
            rects = _fusionar([(max(0, a), max(0, b_), min(self.W, c), min(self.H, d))
                               for a, b_, c, d in self.sucios])
        self.sucios, self.sucio_total = [], False
        for x0, y0, x1, y1 in rects:
            buf = self.base[y0:y1, x0:x1].copy()
            for sx, sy, arr in self.sprites:
                ah, aw = arr.shape[:2]
                if sx >= x1 or sy >= y1 or sx + aw <= x0 or sy + ah <= y0:
                    continue
                _pegar(buf, arr, sx - x0, sy - y0)
            self.dib[y0:y1, x0:x1] = buf

        # temblor de pantalla: se mueve la capa entera (coste casi nulo)
        t0, amp = self.temblor
        if ahora - t0 < TEMBLOR_DURACION:
            f = 1.0 - (ahora - t0) / TEMBLOR_DURACION
            off = (int(self.rng.uniform(-1, 1) * amp * f),
                   int(self.rng.uniform(-1, 1) * amp * f))
        else:
            off = (0, 0)
        return bool(rects), off


# --------------------------------------------------------------------------
# Icono propio de la aplicacion (boton de la barra de tareas)
# --------------------------------------------------------------------------
def cargar_iconos():
    """Genera un .ico con Pillow y devuelve (HICON grande, HICON pequeño)."""
    try:
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([8, 8, 248, 248], radius=48, fill=(37, 99, 235, 255))
        for r in range(3):
            for c in range(3):
                x, y = 54 + c * 54, 54 + r * 54
                d.rounded_rectangle([x, y, x + 36, y + 36], radius=9,
                                    fill=(255, 255, 255, 240))
        ruta = os.path.join(tempfile.gettempdir(), "escritorio_hd_icono.ico")
        img.save(ruta, format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                            (48, 48), (64, 64), (128, 128),
                                            (256, 256)])
        grande = user32.LoadImageW(None, ruta, IMAGE_ICON,
                                   user32.GetSystemMetrics(11),
                                   user32.GetSystemMetrics(12), LR_LOADFROMFILE)
        chico = user32.LoadImageW(None, ruta, IMAGE_ICON,
                                  user32.GetSystemMetrics(49),
                                  user32.GetSystemMetrics(50), LR_LOADFROMFILE)
        try:
            os.remove(ruta)
        except OSError:
            pass
        return grande, chico
    except Exception:
        return None, None


# --------------------------------------------------------------------------
# Aplicacion: ventanas, modos y bucle de mensajes
# --------------------------------------------------------------------------
class Aplicacion:
    def __init__(self):
        self.ancho = user32.GetSystemMetrics(0)
        self.alto = user32.GetSystemMetrics(1)
        self.dpi = obtener_dpi()
        self.pid = kernel32.GetCurrentProcessId()
        self.escena = None
        self.modo = False
        self.hover = -1
        self.sel = set()
        self.tracking = False
        self.hwnd_overlay = None
        self.hwnd_boton = None
        self.hdc_mem = self.hbmp = self.hbmp_viejo = self.bits = None
        self._iniciado = False
        self._ultimo_fg = None
        self._gracia_hasta = 0.0
        self._hook = None
        self._iconos = (None, None)
        self.dib = None                  # vista numpy del DIB (solo animacion)
        self.anim = None
        self.anim_activa = False
        self._recursos = None            # grietas/mascara cargadas una vez
        self.off = (0, 0)                # desplazamiento actual (temblor)
        # Referencias fuertes a los callbacks (evita que los recoja el GC)
        self._wndproc = WNDPROC(self._proc)
        self._cb_evento = WINEVENTPROC(self._evento)

    # -- ventanas -----------------------------------------------------------
    def crear_ventanas(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._iconos = cargar_iconos()
        grande, chico = self._iconos
        for nombre, estilo, es_boton in ((CLASE_OVERLAY, CS_DBLCLKS, False),
                                         (CLASE_BOTON, 0, True)):
            wc = WNDCLASSEXW()
            wc.cbSize = sizeof(WNDCLASSEXW)
            wc.style = estilo
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.hIcon = grande if es_boton else None
            wc.hIconSm = chico if es_boton else None
            wc.hCursor = user32.LoadCursorW(None, 32512)         # IDC_ARROW
            wc.hbrBackground = 6 if es_boton else None            # COLOR_WINDOW+1
            wc.lpszClassName = nombre
            if not user32.RegisterClassExW(byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())

        # Capa transparente: sin boton en la barra, sin robar foco
        self.hwnd_overlay = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, CLASE_OVERLAY,
            TITULO_APP + " (capa)", WS_POPUP, 0, 0, self.ancho, self.alto,
            None, None, hinst, None)
        # Ventana normal diminuta, minimizada: es el boton de la barra de tareas
        self.hwnd_boton = user32.CreateWindowExW(
            WS_EX_APPWINDOW, CLASE_BOTON, TITULO_APP, WS_OVERLAPPEDWINDOW,
            100, 100, 320, 120, None, None, hinst, None)
        if not self.hwnd_overlay or not self.hwnd_boton:
            raise ctypes.WinError(ctypes.get_last_error())
        if grande:
            user32.SendMessageW(self.hwnd_boton, WM_SETICON, ICON_BIG, grande)
        if chico:
            user32.SendMessageW(self.hwnd_boton, WM_SETICON, ICON_SMALL, chico)
        user32.ShowWindow(self.hwnd_boton, SW_SHOWMINNOACTIVE)
        self._iniciado = True

    # -- superficie (DIB de 32 bits, BGRA premultiplicado) --------------------
    def _crear_superficie(self):
        hdc_pant = user32.GetDC(None)
        try:
            self.hdc_mem = gdi32.CreateCompatibleDC(hdc_pant)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = self.ancho
            bmi.bmiHeader.biHeight = -self.alto               # de arriba a abajo
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0                   # BI_RGB
            bits = c_void_p()
            self.hbmp = gdi32.CreateDIBSection(hdc_pant, byref(bmi), 0,
                                               byref(bits), None, 0)
        finally:
            user32.ReleaseDC(None, hdc_pant)
        if not self.hbmp or not bits.value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.bits = bits.value
        self.hbmp_viejo = gdi32.SelectObject(self.hdc_mem, self.hbmp)
        if HAY_ANIM:                      # vista sin copia sobre la memoria del DIB
            buf = (c_ubyte * (self.ancho * self.alto * 4)).from_address(self.bits)
            self.dib = np.frombuffer(buf, dtype=np.uint8).reshape(
                self.alto, self.ancho, 4)

    def _liberar_superficie(self):
        self.dib = None
        if self.hdc_mem:
            if self.hbmp_viejo:
                gdi32.SelectObject(self.hdc_mem, self.hbmp_viejo)
            if self.hbmp:
                gdi32.DeleteObject(self.hbmp)
            gdi32.DeleteDC(self.hdc_mem)
        self.hdc_mem = self.hbmp = self.hbmp_viejo = self.bits = None

    def _escribir_region(self, rect, img):
        x, y, w, h = rect
        datos = _a_bgra_premult(img)
        fila = w * 4
        if x == 0 and w == self.ancho:
            ctypes.memmove(self.bits + y * self.ancho * 4, datos, len(datos))
        else:
            for r in range(h):
                ctypes.memmove(self.bits + ((y + r) * self.ancho + x) * 4,
                               datos[r * fila:(r + 1) * fila], fila)

    def _repintar_rect(self, rect):
        x0, y0 = max(0, rect[0]), max(0, rect[1])
        x1 = min(self.ancho, rect[0] + rect[2])
        y1 = min(self.alto, rect[1] + rect[3])
        if x1 <= x0 or y1 <= y0:
            return
        r = (x0, y0, x1 - x0, y1 - y0)
        self._escribir_region(r, self.escena.componer(r, self.hover, self.sel))

    def _actualizar_capa(self):
        pt, pts = POINT(self.off[0], self.off[1]), POINT(0, 0)
        sz = SIZE(self.ancho, self.alto)
        mezcla = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        hdc_pant = user32.GetDC(None)
        try:
            user32.UpdateLayeredWindow(self.hwnd_overlay, hdc_pant, byref(pt),
                                       byref(sz), self.hdc_mem, byref(pts), 0,
                                       byref(mezcla), ULW_ALPHA)
        finally:
            user32.ReleaseDC(None, hdc_pant)

    # -- Z-order: justo encima del fondo de pantalla, debajo de todo lo demas --
    def _posicionar_z(self):
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        host = ventana_host_escritorio()
        if not host:
            user32.SetWindowPos(self.hwnd_overlay, HWND_BOTTOM, 0, 0, 0, 0, flags)
            return
        if user32.GetWindow(self.hwnd_overlay, GW_HWNDNEXT) == host:
            return                                   # ya esta en su sitio
        prev = user32.GetWindow(host, GW_HWNDPREV)   # ventana justo encima del host
        if prev and not (user32.GetWindowLongW(prev, GWL_EXSTYLE) & WS_EX_TOPMOST):
            user32.SetWindowPos(self.hwnd_overlay, prev, 0, 0, 0, 0, flags)
        else:                                        # nada normal por encima del host
            user32.SetWindowPos(self.hwnd_overlay, HWND_NOTOPMOST, 0, 0, 0, 0, flags)

    # -- modos ----------------------------------------------------------------
    def entrar_modo_escritorio(self):
        """Activa el modo escritorio (relee el JSON). Lanza ErrorDatos si falla;
        en ese caso no oculta nada."""
        carpeta, datos = cargar_datos()
        escena = Escena(carpeta, datos, self.ancho, self.alto, self.dpi)
        if int((datos.get("pantalla") or {}).get("ancho", self.ancho) or 0) not in (0, self.ancho):
            print("Aviso: la resolucion actual difiere de la del JSON.", file=sys.stderr)
        self._detener_anim()
        self._liberar_superficie()
        self.escena, self.hover, self.sel, self.tracking = escena, -1, set(), False
        self.off = (0, 0)
        self._crear_superficie()
        self._repintar_rect((0, 0, self.ancho, self.alto))
        self._actualizar_capa()
        self.anim = self._crear_anim(carpeta)
        user32.ShowWindow(self.hwnd_overlay, SW_SHOWNOACTIVATE)
        self._posicionar_z()
        ocultar_iconos_nativos()          # primero se ve la capa, luego se ocultan
        self.modo = True
        if self.anim is not None and DISPARO_ANIMACION == "al_iniciar":
            self._iniciar_anim()

    def salir_modo_escritorio(self):
        mostrar_iconos_nativos()          # primero los nativos, luego se oculta la capa
        self._detener_anim()
        if self.hwnd_overlay:
            user32.ShowWindow(self.hwnd_overlay, SW_HIDE)
        self._liberar_superficie()
        self.escena, self.modo, self.hover, self.sel = None, False, -1, set()

    # -- animacion -----------------------------------------------------------
    def _crear_anim(self, carpeta):
        if not ANIMACION_ACTIVA:
            return None
        if not HAY_ANIM:
            print("Aviso: instala numpy y opencv-python para la animacion.",
                  file=sys.stderr)
            return None
        try:
            if self._recursos is None:
                self._recursos = RecursosAnim(self.ancho, self.alto, carpeta)
            return Animacion(self.escena, self._recursos, self.dpi,
                             suelo_pantalla(self.alto))
        except Exception as e:
            print(f"Aviso: animacion desactivada: {e}", file=sys.stderr)
            return None

    def _iniciar_anim(self):
        if self.anim is None or self.anim_activa or self.dib is None:
            return
        try:
            self.anim.iniciar(self.dib, self.hover, self.sel)
        except Exception:
            traceback.print_exc()
            return
        self.anim_activa = True
        user32.SetTimer(self.hwnd_boton, ID_TIMER_ANIM, 16, None)

    def _detener_anim(self):
        if self.hwnd_boton:
            user32.KillTimer(self.hwnd_boton, ID_TIMER_ANIM)
        if self.anim is not None:
            try:
                self.anim.detener()
            except Exception:
                traceback.print_exc()
        self.anim, self.anim_activa = None, False
        self.off = (0, 0)

    def _tick_anim(self):
        if not (self.anim_activa and self.anim is not None and self.modo):
            return
        try:
            p = POINT()
            user32.GetCursorPos(byref(p))
            cur = (p.x - self.off[0], p.y - self.off[1])    # coordenadas de la capa
            boton = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            cambio, off = self.anim.tick(cur, boton)
            if cambio or off != self.off:
                self.off = off
                self._actualizar_capa()
        except Exception:                                    # nunca dejar caer la app
            traceback.print_exc()
            self._detener_anim()
            self._repintar_rect((0, 0, self.ancho, self.alto))
            self._actualizar_capa()

    def _hit(self, x, y):
        if self.anim_activa and self.anim is not None:
            return self.anim.hit(x, y)
        return self.escena.hit(x, y)

    def volver_al_escritorio(self):
        try:
            self.entrar_modo_escritorio()
        except ErrorDatos as e:
            mensaje(str(e))
            return
        self._gracia_hasta = time.monotonic() + GRACIA_TRANSICION_S
        if MINIMIZAR_AL_VOLVER:
            minimizar_todas()

    def pedir_salida(self):
        """Solicita cerrar la app (seguro desde otros hilos / señales)."""
        if self.hwnd_boton:
            user32.PostMessageW(self.hwnd_boton, WM_CLOSE, 0, 0)

    # -- deteccion del primer plano ---------------------------------------------
    def es_del_shell(self, hwnd):
        if hwnd in (self.hwnd_overlay, self.hwnd_boton):
            return True
        cls = clase_de(hwnd)
        if cls in CLASES_SHELL:
            return True
        pid = pid_de(hwnd)
        if pid == self.pid:
            return True
        if cls in CLASES_HOST_UWP and exe_de(pid) in EXE_SHELL:
            return True
        return False

    def comprobar_primer_plano(self, hwnd):
        """Sale del modo escritorio cuando el primer plano CAMBIA a una ventana
        que no es del shell ni de esta aplicacion."""
        if not hwnd or hwnd == self._ultimo_fg:
            return
        self._ultimo_fg = hwnd
        if time.monotonic() < self._gracia_hasta:
            return
        if self.modo and not self.es_del_shell(hwnd):
            self.salir_modo_escritorio()

    def _evento(self, _hook, evento, hwnd, _obj, _hijo, _hilo, _t):
        try:
            if evento == EVENT_SYSTEM_FOREGROUND:
                self.comprobar_primer_plano(hwnd)
        except Exception:
            traceback.print_exc()

    # -- mouse / seleccion ----------------------------------------------------
    def _cambiar_estado(self, indices):
        """Repinta los iconos indicados y actualiza la capa una sola vez."""
        if self.anim_activa and self.anim is not None:   # lo repinta el tick
            self.anim.actualizar_estado(indices, self.hover, self.sel)
            return
        for i in indices:
            if 0 <= i < len(self.escena.elementos):
                self._repintar_rect(self.escena.elementos[i].caja)
        self._actualizar_capa()

    def _set_hover(self, nuevo):
        if nuevo != self.hover:
            viejo, self.hover = self.hover, nuevo
            self._cambiar_estado((viejo, nuevo))

    def _seleccionar(self, idx):
        if idx < 0:
            return
        antes = set(self.sel)
        if user32.GetKeyState(VK_CONTROL) & 0x8000:
            self.sel ^= {idx}
        else:
            self.sel = {idx}
        cambiados = antes ^ self.sel
        if cambiados:
            self._cambiar_estado(cambiados)

    def _limpiar_seleccion_si_clic_fuera(self):
        if not (self.modo and self.sel):
            return
        if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
            p = POINT()
            user32.GetCursorPos(byref(p))
            if self._hit(p.x - self.off[0], p.y - self.off[1]) < 0:
                antes, self.sel = self.sel, set()
                self._cambiar_estado(antes)

    # -- procedimientos de ventana ----------------------------------------------
    def _proc(self, hwnd, msg, wp, lp):
        try:
            r = None
            if hwnd is not None and hwnd == self.hwnd_overlay:
                r = self._proc_overlay(hwnd, msg, wp, lp)
            elif hwnd is not None and hwnd == self.hwnd_boton:
                r = self._proc_boton(hwnd, msg, wp, lp)
            if r is not None:
                return r
        except Exception:
            traceback.print_exc()
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _proc_overlay(self, hwnd, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE                 # nunca roba el foco
        if not self.modo or self.escena is None:
            return None
        if msg == WM_MOUSEMOVE:
            x = ctypes.c_short(lp & 0xFFFF).value
            y = ctypes.c_short((lp >> 16) & 0xFFFF).value
            if not self.tracking:
                tme = TRACKMOUSEEVENT(sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                self.tracking = bool(user32.TrackMouseEvent(byref(tme)))
            self._set_hover(self._hit(x, y))
            return 0
        if msg == WM_MOUSELEAVE:
            self.tracking = False
            self._set_hover(-1)
            return 0
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            x = ctypes.c_short(lp & 0xFFFF).value
            y = ctypes.c_short((lp >> 16) & 0xFFFF).value
            idx = self._hit(x, y)
            self._seleccionar(idx)
            if (idx >= 0 and msg == WM_LBUTTONDOWN and self.anim is not None
                    and DISPARO_ANIMACION == "clic_icono"):
                self._iniciar_anim()
            return 0
        if msg == WM_LBUTTONDBLCLK:
            x = ctypes.c_short(lp & 0xFFFF).value
            y = ctypes.c_short((lp >> 16) & 0xFFFF).value
            i = self._hit(x, y)
            if i >= 0:
                abrir_elemento(self.escena.elementos[i].texto)
            return 0
        return None

    def _proc_boton(self, hwnd, msg, wp, lp):
        if msg == WM_TIMER:
            if wp == ID_TIMER_FG:                # sondeo de respaldo cada 200 ms
                self.comprobar_primer_plano(user32.GetForegroundWindow())
                if self.modo:
                    self._mantener_modo()
            elif wp == ID_TIMER_SEL:
                self._limpiar_seleccion_si_clic_fuera()
            elif wp == ID_TIMER_ANIM:
                self._tick_anim()
            return 0
        if msg == WM_SYSCOMMAND and (wp & 0xFFF0) == SC_RESTORE:
            # Clic en el boton de la barra de tareas: no restaurar, volver
            user32.PostMessageW(hwnd, WM_APP_REACTIVAR, 0, 0)
            return 0
        if msg == WM_SIZE and self._iniciado and wp in (SIZE_RESTORED, SIZE_MAXIMIZED):
            user32.PostMessageW(hwnd, WM_APP_REACTIVAR, 0, 0)
            return None
        if msg == WM_APP_REACTIVAR:
            self.volver_al_escritorio()
            user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)   # vuelve a minimizarse
            return 0
        if msg == WM_QUERYENDSESSION:
            mostrar_iconos_nativos()
            return 1
        if msg == WM_ENDSESSION:
            mostrar_iconos_nativos()
            return 0
        if msg == WM_CLOSE:
            mostrar_iconos_nativos()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return None

    def _mantener_modo(self):
        """Cada tick: Z-order correcto y ListView nativo oculto (p. ej. si
        el Explorador se reinicio o el usuario pulso F5 en el escritorio)."""
        self._posicionar_z()
        lv = buscar_listview()
        if lv and not listview_oculto(lv):
            user32.ShowWindow(lv, SW_HIDE)
            _ESTADO["oculto"] = True

    # -- bucle principal --------------------------------------------------------
    def ejecutar(self):
        self.crear_ventanas()
        self._ultimo_fg = user32.GetForegroundWindow()   # p. ej. la consola
        try:
            self.entrar_modo_escritorio()
        except ErrorDatos as e:
            mensaje(str(e))
            self._limpiar()
            return 2
        self._hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
            self._cb_evento, 0, 0, WINEVENT_OUTOFCONTEXT)
        user32.SetTimer(self.hwnd_boton, ID_TIMER_FG, INTERVALO_SONDEO_MS, None)
        user32.SetTimer(self.hwnd_boton, ID_TIMER_SEL, 50, None)
        try:
            msg = MSG()
            while True:
                r = user32.GetMessageW(byref(msg), None, 0, 0)
                if r in (0, -1):
                    break
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
        finally:
            self._limpiar()
        return 0

    def _limpiar(self):
        mostrar_iconos_nativos()
        try:
            self._detener_anim()
            if self._hook:
                user32.UnhookWinEvent(self._hook)
                self._hook = None
            if self.hwnd_boton and user32.IsWindow(self.hwnd_boton):
                user32.KillTimer(self.hwnd_boton, ID_TIMER_FG)
                user32.KillTimer(self.hwnd_boton, ID_TIMER_SEL)
            for h in (self.hwnd_overlay, self.hwnd_boton):
                if h and user32.IsWindow(h):
                    user32.DestroyWindow(h)
            self._liberar_superficie()
            for ico in self._iconos:
                if ico:
                    user32.DestroyIcon(ico)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Punto de entrada
# --------------------------------------------------------------------------
_REFS = []        # referencias a callbacks globales (handler de consola)


def main():
    if sys.platform != "win32":
        print("Este script solo funciona en Windows 10/11.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description="Escritorio HD (overlay transparente).")
    parser.add_argument("--restaurar", action="store_true",
                        help="solo vuelve a mostrar los iconos nativos y sale")
    args = parser.parse_args()

    configurar_dpi()                       # antes de crear ventanas / medir pantalla

    if args.restaurar:
        mostrar_iconos_nativos(force=True)
        print("Iconos nativos restaurados.")
        return 0

    if Image is None:
        mensaje("Falta la dependencia Pillow.\nInstala con:  pip install pillow")
        return 1

    # Instancia unica (si ya hay una, no tocar el estado del escritorio)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\EscritorioHD_Overlay")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        mensaje("Escritorio HD ya se esta ejecutando.", icono=0x40)
        return 0

    # Validar datos ANTES de ocultar nada
    try:
        cargar_datos()
    except ErrorDatos as e:
        mensaje(str(e))
        return 2

    # Restaura un ListView oculto por una ejecucion anterior
    lv = buscar_listview()
    if lv and listview_oculto(lv):
        mostrar_iconos_nativos(force=True)

    app = Aplicacion()
    atexit.register(mostrar_iconos_nativos)

    def _ctrl(tipo):                       # hilo propio del sistema
        mostrar_iconos_nativos()
        if tipo in (0, 1):                 # CTRL_C / CTRL_BREAK: salida limpia
            app.pedir_salida()
            return True
        return False                       # cierre/logoff/apagado: ya restaurado

    handler = HANDLER_ROUTINE(_ctrl)
    _REFS.append(handler)
    try:
        kernel32.SetConsoleCtrlHandler(handler, True)
    except Exception:
        pass

    def _señal(_s, _f):
        mostrar_iconos_nativos()
        app.pedir_salida()

    for nombre in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, nombre):
            try:
                signal.signal(getattr(signal, nombre), _señal)
            except (ValueError, OSError):
                pass

    codigo = 0
    try:
        codigo = app.ejecutar()
    except Exception:
        traceback.print_exc()
        mensaje("Error inesperado:\n" + traceback.format_exc()[-800:])
        codigo = 1
    finally:
        mostrar_iconos_nativos(force=True)   # los iconos nunca deben quedar ocultos
    return codigo


if __name__ == "__main__":
    sys.exit(main())
