"""Drop bootstrap privileges before executing the adapter (no extra packages)."""
import os
import sys

os.setgroups([])
os.setgid(10001)
os.setuid(10001)
os.execvp(sys.argv[1], sys.argv[1:])
