"""
e2b_tools.py
Production-grade E2B toolkit with multi-sandbox management, concurrency,
background jobs, and a SandboxManager layer.

Requirements:
- e2b_code_interpreter (SDK v2-ish)
- agno.* classes used for integration (Agent, Team, Image, Toolkit, ToolResult)
"""

import base64
import json
import logging
import tempfile
import time
import threading
from dataclasses import dataclass, field
from os import fdopen, getenv
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

# agno imports (keep these if you integrate into your agent system)
from agno.agent import Agent
from agno.media import Image
from agno.team.team import Team
from agno.tools import Toolkit
from agno.tools.function import ToolResult
from agno.utils.code_execution import prepare_python_code

# Try to import the E2B SDK
try:
    from e2b_code_interpreter import Sandbox
except ImportError as e:
    raise ImportError("`e2b_code_interpreter` not installed. Please install using `pip install e2b_code_interpreter`") from e

logger = logging.getLogger("e2b_tools")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(ch)


@dataclass
class JobRecord:
    job_id: str
    sandbox_id: str
    job_type: str  # 'python' | 'command' | 'file' | ...
    future: Future
    process_obj: Optional[Any] = None  # if SDK returns a process object (for commands)
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxSlot:
    sandbox: Any
    sandbox_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2))
    last_execution: Optional[Any] = None
    downloaded_files: Dict[int, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    jobs: Dict[str, JobRecord] = field(default_factory=dict)


class SandboxManager:
    """
    Encapsulates lifecycle operations for E2B sandboxes:
    - create
    - connect (attach to an existing sandbox)
    - list (account-wide)
    - shutdown
    """

    def __init__(self, api_key: Optional[str] = None, default_timeout: int = 300, default_options: Optional[Dict] = None):
        # Auto-load API key from environment if not provided
        self.api_key = api_key or getenv("E2B_API_KEY")
        if not self.api_key:
            raise ValueError("E2B_API_KEY is not set and no api_key was provided. Set E2B_API_KEY or pass api_key explicitly.")

        self.default_timeout = default_timeout
        self.default_options = default_options or {}
        self.slots: Dict[str, SandboxSlot] = {}
        self._lock = threading.Lock()

    def create(self, timeout: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None) -> SandboxSlot:
        """
        Create a new sandbox and return a SandboxSlot wrapper.
        Raises on failure.
        """
        opts = dict(self.default_options)
        timeout = timeout or self.default_timeout
        # merge metadata into options if SDK supports it
        if metadata:
            opts["metadata"] = metadata

        logger.info("Creating sandbox (timeout=%s) ...", timeout)
        sb = Sandbox.create(api_key=self.api_key, timeout=timeout, **opts)
        # SDK may expose sandbox_id or id
        sid = getattr(sb, "sandbox_id", None) or getattr(sb, "id", None) or str(uuid4())
        slot = SandboxSlot(sandbox=sb, sandbox_id=sid, metadata={"created_at": time.time(), "timeout": timeout, **(metadata or {})})
        with self._lock:
            self.slots[sid] = slot
        logger.info("Created sandbox %s", sid)
        return slot

    def connect(self, sandbox_id: str) -> SandboxSlot:
        """
        Connect to an existing sandbox by ID and wrap it in a SandboxSlot.
        """
        logger.info("Connecting to sandbox %s ...", sandbox_id)
        sb = Sandbox.connect(sandbox_id)
        sid = getattr(sb, "sandbox_id", None) or getattr(sb, "id", None) or sandbox_id
        slot = SandboxSlot(sandbox=sb, sandbox_id=sid, metadata={"connected_at": time.time()})
        with self._lock:
            self.slots[sid] = slot
        logger.info("Connected to sandbox %s", sid)
        return slot

    def get_slot(self, sandbox_id: Optional[str] = None) -> SandboxSlot:
        """
        Retrieve a SandboxSlot by ID or (if sandbox_id is None and there is one) a single default slot.
        """
        with self._lock:
            if sandbox_id:
                slot = self.slots.get(sandbox_id)
                if not slot:
                    raise KeyError(f"Sandbox {sandbox_id} not found")
                return slot
            # if no ID provided, return a deterministic default (first created)
            if not self.slots:
                raise KeyError("No sandboxes available")
            # prefer the first inserted (inserting order preserved)
            sid = next(iter(self.slots))
            return self.slots[sid]

    def list_account_sandboxes(self) -> List[Dict[str, Any]]:
        """
        List sandboxes in the E2B account (server side). Returns list of dicts.
        """
        # SDK often has Sandbox.list() -> paginator
        logger.info("Listing account sandboxes via SDK")
        paginator = Sandbox.list()
        try:
            items = paginator.next_items()
        except Exception:
            # Fallback if list() returns a plain list
            items = paginator
        results = []
        for s in items or []:
            results.append({
                "sandbox_id": getattr(s, "sandbox_id", None) or getattr(s, "id", None),
                "started_at": str(getattr(s, "started_at", None)),
                "template_id": getattr(s, "template_id", None),
                "metadata": getattr(s, "metadata", None),
            })
        return results

    def shutdown(self, sandbox_id: str) -> Dict[str, Any]:
        """
        Shutdown a sandbox and remove it from management.
        """
        slot = self.get_slot(sandbox_id)
        with slot.lock:
            logger.info("Shutting down sandbox %s", sandbox_id)
            try:
                # SDK kill semantics
                result = slot.sandbox.kill()
            except Exception as e:
                logger.exception("Error shutting down sandbox %s", sandbox_id)
                raise
            # shutdown executor
            try:
                slot.executor.shutdown(wait=False)
            except Exception:
                pass
        with self._lock:
            self.slots.pop(sandbox_id, None)
        return {"status": "success", "sandbox_id": sandbox_id, "result": result}


class E2BToolkit(Toolkit):
    """
    High-level toolkit integrating SandboxManager + concurrent execution and job management.

    Usage:
        mgr = SandboxManager(api_key="...", default_timeout=300)
        toolkit = E2BToolkit(mgr)
    """

    def __init__(self, sandbox_manager: SandboxManager, auto_create_default: bool = True, global_workers: int = 10, **kwargs):
        self.manager = sandbox_manager
        self.global_executor = ThreadPoolExecutor(max_workers=global_workers)
        self.jobs: Dict[str, JobRecord] = {}
        self.default_sandbox_id: Optional[str] = None
        # Initialize default sandbox optionally
        if auto_create_default:
            slot = self.manager.create(timeout=self.manager.default_timeout)
            self.default_sandbox_id = slot.sandbox_id
        # Build tools list for integration
        tools = [
            # sandbox lifecycle
            self.create_sandbox,
            self.connect_to_sandbox,
            self.list_managed_sandboxes,
            self.list_account_sandboxes,
            self.set_default_sandbox,
            # code execution
            self.run_python_code,
            self.run_python_code_background,
            self.run_in_all_sandboxes,
            # commands
            self.run_command,
            self.run_command_background,
            self.kill_job,
            self.get_job_status,
            # file helpers
            self.upload_file,
            self.download_file_from_sandbox,
            self.download_png_result,
            self.download_chart_data,
            # NEW: public url / server helpers
            self.get_public_url,
            self.run_server,
            # shutdown helpers that work across runs
            self.force_shutdown,
            self.force_shutdown_all,
            # sandbox controls
            self.set_sandbox_timeout,
            self.get_sandbox_status,
            self.shutdown_sandbox,
            self.shutdown_all_sandboxes,
        ]
        super().__init__(name="e2b_tools", tools=tools, **kwargs)

    #
    # Sandbox helper wrappers
    #
    def create_sandbox(self, timeout: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None, set_as_default: bool = False) -> Dict[str, Any]:
        try:
            slot = self.manager.create(timeout=timeout, metadata=metadata)
            if set_as_default or not self.default_sandbox_id:
                self.default_sandbox_id = slot.sandbox_id
            return {"status": "success", "sandbox_id": slot.sandbox_id}
        except Exception as e:
            logger.exception("create_sandbox failed")
            return {"status": "error", "message": str(e)}

    def connect_to_sandbox(self, sandbox_id: str, set_as_default: bool = False) -> Dict[str, Any]:
        try:
            slot = self.manager.connect(sandbox_id)
            if set_as_default:
                self.default_sandbox_id = slot.sandbox_id
            return {"status": "success", "sandbox_id": slot.sandbox_id}
        except Exception as e:
            logger.exception("connect_to_sandbox failed")
            return {"status": "error", "message": str(e)}

    def list_managed_sandboxes(self) -> Dict[str, Any]:
        try:
            with self.manager._lock:
                entries = [
                    {
                        "sandbox_id": sid,
                        "is_default": sid == self.default_sandbox_id,
                        "metadata": slot.metadata,
                        "has_jobs": bool(slot.jobs),
                    }
                    for sid, slot in self.manager.slots.items()
                ]
            return {"status": "success", "sandboxes": entries, "count": len(entries)}
        except Exception as e:
            logger.exception("list_managed_sandboxes failed")
            return {"status": "error", "message": str(e)}

    def list_account_sandboxes(self) -> Dict[str, Any]:
        try:
            items = self.manager.list_account_sandboxes()
            return {"status": "success", "sandboxes": items, "count": len(items)}
        except Exception as e:
            logger.exception("list_account_sandboxes failed")
            return {"status": "error", "message": str(e)}

    def set_default_sandbox(self, sandbox_id: str) -> Dict[str, Any]:
        if sandbox_id not in self.manager.slots:
            return {"status": "error", "message": f"Sandbox {sandbox_id} not managed by toolkit"}
        self.default_sandbox_id = sandbox_id
        return {"status": "success", "sandbox_id": sandbox_id}

    #
    # Core execution helpers (synchronous and background)
    #
    def _resolve_slot(self, sandbox_id: Optional[str]) -> SandboxSlot:
        sid = sandbox_id or self.default_sandbox_id
        if sid is None:
            raise KeyError("No sandbox specified and no default sandbox is set")
        return self.manager.get_slot(sid)

    def run_python_code(self, code: str, sandbox_id: Optional[str] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Synchronous execution of Python code in a sandbox.

        Returns a dict with status/result or error.
        """
        try:
            slot = self._resolve_slot(sandbox_id)
            executable = prepare_python_code(code)
            with slot.lock:
                execution = slot.sandbox.run_code(executable, timeout=timeout or slot.metadata.get("timeout"))
                slot.last_execution = execution

            if getattr(execution, "error", None):
                return {"status": "error", "error": {
                    "name": execution.error.name,
                    "value": execution.error.value,
                    "traceback": getattr(execution.error, "traceback", None),
                }}

            # Build results list
            results = []
            if getattr(execution, "logs", None):
                results.append({"logs": execution.logs})

            for i, r in enumerate(getattr(execution, "results", []) or []):
                entry = {"index": i}
                if getattr(r, "text", None):
                    entry["text"] = r.text
                if getattr(r, "png", None):
                    entry["png"] = True
                if getattr(r, "chart", None):
                    entry["chart"] = r.chart
                results.append(entry)

            return {"status": "success", "results": results}
        except Exception as e:
            logger.exception("run_python_code failed")
            return {"status": "error", "message": str(e)}

    def run_python_code_background(self, code: str, sandbox_id: Optional[str] = None, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a background job that runs python code in a sandbox. Returns job_id.
        """
        try:
            slot = self._resolve_slot(sandbox_id)
            jid = job_id or str(uuid4())

            def target():
                return self.run_python_code(code, sandbox_id=slot.sandbox_id)

            future = slot.executor.submit(target)
            job = JobRecord(job_id=jid, sandbox_id=slot.sandbox_id, job_type="python", future=future)
            slot.jobs[jid] = job
            self.jobs[jid] = job
            return {"status": "accepted", "job_id": jid}
        except Exception as e:
            logger.exception("run_python_code_background failed")
            return {"status": "error", "message": str(e)}

    def run_in_all_sandboxes(self, code: str, timeout_each: Optional[int] = None) -> Dict[str, Any]:
        """
        Run the same code in all managed sandboxes concurrently.
        Returns a dict mapping sandbox_id -> result dict
        """
        results: Dict[str, Any] = {}
        futures = []
        for sid, slot in list(self.manager.slots.items()):
            futures.append((sid, slot.executor.submit(lambda s=slot: self.run_python_code(code, sandbox_id=s.sandbox_id, timeout=timeout_each))))
        for sid, fut in futures:
            try:
                res = fut.result()
            except Exception as e:
                res = {"status": "error", "message": str(e)}
            results[sid] = res
        return {"status": "success", "results": results}

    #
    # Commands
    #
    def run_command(self, command: str, sandbox_id: Optional[str] = None, background: bool = False) -> Dict[str, Any]:
        """
        Run a shell command. If background=True, returns a job id immediately.
        Otherwise returns command stdout/stderr info (if provided by the SDK).
        """
        try:
            slot = self._resolve_slot(sandbox_id)

            def do_run():
                with slot.lock:
                    # For synchronous execution, ask SDK for not-background run
                    res = slot.sandbox.commands.run(command, background=False)
                output = {}
                if hasattr(res, "stdout") and res.stdout:
                    output["stdout"] = res.stdout
                if hasattr(res, "stderr") and res.stderr:
                    output["stderr"] = res.stderr
                return {"status": "success", "output": output}

            if background:
                jid = str(uuid4())
                future = slot.executor.submit(lambda: do_run())
                job = JobRecord(job_id=jid, sandbox_id=slot.sandbox_id, job_type="command", future=future)
                slot.jobs[jid] = job
                self.jobs[jid] = job
                return {"status": "accepted", "job_id": jid}
            else:
                return do_run()
        except Exception as e:
            logger.exception("run_command failed")
            return {"status": "error", "message": str(e)}

    def run_command_background(self, command: str, sandbox_id: Optional[str] = None, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Variant that attempts to capture the SDK process object if SDK supports background processes.
        """
        try:
            slot = self._resolve_slot(sandbox_id)
            jid = job_id or str(uuid4())

            def _run_bg():
                with slot.lock:
                    # If the SDK supports background=True returning a process-like object, try using it
                    try:
                        proc = slot.sandbox.commands.run(command, background=True)
                        # If proc is an object with .wait or .stdout, block and return info
                        if hasattr(proc, "wait"):
                            rc = proc.wait()
                            return {"status": "success", "returncode": rc}
                        else:
                            # If SDK returns immediate background handle, return a handle summary
                            return {"status": "success", "process_handle": str(proc)}
                    except TypeError:
                        # Fallback: synchronous run if background param not supported
                        res = slot.sandbox.commands.run(command, background=False)
                        output = {}
                        if hasattr(res, "stdout") and res.stdout:
                            output["stdout"] = res.stdout
                        if hasattr(res, "stderr") and res.stderr:
                            output["stderr"] = res.stderr
                        return {"status": "success", "output": output}

            future = slot.executor.submit(_run_bg)
            job = JobRecord(job_id=jid, sandbox_id=slot.sandbox_id, job_type="command", future=future)
            slot.jobs[jid] = job
            self.jobs[jid] = job
            return {"status": "accepted", "job_id": jid}
        except Exception as e:
            logger.exception("run_command_background failed")
            return {"status": "error", "message": str(e)}

    #
    # Job management
    #
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            return {"status": "not_found", "job_id": job_id}
        fut = job.future
        if fut.cancelled():
            return {"status": "cancelled", "job_id": job_id}
        if fut.running():
            return {"status": "running", "job_id": job_id}
        if fut.done():
            try:
                res = fut.result()
                return {"status": "done", "result": res, "job_id": job_id}
            except Exception as e:
                return {"status": "error", "error": str(e), "job_id": job_id}
        return {"status": "pending", "job_id": job_id}

    def kill_job(self, job_id: str) -> Dict[str, Any]:
        """
        Attempt to cancel a job and kill underlying process if we have it.
        """
        job = self.jobs.get(job_id)
        if not job:
            return {"status": "not_found", "job_id": job_id}
        # Try to cancel future
        cancelled = job.future.cancel()
        # If we have a process object recorded, attempt to kill it
        killed_proc = False
        if job.process_obj and hasattr(job.process_obj, "kill"):
            try:
                job.process_obj.kill()
                killed_proc = True
            except Exception:
                logger.exception("failed to kill process for job %s", job_id)

        # Clean references if cancellation succeeded
        try:
            slot = self.manager.get_slot(job.sandbox_id)
            slot.jobs.pop(job_id, None)
        except Exception:
            pass
        self.jobs.pop(job_id, None)
        return {"status": "cancelled" if cancelled else "not_cancelled", "killed_proc": killed_proc, "job_id": job_id}

    #
    # Filesystem and artifact helpers
    #
    def upload_file(self, local_path: str, sandbox_id: Optional[str] = None, sandbox_path: Optional[str] = None) -> Dict[str, Any]:
        try:
            slot = self._resolve_slot(sandbox_id)
            spath = sandbox_path or Path(local_path).name
            with slot.lock:
                with open(local_path, "rb") as f:
                    # SDK might accept a file-like or bytes
                    file_info = slot.sandbox.files.write(spath, f)
            return {"status": "success", "sandbox_path": getattr(file_info, "path", spath)}
        except Exception as e:
            logger.exception("upload_file failed")
            return {"status": "error", "message": str(e)}

    def download_file_from_sandbox(self, sandbox_path: str, sandbox_id: Optional[str] = None, local_path: Optional[str] = None) -> Dict[str, Any]:
        try:
            slot = self._resolve_slot(sandbox_id)
            local = local_path or Path(sandbox_path).name
            with slot.lock:
                content = slot.sandbox.files.read(sandbox_path)
            # content may be bytes or str
            mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
            with open(local, mode) as f:
                f.write(content)
            return {"status": "success", "local_path": str(local)}
        except Exception as e:
            logger.exception("download_file_from_sandbox failed")
            return {"status": "error", "message": str(e)}

    def download_png_result(self, agent: Union[Agent, Team], result_index: int = 0, sandbox_id: Optional[str] = None, output_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Save a PNG from the last execution in the chosen sandbox and create an Image artifact.
        """
        try:
            slot = self._resolve_slot(sandbox_id)
            execution = slot.last_execution
            if not execution:
                return {"status": "error", "message": "No execution found for this sandbox"}
            if result_index >= len(execution.results):
                return {"status": "error", "message": f"Result index {result_index} out of range"}

            result = execution.results[result_index]
            png_b64 = getattr(result, "png", None)
            if not png_b64:
                return {"status": "error", "message": "Result is not a PNG"}

            png_bytes = base64.b64decode(png_b64)
            if output_path:
                with open(output_path, "wb") as f:
                    f.write(png_bytes)
                slot.downloaded_files[result_index] = output_path

            fd, temp_path = tempfile.mkstemp(suffix=".png")
            with fdopen(fd, "wb") as tmp:
                tmp.write(png_bytes)
            file_url = f"file://{temp_path}"
            image_id = str(uuid4())
            image_obj = Image(id=image_id, url=file_url, original_prompt=f"Generated from sandbox {slot.sandbox_id} result {result_index}")
            # Typically you would attach artifact to agent using agent.add_artifact or similar.
            return {"status": "success", "image_id": image_id, "file_url": file_url}
        except Exception as e:
            logger.exception("download_png_result failed")
            return {"status": "error", "message": str(e)}

    def download_chart_data(self, agent: Agent, result_index: int = 0, sandbox_id: Optional[str] = None, output_path: Optional[str] = None, add_as_artifact: bool = True) -> Dict[str, Any]:
        try:
            slot = self._resolve_slot(sandbox_id)
            execution = slot.last_execution
            if not execution:
                return {"status": "error", "message": "No execution found for this sandbox"}
            if result_index >= len(execution.results):
                return {"status": "error", "message": f"Result index {result_index} out of range"}

            result = execution.results[result_index]
            chart = getattr(result, "chart", None)
            if not chart:
                return {"status": "error", "message": "Result does not contain chart data"}

            out = output_path or f"chart-data-{slot.sandbox_id}-{result_index}.json"
            with open(out, "w") as f:
                json.dump(chart, f, indent=2)

            response = {"status": "success", "chart_path": out}
            if add_as_artifact and getattr(result, "png", None):
                png_b64 = result.png
                png_bytes = base64.b64decode(png_b64)
                fd, temp_path = tempfile.mkstemp(suffix=".png")
                with fdopen(fd, "wb") as tmp:
                    tmp.write(png_bytes)
                file_url = f"file://{temp_path}"
                image_id = str(uuid4())
                response["chart_image"] = {"image_id": image_id, "file_url": file_url}
            return response
        except Exception as e:
            logger.exception("download_chart_data failed")
            return {"status": "error", "message": str(e)}

    #
    # Internet Access Functions (public URL & server helpers)
    #
    def get_public_url(self, port: int, sandbox_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a public URL for a service running in the sandbox on the specified port.

        Args:
            port: Port number the service is running on in the sandbox
            sandbox_id: Optional sandbox id (uses default if None)

        Returns:
            dict: {"status": "success", "url": "http://..."} or error
        """
        try:
            slot = self._resolve_slot(sandbox_id)
            # SDK convention used earlier: sandbox.get_host(port)
            host = slot.sandbox.get_host(port)
            if not host:
                return {"status": "error", "message": "SDK returned no host for the given port"}
            url = f"http://{host}"
            return {"status": "success", "url": url}
        except Exception as e:
            logger.exception("get_public_url failed")
            return {"status": "error", "message": str(e)}

    def run_server(self, command: str, port: int, sandbox_id: Optional[str] = None, wait_seconds: int = 2) -> Dict[str, Any]:
        """
        Start a server in the sandbox and return its public URL.

        Tries to start the server with the SDK in background (if supported),
        waits `wait_seconds` to allow the server to bind, then fetches the public host.

        Args:
            command: The shell command to run (e.g. `python -m http.server 8000`)
            port: Port the server will listen on inside the sandbox
            sandbox_id: Optional sandbox id (uses default if None)
            wait_seconds: Seconds to wait before querying public URL

        Returns:
            dict: {"status":"success", "url":"http://..."} or error
        """
        try:
            slot = self._resolve_slot(sandbox_id)

            # Attempt to start the server in background if SDK supports it:
            with slot.lock:
                try:
                    slot.sandbox.commands.run(command, background=True)
                except TypeError:
                    # Some SDK variations might not accept background param; fall back to background thread:
                    slot.executor.submit(lambda: slot.sandbox.commands.run(command))

            # Wait a short moment for the server to bind
            time.sleep(wait_seconds)

            host = slot.sandbox.get_host(port)
            if not host:
                return {"status": "error", "message": "Could not obtain public host for the port (server may not have started yet)"}

            url = f"http://{host}"
            return {"status": "success", "url": url}
        except Exception as e:
            logger.exception("run_server failed")
            return {"status": "error", "message": str(e)}

    #
    # Force shutdown helpers (work across runs)
    #
    def force_shutdown(self, sandbox_id: str) -> Dict[str, Any]:
        """
        Force shutdown a sandbox by ID using direct SDK connect + kill.
        Works even if the manager hasn't seen this sandbox before.
        """
        try:
            sb = Sandbox.connect(sandbox_id)
            # attempt kill
            try:
                res = sb.kill()
                # if we had this sandbox in manager, remove it
                with self.manager._lock:
                    if sandbox_id in self.manager.slots:
                        self.manager.slots.pop(sandbox_id, None)
                return {"status": "success", "sandbox_id": sandbox_id, "result": res}
            except Exception as e:
                logger.exception("Error killing sandbox via SDK")
                return {"status": "error", "message": str(e)}
        except Exception as e:
            logger.exception("force_shutdown failed")
            return {"status": "error", "message": str(e)}

    def force_shutdown_all(self) -> Dict[str, Any]:
        """
        Force shutdown all sandboxes in the account (best-effort).
        WARNING: This will attempt to kill every sandbox visible to the API key.
        """
        results = {}
        try:
            paginator = Sandbox.list()
            try:
                items = paginator.next_items()
            except Exception:
                items = paginator
            for s in items or []:
                sid = getattr(s, "sandbox_id", None) or getattr(s, "id", None)
                if not sid:
                    continue
                try:
                    sb = Sandbox.connect(sid)
                    r = sb.kill()
                    # prune from manager if present
                    with self.manager._lock:
                        if sid in self.manager.slots:
                            self.manager.slots.pop(sid, None)
                    results[sid] = {"status": "killed", "result": r}
                except Exception as e:
                    logger.exception("Failed to kill sandbox %s", sid)
                    results[sid] = {"status": "error", "message": str(e)}
            return {"status": "success", "results": results}
        except Exception as e:
            logger.exception("force_shutdown_all failed")
            return {"status": "error", "message": str(e)}

    #
    # Sandbox controls
    #
    def set_sandbox_timeout(self, timeout: int, sandbox_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            slot = self._resolve_slot(sandbox_id)
            with slot.lock:
                if hasattr(slot.sandbox, "set_timeout"):
                    slot.sandbox.set_timeout(timeout)
                else:
                    slot.sandbox.timeout = timeout
                slot.metadata["timeout"] = timeout
            return {"status": "success", "sandbox_id": slot.sandbox_id, "timeout": timeout}
        except Exception as e:
            logger.exception("set_sandbox_timeout failed")
            return {"status": "error", "message": str(e)}

    def get_sandbox_status(self, sandbox_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            slot = self._resolve_slot(sandbox_id)
            sid = slot.sandbox_id
            # Expose limited status info
            return {"status": "success", "sandbox_id": sid, "metadata": slot.metadata}
        except Exception as e:
            logger.exception("get_sandbox_status failed")
            return {"status": "error", "message": str(e)}

    def shutdown_sandbox(self, sandbox_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Shutdown sandbox by ID. If the toolbox/manager doesn't already manage the sandbox,
        the method will attempt to connect via SDK and then perform the shutdown.
        """
        try:
            sid = sandbox_id or self.default_sandbox_id
            if sid is None:
                return {"status": "error", "message": "No sandbox specified and no default set"}

            # If manager does not currently manage this sandbox, try to connect and add it
            try:
                _ = self.manager.get_slot(sid)
            except KeyError:
                logger.info("Sandbox %s not in manager.slots — attempting SDK.connect before shutdown", sid)
                try:
                    sb = Sandbox.connect(sid)
                    # wrap into a temporary slot so manager.shutdown can work
                    tmp_slot = SandboxSlot(sandbox=sb, sandbox_id=sid, metadata={"adopted_via_shutdown": time.time()})
                    with self.manager._lock:
                        self.manager.slots[sid] = tmp_slot
                except Exception as e:
                    logger.exception("Could not connect to sandbox %s: %s", sid, e)
                    return {"status": "error", "message": f"Sandbox {sid} not managed and SDK.connect failed: {e}"}

            # Now call manager.shutdown which will call slot.sandbox.kill()
            res = self.manager.shutdown(sid)

            # If default was this, reset default to another managed slot (if any)
            if self.default_sandbox_id == sid:
                self.default_sandbox_id = next(iter(self.manager.slots.keys()), None)
            return {"status": "success", "result": res}
        except Exception as e:
            logger.exception("shutdown_sandbox failed")
            return {"status": "error", "message": str(e)}

    def shutdown_all_sandboxes(self) -> Dict[str, Any]:
        """
        Shutdown all sandboxes that the manager currently knows about.
        Note: for account-wide shutdown, use force_shutdown_all().
        """
        results = {}
        # copy keys to avoid mutation issues
        for sid in list(self.manager.slots.keys()):
            try:
                results[sid] = self.manager.shutdown(sid)
            except Exception as e:
                results[sid] = {"status": "error", "message": str(e)}
        return {"status": "success", "results": results}
