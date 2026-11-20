"""Sandbox-friendly pytest defaults.

Two adjustments, both no-ops on a machine without a file sandbox:

* os.mkdir is wrapped so directories are created with mode 0o777 (the platform
  default).  pytest asks for 0o700, and on Windows that produces a protected
  DACL that a confined process cannot traverse, so its own temporary
  directories become unreadable and the session cannot clean up.
* When no --basetemp is given, the suite uses .pytest-tmp in the repository
  root instead of the system temporary directory.  That keeps a sandboxed run
  inside the workspace.
"""

import os

_real_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *, dir_fd=None):
    return _real_mkdir(path, 0o777, dir_fd=dir_fd)


os.mkdir = _mkdir


def pytest_configure(config):
    if getattr(config.option, "basetemp", None) is None:
        config.option.basetemp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pytest-tmp")
