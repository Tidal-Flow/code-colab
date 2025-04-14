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
import re
import time
import uuid
import json
import shutil
import hashlib
import tempfile
import subprocess
import threading
import traceback
import bittensor as bt
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Union

# Import for native Git handling
import pygit2

# Imports for HTTP Git server
from flask import Flask, request, Response, send_file, abort
import waitress

class GitOperationException(Exception):
    """Exception raised for Git operations errors"""
    pass

class GitServer:
    """
    Git server implementation using pygit2 and Flask for HTTP access.
    
    This class provides methods to:
    1. Create/manage Git repositories with pygit2
    2. Serve repositories over HTTP using Flask
    3. Handle clone/fetch/push operations from Git clients
    4. Maintain isolation between validators using their hotkeys
    """
    
    def __init__(self, base_dir: str, host: str = '0.0.0.0', port: int = 8080):
        """
        Initialize the Git server.
        
        Args:
            base_dir: Base directory to store all repositories
            host: Host to bind the HTTP server
            port: Port to bind the HTTP server
        """
        self.base_dir = os.path.expanduser(base_dir)
        self.host = host
        self.port = port
        self.server_thread = None
        self.running = False
        self.app = self._create_app()
        
        # Create base directory if it doesn't exist
        os.makedirs(self.base_dir, exist_ok=True)
        bt.logging.info(f"Git server initialized with base directory: {self.base_dir}")
    
    def _create_app(self) -> Flask:
        """
        Create and configure Flask app for Git HTTP server.
        
        Returns:
            Flask: Configured Flask application
        """
        app = Flask(__name__)
        
        @app.route('/', methods=['GET'])
        def index():
            return Response(
                json.dumps({"status": "running", "service": "Git HTTP Server"}),
                mimetype='application/json'
            )
        
        @app.route('/<validator_hotkey>/<repo_name>/info/refs', methods=['GET'])
        def get_refs(validator_hotkey, repo_name):
            service = request.args.get('service')
            if not service or not service.startswith('git-'):
                abort(400, "Invalid service parameter")
            
            repo_path = self._get_repo_path(validator_hotkey, repo_name)
            if not os.path.exists(repo_path):
                abort(404, f"Repository {repo_name} not found")
            
            try:
                # Use git command-line to generate refs advertisement
                # This is the most reliable way to handle the Git smart HTTP protocol
                output = subprocess.check_output(
                    [
                        'git', 
                        f'--git-dir={repo_path}', 
                        service[4:], 
                        '--stateless-rpc', 
                        '--advertise-refs', 
                        '.'
                    ],
                    stderr=subprocess.PIPE
                )
                
                # Prepare the response according to Git smart HTTP protocol
                service_name = service[4:]
                header = f"# service=git-{service_name}\n0000"
                response = f"{header}{output.decode('utf-8')}"
                
                return Response(
                    response,
                    mimetype=f'application/x-git-{service_name}-advertisement'
                )
            except Exception as e:
                bt.logging.error(f"Error getting refs: {e}")
                abort(500, str(e))
        
        @app.route('/<validator_hotkey>/<repo_name>/git-upload-pack', methods=['POST'])
        def git_upload_pack(validator_hotkey, repo_name):
            """Handle git fetch and git clone requests"""
            repo_path = self._get_repo_path(validator_hotkey, repo_name)
            if not os.path.exists(repo_path):
                abort(404, f"Repository {repo_name} not found")
            
            try:
                # Use git command-line for upload-pack (handles fetch/clone)
                process = subprocess.Popen(
                    ['git', f'--git-dir={repo_path}', 'upload-pack', '--stateless-rpc', '.'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Pass client data to git process
                stdout, stderr = process.communicate(request.data)
                
                if process.returncode != 0:
                    bt.logging.error(f"Git upload-pack error: {stderr.decode('utf-8')}")
                    abort(500, stderr.decode('utf-8'))
                
                return Response(
                    stdout,
                    mimetype='application/x-git-upload-pack-result'
                )
            except Exception as e:
                bt.logging.error(f"Error in upload-pack: {e}")
                abort(500, str(e))
        
        @app.route('/<validator_hotkey>/<repo_name>/git-receive-pack', methods=['POST'])
        def git_receive_pack(validator_hotkey, repo_name):
            """Handle git push requests"""
            repo_path = self._get_repo_path(validator_hotkey, repo_name)
            if not os.path.exists(repo_path):
                abort(404, f"Repository {repo_name} not found")
            
            try:
                # Use git command-line for receive-pack (handles push)
                process = subprocess.Popen(
                    ['git', f'--git-dir={repo_path}', 'receive-pack', '--stateless-rpc', '.'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Pass client data to git process
                stdout, stderr = process.communicate(request.data)
                
                if process.returncode != 0:
                    bt.logging.error(f"Git receive-pack error: {stderr.decode('utf-8')}")
                    abort(500, stderr.decode('utf-8'))
                
                return Response(
                    stdout,
                    mimetype='application/x-git-receive-pack-result'
                )
            except Exception as e:
                bt.logging.error(f"Error in receive-pack: {e}")
                abort(500, str(e))
        
        @app.errorhandler(Exception)
        def handle_error(e):
            bt.logging.error(f"Server error: {str(e)}")
            bt.logging.error(traceback.format_exc())
            return Response(
                json.dumps({"error": str(e)}),
                status=500,
                mimetype='application/json'
            )
        
        return app
    
    def start(self) -> None:
        """Start the Git HTTP server in a separate thread"""
        if self.running:
            bt.logging.warning("Git server is already running")
            return
        
        def run_server():
            bt.logging.info(f"Starting Git HTTP server on {self.host}:{self.port}")
            waitress.serve(self.app, host=self.host, port=self.port)
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.running = True
        bt.logging.info("Git server started")
    
    def stop(self) -> None:
        """Stop the Git HTTP server"""
        if not self.running:
            bt.logging.warning("Git server is not running")
            return
            
        self.running = False
        bt.logging.info("Git server stopped")
    
    def create_repository(self, validator_hotkey: str, repo_name: str, 
                          is_bare: bool = True) -> str:
        """
        Create a new Git repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            is_bare: Whether the repository should be bare (no working directory)
            
        Returns:
            str: Path to the created repository
        """
        # Create validator directory if it doesn't exist
        validator_dir = os.path.join(self.base_dir, validator_hotkey)
        os.makedirs(validator_dir, exist_ok=True)
        
        # Create repository path
        repo_path = self._get_repo_path(validator_hotkey, repo_name)
        
        # Check if repository already exists
        if os.path.exists(repo_path):
            bt.logging.info(f"Repository {repo_name} already exists for validator {validator_hotkey}")
            return repo_path
        
        # Create the repository
        try:
            # Initialize bare repository
            pygit2.init_repository(repo_path, is_bare)
            bt.logging.info(f"Created {'bare' if is_bare else ''} repository {repo_name} for validator {validator_hotkey}")
            return repo_path
        except Exception as e:
            bt.logging.error(f"Error creating repository: {e}")
            raise GitOperationException(f"Failed to create repository: {str(e)}")
    
    def clone_to_local(self, source_url: str, validator_hotkey: str, 
                     repo_name: str, is_bare: bool = True) -> str:
        """
        Clone a remote repository to the local Git server.
        
        Args:
            source_url: URL of the source repository
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            is_bare: Whether the repository should be bare (no working directory)
            
        Returns:
            str: Path to the cloned repository
        """
        repo_path = self._get_repo_path(validator_hotkey, repo_name)
        
        # Check if repository already exists
        if os.path.exists(repo_path):
            bt.logging.info(f"Repository {repo_name} already exists for validator {validator_hotkey}")
            return repo_path
        
        # Create parent directory
        os.makedirs(os.path.dirname(repo_path), exist_ok=True)
        
        try:
            # Clone repository using Git command line (more reliable for remote URLs)
            args = ["git", "clone"]
            if is_bare:
                args.append("--bare")
            args.extend([source_url, repo_path])
            
            process = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=True
            )
            
            bt.logging.info(f"Cloned repository {source_url} to {repo_path}")
            return repo_path
        except subprocess.CalledProcessError as e:
            bt.logging.error(f"Error cloning repository: {e.stderr}")
            raise GitOperationException(f"Failed to clone repository: {e.stderr}")
        except Exception as e:
            bt.logging.error(f"Error cloning repository: {e}")
            raise GitOperationException(f"Failed to clone repository: {str(e)}")
    
    def get_repo_info(self, validator_hotkey: str, repo_name: str) -> Dict[str, Any]:
        """
        Get information about a repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            
        Returns:
            Dict: Repository information
        """
        repo_path = self._get_repo_path(validator_hotkey, repo_name)
        
        # Check if repository exists
        if not os.path.exists(repo_path):
            raise GitOperationException(f"Repository {repo_name} not found")
        
        try:
            # Open repository
            repo = pygit2.Repository(repo_path)
            
            # Get branches
            branches = []
            for branch_name in repo.branches.local:
                branch = repo.branches.local[branch_name]
                commit = repo[branch.target]
                branches.append({
                    'name': branch_name,
                    'commit_id': str(branch.target),
                    'commit_message': commit.message.strip() if hasattr(commit, 'message') else '',
                    'commit_time': datetime.fromtimestamp(commit.commit_time).isoformat() if hasattr(commit, 'commit_time') else ''
                })
            
            # Get repository info
            return {
                'name': repo_name,
                'path': repo_path,
                'is_bare': repo.is_bare,
                'is_empty': repo.is_empty,
                'head': str(repo.head.target) if not repo.is_empty else None,
                'branches': branches,
                'branch_count': len(branches),
                'http_url': f"http://{self.host}:{self.port}/{validator_hotkey}/{repo_name}"
            }
        except Exception as e:
            bt.logging.error(f"Error getting repository info: {e}")
            raise GitOperationException(f"Failed to get repository info: {str(e)}")
    
    def create_branch(self, validator_hotkey: str, repo_name: str, 
                     branch_name: str, source_branch: str = "main") -> bool:
        """
        Create a new branch in the repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            branch_name: Name of the new branch
            source_branch: Source branch to create from
            
        Returns:
            bool: Success status
        """
        repo_path = self._get_repo_path(validator_hotkey, repo_name)
        
        # Check if repository exists
        if not os.path.exists(repo_path):
            raise GitOperationException(f"Repository {repo_name} not found")
        
        try:
            # Open repository
            repo = pygit2.Repository(repo_path)
            
            # Check if branch already exists
            if branch_name in repo.branches:
                bt.logging.warning(f"Branch {branch_name} already exists in repository {repo_name}")
                return True
            
            # Get source branch
            if source_branch not in repo.branches:
                raise GitOperationException(f"Source branch {source_branch} not found")
            
            source = repo.branches[source_branch]
            target = source.target
            
            # Create new branch
            repo.branches.create(branch_name, repo[target])
            bt.logging.info(f"Created branch {branch_name} in repository {repo_name}")
            return True
        except Exception as e:
            bt.logging.error(f"Error creating branch: {e}")
            raise GitOperationException(f"Failed to create branch: {str(e)}")
    
    def delete_repository(self, validator_hotkey: str, repo_name: str) -> bool:
        """
        Delete a repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            
        Returns:
            bool: Success status
        """
        repo_path = self._get_repo_path(validator_hotkey, repo_name)
        
        # Check if repository exists
        if not os.path.exists(repo_path):
            bt.logging.warning(f"Repository {repo_name} not found")
            return False
        
        try:
            # Remove repository directory
            shutil.rmtree(repo_path)
            bt.logging.info(f"Deleted repository {repo_name}")
            return True
        except Exception as e:
            bt.logging.error(f"Error deleting repository: {e}")
            raise GitOperationException(f"Failed to delete repository: {str(e)}")
    
    def _get_repo_path(self, validator_hotkey: str, repo_name: str) -> str:
        """
        Get the path to a repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            
        Returns:
            str: Path to the repository
        """
        # Sanitize repository name (remove .git if present)
        repo_name = repo_name.replace(".git", "")
        
        # Combine path components
        repo_path = os.path.join(self.base_dir, validator_hotkey, f"{repo_name}.git")
        return repo_path
    
    def get_repo_url(self, validator_hotkey: str, repo_name: str) -> str:
        """
        Get the HTTP URL for a repository.
        
        Args:
            validator_hotkey: Validator's hotkey for isolation
            repo_name: Name of the repository
            
        Returns:
            str: HTTP URL for the repository
        """
        # Sanitize repository name (remove .git if present)
        repo_name = repo_name.replace(".git", "")
        
        # Build URL
        repo_url = f"http://{self.host}:{self.port}/{validator_hotkey}/{repo_name}.git"
        return repo_url 