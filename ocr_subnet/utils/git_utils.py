# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import os
import git
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union


def hash_repo_url(repo_url: str) -> str:
    """
    Create a filesystem-safe hash of the repository URL.
    
    Args:
        repo_url: The URL of the Git repository.
        
    Returns:
        A string that can be safely used as a directory name.
    """
    return repo_url.replace('/', '_').replace(':', '_').replace('.', '_')


def clone_repository(repo_url: str, target_dir: str) -> str:
    """
    Clone a Git repository to a target directory.
    
    Args:
        repo_url: The URL of the Git repository.
        target_dir: The directory where the repository should be cloned.
        
    Returns:
        The path to the cloned repository.
    """
    repo_hash = hash_repo_url(repo_url)
    repo_path = os.path.join(target_dir, repo_hash)
    
    # Create the target directory if it doesn't exist
    os.makedirs(os.path.dirname(repo_path), exist_ok=True)
    
    # Clone the repository
    git.Repo.clone_from(repo_url, repo_path)
    
    return repo_path


def fetch_repository(repo_path: str, ref: Optional[str] = None) -> None:
    """
    Fetch updates for a repository.
    
    Args:
        repo_path: The path to the Git repository.
        ref: Optional Git reference (branch, tag, or commit) to fetch.
    """
    repo = git.Repo(repo_path)
    if ref:
        repo.git.fetch('origin', ref)
    else:
        repo.git.fetch('--all')


def get_repo_info(repo_path: str) -> Dict[str, Any]:
    """
    Get information about a repository.
    
    Args:
        repo_path: The path to the Git repository.
        
    Returns:
        A dictionary containing repository metadata.
    """
    repo = git.Repo(repo_path)
    
    # Get branches
    branches = [str(branch) for branch in repo.branches]
    remote_branches = [ref.name for ref in repo.remote().refs]
    
    # Get latest commit
    latest_commit = None
    if repo.head.is_valid():
        commit = repo.head.commit
        latest_commit = {
            "hash": commit.hexsha,
            "author": f"{commit.author.name} <{commit.author.email}>",
            "timestamp": commit.committed_date,
            "message": commit.message.strip()
        }
    
    return {
        "branches": branches,
        "remote_branches": remote_branches,
        "latest_commit": latest_commit,
        "local_path": repo_path
    }


def create_branch(repo_path: str, branch_name: str, base_branch: str = "main") -> None:
    """
    Create a new branch in the repository.
    
    Args:
        repo_path: The path to the Git repository.
        branch_name: The name of the new branch.
        base_branch: The name of the branch to base the new branch on.
    """
    repo = git.Repo(repo_path)
    
    # Fetch the latest changes
    fetch_repository(repo_path)
    
    # Check if the base branch exists
    base_exists = False
    for ref in repo.references:
        if ref.name == base_branch or ref.name == f"origin/{base_branch}":
            base_exists = True
            break
    
    if not base_exists:
        raise ValueError(f"Base branch '{base_branch}' does not exist")
    
    # Create and checkout the new branch
    if base_branch.startswith("origin/"):
        repo.git.checkout('-b', branch_name, base_branch)
    else:
        try:
            repo.git.checkout(base_branch)
        except git.GitCommandError:
            # Try with origin/ prefix
            repo.git.checkout('-b', base_branch, f"origin/{base_branch}")
        
        repo.git.checkout('-b', branch_name)


def commit_changes(repo_path: str, commit_message: str, files: Optional[List[str]] = None) -> str:
    """
    Commit changes to a repository.
    
    Args:
        repo_path: The path to the Git repository.
        commit_message: The commit message.
        files: Optional list of files to add to the commit. If None, all changes are added.
        
    Returns:
        The commit hash.
    """
    repo = git.Repo(repo_path)
    
    # Add files to the index
    if files:
        repo.git.add(files)
    else:
        repo.git.add('.')
    
    # Commit changes
    repo.git.commit('-m', commit_message)
    
    # Return the commit hash
    return repo.head.commit.hexsha


def push_branch(repo_path: str, branch_name: str, remote: str = "origin") -> None:
    """
    Push a branch to a remote repository.
    
    Args:
        repo_path: The path to the Git repository.
        branch_name: The name of the branch to push.
        remote: The name of the remote repository.
    """
    repo = git.Repo(repo_path)
    repo.git.push(remote, branch_name)


def apply_patch(repo_path: str, patch_content: str) -> bool:
    """
    Apply a Git patch to a repository.
    
    Args:
        repo_path: The path to the Git repository.
        patch_content: The content of the patch to apply.
        
    Returns:
        True if the patch was applied successfully, False otherwise.
    """
    # Create a temporary file for the patch
    with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as patch_file:
        patch_file.write(patch_content)
        patch_path = patch_file.name
    
    try:
        # Apply the patch
        repo = git.Repo(repo_path)
        repo.git.apply(patch_path)
        return True
    except git.GitCommandError:
        return False
    finally:
        # Clean up the temporary file
        os.unlink(patch_path)


def create_diff(repo_path: str, base_ref: str, head_ref: str = "HEAD") -> str:
    """
    Create a diff between two references.
    
    Args:
        repo_path: The path to the Git repository.
        base_ref: The base reference for the diff.
        head_ref: The head reference for the diff (default: HEAD).
        
    Returns:
        The diff as a string.
    """
    repo = git.Repo(repo_path)
    return repo.git.diff(base_ref, head_ref)


def run_tests(repo_path: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Run tests in a repository.
    
    Args:
        repo_path: The path to the Git repository.
        
    Returns:
        A tuple with (success, results) where success is a boolean indicating
        whether all tests passed, and results is a dictionary with test details.
    """
    # Check for different test frameworks
    test_commands = [
        ["pytest", "--cov=."],
        ["python", "-m", "unittest", "discover"],
        ["npm", "test"],
        ["yarn", "test"]
    ]
    
    for cmd in test_commands:
        try:
            result = subprocess.run(
                cmd, 
                cwd=repo_path, 
                capture_output=True, 
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            success = result.returncode == 0
            
            return success, {
                "command": " ".join(cmd),
                "success": success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.SubprocessError:
            continue
    
    # If no test command worked
    return False, {
        "command": None,
        "success": False,
        "stdout": "",
        "stderr": "No supported test framework found",
        "returncode": -1
    }


def run_linters(repo_path: str) -> Dict[str, Any]:
    """
    Run linters on a repository.
    
    Args:
        repo_path: The path to the Git repository.
        
    Returns:
        A dictionary with linting results.
    """
    results = {}
    
    # Python linters
    if any(Path(repo_path).glob("*.py")):
        # Flake8
        try:
            flake8_result = subprocess.run(
                ["flake8", "."],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            results["flake8"] = {
                "success": flake8_result.returncode == 0,
                "output": flake8_result.stdout
            }
        except subprocess.SubprocessError:
            results["flake8"] = {"success": False, "output": "Failed to run flake8"}
        
        # Pylint
        try:
            pylint_result = subprocess.run(
                ["pylint", "**/*.py"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
                shell=True
            )
            results["pylint"] = {
                "success": pylint_result.returncode == 0,
                "output": pylint_result.stdout
            }
        except subprocess.SubprocessError:
            results["pylint"] = {"success": False, "output": "Failed to run pylint"}
    
    # JavaScript/TypeScript linters
    if any(Path(repo_path).glob("*.js")) or any(Path(repo_path).glob("*.ts")):
        try:
            eslint_result = subprocess.run(
                ["npx", "eslint", "."],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            results["eslint"] = {
                "success": eslint_result.returncode == 0,
                "output": eslint_result.stdout
            }
        except subprocess.SubprocessError:
            results["eslint"] = {"success": False, "output": "Failed to run ESLint"}
    
    return results


def analyze_code_quality(repo_path: str) -> Dict[str, Any]:
    """
    Analyze code quality using various metrics.
    
    Args:
        repo_path: The path to the Git repository.
        
    Returns:
        A dictionary with code quality metrics.
    """
    metrics = {}
    
    # Count lines of code
    try:
        loc_result = subprocess.run(
            ["find", ".", "-name", "*.py", "-o", "-name", "*.js", "-o", "-name", "*.ts", 
             "-o", "-name", "*.java", "-o", "-name", "*.c", "-o", "-name", "*.cpp", 
             "-o", "-name", "*.h", "-o", "-name", "*.hpp", "|", "xargs", "wc", "-l"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
            shell=True
        )
        metrics["lines_of_code"] = loc_result.stdout
    except subprocess.SubprocessError:
        metrics["lines_of_code"] = "Failed to count lines of code"
    
    # Analyze Python code with radon for complexity metrics
    if any(Path(repo_path).glob("*.py")):
        try:
            complexity_result = subprocess.run(
                ["radon", "cc", ".", "-a"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            metrics["complexity"] = complexity_result.stdout
            
            maintainability_result = subprocess.run(
                ["radon", "mi", "."],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            metrics["maintainability"] = maintainability_result.stdout
        except subprocess.SubprocessError:
            metrics["complexity"] = "Failed to analyze code complexity"
            metrics["maintainability"] = "Failed to analyze maintainability"
    
    return metrics 