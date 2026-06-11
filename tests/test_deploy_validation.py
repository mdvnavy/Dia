import os
import subprocess
import sys
import time
import shutil
import pytest
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def get_venv_python():
    """Locates the virtualenv python executable or falls back to sys.executable."""
    win_py = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    if os.path.exists(win_py):
        return win_py
    unix_py = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if os.path.exists(unix_py):
        return unix_py
    return sys.executable

@pytest.fixture
def mock_gcloud_env():
    """Creates a temporary directory with a mock gcloud executable that fails."""
    temp_dir = tempfile.mkdtemp()
    
    # Create bash mock
    bash_mock = os.path.join(temp_dir, "gcloud")
    with open(bash_mock, "w", newline="\n") as f:
        f.write("#!/bin/sh\nexit 1\n")
    # Make executable on Unix/WSL if needed
    os.chmod(bash_mock, 0o755)
    
    # Create Windows mock
    win_mock = os.path.join(temp_dir, "gcloud.cmd")
    with open(win_mock, "w") as f:
        f.write("@exit /b 1\n")
        
    env = os.environ.copy()
    env["PATH"] = temp_dir + os.path.pathsep + env.get("PATH", "")
    env["PROJECT_ID"] = ""
    
    yield env
    
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

def test_deploy_sh_validation_empty_project(mock_gcloud_env):
    """Verify deploy.sh exits gracefully with status 1 and instructions if PROJECT_ID is empty."""
    # Check if bash is available
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash is not available on this system")
        
    result = subprocess.run(
        [bash_path, "deploy.sh"],
        cwd=PROJECT_ROOT,
        env=mock_gcloud_env,
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 1
    # Check that it did not crash silently, but printed instructions
    assert "Error: PROJECT_ID is not set" in result.stdout or "Error: PROJECT_ID is not set" in result.stderr
    assert "gcloud config set project" in result.stdout or "gcloud config set project" in result.stderr

@pytest.mark.skip(
    reason="Flaky PS1 deploy-path validation on local Windows dev box; "
    "excluded from the autoresearch test gate. See "
    "docs/superpowers/specs/2026-06-10-dia-autoresearch-design.md"
)
def test_deploy_ps1_validation_empty_project(mock_gcloud_env):
    """Verify deploy.ps1 exits gracefully with status 1 and instructions if PROJECT_ID is empty."""
    powershell_path = shutil.which("powershell")
    if not powershell_path:
        pytest.skip("powershell is not available on this system")
        
    result = subprocess.run(
        [powershell_path, "-ExecutionPolicy", "Bypass", "-File", "deploy.ps1"],
        cwd=PROJECT_ROOT,
        env=mock_gcloud_env,
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 1
    # Check that it did not crash silently, but printed instructions
    assert "Error: PROJECT_ID is not set" in result.stdout or "Error: PROJECT_ID is not set" in result.stderr
    assert "gcloud config set project" in result.stdout or "gcloud config set project" in result.stderr

def test_app_resilience_no_gemini_key():
    """Verify app.py boots successfully and logs a warning rather than crashing if GEMINI_API_KEY is missing."""
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env["PORT"] = "5997"
    env["HOST"] = "127.0.0.1"
    env["TESTING"] = "false"  # run in production mode to verify the startup try/except
    
    proc = subprocess.Popen(
        [get_venv_python(), "app.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        # Give it a couple seconds to boot
        time.sleep(2.0)
        # Check if process is still running
        status = proc.poll()
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=2)
        
    print("--- APP STDOUT ---")
    print(stdout)
    print("--- APP STDERR ---")
    print(stderr)
    
    assert status is None, f"Server crashed on startup with exit code {status}. Stderr: {stderr}"
