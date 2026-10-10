"""Drop bootstrap privileges before executing the adapter (no extra packages)."""
import os
import sys

os.setgroups([])
os.setgid(10001)
os.setuid(10001)
os.chmod(os.environ['PRISM_ADAPTER_STATE_DIR'], 0o700)
os.chmod(os.environ['HOME'], 0o700)
os.execvp(sys.argv[1], sys.argv[1:])
