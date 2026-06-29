"""Project entry point — run from repo root: python app.py"""
import os
import runpy
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_APP = os.path.join(ROOT, 'backend', 'app.py')
VENV_PYTHON = os.path.normpath(os.path.join(ROOT, '.venv', 'Scripts', 'python.exe'))
LAUNCHER = os.path.normpath(os.path.abspath(__file__))


def _use_project_venv():
    if not os.path.isfile(VENV_PYTHON):
        return
    if os.path.normcase(os.path.normpath(sys.executable)) == os.path.normcase(VENV_PYTHON):
        return
    # os.execv breaks on Windows when the path contains spaces (e.g. TIH PROJECTS)
    result = subprocess.run([VENV_PYTHON, LAUNCHER, *sys.argv[1:]], cwd=ROOT)
    sys.exit(result.returncode)


_use_project_venv()

if not os.path.isfile(BACKEND_APP):
    print('ERROR: backend/app.py not found. Run from Geo-Trip-Planner folder.')
    sys.exit(1)

runpy.run_path(BACKEND_APP, run_name='__main__')
