# -*- coding: utf-8 -*-
"""
Escritorio HD  (extraer iconos + posiciones, en un solo script)
================================================================
Cada ejecucion BORRA la carpeta de salida anterior y extrae todo de nuevo
        (asi no se acumulan duplicados ni datos viejos).
PASO 1  Localiza el Escritorio real (Registro -> API Shell -> PowerShell) y
        exporta a PNG transparente el icono / miniatura exacta de cada
        elemento (videos, PDF, imagenes, .lnk, .url, .exe, carpetas y objetos
        del sistema como la Papelera) en TEMP/Iconos_Escritorio_HD (o --salida)
PASO 2  Lee del ListView de Explorer la posicion de cada icono, la cruza con su
        PNG y guarda TODO en  Iconos_Escritorio_HD/posiciones_escritorio.json

Requisitos: Windows 10/11 de 64 bits, Python de 64 bits, pip install pillow

Uso:
    python escritorio_hd.py                    # paso 1 + paso 2
    python escritorio_hd.py --ocultar          # ademas oculta los iconos nativos
                                               # hasta pulsar Ctrl+C
    python escritorio_hd.py --restaurar        # vuelve a mostrar los iconos nativos
    Opciones: --tam 256  --salida CARPETA  --incluir-ocultos  --sin-publico  --sin-sistema
"""
import argparse
import base64
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import winreg
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops

# --------------------------------------------------------------------------
# Consola / codificacion
# --------------------------------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CREATE_NO_WINDOW = 0x08000000


def run_powershell(script: str) -> str:
    """PowerShell con salida UTF-8 garantizada (script via -EncodedCommand)."""
    prefijo = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    )
    cod = base64.b64encode((prefijo + script).encode("utf-16-le")).decode("ascii")
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", cod],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    return p.stdout.decode("utf-8-sig", errors="replace").strip()


# --------------------------------------------------------------------------
# Tipos / prototipos ctypes
# --------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class LVITEMW(ctypes.Structure):
    _fields_ = [("mask", wintypes.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("state", wintypes.UINT), ("stateMask", wintypes.UINT),
                ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int), ("lParam", ctypes.c_ssize_t),
                ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int),
                ("cColumns", wintypes.UINT), ("puColumns", ctypes.c_void_p),
                ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int)]


ole32 = ctypes.WinDLL("ole32")
shell32 = ctypes.WinDLL("shell32")
gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.IIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
ole32.IIDFromString.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.restype = None

shell32.SHCreateItemFromParsingName.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
shell32.SHGetKnownFolderPath.restype = ctypes.c_long

gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = ctypes.c_void_p
user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def _guid(texto: str) -> GUID:
    g = GUID()
    if ole32.IIDFromString(texto, ctypes.byref(g)) != 0:
        raise ValueError(f"GUID invalido: {texto}")
    return g


IID_IShellItemImageFactory = _guid("{BCC18B79-BA16-442F-80C4-8A59C30C463B}")
IID_IShellItem = _guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
FOLDERID_Desktop = _guid("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")
FOLDERID_PublicDesktop = _guid("{C4AA340D-F20F-4163-91AE-29B3ED04DE15}")

SIIGBF_RESIZETOFIT, SIIGBF_BIGGERSIZEOK, SIIGBF_ICONONLY = 0x00, 0x01, 0x04

CLSID_PAPELERA = "{645FF040-5081-101B-9F08-002897C1092F}"
ICONOS_SISTEMA = [  # (nombre de archivo, CLSID, visible por defecto)
    ("Papelera_de_reciclaje", CLSID_PAPELERA, True),
    ("Este_equipo", "{20D04FE0-3AEA-1069-A2D8-08002B30309D}", False),
    ("Archivos_del_usuario", "{59031a47-3f72-44a7-89c5-5595fe6b30ee}", False),
    ("Red", "{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}", False),
    ("Panel_de_control", "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}", False),
]
EXT_SOLO_ICONO = {".lnk", ".url", ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".appref-ms"}

LVM_GETITEMCOUNT, LVM_GETITEMRECT, LVM_GETITEMPOSITION = 0x1004, 0x100E, 0x1010
LVM_GETICONSPACING, LVM_GETVIEW, LVM_GETITEMTEXTW = 0x1033, 0x108F, 0x1073
LVIR_BOUNDS, LVIR_ICON, LVIR_LABEL = 0, 1, 2
SW_HIDE, SW_SHOW = 0, 5
PROCESS_ACCESO = 0x0008 | 0x0010 | 0x0020 | 0x0400  # VM_OPERATION|READ|WRITE|QUERY_INFO
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_RW = 0x3000, 0x8000, 0x04


# ==========================================================================
#  PASO 1 - Localizar el Escritorio y exportar iconos
# ==========================================================================
def _known_folder(guid: GUID):
    p = ctypes.c_void_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) != 0:
        return None
    try:
        return ctypes.wstring_at(p.value)
    finally:
        ole32.CoTaskMemFree(p)


def localizar_escritorio():
    """Devuelve (ruta, metodo). Orden: Registro -> API Shell -> PowerShell."""
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            valor, _ = winreg.QueryValueEx(k, "Desktop")
        ruta = os.path.expandvars(valor)
        if os.path.isdir(ruta):
            return Path(ruta), "Registro (User Shell Folders)"
    except OSError:
        pass
    ruta = _known_folder(FOLDERID_Desktop)
    if ruta and os.path.isdir(ruta):
        return Path(ruta), "API Shell (FOLDERID_Desktop)"
    ruta = run_powershell("[Environment]::GetFolderPath('Desktop')")
    if ruta and os.path.isdir(ruta):
        return Path(ruta), "PowerShell"
    raise RuntimeError("No se pudo localizar la carpeta del Escritorio.")


def icono_visible(clsid, defecto):
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel") as k:
            v, _ = winreg.QueryValueEx(k, clsid)
            return v == 0
    except OSError:
        return defecto


def _hbitmap_a_pil(hbm) -> Image.Image:
    bm = BITMAP()
    gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
    w, h = bm.bmWidth, abs(bm.bmHeight)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0      # BI_RGB
    buf = (ctypes.c_ubyte * (w * h * 4))()
    hdc = user32.GetDC(None)
    try:
        if gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0) == 0:
            raise OSError("GetDIBits fallo")
    finally:
        user32.ReleaseDC(None, hdc)
    datos = bytes(buf)
    im = Image.frombuffer("RGBA", (w, h), datos, "raw", "BGRA", 0, 1)
    r, g, b, a = im.split()
    ext = a.getextrema()
    if ext == (0, 0):
        im.putalpha(255)                 # miniatura opaca sin alfa valido
    elif ext[0] < 255:
        mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
        if ImageChops.subtract(mx, a).getbbox() is None:   # premultiplicado
            im = Image.frombuffer("RGBa", (w, h), datos, "raw", "BGRa", 0, 1).convert("RGBA")
    return im


def imagen_shell(parsing: str, tam: int, flags: int) -> Image.Image:
    ppv = ctypes.c_void_p()
    hr = shell32.SHCreateItemFromParsingName(
        parsing, None, ctypes.byref(IID_IShellItemImageFactory), ctypes.byref(ppv))
    if hr < 0 or not ppv:
        raise OSError(f"SHCreateItemFromParsingName HRESULT=0x{hr & 0xFFFFFFFF:08X}")
    vtbl = ctypes.cast(ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    get_image = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, wintypes.SIZE, wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p))(vtbl[3])      # IShellItemImageFactory::GetImage
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])
    hbm = ctypes.c_void_p()
    try:
        hr = get_image(ppv, wintypes.SIZE(tam, tam), flags, ctypes.byref(hbm))
        if hr < 0 or not hbm:
            raise OSError(f"GetImage HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        try:
            return _hbitmap_a_pil(hbm)
        finally:
            gdi32.DeleteObject(hbm)
    finally:
        release(ppv)


def extraer(parsing: str, solo_icono: bool, tam: int):
    """Devuelve (imagen, metodo). Reintenta con icono si no hay miniatura."""
    if solo_icono:
        return imagen_shell(parsing, tam, SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK), "icono"
    try:
        im = imagen_shell(parsing, tam, SIIGBF_RESIZETOFIT | SIIGBF_BIGGERSIZEOK)
        caja = im.getchannel("A").getbbox()      # recorta bordes transparentes
        if caja and caja != (0, 0, im.width, im.height):
            im = im.crop(caja)
        return im, "miniatura/icono"
    except OSError:
        return imagen_shell(parsing, tam, SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK), "icono (respaldo)"


def nombre_mostrado(parsing: str) -> str:
    """Nombre tal como lo muestra el Explorador (sin extensiones ocultas, etc.)."""
    ppv = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(parsing, None, ctypes.byref(IID_IShellItem),
                                           ctypes.byref(ppv)) < 0 or not ppv:
        return ""
    vtbl = ctypes.cast(ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    get_name = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p))(vtbl[5])   # GetDisplayName
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])
    try:
        p = ctypes.c_void_p()
        if get_name(ppv, 0, ctypes.byref(p)) < 0 or not p:
            return ""
        try:
            return ctypes.wstring_at(p.value)
        finally:
            ole32.CoTaskMemFree(p)
    finally:
        release(ppv)


def normalizar(s: str) -> str:
    """Minusculas, sin marcas invisibles (LRM...), sin sufijo ' - Acceso directo'."""
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in s if unicodedata.category(c) not in ("Cf", "Cc"))
    s = s.strip().casefold()
    return re.sub(r"\s*-\s*(acceso directo|shortcut)$", "", s)


def nombre_seguro(n: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", n).strip(" .") or "sin_nombre"


def excluir(nombre: str) -> bool:
    return nombre.lower() in ("desktop.ini", "thumbs.db") or nombre.startswith("~$")


def es_oculto(entrada: os.DirEntry) -> bool:
    try:
        return bool(entrada.stat(follow_symlinks=False).st_file_attributes & 0x2)
    except (OSError, AttributeError):
        return False


def ruta_unica(carpeta: Path, base: str) -> Path:
    destino = carpeta / f"{base}.png"
    n = 2
    while destino.exists():
        destino = carpeta / f"{base}_{n}.png"
        n += 1
    return destino


def _unicos(rutas):
    vistas, res = set(), []
    for r in rutas:
        if not r or not os.path.isdir(r):
            continue
        clave = os.path.normcase(os.path.realpath(r))
        if clave not in vistas:
            vistas.add(clave)
            res.append(Path(r))
    return res


def raices_escritorio(escritorio: Path, incluir_publico: bool):
    """Carpetas que Windows fusiona en el Escritorio: la del usuario (puede estar en
    otra unidad) y la publica (accesos directos de instaladores: Steam, Brave...)."""
    rutas = [escritorio]
    if incluir_publico:
        try:
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, "Common Desktop")
            rutas.append(os.path.expandvars(v))
        except OSError:
            pass
        rutas.append(_known_folder(FOLDERID_PublicDesktop))
        if os.environ.get("PUBLIC"):
            rutas.append(os.path.join(os.environ["PUBLIC"], "Desktop"))
    return _unicos(rutas)


def claves_de(nombre: str, ruta: str):
    c = {normalizar(nombre), normalizar(Path(nombre).stem)}
    vis = nombre_mostrado(ruta)
    if vis:
        c.add(normalizar(vis))
    return c


def exportar_entrada(ruta, nombre, es_dir, salida, tam):
    ext = Path(nombre).suffix.lower()
    im, met = extraer(ruta, es_dir or ext in EXT_SOLO_ICONO, tam)
    destino = ruta_unica(salida, nombre_seguro(nombre))
    im.save(destino, "PNG")
    print(f"[OK] {nombre} -> {destino.name} ({im.width}x{im.height}, {met})")
    return {"nombre": nombre, "archivo": destino.name, "claves": claves_de(nombre, ruta)}


def exportar_iconos(raices, salida: Path, args):
    """Paso 1. Devuelve (exportados, ocultos). 'ocultos' son archivos con atributo
    oculto que NO se exportaron; se rescatan luego si el escritorio los muestra."""
    exportados, ocultos = [], []
    for raiz in raices:
        try:
            entradas = sorted(os.scandir(raiz), key=lambda e: e.name.lower())
        except OSError as e:
            print(f"[!] No se pudo listar {raiz}: {e}")
            continue
        print(f"Carpeta {raiz}: {len(entradas)} entradas")
        for e in entradas:
            if excluir(e.name):
                continue
            if es_oculto(e) and not args.incluir_ocultos:
                ocultos.append({"ruta": e.path, "nombre": e.name, "es_dir": e.is_dir(),
                                "claves": claves_de(e.name, e.path)})
                continue
            try:
                exportados.append(exportar_entrada(e.path, e.name, e.is_dir(), salida, args.tam))
            except Exception as ex:
                print(f"[X ] {e.name}: {ex}")

    if not args.sin_sistema:
        for nombre, clsid, defecto in ICONOS_SISTEMA:
            if not icono_visible(clsid, defecto):
                continue
            parsing = f"shell:::{clsid}"
            try:
                im, _ = extraer(parsing, True, args.tam)
                destino = ruta_unica(salida, nombre)
                im.save(destino, "PNG")
                print(f"[OK] {nombre} -> {destino.name} ({im.width}x{im.height})")
                claves = {normalizar(nombre.replace("_", " "))}   # p. ej. "papelera de reciclaje"
                vis = nombre_mostrado(parsing)
                if vis:
                    claves.add(normalizar(vis))
                exportados.append({"nombre": nombre, "archivo": destino.name, "claves": claves})
            except Exception as ex:
                print(f"[X ] {nombre}: {ex}")
    return exportados, ocultos


def rescatar_ocultos(items, ocultos, exportados, salida: Path, tam):
    """Exporta los archivos ocultos que el escritorio SI esta mostrando."""
    textos = {normalizar(i["texto"]) for i in items}
    for o in ocultos:
        if o["claves"] & textos:
            try:
                exportados.append(exportar_entrada(o["ruta"], o["nombre"], o["es_dir"], salida, tam))
                print("     (archivo oculto, pero Windows lo muestra en el escritorio)")
            except Exception as ex:
                print(f"[X ] {o['nombre']}: {ex}")


# ==========================================================================
#  PASO 2 - Posiciones de los iconos (ListView del Escritorio)
# ==========================================================================
def encontrar_listview():
    """HWND del SysListView32 del Escritorio (cuelga de Progman o de un WorkerW)."""
    defview = None
    progman = user32.FindWindowW("Progman", None)
    if progman:
        defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if not defview:
        hallado = []

        @WNDENUMPROC
        def cb(hwnd, _):
            d = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if d:
                hallado.append(d)
                return False
            return True
        user32.EnumWindows(cb, None)
        defview = hallado[0] if hallado else None
    if not defview:
        return None
    return user32.FindWindowExW(defview, None, "SysListView32", None)


class MemoriaRemota:
    """Bloque de memoria dentro del proceso de Explorer para los mensajes LVM_*."""
    TAM = 4096
    OFF_ITEM, OFF_TEXTO, OFF_DATO = 0, 256, 2048

    def __init__(self, hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self.proc = kernel32.OpenProcess(PROCESS_ACCESO, False, pid.value)
        if not self.proc:
            raise OSError(f"OpenProcess fallo (error {ctypes.get_last_error()}). "
                          "Usa Python de 64 bits y el mismo usuario que Explorer.")
        self.base = kernel32.VirtualAllocEx(self.proc, None, self.TAM, MEM_COMMIT_RESERVE, PAGE_RW)
        if not self.base:
            kernel32.CloseHandle(self.proc)
            raise OSError("VirtualAllocEx fallo")

    def escribir(self, offset, obj):
        n = ctypes.c_size_t()
        kernel32.WriteProcessMemory(self.proc, self.base + offset, ctypes.byref(obj),
                                    ctypes.sizeof(obj), ctypes.byref(n))

    def leer(self, offset, obj):
        n = ctypes.c_size_t()
        kernel32.ReadProcessMemory(self.proc, self.base + offset, ctypes.byref(obj),
                                   ctypes.sizeof(obj), ctypes.byref(n))
        return obj

    def __enter__(self):
        return self

    def __exit__(self, *a):
        kernel32.VirtualFreeEx(self.proc, self.base, 0, MEM_RELEASE)
        kernel32.CloseHandle(self.proc)


def _rect_dict(rc, ox, oy):
    return {"x": rc.left + ox, "y": rc.top + oy,
            "ancho": rc.right - rc.left, "alto": rc.bottom - rc.top}


def leer_items(lv):
    """Iconos del escritorio: texto, posicion y rectangulos en pixeles de pantalla."""
    origen = wintypes.POINT(0, 0)
    user32.ClientToScreen(lv, ctypes.byref(origen))
    ox, oy = origen.x, origen.y
    n = user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0)
    items = []
    with MemoriaRemota(lv) as r:
        for i in range(n):
            it = LVITEMW()
            it.mask, it.iItem, it.iSubItem = 1, i, 0          # LVIF_TEXT
            it.pszText = r.base + r.OFF_TEXTO
            it.cchTextMax = 260
            r.escribir(r.OFF_ITEM, it)
            user32.SendMessageW(lv, LVM_GETITEMTEXTW, i, r.base + r.OFF_ITEM)
            buf = (ctypes.c_wchar * 260)()
            r.leer(r.OFF_TEXTO, buf)

            user32.SendMessageW(lv, LVM_GETITEMPOSITION, i, r.base + r.OFF_DATO)
            pt = r.leer(r.OFF_DATO, wintypes.POINT())

            rects = {}
            for nombre, code in (("icono", LVIR_ICON), ("etiqueta", LVIR_LABEL), ("limites", LVIR_BOUNDS)):
                rc = wintypes.RECT()
                rc.left = code
                r.escribir(r.OFF_DATO, rc)
                user32.SendMessageW(lv, LVM_GETITEMRECT, i, r.base + r.OFF_DATO)
                rects[nombre] = _rect_dict(r.leer(r.OFF_DATO, wintypes.RECT()), ox, oy)

            items.append({"texto": buf.value, "x": pt.x + ox, "y": pt.y + oy, **rects})
    return items


def cruzar(items, exportados):
    """Asocia cada icono del ListView con su PNG (cada PNG se usa una sola vez)."""
    indice = {}
    for m in exportados:
        for k in m["claves"]:
            indice.setdefault(k, []).append(m)
    usados = set()
    for it in items:
        it["archivo"] = None
        for m in indice.get(normalizar(it["texto"]), []):
            if id(m) not in usados:
                usados.add(id(m))
                it["archivo"] = m["archivo"]
                break
    return items


def _borrar_forzado(func, ruta, _exc):
    """Si un archivo es de solo lectura, quita el atributo y reintenta."""
    try:
        os.chmod(ruta, stat.S_IWRITE)
        func(ruta)
    except Exception:
        pass


def limpiar_salida(salida: Path):
    """Elimina la carpeta de salida si ya existe, para extraer todo desde cero
    (evita PNG duplicados tipo nombre_1.png y datos viejos)."""
    if not salida.exists():
        return
    salida = salida.resolve()
    # --- seguridad: nunca borrar carpetas importantes por error con --salida ---
    peligrosas = {Path(salida.anchor), Path.home().resolve()}
    try:
        peligrosas.add(Path(localizar_escritorio()[0]).resolve())
    except Exception:
        pass
    if salida in peligrosas:
        sys.exit(f"[!] Por seguridad no se borra {salida}. Usa otra carpeta con --salida.")
    es_nuestra = (salida.name == "Iconos_Escritorio_HD"
                  or (salida / "posiciones_escritorio.json").exists()
                  or not any(salida.iterdir()))
    if not es_nuestra:
        sys.exit(f"[!] {salida} existe pero no parece una carpeta creada por este script; "
                 "no se borra. Elige otra carpeta con --salida o borrala a mano.")
    print(f"Carpeta existente detectada, eliminando: {salida}")
    shutil.rmtree(salida, onerror=_borrar_forzado)
    if salida.exists():
        sys.exit(f"[!] No se pudo borrar {salida} (algun archivo esta en uso). "
                 "Cierra lo que lo use e intenta de nuevo.")


# ==========================================================================
#  Programa principal
# ==========================================================================
def main():
    ap = argparse.ArgumentParser(description="Exporta iconos del Escritorio y sus posiciones.")
    ap.add_argument("--tam", type=int, default=256, help="Tamano maximo de los PNG en px (def. 256)")
    ap.add_argument("--salida", help="Carpeta de salida (por defecto: TEMP/Iconos_Escritorio_HD)")
    ap.add_argument("--incluir-ocultos", action="store_true", help="Incluye archivos ocultos")
    ap.add_argument("--sin-publico", action="store_true", help="Ignora el Escritorio publico")
    ap.add_argument("--sin-sistema", action="store_true",
                    help="Ignora iconos del sistema (Papelera, Este equipo...)")
    ap.add_argument("--ocultar", action="store_true",
                    help="Oculta los iconos nativos de Windows hasta pulsar Ctrl+C")
    ap.add_argument("--restaurar", action="store_true",
                    help="Vuelve a mostrar los iconos nativos y termina")
    args = ap.parse_args()

    if os.name != "nt":
        sys.exit("Este script solo funciona en Windows.")
    try:  # coordenadas reales en pixeles (sin escalado DPI)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED

    lv = encontrar_listview()
    if not lv:
        sys.exit("No se encontro el ListView del Escritorio.")

    if args.restaurar:
        user32.ShowWindow(lv, SW_SHOW)
        print("Iconos nativos restaurados.")
        return

    salida = (Path(args.salida) if args.salida
              else Path(tempfile.gettempdir()).resolve() / "Iconos_Escritorio_HD")
    limpiar_salida(salida)          # borra la carpeta anterior (si existe)
    salida.mkdir(parents=True, exist_ok=True)

    # ---- PASO 1: iconos ----
    escritorio, metodo = localizar_escritorio()
    print(f"Escritorio : {escritorio}  [{metodo}]")
    print(f"Salida     : {salida}\n")
    raices = raices_escritorio(escritorio, not args.sin_publico)
    exportados, ocultos = exportar_iconos(raices, salida, args)

    # ---- PASO 2: posiciones ----
    print("\nLeyendo posiciones...")
    items = leer_items(lv)
    rescatar_ocultos(items, ocultos, exportados, salida, args.tam)
    items = cruzar(items, exportados)

    if items and all(not i["texto"] for i in items):
        print("[!] Todos los textos salieron VACIOS: no se pudieron leer los nombres del ListView.")
    sin_png = [i for i in items if not i["archivo"]]
    if sin_png:
        print(f"[!] {len(sin_png)} de {len(items)} iconos sin PNG asociado.")
        print("    Textos del escritorio :", [i["texto"] for i in sin_png][:15])
        print("    Nombres exportados    :", [m["nombre"] for m in exportados][:15])

    esp = user32.SendMessageW(lv, LVM_GETICONSPACING, 0, 0)
    resultado = {
        "pantalla": {"ancho": user32.GetSystemMetrics(0), "alto": user32.GetSystemMetrics(1)},
        "modo_vista": user32.SendMessageW(lv, LVM_GETVIEW, 0, 0),   # 0 = iconos grandes
        "espaciado_celda": {"horizontal": esp & 0xFFFF, "vertical": (esp >> 16) & 0xFFFF},
        "iconos": items,
        "nota": "Pixeles de pantalla. 'icono' = rectangulo del bitmap, 'etiqueta' = texto, "
                "'limites' = celda completa.",
    }
    destino = salida / "posiciones_escritorio.json"
    with open(destino, "w", encoding="utf-8-sig") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    for it in items:
        ic = it["icono"]
        print(f"{it['texto']:<35} x={ic['x']:<5} y={ic['y']:<5} {ic['ancho']}x{ic['alto']}  "
              f"-> {it['archivo'] or '(sin PNG)'}")
    print(f"\n{len(items)} iconos. Guardado en {destino}")

    if args.ocultar:
        user32.ShowWindow(lv, SW_HIDE)
        print("\nIconos nativos OCULTOS. Pulsa Ctrl+C para restaurarlos.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            user32.ShowWindow(lv, SW_SHOW)
            print("Iconos nativos restaurados.")


if __name__ == "__main__":
    main()