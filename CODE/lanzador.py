# --- Imports para que PyInstaller empaquete TODAS las dependencias de los 2 scripts ---
import argparse, atexit, base64, ctypes, json, math, os, random, re, shutil
import signal, stat, subprocess, sys, tempfile, threading, time, traceback
import unicodedata, winreg
from ctypes import wintypes
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont  # overlay usa Draw/Filter/Font
import numpy                                                          # animacion de grietas
import cv2                                                            # animacion + video FUEGO.webm
# ------------------------------------------------------------------------------------

import runpy


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


# Con el exe sin consola (--noconsole), subprocess.run(capture_output=True) falla
# con "WinError 6" si no se indica stdin. escritorio_hd.py lanza PowerShell asi.
_run_original = subprocess.run


def _run_seguro(*args, **kwargs):
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return _run_original(*args, **kwargs)


subprocess.run = _run_seguro


def ejecutar(nombre):
    # Si el script termina con sys.exit(), no debe cortar el lanzador
    try:
        runpy.run_path(resource_path(nombre), run_name="__main__")
    except SystemExit:
        pass


if __name__ == "__main__":
    ejecutar("escritorio_hd.py")       # 1) espera a que termine
    ejecutar("escritorio_overlay.py")  # 2) luego el juego/grafico
