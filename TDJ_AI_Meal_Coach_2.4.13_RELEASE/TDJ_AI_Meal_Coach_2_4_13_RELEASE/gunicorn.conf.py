# TDJ 2.4.12: bounded-memory Gunicorn profile for Render 512 MB.
# One process keeps the in-process FIFO queue authoritative. Two threads allow
# the queue to admit up to two analyses while avoiding multiple Python worker
# processes duplicating the Flask/OpenAI/Pillow baseline memory.
workers = 1
worker_class = "gthread"
threads = 2
timeout = 120
graceful_timeout = 30
keepalive = 5
# Python/Pillow/http libraries may return objects to their allocators without
# immediately shrinking RSS. Periodic worker recycling returns that memory to
# the OS, preventing slow RSS accumulation on a long-lived 512 MB instance.
max_requests = 20
max_requests_jitter = 5
