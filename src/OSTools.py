import gzip
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler


def compressor(source, dest):
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def namer(name):
    return name + ".gz"


def setupRotatingLogger(logName, logConsole):
    logSize = 5 * 1024 * 1024  # 5 MB
    if logConsole:
        folder = os.path.dirname(os.path.abspath(__file__))
    else:
        folder = os.path.join(os.path.expanduser("~"), ".config", logName)
        os.makedirs(folder, exist_ok=True)
    logPath = os.path.join(folder, logName + ".log")
    fh = RotatingFileHandler(logPath, maxBytes=logSize, backupCount=5)
    fh.rotator = compressor
    fh.namer = namer
    handlers = [fh]
    if logConsole:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        handlers=handlers,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s : %(message)s",
    )


Log = logging.getLogger("Main")
