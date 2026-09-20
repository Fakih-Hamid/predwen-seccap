import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("SECCAP_WORKERS", min(4, multiprocessing.cpu_count())))
worker_class = "sync"
threads = 2

preload_app = False

timeout = 60
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("SECCAP_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(D)sus'
